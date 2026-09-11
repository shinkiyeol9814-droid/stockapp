"""
dart_fin.py — DART OpenAPI로 재고자산 / 수주잔고 추이를 뽑는다.

두 지표의 출처가 완전히 다르다는 점이 이 모듈의 핵심이다.

  재고자산 : 재무상태표 계정 → fnlttSinglAcntAll.json (가볍고 정형)
  수주잔고 : 계정과목이 아니다. 사업보고서 본문의 「수주상황」 표(또는
             재무제표 주석의 「주요 수주계약 현황」)에만 있다.
             → document.xml 로 공시원문(수 MB)을 받아 표를 파싱해야 한다.

수주잔고 표는 회사마다 양식이 다르다. 실제로 확인한 것만 해도:

  현대로템   품목|수주일자|납기|수주총액(수량,금액)|기납품액(..)|수주잔고(수량,금액)
  한화시스템 사업부문|해당회사|품목|... 앞에 컬럼이 2개 더 붙는다
  두산에너빌리티 주석 표. 헤더가 TH가 아니라 TD이고, 수주잔고 뒤에
             진행률·미청구공사·공사미수금이 더 붙는다 (= "마지막 숫자
             컬럼" 같은 편법이 통하지 않는다)
  LIG        구분|수주잔액 두 칸짜리, 단위도 억원

그래서 COLSPAN/ROWSPAN을 실제로 펼쳐 표를 격자로 복원한 뒤, '수주잔고'
헤더가 차지한 열을 찾아 그 열만 읽는다. 편법으로는 회사 하나 넘어갈 때마다
깨진다.
"""
import concurrent.futures
import io
import json
import os
import re
import zipfile
from datetime import datetime, timedelta

import requests

_DIR = os.path.dirname(os.path.abspath(__file__))
_DART_DIR = os.path.join(_DIR, "data", "dart")
_CORP_MAP = os.path.join(_DART_DIR, "corp_map.json")

_BASE = "https://opendart.fss.or.kr/api"
# (connect, read) — read는 바이트 간격 제한일 뿐이라 전체 시간은 _map_with_deadline이 막는다.
_TIMEOUT = (5, 20)
_DOC_TIMEOUT = (5, 60)
_INV_BUDGET = 30      # 재고자산 병렬 조회 전체 예산(초)
_REPORT_BUDGET = 150  # 정기보고서 원문 병렬 조회 예산(초) — 못 받은 원문은 pending으로 남아 다음 실행이 이어받는다
# corp_code 목록은 신규 상장/사명변경 때만 바뀐다. 매번 3.6MB를 받을 이유가 없다.
_CORP_MAP_TTL_DAYS = 7


def _api_key() -> str:
    """st.secrets 우선, 없으면 환경변수. 배치에서도 쓸 수 있게 둘 다 본다."""
    try:
        import streamlit as st
        k = st.secrets.get("DART_API_KEY", "")
        if k:
            return str(k).strip()
    except Exception:
        pass
    return (os.environ.get("DART_API_KEY") or "").strip()


class DartError(RuntimeError):
    pass


class DartTimeout(DartError):
    pass


def _redact(msg) -> str:
    """requests 예외 메시지엔 요청 URL이 통째로 들어가므로 API 키를 가린다."""
    return re.sub(r"(crtfc_key=)[^&\s'\")]+", r"\1***", str(msg))


def _map_until(fn, items, workers, budget):
    """budget초 안에 끝난 것만 입력 순서대로 돌려준다 -> (결과, 미완료 건수). 미완료 자리는 None."""
    ex = concurrent.futures.ThreadPoolExecutor(max_workers=workers)
    futs = [ex.submit(fn, it) for it in items]
    done, pending = concurrent.futures.wait(futs, timeout=budget)
    # with 블록은 종료 시 모든 스레드를 기다리므로 데드라인이 무력해진다.
    ex.shutdown(wait=False, cancel_futures=True)
    return [f.result() if f in done else None for f in futs], len(pending)


def _map_with_deadline(fn, items, workers, budget, what):
    """ex.map과 같은 순서로 결과를 돌려주되, budget초를 넘기면 기다리지 않고 DartTimeout."""
    results, pending = _map_until(fn, items, workers, budget)
    if pending:
        raise DartTimeout(f"{what}: {len(items)}건 중 {pending}건이 {budget}초 내 무응답")
    return results


_STATUS_MSG = {
    "010": "등록되지 않은 API 키입니다.",
    "011": "사용할 수 없는 API 키입니다 (오픈API 등록 여부 확인).",
    "012": "접근할 수 없는 IP입니다.",
    "013": "조회된 데이터가 없습니다.",
    "020": "요청 제한(일 20,000건)을 초과했습니다.",
    "800": "DART 시스템 점검 중입니다.",
    "900": "정의되지 않은 오류입니다.",
}


# ─────────────────────────────────────────────────────────────────────────────
# corp_code 매핑
# ─────────────────────────────────────────────────────────────────────────────
def _download_corp_map() -> dict:
    r = requests.get(f"{_BASE}/corpCode.xml",
                     params={"crtfc_key": _api_key()}, timeout=_DOC_TIMEOUT)
    r.raise_for_status()
    # 실패 시엔 zip이 아니라 JSON 에러가 온다.
    if r.content[:2] != b"PK":
        try:
            j = r.json()
            raise DartError(_STATUS_MSG.get(j.get("status"),
                                            j.get("message", "알 수 없는 오류")))
        except ValueError:
            raise DartError("corpCode 응답을 해석할 수 없습니다.")

    import xml.etree.ElementTree as ET
    z = zipfile.ZipFile(io.BytesIO(r.content))
    out = {}
    # 스트리밍 파싱 — 트리 전체를 올리면 피크 메모리가 약 13배(10MB→128MB).
    with z.open(z.namelist()[0]) as fp:
        for _ev, e in ET.iterparse(fp, events=("end",)):
            if e.tag != "list":
                continue
            sc = (e.findtext("stock_code") or "").strip()
            if sc:  # 상장사만 (비상장 11만 건은 쓸 일이 없다)
                out[sc] = [(e.findtext("corp_code") or "").strip(),
                           (e.findtext("corp_name") or "").strip()]
            e.clear()
    if not out:
        raise DartError("corpCode에서 상장사를 찾지 못했습니다.")
    os.makedirs(_DART_DIR, exist_ok=True)
    with io.open(_CORP_MAP, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    return out


def get_corp_map(force: bool = False) -> dict:
    """{종목코드: [corp_code, 회사명]}."""
    if not force and os.path.exists(_CORP_MAP):
        age = datetime.now() - datetime.fromtimestamp(os.path.getmtime(_CORP_MAP))
        if age < timedelta(days=_CORP_MAP_TTL_DAYS):
            try:
                with io.open(_CORP_MAP, encoding="utf-8") as f:
                    m = json.load(f)
                if m:
                    return m
            except Exception:
                pass
    try:
        return _download_corp_map()
    except Exception:
        # 갱신에 실패해도 오래된 캐시가 있으면 그걸 쓴다 — 종목코드 매핑은
        # 하루이틀 묵어도 거의 문제가 없다.
        if os.path.exists(_CORP_MAP):
            with io.open(_CORP_MAP, encoding="utf-8") as f:
                return json.load(f)
        raise


def corp_code_of(stock_code: str):
    m = get_corp_map()
    hit = m.get(str(stock_code).zfill(6))
    return (hit[0], hit[1]) if hit else (None, None)


# ─────────────────────────────────────────────────────────────────────────────
# 재고자산
# ─────────────────────────────────────────────────────────────────────────────
_INV_NAMES = ("재고자산",)
_INV_IDS = ("ifrs-full_Inventories", "ifrs_Inventories")
# 💡 매출액은 계정명이 회사마다 다르다 — "매출액", "수익(매출액)", "영업수익",
# "매출" 등. 이름만 보고 찾았더니 LG화학·한화에어로스페이스가 연도별로
# 들쭉날쭉하게 비었다. 표준계정코드(account_id)를 먼저 보고 이름은 보조로 쓴다.
_REV_IDS = ("ifrs-full_Revenue", "ifrs_Revenue", "dart_Revenue")
_REV_NAMES = ("매출액", "수익(매출액)", "영업수익", "매출", "수익")


def _acnt(corp_code: str, year: int, reprt: str = "11011", fs_div: str = "CFS"):
    r = requests.get(f"{_BASE}/fnlttSinglAcntAll.json",
                     params={"crtfc_key": _api_key(), "corp_code": corp_code,
                             "bsns_year": str(year), "reprt_code": reprt,
                             "fs_div": fs_div}, timeout=_TIMEOUT)
    j = r.json()
    if j.get("status") != "000":
        return None
    return j.get("list", [])


def _amt(row, field):
    v = (row.get(field) or "").replace(",", "").strip()
    if not v or v == "-":
        return None
    try:
        return float(v)
    except ValueError:
        return None


def get_inventory_series(stock_code: str, years: int = 5) -> dict:
    """
    최근 사업연도별 재고자산 + 매출액.

    반환 {"corp_name":..., "rows":[{"연도","재고자산","매출액","비율"}...]}
    금액 단위는 원.

    💡 연결(CFS)을 먼저 보고 없으면 별도(OFS)로 떨어진다 — 지주사나
    소형사는 연결 재무제표를 안 내는 경우가 있다.
    """
    cc, nm = corp_code_of(stock_code)
    if not cc:
        raise DartError(f"종목코드 {stock_code}의 DART corp_code를 찾지 못했습니다.")

    this_year = datetime.now().year
    targets = list(range(this_year - 1, this_year - 1 - years, -1))

    def one(y):
        for fs in ("CFS", "OFS"):
            rows = _acnt(cc, y, "11011", fs)
            if rows:
                return y, rows, fs
        return y, None, None

    got = {}
    for y, rows, fs in _map_with_deadline(one, targets, 5, _INV_BUDGET, "연간 재고자산"):
        if rows:
            got[y] = (rows, fs)

    out = []
    for y in sorted(got):
        rows, fs = got[y]
        inv = rev = None
        rev_by_name = None
        for r in rows:
            nm_ = (r.get("account_nm") or "").strip()
            id_ = (r.get("account_id") or "").strip()
            sj = r.get("sj_div")
            if sj == "BS" and inv is None and (id_ in _INV_IDS or nm_ in _INV_NAMES):
                inv = _amt(r, "thstrm_amount")
            elif sj in ("IS", "CIS"):
                if id_ in _REV_IDS and rev is None:
                    rev = _amt(r, "thstrm_amount")
                elif nm_ in _REV_NAMES and rev_by_name is None:
                    rev_by_name = _amt(r, "thstrm_amount")
        if rev is None:
            rev = rev_by_name
        if inv is not None:
            out.append({"연도": y, "재고자산": inv, "매출액": rev,
                        "비율": (inv / rev * 100) if rev else None,
                        "기준": fs})
    return {"corp_name": nm, "rows": out}


# 분기 코드 → (분기번호, 라벨). 사업보고서(11011)는 4분기 자리를 맡는다.
_QUARTERS = (("11013", 1), ("11012", 2), ("11014", 3), ("11011", 4))


def _find_inventory(rows):
    for r in rows:
        if r.get("sj_div") != "BS":
            continue
        if (r.get("account_id") or "").strip() in _INV_IDS:
            return _amt(r, "thstrm_amount")
    for r in rows:
        if r.get("sj_div") == "BS" and (r.get("account_nm") or "").strip() in _INV_NAMES:
            return _amt(r, "thstrm_amount")
    return None


def _find_cum_revenue(rows, reprt):
    """
    보고서 시점까지의 '누적' 매출.

    💡 분기보고서의 thstrm_amount는 당분기 3개월치이고 thstrm_add_amount가
    누적인데, 회사에 따라 누적을 안 채우기도 한다. 그래서 누적을 먼저 모아
    나중에 차분해 당분기를 만든다 — 어느 쪽이 비어도 계산이 성립한다.
    사업보고서(11011)는 thstrm_amount 자체가 연간 누적이다.
    """
    cur = add = None
    for r in rows:
        if r.get("sj_div") not in ("IS", "CIS"):
            continue
        id_ = (r.get("account_id") or "").strip()
        nm_ = (r.get("account_nm") or "").strip()
        if id_ in _REV_IDS or nm_ in _REV_NAMES:
            cur = _amt(r, "thstrm_amount")
            add = _amt(r, "thstrm_add_amount")
            if id_ in _REV_IDS:
                break  # 표준계정코드 매칭이 우선
    if reprt == "11011":
        return cur
    return add if add is not None else cur


def get_inventory_quarterly(stock_code: str, quarters: int = 12) -> dict:
    """
    분기말 재고자산 추이.

    반환 rows: [{"기간":"25.2Q", "재고자산":..., "매출액":(당분기),
                 "TTM매출":..., "비율":(재고/TTM매출 %), "기준":...}]

    ⚠️ 비율은 연간판(get_inventory_series)과 눈금을 맞추려고 최근 4개 분기
    매출 합(TTM)으로 나눈다. 당분기 매출로 나누면 값이 4배로 튀어 연간
    그래프와 나란히 놓을 수 없다.
    """
    cc, nm = corp_code_of(stock_code)
    if not cc:
        raise DartError(f"종목코드 {stock_code}의 DART corp_code를 찾지 못했습니다.")

    this_year = datetime.now().year
    # TTM에 직전 4개 분기가 더 필요해서 1년을 넉넉히 더 받는다.
    n_years = (quarters + 3) // 4 + 1
    combos = [(y, rc, qn)
              for y in range(this_year, this_year - n_years, -1)
              for rc, qn in _QUARTERS]

    def one(t):
        y, rc, qn = t
        for fs in ("CFS", "OFS"):
            rows = _acnt(cc, y, rc, fs)
            if rows:
                return y, rc, qn, rows, fs
        return y, rc, qn, None, None

    got = {}
    for y, rc, qn, rows, fs in _map_with_deadline(one, combos, 8, _INV_BUDGET, "분기 재고자산"):
        if rows:
            got[(y, qn)] = (rc, rows, fs)

    # 누적 매출 → 당분기 매출로 차분
    seq = sorted(got)
    out = []
    for y, qn in seq:
        rc, rows, fs = got[(y, qn)]
        inv = _find_inventory(rows)
        if inv is None:
            continue
        cum = _find_cum_revenue(rows, rc)
        rev = None
        if cum is not None:
            if qn == 1:
                rev = cum
            else:
                prev = got.get((y, qn - 1))
                if prev:
                    pcum = _find_cum_revenue(prev[1], prev[0])
                    if pcum is not None:
                        rev = cum - pcum
        out.append({"기간": f"{str(y)[2:]}.{qn}Q", "_y": y, "_q": qn,
                    "재고자산": inv, "매출액": rev, "기준": fs})

    # TTM(최근 4개 분기 합) — 앞쪽 3개 분기는 계산할 수 없어 비율이 빈다.
    for i, row in enumerate(out):
        window = out[max(0, i - 3):i + 1]
        vals = [w["매출액"] for w in window]
        if len(window) == 4 and all(v is not None for v in vals):
            ttm = sum(vals)
            row["TTM매출"] = ttm
            row["비율"] = (row["재고자산"] / ttm * 100) if ttm else None
        else:
            row["TTM매출"] = None
            row["비율"] = None

    return {"corp_name": nm, "rows": out[-quarters:]}


# ─────────────────────────────────────────────────────────────────────────────
# 수주잔고 — 공시원문 표 파싱
# ─────────────────────────────────────────────────────────────────────────────
_TAG = re.compile(r"<[^>]+>")
_TABLE = re.compile(r"<TABLE\b.*?</TABLE>", re.S | re.I)
_TR = re.compile(r"<TR\b[^>]*>(.*?)</TR>", re.S | re.I)
_CELL = re.compile(r"<(TH|TD)\b([^>]*)>(.*?)</\1>", re.S | re.I)
_BACKLOG_KW = re.compile(r"수주잔고|수주잔액|계약잔액")
# 💡 '기초수주잔액'은 기간 초 잔고다. 이걸 잡으면 분기가 바뀌어도 값이 그대로라
# 추이가 일직선이 된다 (HD현대중공업이 실제로 3분기 연속 46.93조로 나왔다).
_OPENING_KW = re.compile(r"기초|전기말|기시")
_TOTAL_KW = re.compile(r"^(합계|계|소계|총계)$")
_UNIT_RE = re.compile(r"단위\s*[:：]\s*([^\)<,\s]+)")

_UNIT_MULT = {"조원": 1e12, "십억원": 1e9, "억원": 1e8,
              "백만원": 1e6, "천원": 1e3, "원": 1.0}


def _txt(x: str) -> str:
    return re.sub(r"\s+", " ", _TAG.sub(" ", x)).strip()


def _attr(attrs: str, name: str) -> int:
    m = re.search(name + r'\s*=\s*"?(\d+)"?', attrs, re.I)
    return int(m.group(1)) if m else 1


def _grid(block: str):
    """
    COLSPAN/ROWSPAN을 펼쳐 표를 2차원 리스트로 복원한다.

    이 복원이 없으면 '수주잔고'가 몇 번째 열인지 알 수 없다. 헤더는
    병합 셀 때문에 데이터 행과 열 개수가 서로 다르기 때문이다.
    """
    out = []
    carry = {}  # 열 -> [셀텍스트, 남은 행 수]
    for rhtml in _TR.findall(block):
        row = {}
        for col in list(carry):
            t, rem = carry[col]
            row[col] = t
            if rem <= 1:
                del carry[col]
            else:
                carry[col] = [t, rem - 1]
        c = 0
        for _tag, attrs, inner in _CELL.findall(rhtml):
            while c in row:
                c += 1
            t = _txt(inner)
            cs, rs = _attr(attrs, "COLSPAN"), _attr(attrs, "ROWSPAN")
            for k in range(cs):
                row[c + k] = t
                if rs > 1:
                    carry[c + k] = [t, rs - 1]
            c += cs
        width = max(row) + 1 if row else 0
        out.append([row.get(i, "") for i in range(width)])
    return out


def _pick_column(grid):
    """(수주잔고 값이 든 열 index, 데이터 시작 행) 또는 (None, None)."""
    for i, row in enumerate(grid):
        cols = []
        for c, v in enumerate(row):
            t = v.replace(" ", "")
            if _BACKLOG_KW.search(t) and not _OPENING_KW.search(t):
                cols.append(c)
        if not cols:
            continue
        # 기초/기말이 나란히 있는 표에서는 기말이 뒤에 온다. 마지막 그룹만 쓴다.
        group = [cols[-1]]
        for c in reversed(cols[:-1]):
            if c == group[0] - 1 and row[c] == row[group[0]]:
                group.insert(0, c)
            else:
                break
        # 수주잔고가 '수량/금액'으로 한 번 더 쪼개지는 양식이 흔하다.
        if i + 1 < len(grid):
            sub = grid[i + 1]
            money = [c for c in group if c < len(sub) and "금액" in sub[c].replace(" ", "")]
            if money:
                return money[0], i + 2
        return group[-1], i + 1
    return None, None


def _num(x: str):
    x = x.replace(",", "").replace(" ", "").strip()
    neg = x.startswith("(") and x.endswith(")")
    if neg:
        x = x[1:-1]
    if re.fullmatch(r"-?\d+(\.\d+)?", x):
        v = float(x)
        return -v if neg else v
    return None


def _unit_mult(txt: str, pos: int):
    """표 바로 앞에 있는 '(단위 : 백만원)'을 찾아 배수로 바꾼다."""
    head = txt[max(0, pos - 3000):pos]
    last = None
    for m in _UNIT_RE.finditer(head):
        last = m.group(1).strip()
    if not last:
        return None, None
    for k, v in _UNIT_MULT.items():
        if last.endswith(k):
            return v, last
    return None, last  # 외화 표기 등 — 원화 금액으로 환산하지 않는다


def parse_backlog(txt: str):
    """공시원문에서 수주잔고 합계를 뽑는다. 못 찾으면 None."""
    best = None
    for tm in _TABLE.finditer(txt):
        block = tm.group(0)
        if not _BACKLOG_KW.search(_txt(block)[:4000]):
            continue
        grid = _grid(block)
        col, start = _pick_column(grid)
        if col is None:
            continue
        mult, unit_txt = _unit_mult(txt, tm.start())
        if mult is None:
            continue  # 단위를 모르면 숫자에 의미가 없다

        total = None
        acc, n = 0.0, 0
        items = []
        for row in grid[start:]:
            if col >= len(row):
                continue
            v = _num(row[col])
            if v is None:
                continue
            first = (row[0] if row else "").replace(" ", "")
            if _TOTAL_KW.match(first):
                total = v  # 합계 행이 있으면 그걸 쓴다 (더하면 2배가 된다)
                break
            acc += v
            n += 1
            # 부문/품목별 내역도 같이 들고 나온다. 어차피 합을 내려고 한 줄씩
            # 읽고 있어서 추가 비용이 없고, 화면에서 "어디서 늘었나"를 보려면
            # 이게 있어야 한다.
            label = (row[0] or "").strip()
            if label:
                items.append({"이름": label, "금액": v * mult})
        if total is None and n:
            total = acc
        if total is None:
            continue
        # 💡 합계 행이 개별 합과 심하게 어긋나면 원문 오타로 본다. 실제로
        # 삼성중공업 2025.03 사업보고서는 합계를 "323.129"로 적어놨다
        # (개별 합 317,698 + 5,431 = 323,129 — 쉼표가 마침표로 찍힌 것).
        if n and acc > 0 and not (acc * 0.5 <= total <= acc * 2.0):
            total = acc

        # 여러 표가 잡히면 앞에 나온 것을 쓴다. 「II. 사업의 내용 - 수주상황」이
        # 재무제표 주석보다 먼저 나오고 전사 기준이라 그쪽이 맞다.
        best = {"total_won": total * mult, "unit": unit_txt, "rows": n,
                "items": items}
        break
    return best


def _report_list(corp_code: str, bgn: str, end: str):
    r = requests.get(f"{_BASE}/list.json",
                     params={"crtfc_key": _api_key(), "corp_code": corp_code,
                             "bgn_de": bgn, "end_de": end,
                             "pblntf_ty": "A", "page_count": 100}, timeout=_TIMEOUT)
    j = r.json()
    if j.get("status") != "000":
        return []
    out = []
    for x in j.get("list", []):
        nm = x.get("report_nm", "")
        if not re.search(r"(사업|분기|반기)보고서", nm):
            continue
        m = re.search(r"\((\d{4})\.(\d{2})\)", nm)
        if not m:
            continue
        out.append({"rcept_no": x["rcept_no"], "period": f"{m.group(1)}.{m.group(2)}",
                    "name": nm, "dt": x.get("rcept_dt", "")})
    # [기재정정]이 있으면 같은 기간이 두 번 나온다 — 접수일이 늦은 쪽만 남긴다.
    latest = {}
    for x in sorted(out, key=lambda z: z["dt"]):
        latest[x["period"]] = x
    return sorted(latest.values(), key=lambda z: z["period"], reverse=True)


def _document_text(rcept_no: str):
    r = requests.get(f"{_BASE}/document.xml",
                     params={"crtfc_key": _api_key(), "rcept_no": rcept_no},
                     timeout=_DOC_TIMEOUT)
    if r.content[:2] != b"PK":
        return None
    z = zipfile.ZipFile(io.BytesIO(r.content))
    # 💡 사업보고서는 zip 안에 XML이 여러 개다 (본문 + 첨부). namelist()[0]이
    # 본문이라는 보장이 없어서, 첫 파일만 읽으면 한화오션 2025.12처럼
    # 첨부(1MB)만 읽고 본문(9.5MB)을 통째로 놓친다. 전부 이어붙인다.
    names = sorted(z.namelist(), key=lambda n: -z.getinfo(n).file_size)
    parts = []
    for n in names:
        b = z.read(n)
        try:
            parts.append(b.decode("utf-8"))
        except UnicodeDecodeError:
            parts.append(b.decode("euc-kr", errors="replace"))
    return "\n".join(parts) if parts else None


def get_backlog_series(stock_code: str, limit: int = 8) -> dict:
    """
    최근 정기보고서별 수주잔고 추이.

    ⚠️ 보고서 1건이 압축 400KB / 원문 6MB다. limit을 키우면 그만큼 느려진다.
    """
    cc, nm = corp_code_of(stock_code)
    if not cc:
        raise DartError(f"종목코드 {stock_code}의 DART corp_code를 찾지 못했습니다.")

    end = datetime.now().strftime("%Y%m%d")
    bgn = (datetime.now() - timedelta(days=365 * 4)).strftime("%Y%m%d")
    reps = _report_list(cc, bgn, end)[:limit]
    if not reps:
        return {"corp_name": nm, "rows": []}

    def one(rep):
        try:
            t = _document_text(rep["rcept_no"])
            if not t:
                return None
            got = parse_backlog(t)
            if not got:
                return None
            return {"기간": rep["period"], "수주잔고": got["total_won"],
                    "단위": got["unit"], "건수": got["rows"], "보고서": rep["name"],
                    "rcept_no": rep["rcept_no"], "내역": got.get("items") or []}
        except Exception as e:
            print(f"[dart_fin] {rep['rcept_no']} 파싱 실패: {type(e).__name__}: {_redact(e)}")
            return None

    rows = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        for r in ex.map(one, reps):
            if r:
                rows.append(r)
    rows.sort(key=lambda x: x["기간"])

    # 💡 이상치 제거. 수주잔고는 천천히 움직이는 잔액이라 한 분기에 90%가
    # 사라졌다 되돌아오는 일은 없다. 그렇게 보인다면 그 분기 보고서만 표가
    # 다르게 생겨 일부만 긁힌 것이다(한국항공우주가 실제로 그랬다:
    # 0.08조 → 13.08조). 그대로 그리면 V자 폭락 차트가 되어 안 보여주느니
    # 못하므로, 중앙값의 25%에 못 미치는 점은 버린다.
    dropped = 0
    if len(rows) >= 3:
        vals = sorted(r["수주잔고"] for r in rows)
        med = vals[len(vals) // 2]
        if med > 0:
            keep = [r for r in rows if r["수주잔고"] >= med * 0.25]
            dropped = len(rows) - len(keep)
            rows = keep
    return {"corp_name": nm, "rows": rows, "dropped": dropped}


# ─────────────────────────────────────────────────────────────────────────────
# 가동률 — 「II. 사업의 내용 · 생산 및 설비」의 가동률 표
# ─────────────────────────────────────────────────────────────────────────────
_UTIL_KW = re.compile(r"가동률")


def _pct(x: str):
    """'109.8%' -> 109.8. 퍼센트가 아니면 None."""
    x = x.replace(",", "").replace(" ", "").strip()
    if not x.endswith("%"):
        return None
    try:
        return float(x[:-1])
    except ValueError:
        return None


def parse_utilization(txt: str):
    """
    공시원문에서 부문별 가동률을 뽑는다.

    양식이 두 갈래인데 규칙은 같다 — '가동률'이 적힌 마지막 헤더 줄을 찾고,
    그 줄에서 가동률이 놓인 열만 읽으면 된다.

      현대로템   사업부문 | 가동가능시간 | 실제가동시간 | 평균가동률   (헤더 1줄)
      삼성전자   부문 | 품목 | 제58기반기(3칸 병합)
                 부문 | 품목 | 생산능력 대수 | 실제 생산 대수 | 가동률  (헤더 2줄)

    앞쪽 라벨 칸이 1개(사업부문)일 수도 2개(부문+품목)일 수도 있어서,
    숫자가 처음 나오기 전까지를 라벨로 본다.
    """
    rows, total = [], None
    for tm in _TABLE.finditer(txt):
        block = tm.group(0)
        if not _UTIL_KW.search(_txt(block)[:3000]):
            continue
        grid = _grid(block)
        if len(grid) < 2:
            continue

        # '가동률'이 적힌 마지막 줄 = 실제 열 이름이 확정된 헤더 줄
        hdr = col = None
        for i, row in enumerate(grid):
            hits = [c for c, v in enumerate(row) if _UTIL_KW.search(v.replace(" ", ""))]
            if hits:
                hdr, col = i, hits[-1]
        if hdr is None:
            continue

        # 💡 표를 하나만 보고 끝내면 안 된다. 삼성전자는 가동률 표가 둘인데
        # (DX는 '천대', DS·SDC는 '시간' 기준) 첫 표만 읽으면 반도체 부문이
        # 통째로 빠진다. 찾은 표를 모두 합치고 부문 이름으로 중복만 막는다.
        for row in grid[hdr + 1:]:
            if col >= len(row):
                continue
            v = _pct(row[col])
            if v is None:
                continue
            # 숫자가 처음 나오기 전까지가 라벨. 병합 때문에 같은 값이 연달아
            # 오는 경우가 있어 중복은 접는다.
            # 💡 '숫자로 파싱되면 멈춤'으로는 부족하다. HD현대중공업은 라벨
            # 칸 뒤에 '18,437천M/H' 같은 단위 붙은 칸이 와서 숫자 파싱에
            # 걸리지 않고 라벨에 끌려 들어왔다. 숫자가 섞인 칸이면 자른다.
            parts = []
            for c in range(min(col, len(row))):
                cell = (row[c] or "").strip()
                if not cell or re.search(r"\d", cell):
                    break
                if not parts or parts[-1] != cell:
                    parts.append(cell)
            label = " · ".join(parts[:3]) or "-"
            if _TOTAL_KW.match(label.replace(" ", "")):
                if total is None:
                    total = v
                continue
            if not any(x["부문"] == label for x in rows):
                rows.append({"부문": label, "가동률": v})

    if rows or total is not None:
        return {"rows": rows, "total": total}
    return _utilization_from_text(txt)


# 서술형으로 쓰는 회사들을 위한 폴백.
# 한화오션이 대표적이다 — 표가 아니라 문장으로 적는다:
#   "한화오션(주)의 가동률은 … ③ 평균가동률 = 100.0%"
#   "한화해양공정(산동)유한공사의 가동률은 … ③ 평균가동률 = 97.3%"
# 조선처럼 설비 가동률로 재기 곤란한 업종이 이 형식을 쓴다.
_UTIL_SENT = re.compile(r"평균\s*가동률\s*[=:]?\s*([\d.]+)\s*%")
# 💡 '가동률은'까지 요구하는 게 중요하다. 그냥 '가동률'로 잡으면 바로 뒤에
# 붙는 상투구 "…공장 생산설비의 가동률로 측정하기는 곤란하여"에 걸려
# 부문 이름이 '생산설비'로 나온다(한화오션에서 실제로 그랬다).
_UTIL_OWNER = re.compile(r"([^\s|.,·]{2,30}?)\s*의\s*가동률은")


def _utilization_from_text(txt: str):
    plain = re.sub(r"\s+", " ", _TAG.sub(" ", txt))
    out = []
    for m in _UTIL_SENT.finditer(plain):
        try:
            v = float(m.group(1))
        except ValueError:
            continue
        if not 0 < v <= 200:
            continue
        owners = _UTIL_OWNER.findall(plain[max(0, m.start() - 600):m.start()])
        label = owners[-1].strip() if owners else "전사"
        # 앞 문장이 공백 없이 붙는 경우가 있다("… 실 투입된 MH한화오션에코텍(주)의").
        label = re.sub(r"^[A-Za-z/]{1,4}(?=[가-힣])", "", label)
        if not any(x["부문"] == label for x in out):
            out.append({"부문": label, "가동률": v})
    return {"rows": out, "total": None} if out else None


def get_utilization(stock_code: str) -> dict:
    """가장 최근 정기보고서의 부문별 가동률."""
    cc, nm = corp_code_of(stock_code)
    if not cc:
        raise DartError(f"종목코드 {stock_code}의 DART corp_code를 찾지 못했습니다.")

    end = datetime.now().strftime("%Y%m%d")
    bgn = (datetime.now() - timedelta(days=400)).strftime("%Y%m%d")
    reps = _report_list(cc, bgn, end)
    for rep in reps[:2]:  # 최신이 파싱 안 되면 직전 것까지만 본다
        try:
            t = _document_text(rep["rcept_no"])
            if not t:
                continue
            got = parse_utilization(t)
            if got:
                return {"corp_name": nm, "기간": rep["period"],
                        "보고서": rep["name"], "rcept_no": rep["rcept_no"],
                        **got}
        except Exception as e:
            print(f"[dart_fin] 가동률 파싱 실패 {rep['rcept_no']}: {type(e).__name__}: {_redact(e)}")
    return {"corp_name": nm, "rows": [], "total": None}


def get_report_series(stock_code: str, limit: int = 12, parsed: dict | None = None) -> dict:
    """
    정기보고서를 **한 번만** 내려받아 수주잔고와 가동률을 함께 뽑는다.

    둘을 따로 조회하면 같은 원문(압축 400KB / 펼치면 6~10MB)을 두 번씩
    받게 된다. 12개 분기면 왕복이 24번이라 체감이 크다.
    parsed(직전 결과의 rcept_no별 파싱값)를 주면 이미 읽은 원문은 다시 받지 않는다.
    """
    cc, nm = corp_code_of(stock_code)
    if not cc:
        raise DartError(f"종목코드 {stock_code}의 DART corp_code를 찾지 못했습니다.")

    end = datetime.now().strftime("%Y%m%d")
    bgn = (datetime.now() - timedelta(days=365 * 4)).strftime("%Y%m%d")
    reps = _report_list(cc, bgn, end)[:limit]
    if not reps:
        return {"corp_name": nm, "backlog": [], "util": [], "dropped": 0, "parsed": {}, "pending": 0}
    # 공시 원문은 한 번 나오면 바뀌지 않는다(정정은 새 rcept_no) — 이미 파싱한 원문은 다시 받지 않는다.
    current = {r["rcept_no"] for r in reps}
    parsed = {k: v for k, v in (parsed or {}).items() if k in current}
    todo = [r for r in reps if r["rcept_no"] not in parsed]

    def one(rep):
        try:
            t = _document_text(rep["rcept_no"])
            if not t:
                return None
            bl = parse_backlog(t)
            ut = parse_utilization(t)
            return {
                "기간": rep["period"], "보고서": rep["name"],
                "rcept_no": rep["rcept_no"],
                "backlog": bl, "util": ut,
            }
        except Exception as e:
            print(f"[dart_fin] {rep['rcept_no']} 파싱 실패: {type(e).__name__}: {_redact(e)}")
            return None

    results, _ = _map_until(one, todo, 4, _REPORT_BUDGET)
    for r in results:
        if r:
            parsed[r["rcept_no"]] = r
    pending = len(current) - len(parsed)
    if not parsed and pending:
        raise DartTimeout(f"정기보고서 원문: {len(current)}건 모두 {_REPORT_BUDGET}초 내 수집 실패")
    got = sorted(parsed.values(), key=lambda x: x["기간"])

    backlog = [{"기간": g["기간"], "보고서": g["보고서"], "rcept_no": g["rcept_no"],
                "수주잔고": g["backlog"]["total_won"], "단위": g["backlog"]["unit"],
                "건수": g["backlog"]["rows"], "내역": g["backlog"].get("items") or []}
               for g in got if g["backlog"]]

    # 이상치 제거 사유는 get_backlog_series의 주석 참고 (V자 폭락 방지).
    dropped = 0
    if len(backlog) >= 3:
        vals = sorted(r["수주잔고"] for r in backlog)
        med = vals[len(vals) // 2]
        if med > 0:
            keep = [r for r in backlog if r["수주잔고"] >= med * 0.25]
            dropped = len(backlog) - len(keep)
            backlog = keep

    util = [{"기간": g["기간"], "보고서": g["보고서"],
             "rows": g["util"]["rows"], "total": g["util"].get("total")}
            for g in got if g["util"]]

    return {"corp_name": nm, "backlog": backlog, "util": util, "dropped": dropped,
            "parsed": parsed, "pending": pending}
