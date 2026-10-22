"""
foreign_holding.py — 코스피·코스닥 외국인 보유 비중(시가총액 기준) 수집.

━━ 무엇인가 ━━
순매수(index_valuation의 외국인 칸)는 "그날 얼마나 사고팔았나"이고, 이건 "시장 전체 중
외국인이 얼마나 들고 있나"다. 기사에서 말하는 "코스피 외국인 시가총액 비중 ○○%"와 같은 개념.

KRX에 시장 단위 시계열은 없고 종목별 외국인 보유량(전종목, 날짜 하나씩 — [12023]
MDCSTAT03701)만 있어서, 날짜마다 직접 합산한다:
    비중 = Σ(종가 × 외국인 보유수량) / Σ(종가 × 상장주식수)
pykrx의 공개 함수(get_exhaustion_rates_of_foreign_investment)는 종가를 버려서, 같은 요청을
하는 원시 클래스(외국인보유량_전종목)를 직접 쓴다. 로그인 세션은 pykrx가 KRX_ID/KRX_PW로 맺는다.

━━ 저장 ━━
data/macro/foreign_hold/{연도}.json
    {"KOSPI": [["2026-10-21", 비중%, 외국인보유 시총(원), 시가총액(원)], ...], "KOSDAQ": [...]}

━━ 실행 ━━
python foreign_holding.py                  # 마지막 저장일부터 오늘(16시 전이면 어제)까지 매일
python foreign_holding.py --backfill 2006  # 2006년부터 주 단위(주의 마지막 거래일) 백필
    매일 받으면 20년 × 2시장 = 1만 번 요청이라, 과거는 주 단위로 채운다. 비중은 천천히
    움직여서 주 단위로도 추세가 충분히 보인다.
"""
import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(_DIR, "data", "macro", "foreign_hold")
MARKETS = {"KOSPI": "STK", "KOSDAQ": "KSQ"}


def _num(s) -> float:
    try:
        return float(str(s).replace(",", "") or 0)
    except ValueError:
        return 0.0


def _path(year: int) -> str:
    return os.path.join(OUT_DIR, f"{year}.json")


def _load_year(year: int) -> dict:
    try:
        with open(_path(year), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_year(year: int, data: dict) -> bool:
    if _load_year(year) == data:
        return False
    os.makedirs(OUT_DIR, exist_ok=True)
    tmp = _path(year) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("{\n")
        parts = []
        for name in MARKETS:
            rows = data.get(name, [])
            body = ",\n".join("  " + json.dumps(r, ensure_ascii=False) for r in rows)
            parts.append(f' "{name}": [\n{body}\n ]')
        f.write(",\n".join(parts) + "\n}\n")
    os.replace(tmp, _path(year))
    return True


def holding_on(d: date, mkt_id: str):
    """그날 시장 전체 (비중%, 외국인보유 시총, 시가총액). 휴장일·실패면 None."""
    from pykrx.website.krx.market.core import 외국인보유량_전종목
    df = 외국인보유량_전종목().fetch(d.strftime("%Y%m%d"), mkt_id, 0)
    if df is None or df.empty or "FORN_HD_QTY" not in df.columns:
        return None
    px = df["TDD_CLSPRC"].map(_num)
    cap = float((px * df["LIST_SHRS"].map(_num)).sum())
    forn = float((px * df["FORN_HD_QTY"].map(_num)).sum())
    if cap <= 0:
        return None
    return round(forn / cap * 100, 3), int(forn), int(cap)


def _trading_days(start: date, end: date) -> list:
    """KRX 거래일 — index_val에 이미 저장된 날짜를 쓴다(같은 KRX 거래일). 없으면 평일."""
    days = set()
    vdir = os.path.join(_DIR, "data", "macro", "index_val")
    for y in range(start.year, end.year + 1):
        try:
            with open(os.path.join(vdir, f"{y}.json"), "r", encoding="utf-8") as f:
                for r in json.load(f).get("KOSPI", []):
                    days.add(date.fromisoformat(r[0]))
        except Exception:
            pass
    out = sorted(d for d in days if start <= d <= end)
    # index_val이 아직 없는 최근 날짜는 평일로 보충한다(휴장일이면 KRX가 빈 표를 줘서 걸러진다).
    tail = (out[-1] + timedelta(days=1)) if out else start
    d = tail
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _weekly(days: list) -> list:
    """주(ISO 주차)마다 마지막 거래일만."""
    last = {}
    for d in days:
        last[d.isocalendar()[:2]] = d
    return sorted(last.values())


def last_saved_date() -> date | None:
    if not os.path.isdir(OUT_DIR):
        return None
    years = sorted(int(f[:4]) for f in os.listdir(OUT_DIR) if f[:4].isdigit() and f.endswith(".json"))
    for y in reversed(years):
        d = _load_year(y)
        last = [rows[-1][0] for rows in d.values() if rows]
        if last:
            return date.fromisoformat(min(last))
    return None


def update(backfill_since: int | None = None) -> bool:
    if not (os.environ.get("KRX_ID") and os.environ.get("KRX_PW")):
        print("::error::KRX_ID / KRX_PW 가 없습니다 — Actions Secrets 확인")
        sys.exit(1)
    now = datetime.now(KST)
    # 장 마감 확정치 전(16시 전)엔 오늘을 받지 않는다 — index_valuation과 같은 이유.
    end = now.date() if now.hour >= 16 else now.date() - timedelta(days=1)

    if backfill_since:
        dates = _weekly(_trading_days(date(backfill_since, 1, 1), end))
        mode = "주 단위 백필"
    else:
        last = last_saved_date()
        start = last if last else end - timedelta(days=10)   # 마지막 저장일은 다시 받아 덮어쓴다
        dates = _trading_days(start, end)
        mode = "일 단위"
    print(f"{mode}: {len(dates)}일 ({dates[0] if dates else '-'} ~ {dates[-1] if dates else '-'})")

    got = {name: [] for name in MARKETS}
    fails = 0
    for i, d in enumerate(dates):
        for name, mkt in MARKETS.items():
            try:
                r = holding_on(d, mkt)
            except Exception as e:
                r = None
                fails += 1
                print(f"  ⚠️ {d} {name} 실패: {type(e).__name__}: {e}")
            if r:
                got[name].append([d.isoformat(), *r])
            time.sleep(0.3)
        if (i + 1) % 50 == 0:
            print(f"  … {i + 1}/{len(dates)} ({d}) 코스피 {got['KOSPI'][-1][1] if got['KOSPI'] else '-'}%")
    for name, rows in got.items():
        print(f"  {name}: {len(rows)}일" + (f"  최근 {rows[-1][0]} {rows[-1][1]}%" if rows else ""))

    if not any(got.values()):
        if len(dates) >= 4 or fails:
            print("::error::외국인 보유 데이터를 하나도 받지 못했습니다 — 로그인/접속 확인")
            sys.exit(1)
        return False

    changed = False
    years = sorted({int(r[0][:4]) for rows in got.values() for r in rows})
    for y in years:
        data = _load_year(y)
        for name, rows in got.items():
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
    since = None
    if "--backfill" in sys.argv:
        since = int(sys.argv[sys.argv.index("--backfill") + 1])
    else:
        skip_if_closed("외국인 보유 비중")
    update(since)
