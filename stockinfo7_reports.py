"""
stockinfo7_reports.py — 텔레그램 @companyreport(stockinfo7.com)가 모아 주는 증권사 리포트를
증권사 원문 PDF로 받아 batch_report의 PDF 파이프라인에 넘긴다. (2026-11-18)

━━ 구조 (실측) ━━
채널의 `🔗 원문보기`는 https://stockinfo7.com/stock/report/url/{id} 이고, 그 페이지의
**`og:url` 메타태그에 증권사 원문 주소가 그대로** 들어 있다. 대부분 PDF 직링크다.

    하나증권      www.hanaw.com/.../download.cmd?bbsSeq=…&bbsCd=2224   그대로 GET
    신한투자증권  bbs2.shinhansec.com/board/message/file.pdf.do?…    그대로
    현대차증권    www.hmsec.com/documents/research/…_ko.pdf          그대로
    유안타증권    file.myasset.com/sitemanager/upload/….pdf          그대로
    BNK투자증권   www.bnkfn.co.kr:43674/uploads/….pdf                그대로
    DS투자증권    www.ds-sec.co.kr/bbs/board.php?…  ← 게시판 페이지. 안의
                  download.php?…&no=0 을 **게시판 주소를 Referer로** 받아야 PDF가 온다

97건(139470~139566) 중 하나 39 · 신한 24 · DS 11 · 현대차 10 · 유안타 8 · BNK 5.
증권사는 이 여섯 곳뿐이다(넓은 수집처가 아니다). 그런데 그 여섯 곳의 리포트를 기존
텔레그램 채널들이 다 받아오지는 못해서, 97건 중 종목 리포트 27건이 새것이었다
(32건은 이미 있었고 38건은 종목 없는 산업·전략 노트).

⚠️ 하나증권 주소는 `bbsCd=2224`까지 **전체를 그대로** 써야 한다 — 그걸 빼면 200 + 1.6KB짜리
   "요청하신 페이지는 연결이 잠시 중단되었습니다" HTML이 온다(상태코드로는 못 거른다).
   그래서 받은 바이트가 `%PDF`로 시작하는지로 판정한다.

━━ 어디까지 읽었나 — id가 순번이다 ━━
로그인 없이 보이는 목록은 최신 10건뿐이다(`?page=1`부터는 빈 목록). 대신 id가 순번이고
(138000=09/18 → 139566=11/18, 하루 약 26건) 없는 id는 302를 준다. 그래서 목록 첫 페이지로
최신 id만 알아내고, 지난번 마지막 id 다음부터 거기까지를 걷는다.
상태는 `data/broker_report/.stockinfo7_state.json` — 레포트 배치가 어차피 매번 main에
커밋하는 폴더라 커밋(=앱 재시작)이 늘지 않고, 이름이 점으로 시작해 화면의
`glob('*.json')`에는 안 잡힌다.

━━ 안 되는 걸 어떻게 아나 ━━
GitHub 러너는 미국이라 국내 증권사가 해외 IP를 막으면 PDF가 하나도 안 온다(로컬은 한국 IP라
여기서 미리 확인할 수 없었다). 그 상황이 **조용히 0건**으로 끝나지 않게:
  · 실행마다 `::notice::`로 요약(새 id · PDF · 분석대상 · 실패) — 공개 API로 읽힌다
  · 증권사별 실패는 `::warning::`
  · **받을 게 있었는데 PDF가 한 건도 안 왔거나, 목록 자체를 못 읽으면** 상태 파일의
    `problem`에 적고, 워크플로우의 마지막 점검 단계가 그걸 보고 **빨간불** → 리포 주인에게 메일
    (매크로 배치의 `.fetch_fail.json`과 같은 수법 — 경고는 메일이 안 간다)
"""
import html as html_mod
import json
import os
import re
from urllib.parse import urljoin, urlparse

import requests

BASE = "https://stockinfo7.com"
LIST_URL = BASE + "/stock/report/list?page=0&search="
SHIM_URL = BASE + "/stock/report/url/{}"

_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(_DIR, "data", "broker_report", ".stockinfo7_state.json")

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"}

# 호스트 → 증권사. 이미 받은 리포트인지 대조하는 데 쓴다
# (카드에 찍히는 증권사는 Gemini가 PDF에서 읽은 값이다).
BROKERS = {
    "www.hanaw.com": "하나증권",
    "bbs2.shinhansec.com": "신한투자증권",
    "www.ds-sec.co.kr": "DS투자증권",
    "www.hmsec.com": "현대차증권",
    "file.myasset.com": "유안타증권",
    "www.bnkfn.co.kr": "BNK투자증권",
}

FIRST_RUN_BACK = 40     # 상태가 없을 때 최신 id에서 몇 건 전부터 (약 1.5일)
MAX_PER_RUN = 150       # 한 번에 걷는 상한 (며칠 밀려도 폭주하지 않게)
MAX_RETRY = 3           # 받기 실패한 id를 몇 번까지 시도하나(다음 실행들에서)

_OG_TITLE = re.compile(r'<meta property="og:title" content="([^"]*)"')
_OG_URL = re.compile(r'<meta property="og:url" content="([^"]*)"')


def _notice(msg):
    print(f"::notice title=레포트 stockinfo7::{msg}")


def _warn(msg):
    print(f"::warning title=레포트 stockinfo7::{msg}")


def load_state() -> dict:
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            s = json.load(f)
        return s if isinstance(s, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)


def latest_id(sess) -> int:
    """목록 첫 페이지의 가장 큰 id. 못 읽으면 예외."""
    r = sess.get(LIST_URL, timeout=25)
    r.raise_for_status()
    ids = [int(x) for x in re.findall(r"/stock/report/(?:url|view)/(\d+)", r.text)]
    if not ids:
        raise RuntimeError("목록 페이지에서 id를 하나도 못 찾았습니다(페이지 모양이 바뀌었을 수 있음)")
    return max(ids)


def resolve(sess, rid: int):
    """(제목, 원문 주소) 또는 None(없는 id — 302)."""
    r = sess.get(SHIM_URL.format(rid), timeout=25, allow_redirects=False)
    if r.status_code != 200:
        return None
    t, u = _OG_TITLE.search(r.text), _OG_URL.search(r.text)
    if not u:
        return None
    return (html_mod.unescape(t.group(1)).strip() if t else "",
            html_mod.unescape(u.group(1)).strip())


def split_title(title: str):
    """'[조선] 동성화인텍-겨울이라…[동성화인텍]' → ('동성화인텍', '[조선] 동성화인텍-겨울이라…').
    끝의 [종목명]은 stockinfo7이 붙인 것이고, 산업·전략 노트에는 없다."""
    m = re.search(r"\[([^\[\]]+)\]\s*$", title or "")
    if not m:
        return None, (title or "").strip()
    return m.group(1).strip(), title[:m.start()].strip()


def download_pdf(sess, url: str) -> bytes:
    """원문 PDF 바이트. PDF가 아니면 예외(이유를 담아서)."""
    host = urlparse(url).hostname or ""
    headers = {}
    if host.endswith("ds-sec.co.kr") and "board.php" in url:
        # DS투자증권은 게시판 페이지 → 첨부 download.php. 게시판 주소가 Referer여야 PDF가 온다.
        page = sess.get(url, timeout=30)
        page.raise_for_status()
        m = re.search(r'href="([^"]*bbs/download\.php[^"]*)"', page.text)
        if not m:
            raise RuntimeError("DS 게시판에 첨부 링크가 없습니다")
        headers["Referer"] = url
        url = urljoin(url, html_mod.unescape(m.group(1)))
    r = sess.get(url, timeout=40, headers=headers)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}")
    if not r.content.startswith(b"%PDF"):
        # 하나증권은 주소가 틀리면 200으로 오류 안내 HTML을 준다 — 상태코드로는 못 거른다
        raise RuntimeError(f"PDF가 아닙니다({r.headers.get('Content-Type', '?')}, {len(r.content):,}B)")
    return r.content


def collect(extract_text, is_wanted, already_have, tmp_dir="temp_pdfs"):
    """
    새 리포트를 받아 batch_report 문서 형식({"source", "text"})의 목록으로 돌려준다.

    extract_text(pdf_path) -> str               batch_report._extract_report_text
    is_wanted(title, text) -> (bool, 사유)      PDF 채널과 같은 블랙/화이트리스트
                                                 (text가 ""이면 제목만 본다 — 받기 전에 거른다)
    already_have(stock, broker, title) -> bool  최근 파일에 이미 있는지(받기 전에 거른다)

    상태 파일은 이 함수가 갱신한다. 실패해도 예외를 올리지 않는다 — 텔레그램 쪽 수집을
    막으면 안 된다. 대신 문제를 state["problem"]에 남겨 워크플로우 점검이 빨간불을 낸다.
    """
    state = load_state()
    sess = requests.Session()
    sess.headers.update(_UA)
    docs = []
    stats = {"new": 0, "have": 0, "skip": 0, "pdf": 0, "docs": 0, "fail": 0}
    fails = {}          # 증권사 → [사유, ...]

    try:
        top = latest_id(sess)
    except Exception as e:
        state["problem"] = f"목록을 못 읽었습니다: {type(e).__name__}: {e}"
        _warn(state["problem"])
        save_state(state)
        return []

    last = int(state.get("last_id") or 0) or (top - FIRST_RUN_BACK)
    start = max(last + 1, top - MAX_PER_RUN + 1)
    if last + 1 < start:
        _warn(f"밀린 id가 {top - last}건이라 최근 {MAX_PER_RUN}건만 받습니다"
              f"({last + 1}~{start - 1} 건너뜀)")
    retry = {int(k): int(v) for k, v in (state.get("retry") or {}).items()}

    def _failed(rid):
        n = retry.get(rid, 0) + 1
        if n < MAX_RETRY:
            retry[rid] = n
        else:
            retry.pop(rid, None)

    os.makedirs(tmp_dir, exist_ok=True)
    for rid in sorted(set(range(start, top + 1)) | set(retry)):
        try:
            got = resolve(sess, rid)
        except Exception:
            _failed(rid)
            continue
        if not got:
            retry.pop(rid, None)
            continue
        stats["new"] += 1
        title, url = got
        host = urlparse(url).hostname or "?"
        broker = BROKERS.get(host, host)
        stock, core = split_title(title)

        if not stock:
            # 끝에 [종목명]이 없으면 산업·전략 노트다(97건 중 38건). 지금 레포트 화면은
            # 종목·목표주가 카드라 넣을 자리가 없다 — 받지도 않는다.
            stats["skip"] += 1
            retry.pop(rid, None)
            continue
        if already_have(stock, broker, core):
            stats["have"] += 1
            retry.pop(rid, None)
            continue
        ok, _ = is_wanted(core, "")
        if not ok:
            stats["skip"] += 1
            retry.pop(rid, None)
            continue

        try:
            pdf = download_pdf(sess, url)
        except Exception as e:
            stats["fail"] += 1
            fails.setdefault(broker, []).append(f"#{rid} {type(e).__name__}: {e}"[:160])
            _failed(rid)
            continue
        retry.pop(rid, None)
        stats["pdf"] += 1

        path = os.path.join(tmp_dir, f"stockinfo7_{rid}.pdf")
        try:
            with open(path, "wb") as f:
                f.write(pdf)
            text = extract_text(path)
        finally:
            if os.path.exists(path):
                os.remove(path)
        ok, _ = is_wanted(core, text) if text else (False, "텍스트 없음")
        if not ok:
            stats["skip"] += 1
            continue
        stats["docs"] += 1
        docs.append({
            "source": f"stockinfo7:{rid}",
            "text": f"--- [stockinfo7 #{rid} · {broker} · {title}] ---\n{text}",
        })

    for broker, why in fails.items():
        _warn(f"{broker} PDF {len(why)}건 실패 — 예: {why[0]}")

    # ⚠️ 빨간불 기준: 받으러 갔는데 **한 건도** 못 받은 것. 한두 건 실패는 경고로 충분하다
    #    (링크가 죽은 리포트가 섞일 수 있다). 이미 있거나 산업 노트라 받기 전에 거른 건
    #    '받으러 간 것'에서 빠진다 — 그게 0이면 그냥 조용한 날이다.
    if stats["fail"] and not stats["pdf"]:
        state["problem"] = (f"PDF를 {stats['fail']}건 시도해 한 건도 못 받았습니다 — "
                            "증권사가 러너(해외 IP)를 막았을 수 있습니다. "
                            + "; ".join(f"{b}: {w[0]}" for b, w in fails.items()))
    else:
        state.pop("problem", None)

    state["last_id"] = max(top, int(state.get("last_id") or 0))
    state["retry"] = {str(k): v for k, v in sorted(retry.items())}
    state["last_run"] = stats
    save_state(state)
    _notice(f"새 id {stats['new']}건 → 이미 있음 {stats['have']} · 거름 {stats['skip']} · "
            f"PDF {stats['pdf']} · 분석 대상 {stats['docs']} · 실패 {stats['fail']} "
            f"(최신 id {top})")
    return docs


def audit() -> int:
    """워크플로우 마지막 단계용: 문제가 남아 있으면 ::error:: + 종료코드 1."""
    p = load_state().get("problem")
    if p:
        print(f"::error title=레포트 stockinfo7::{p}")
        return 1
    print("::notice title=레포트 stockinfo7::정상")
    return 0


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if "--audit" in sys.argv:
        sys.exit(audit())
