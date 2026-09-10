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
_TIMEOUT = 60
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
                     params={"crtfc_key": _api_key()}, timeout=_TIMEOUT)
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
    root = ET.fromstring(z.read(z.namelist()[0]))
    out = {}
    for e in root.iter("list"):
        sc = (e.findtext("stock_code") or "").strip()
        if sc:  # 상장사만 (비상장 11만 건은 쓸 일이 없다)
            out[sc] = [(e.findtext("corp_code") or "").strip(),
                       (e.findtext("corp_name") or "").strip()]
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
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
        for y, rows, fs in ex.map(one, targets):
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
        best = {"total_won": total * mult, "unit": unit_txt, "rows": n}
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
                     timeout=120)
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
                    "rcept_no": rep["rcept_no"]}
        except Exception as e:
            print(f"[dart_fin] {rep['rcept_no']} 파싱 실패: {type(e).__name__}: {e}")
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
