"""
iphone_chain.py — 아이폰 공급망 지표 수집 (매크로 탭 "아이폰 공급망" 카드용).

애플은 2019 회계연도부터 판매 대수를 공개하지 않는다. 기사에 나오는 대수는
조사기관(IDC·카운터포인트 등) 추정치이고 원자료는 유료다. 그래서 공개·무료로
받을 수 있는 두 가지를 묶는다:

  1) 애플 공시의 iPhone 분기 매출 — SEC EDGAR XBRL. 공식 수치지만 분기 종료
     후 약 1개월 뒤에야 나온다.
  2) 대만 공급망 3사 월매출 — 대만 상장사는 매달 10일 전 전월 매출을 공시한다.
     애플 실적보다 2~3개월 먼저 방향을 보여준다.
       2317 鴻海(폭스콘)  아이폰 조립 — 단, AI 서버 비중이 커서 순수 지표가 아니다
       4938 和碩(페가트론) 아이폰 조립
       3008 大立光(라간)   아이폰 카메라 렌즈 — 셋 중 가장 깨끗한 아이폰 신호

batch_macro.py가 매일 update_cache()를 부르고, 앱(ui_macro)은 결과 파일만 읽는다.
앱에서 직접 부르지 않는 이유는 DART와 같다 — 앱 서버(미국)에서 해외 공시
사이트 응답이 불안정하고, 한 화면 그리려고 공시 원문(건당 1MB)을 받을 일이 아니다.

캐시는 증분이다: 이미 파싱한 공시(accession)와 이미 받은 달은 다시 받지 않는다.
"""
import json
import os
import re
import time
from datetime import date, datetime, timedelta, timezone

import requests

_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(_DIR, "data", "macro", "iphone_cache.json")

KST = timezone(timedelta(hours=9))

# 표시 순서 = 카드 범례 순서
TW_COMPANIES = {
    "2317": "폭스콘",
    "4938": "페가트론",
    "3008": "라간",
}
_TW_KEEP_MONTHS = 40          # 화면은 3년 — 여유분만
_TW_OPENAPI = "https://openapi.twse.com.tw/v1/opendata/t187ap05_L"
_TW_MOPS = "https://mopsov.twse.com.tw/nas/t21/sii/t21sc03_{roc}_{m}_0.html"

# SEC는 "이름 + 이메일 형식 연락처" User-Agent를 요구한다. 2026-10-06 실측:
#   URL만 / URL이 섞인 것 / GitHub noreply 주소  → 403
#   "이름 이메일" 형식                            → 200
# 개인 메일을 외부에 보내지 않으려고 이메일 자리는 형식만 맞춘다. 실제 연락처를
# 남기고 싶으면 이 줄의 이메일만 바꾸면 된다(URL은 넣지 말 것 — 403이 난다).
_SEC_UA = {"User-Agent": "stockapp-batch (shinkiyeol9814-droid stockapp) contact@example.com"}
_APPLE_CIK = "0000320193"
_EDGAR_FIRST_RUN = 16         # 첫 실행 때 거슬러 볼 10-Q/10-K 수 (~4년)


# ── 캐시 입출력 ──────────────────────────────────────────────────────────────
def load_cache(path: str = CACHE_PATH) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        # 키 순서를 고정해야 내용이 같을 때 diff가 안 생긴다(불필요한 커밋 방지).
        json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, path)


# ── 1) 애플 iPhone 분기 매출 (SEC EDGAR) ─────────────────────────────────────
_CTX_RE = re.compile(r'<context id="([^"]+)">(.*?)</context>', re.S)
_FACT_RE = re.compile(
    r'<us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax[^>]*'
    r'contextRef="([^"]+)"[^>]*>(\d+)<'
)


def _iphone_periods_from_instance(xml: str) -> dict:
    """
    10-Q/10-K XBRL 인스턴스 -> {"시작|종료": iPhone 매출(USD)}.

    iPhone 매출은 제품축(srt:ProductOrServiceAxis = aapl:IPhoneMember)이 걸린
    차원 팩트라 EDGAR companyfacts API에는 안 나온다(차원 없는 팩트만 준다).
    그래서 공시 원문 인스턴스를 직접 읽는다.

    ⚠️ 축이 하나만 걸린 context만 쓴다 — 지역축까지 겹친 context가 섞이면
    지역별 쪼개진 값이 들어온다. "<xbrldi:explicitMember" 여는 태그로 센다
    (닫는 태그까지 세면 축 하나짜리도 2로 나와 전부 걸러진다 — 실제로 그랬다).
    """
    ctx = {}
    for m in _CTX_RE.finditer(xml):
        body = m.group(2)
        if "aapl:IPhoneMember" not in body or body.count("<xbrldi:explicitMember") != 1:
            continue
        s = re.search(r"<startDate>([^<]+)", body)
        e = re.search(r"<endDate>([^<]+)", body)
        if s and e:
            ctx[m.group(1)] = f"{s.group(1)}|{e.group(1)}"
    out = {}
    for m in _FACT_RE.finditer(xml):
        if m.group(1) in ctx:
            out[ctx[m.group(1)]] = int(m.group(2))
    return out


def _derive_quarters(periods: dict) -> dict:
    """
    {"시작|종료": 값} -> {분기말 "YYYY-MM-DD": 분기 매출}.

    애플 회계 4분기(7~9월)는 10-K에 연간 합계로만 나온다. 같은 회계연도
    시작일을 공유하는 "연간 − 9개월 누적"으로 계산한다.
    """
    q, ytd9, annual = {}, {}, {}
    for key, v in periods.items():
        s, e = (date.fromisoformat(x) for x in key.split("|"))
        days = (e - s).days
        if days < 120:
            q[e.isoformat()] = v
        elif 250 <= days <= 290:
            ytd9[s.isoformat()] = v
        elif days >= 350:
            annual[(s.isoformat(), e.isoformat())] = v
    for (s, e), v in annual.items():
        if e not in q and s in ytd9:
            q[e] = v - ytd9[s]
    return dict(sorted(q.items()))


def update_iphone(cache: dict) -> bool:
    """새 10-Q/10-K만 읽어 cache["iphone"]을 갱신. 바뀌었으면 True."""
    try:
        sub = requests.get(f"https://data.sec.gov/submissions/CIK{_APPLE_CIK}.json",
                           headers=_SEC_UA, timeout=30)
        sub.raise_for_status()
        rec = sub.json()["filings"]["recent"]
    except Exception as e:
        print(f"  iphone(EDGAR) 목록 실패: {type(e).__name__}: {e}")
        return False

    parsed = set(cache.get("edgar_parsed", []))
    periods = dict(cache.get("iphone_periods", {}))
    # 최근 공시 N건 "안에서" 안 읽은 것만 — "안 읽은 것 N건"으로 세면 매일
    # 그보다 더 오래된 공시를 N건씩 거슬러 올라가며 받게 된다.
    todo, seen = [], 0
    for i, form in enumerate(rec["form"]):
        if form not in ("10-Q", "10-K"):
            continue
        seen += 1
        if seen > _EDGAR_FIRST_RUN:
            break
        acc = rec["accessionNumber"][i]
        if acc not in parsed:
            todo.append((acc, rec["primaryDocument"][i]))
    if not todo:
        return False

    for acc, doc in todo:
        url = (f"https://www.sec.gov/Archives/edgar/data/{int(_APPLE_CIK)}/"
               f"{acc.replace('-', '')}/{doc.replace('.htm', '_htm.xml')}")
        try:
            r = requests.get(url, headers=_SEC_UA, timeout=60)
            r.raise_for_status()
            periods.update(_iphone_periods_from_instance(r.text))
            parsed.add(acc)
        except Exception as e:
            # 실패한 공시는 parsed에 안 넣는다 — 다음 실행이 다시 시도한다.
            print(f"  iphone(EDGAR) {acc} 실패: {type(e).__name__}: {e}")
        time.sleep(0.3)   # SEC 공정사용 한도(초당 10회) 안쪽

    quarters = _derive_quarters(periods)
    changed = quarters != cache.get("iphone")
    cache["iphone_periods"] = dict(sorted(periods.items()))
    cache["edgar_parsed"] = sorted(parsed)
    cache["iphone"] = quarters
    return changed or bool(todo)


# ── 2) 대만 3사 월매출 ───────────────────────────────────────────────────────
def _num(s):
    s = (s or "").replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def _tw_from_openapi() -> dict:
    """최신 1개월 -> {"YYYY-MM": {코드: {...}}}. 키 불필요, 대신 최신 달만 준다."""
    rows = requests.get(_TW_OPENAPI, timeout=30).json()
    out = {}
    for x in rows:
        code = x.get("公司代號")
        if code not in TW_COMPANIES:
            continue
        ym_roc = str(x.get("資料年月") or "")            # "11508" = 민국 115년 8월
        ym = f"{int(ym_roc[:-2]) + 1911}-{ym_roc[-2:]}"
        out.setdefault(ym, {})[code] = {
            "rev": _num(x.get("營業收入-當月營收")),
            "yoy": _num(x.get("營業收入-去年同月增減(%)")),
            "note": (x.get("備註") or "").strip(),
        }
    return out


_MOPS_ROW = re.compile(
    r"<td[^>]*>\s*(\d{4})\s*</td>\s*<td[^>]*>[^<]*</td>"      # 코드, 회사명
    r"\s*<td[^>]*>([^<]*)</td>\s*<td[^>]*>[^<]*</td>"          # 당월, 전월
    r"\s*<td[^>]*>[^<]*</td>\s*<td[^>]*>[^<]*</td>"            # 전년동월, 전월비
    r"\s*<td[^>]*>([^<]*)</td>"                                # 전년동월비(%)
    r"(?:\s*<td[^>]*>[^<]*</td>){3}\s*<td[^>]*>([^<]*)</td>",  # 누계 3칸, 비고
    re.I,
)


def _tw_from_mops(year: int, month: int) -> dict:
    """과거 1개월 -> {코드: {...}}. 월별 전체 상장사 표(약 440KB) 한 장."""
    r = requests.get(_TW_MOPS.format(roc=year - 1911, m=month), timeout=40,
                     headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    r.encoding = "big5"
    out = {}
    for m in _MOPS_ROW.finditer(r.text):
        code = m.group(1)
        if code in TW_COMPANIES:
            note = m.group(4).strip()
            out[code] = {"rev": _num(m.group(2)), "yoy": _num(m.group(3)),
                         "note": "" if note == "-" else note}
    return out


def _recent_months(n: int) -> list:
    """지난달부터 n개월 (최신 먼저) -> ["YYYY-MM", ...]."""
    today = datetime.now(KST).date()
    y, m = today.year, today.month
    out = []
    for _ in range(n):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        out.append(f"{y}-{m:02d}")
    return out


def update_taiwan(cache: dict, max_pages: int = 45) -> bool:
    """빠진 달만 채운다. 바뀌었으면 True."""
    tw = cache.setdefault("tw", {})
    changed = False

    # 최신 달은 OpenAPI로 (가볍고 안정적)
    try:
        for ym, rows in _tw_from_openapi().items():
            if tw.get(ym) != rows:
                tw[ym] = rows
                changed = True
    except Exception as e:
        print(f"  iphone(TWSE OpenAPI) 실패: {type(e).__name__}: {e}")

    # 과거 빈 달은 MOPS 월별 표로. 아직 공시 전인 달(매달 10일 전)은 표가 비어
    # 있거나 없다 — 다음 실행이 다시 시도한다.
    pages = 0
    for ym in _recent_months(_TW_KEEP_MONTHS):
        if ym in tw and all(c in tw[ym] for c in TW_COMPANIES):
            continue
        if pages >= max_pages:
            break
        y, m = map(int, ym.split("-"))
        try:
            rows = _tw_from_mops(y, m)
            pages += 1
            if rows:
                tw[ym] = {**tw.get(ym, {}), **rows}
                changed = True
        except Exception as e:
            print(f"  iphone(MOPS {ym}) 실패: {type(e).__name__}: {e}")
            pages += 1
        time.sleep(1.5)   # MOPS는 빠르게 긁으면 차단한다

    keep = set(_recent_months(_TW_KEEP_MONTHS))
    for ym in list(tw):
        if ym not in keep:
            del tw[ym]
            changed = True
    cache["tw"] = dict(sorted(tw.items()))
    return changed


def update_cache(path: str = CACHE_PATH) -> bool:
    """배치 진입점. 내용이 바뀌었을 때만 파일을 쓴다."""
    cache = load_cache(path)
    before = json.dumps(cache, sort_keys=True, ensure_ascii=False)
    update_iphone(cache)
    update_taiwan(cache)
    if json.dumps(cache, sort_keys=True, ensure_ascii=False) == before:
        return False
    # updated_at은 내용이 바뀔 때만 올린다 — 매일 시각만 바뀌어 커밋되는 것을 막는다.
    cache["updated_at"] = datetime.now(KST).isoformat(timespec="seconds")
    _save(path, cache)
    return True


if __name__ == "__main__":
    print("갱신:", update_cache())
