"""
index_valuation.py — 코스피·코스닥 지수 PER·PBR 일별 수집 (매크로 탭 밸류에이션 밴드용).

━━ 출처 ━━
KRX 정보데이터시스템의 지수 PER/PBR/배당수익률(후행, 직전 4개 분기 실적 기준).
2026년부터 KRX가 로그인을 요구해(비로그인 요청은 400 "LOGOUT") pykrx가 환경변수
KRX_ID / KRX_PW로 로그인한다 — Actions Secrets에 있다. 앱(Streamlit Cloud)에는
계정을 두지 않고, 배치가 받아 파일로 커밋하면 앱은 읽기만 한다(DART와 같은 구조).

선행PER 칸도 오지만 실제 값이 전부 0이라 쓰지 않는다. 그래서 매크로 탭의 코스피 PER은
**후행**이고, 증권사 레포트의 '12개월 선행 PER'보다 훨씬 높게 나온다(2026-11-12 실측:
후행 8.9배 vs 대신증권 선행 3.75배 — 지수가 같고 분모만 다르다. 선행 EPS는 FnGuide
컨센서스라 무료 경로가 없다).

⚠️ **배치는 23:30에 돌린다 — 18:30엔 PER·PBR이 아직 안 올라온다.** 커밋 이력으로 잰
값이다(2026-11-15): 18:31 실행은 11/10·11/12·11/13 세 번 다 그날 PER이 `0.0`이었고,
11/10 **20:49**에도 여전히 0이었다. 반면 10/20 23:24 · 10/21 23:33 · 10/22 23:35 ·
10/23 23:19 네 번은 **전부 그날 값이 들어와 있었다**. 즉 KRX는 20:49~23:19 사이에 올린다.
거래량도 18:30엔 잠정치라 나중에 수정된다(11/12: 233,733,121 → 244,834,422).
그동안 PER이 어쨌든 채워진 건 GitHub 기본 schedule이 8시간쯤 늦게 새벽 2시대에 한 번 더
돈 덕이었다 — 설계가 아니라 우연이고, 그 늦은 실행이 토요일 새벽에 걸리는 금요일분은
휴장일 판정에 막혀 월요일까지 0으로 남았다.

━━ 저장 ━━
data/macro/index_val/{연도}.json
    {"KOSPI": [["2026-10-19", PER, PBR, 종가, 거래량(주), 거래대금(원),
                외국인·기관·개인 순매수(원)], ...], "KOSDAQ": [...]}
(줄 길이는 4칸 → 6칸(거래량·대금, 10/22) → 9칸(투자자별 순매수, 10/22)으로 늘었다.
 앱은 짧은 줄을 0으로 채워 읽는다.)
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


def _fetch(ticker: str, start: date, end: date, market: str):
    """
    [(YYYY-MM-DD, PER, PBR, 종가, 거래량(주), 거래대금(원), 외국인, 기관, 개인 순매수(원))]
    — 연 단위로 끊어 받는다(긴 구간 한 번에 요청하면 거절될 수 있다).

    PER·PBR은 지수 펀더멘털, 거래량·거래대금은 지수 OHLCV, 투자자별 순매수는 시장 전체
    (market="KOSPI"/"KOSDAQ") 거래실적에서 온다. 날짜로 맞춰 한 줄에 합친다.
    빠진 날은 0으로 둔다(앱은 0을 "값 없음"으로 그린다).
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
        time.sleep(0.5)
        flow = {}
        try:
            tv = stock.get_market_trading_value_by_date(a, b, market)
            if tv is not None and not tv.empty:
                for idx, row in tv.iterrows():
                    flow[idx.strftime("%Y-%m-%d")] = (int(row.get("외국인합계") or 0),
                                                      int(row.get("기관합계") or 0),
                                                      int(row.get("개인") or 0))
        except Exception as ex:
            print(f"  ⚠️ {market} {a}~{b} 투자자별 순매수 조회 실패: {type(ex).__name__}: {ex}")
        if df is not None and not df.empty:
            for idx, row in df.iterrows():
                d = idx.strftime("%Y-%m-%d")
                per = float(row.get("PER") or 0)
                pbr = float(row.get("PBR") or 0)
                close = float(row.get("종가") or 0)
                v, val = vol.get(d, (0, 0))
                fo, inst, ind = flow.get(d, (0, 0, 0))
                out.append([d, round(per, 2), round(pbr, 2), round(close, 2), v, val, fo, inst, ind])
        s = e + timedelta(days=1)
        time.sleep(0.5)
    return out


def update(since: int | None = None) -> bool:
    if not (os.environ.get("KRX_ID") and os.environ.get("KRX_PW")):
        print("::error::KRX_ID / KRX_PW 가 없습니다 — Actions Secrets 확인")
        sys.exit(1)

    now = datetime.now(KST)
    # 장 마감(15:30) 확정치가 KRX에 올라오기 전엔 오늘 줄을 받지 않는다 — 장중에 돌리면 오늘
    # 줄이 미확정 값(PER 0, 거래량이 평소의 1/10)으로 저장된다(2026-10-22 실제로 그랬다).
    today = now.date() if now.hour >= 16 else now.date() - timedelta(days=1)
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
        rows = _fetch(ticker, start, today, name)
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

    # ⚠️ 줄은 들어왔는데 PER·PBR만 0인 경우가 있다 — KRX가 아직 안 올렸을 때다.
    # 조용히 넘어가면 그 날짜가 영영 0으로 남아 화면 밴드가 하루 전에서 멈춘다
    # (2026-11-13 금요일분이 그랬고, 월요일에 사용자가 눈으로 보고서야 알았다).
    # 다음 실행이 마지막 저장일부터 다시 받으므로 저절로 메워지지만, 안 메워지는
    # 경우(공개 시각이 또 밀렸다)를 알아차리려면 경고가 남아 있어야 한다.
    for name, rows in fetched.items():
        if rows and not (rows[-1][1] and rows[-1][2]):
            print(f"::warning title=지수 PER·PBR::{name} {rows[-1][0]} 의 PER·PBR이 0입니다"
                  " — KRX가 아직 안 올렸습니다. 다음 실행이 같은 날짜를 다시 받습니다")
    return changed


if __name__ == "__main__":
    from market_calendar import skip_if_closed
    skip_if_closed("지수 PER·PBR")
    since = None
    if "--since" in sys.argv:
        since = int(sys.argv[sys.argv.index("--since") + 1])
    update(since)
