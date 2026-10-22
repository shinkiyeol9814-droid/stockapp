"""
index_valuation.py — 코스피·코스닥 지수 PER·PBR 일별 수집 (매크로 탭 밸류에이션 밴드용).

━━ 출처 ━━
KRX 정보데이터시스템의 지수 PER/PBR/배당수익률(후행, 직전 4개 분기 실적 기준).
2026년부터 KRX가 로그인을 요구해(비로그인 요청은 400 "LOGOUT") pykrx가 환경변수
KRX_ID / KRX_PW로 로그인한다 — Actions Secrets에 있다. 앱(Streamlit Cloud)에는
계정을 두지 않고, 배치가 받아 파일로 커밋하면 앱은 읽기만 한다(DART와 같은 구조).

선행PER 칸도 오지만 실제 값이 전부 0이라 쓰지 않는다.

━━ 저장 ━━
data/macro/index_val/{연도}.json
    {"KOSPI": [["2026-10-19", PER, PBR, 종가, 거래량(주), 거래대금(원)], ...], "KOSDAQ": [...]}
(2026-10-22 이전 줄은 종가까지 4칸 — 앱은 4칸·6칸 둘 다 읽는다. 백필로 6칸으로 다시 채웠다.)
연도별로 나눈 이유: 하루 한 줄씩 늘 때마다 20년치 파일 전체를 다시 쓰면 커밋마다
수백 KB가 쌓인다(earnings_store를 분기별로 나눈 것과 같은 이유). 지난해 파일은
다시 바뀌지 않는다.

━━ 실행 ━━
python index_valuation.py              # 마지막 저장일 다음 날부터 오늘까지 (첫 실행은 백필)
python index_valuation.py --since 2006 # 시작 연도 지정 백필
"""
import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(_DIR, "data", "macro", "index_val")

INDEXES = {"KOSPI": "1001", "KOSDAQ": "2001"}
_DEFAULT_SINCE = 2006     # 2008 금융위기 저점까지 들어가도록


def _path(year: int) -> str:
    return os.path.join(OUT_DIR, f"{year}.json")


def _load_year(year: int) -> dict:
    try:
        with open(_path(year), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_year(year: int, data: dict) -> bool:
    """내용이 같으면 쓰지 않는다. 바뀌었으면 True."""
    if _load_year(year) == data:
        return False
    os.makedirs(OUT_DIR, exist_ok=True)
    tmp = _path(year) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        # 한 줄에 하루 — 매일 커밋 diff가 추가된 줄만 남도록
        f.write("{\n")
        parts = []
        for name in INDEXES:
            rows = data.get(name, [])
            body = ",\n".join("  " + json.dumps(r, ensure_ascii=False) for r in rows)
            parts.append(f' "{name}": [\n{body}\n ]')
        f.write(",\n".join(parts) + "\n}\n")
    os.replace(tmp, _path(year))
    return True


def last_saved_date() -> date | None:
    if not os.path.isdir(OUT_DIR):
        return None
    years = sorted(int(f[:4]) for f in os.listdir(OUT_DIR) if f[:4].isdigit() and f.endswith(".json"))
    for y in reversed(years):
        d = _load_year(y)
        last = [rows[-1][0] for rows in d.values() if rows]
        if last:
            return date.fromisoformat(min(last))   # 두 지수 중 더 늦은 쪽에 맞춰 다시 받는다
    return None


def _fetch(ticker: str, start: date, end: date):
    """
    [(YYYY-MM-DD, PER, PBR, 종가, 거래량(주), 거래대금(원))] — 연 단위로 끊어 받는다
    (긴 구간 한 번에 요청하면 거절될 수 있다).

    PER·PBR은 지수 펀더멘털, 거래량·거래대금은 지수 OHLCV에서 온다. 날짜로 맞춰 한 줄에 합친다.
    OHLCV가 비는 날은 0으로 둔다(앱은 0을 "거래량 없음"으로 그린다).
    """
    from pykrx import stock
    out = []
    s = start
    while s <= end:
        e = min(date(s.year, 12, 31), end)
        a, b = s.strftime("%Y%m%d"), e.strftime("%Y%m%d")
        df = stock.get_index_fundamental(a, b, ticker)
        time.sleep(0.5)    # KRX 부하 배려
        vol = {}
        try:
            ov = stock.get_index_ohlcv_by_date(a, b, ticker, name_display=False)
            if ov is not None and not ov.empty:
                for idx, row in ov.iterrows():
                    vol[idx.strftime("%Y-%m-%d")] = (int(row.get("거래량") or 0), int(row.get("거래대금") or 0))
        except Exception as ex:
            print(f"  ⚠️ {ticker} {a}~{b} 거래량 조회 실패: {type(ex).__name__}: {ex}")
        if df is not None and not df.empty:
            for idx, row in df.iterrows():
                d = idx.strftime("%Y-%m-%d")
                per = float(row.get("PER") or 0)
                pbr = float(row.get("PBR") or 0)
                close = float(row.get("종가") or 0)
                v, val = vol.get(d, (0, 0))
                out.append([d, round(per, 2), round(pbr, 2), round(close, 2), v, val])
        s = e + timedelta(days=1)
        time.sleep(0.5)
    return out


def update(since: int | None = None) -> bool:
    if not (os.environ.get("KRX_ID") and os.environ.get("KRX_PW")):
        print("::error::KRX_ID / KRX_PW 가 없습니다 — Actions Secrets 확인")
        sys.exit(1)

    today = datetime.now(KST).date()
    last = last_saved_date()
    if since:
        start = date(since, 1, 1)
    elif last:
        # 마지막 저장일도 다시 받는다 — 장중에 수동 실행하면 그날 줄이 장중 값(미확정 PER·
        # 거래량)으로 저장되는데, "다음 날부터"로 이어 받으면 그 줄이 영영 안 고쳐진다
        # (2026-10-22 백필을 10시대에 돌려 실제로 그렇게 됐다). 같은 날짜는 덮어쓴다.
        start = last
    else:
        start = date(_DEFAULT_SINCE, 1, 1)
    if start > today:
        print("이미 최신")
        return False
    print(f"수집 구간: {start} ~ {today}")

    fetched = {}
    for name, ticker in INDEXES.items():
        rows = _fetch(ticker, start, today)
        print(f"  {name}({ticker}): {len(rows)}일" + (f"  {rows[0][0]} ~ {rows[-1][0]}" if rows else ""))
        fetched[name] = rows

    if not any(fetched.values()):
        # 로그인 실패 시 pykrx는 예외 대신 빈 표를 돌려준다. 휴장일 하루짜리 구간도 비지만
        # 여러 날짜 구간이 통째로 비면 로그인/접속 문제로 본다.
        if (today - start).days >= 4:
            print("::error::KRX에서 받은 데이터가 없습니다 — 로그인 실패 여부를 확인하세요")
            sys.exit(1)
        return False

    changed = False
    years = sorted({int(r[0][:4]) for rows in fetched.values() for r in rows})
    for y in years:
        data = _load_year(y)
        for name, rows in fetched.items():
            merged = {r[0]: r for r in data.get(name, [])}
            for r in rows:
                if r[0].startswith(str(y)):
                    merged[r[0]] = r
            data[name] = [merged[k] for k in sorted(merged)]
        changed |= _save_year(y, data)
    print("저장:", "변경 있음" if changed else "변경 없음")
    return changed


if __name__ == "__main__":
    from market_calendar import skip_if_closed
    skip_if_closed("지수 PER·PBR")
    since = None
    if "--since" in sys.argv:
        since = int(sys.argv[sys.argv.index("--since") + 1])
    update(since)
