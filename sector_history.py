"""
sector_history.py — 섹터(업종)별 등락률을 거래일마다 쌓는다. 배치·앱 공용(streamlit 의존 없음).

━━ 집계 ━━
KRX 업종분류(KIND, 158개) × 종목별 종가 등락률을 시가총액으로 가중한 평균.
구성종목 3개 미만 업종은 한 종목에 끌려가 빼고, 스팩은 거의 안 움직여 업종을 0% 쪽으로
끌어내리므로 뺀다. 앱의 섹터별 등락률(ui_sector)도 이 compute_sector_table을 쓴다 —
두 곳이 따로 계산하면 같은 날 숫자가 어긋난다.

━━ 저장 ━━
data/sector/daily/{YYYY-MM-DD}.json  {"date", "rows": [[업종, 등락률%, 종목수, 상승, 보합, 하락], ...]}
하루 한 파일(약 6KB)이고 한 번 쓰면 다시 쓰지 않는다 — 연도 파일 하나를 매일 다시 쓰면
커밋마다 큰 blob이 쌓인다. 업종은 등락률 높은 순.

━━ 출처와 날짜 ━━
시세는 fdr 캐시 리포지토리의 날짜별 CSV(FinanceData/fdr_krx_data_cache). 파일 이름이 곧
거래일이라 "어느 날 값인지"가 확실하다. 2026-03-08부터 있다. 주말·휴일 파일은 시세가
비어 있어 건너뛴다. 업종 분류는 지금 것을 쓴다(과거 날짜도 현재 분류 기준).

  python sector_history.py              # 최근 10일 중 빠진 날 채우기 (macro 배치가 매일 18시)
  python sector_history.py --since 2026-03-08   # 백필
"""
import io
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import requests

from krx_listing import _FDR_CACHE_REPO, _has_prices, _normalize

KST = timezone(timedelta(hours=9))
_DIR = os.path.dirname(os.path.abspath(__file__))
DAILY_DIR = os.path.join(_DIR, "data", "sector", "daily")
INDUSTRY_CACHE = os.path.join(_DIR, "data", "listing", "krx_industry.csv")
MIN_MEMBERS = 3
_KIND_URL = "http://kind.krx.co.kr/corpgeneral/corpList.do?method=download&searchType=13"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}


# ── 업종 분류 ────────────────────────────────────────────────────────────────
def industry_from_kind() -> dict | None:
    """KIND 다운로드(EUC-KR) 파싱. 실패하면 None."""
    try:
        res = requests.get(_KIND_URL, headers=_HEADERS, timeout=15)
        res.encoding = "euc-kr"   # charset 헤더가 없어 명시하지 않으면 한글이 깨진다
        df = pd.read_html(io.StringIO(res.text), header=0)[0]
        df = df.rename(columns={"회사명": "Name", "종목코드": "Code", "업종": "업종명"})
        if "Code" not in df.columns or "업종명" not in df.columns:
            print(f"[sector] KIND 컬럼 파싱 실패 (cols={list(df.columns)[:5]})")
            return None
        df["Code"] = df["Code"].astype(str).str.strip().str.zfill(6)
        df["업종명"] = df["업종명"].astype(str).str.strip()
        df = df[(df["Code"].str.len() == 6) & (~df["업종명"].isin(["", "nan"]))]
        if len(df) < 1000:
            print(f"[sector] KIND 응답이 너무 짧음({len(df)}행) — 잘린 응답으로 보고 버림")
            return None
        return dict(zip(df["Code"], df["업종명"]))
    except Exception as e:
        print(f"[sector] KIND 조회 실패: {type(e).__name__}: {e}")
        return None


def industry_from_disk() -> dict | None:
    try:
        df = pd.read_csv(INDUSTRY_CACHE, dtype={"Code": str})
        df["Code"] = df["Code"].astype(str).str.zfill(6)
        return dict(zip(df["Code"], df["업종명"].astype(str)))
    except Exception as e:
        print(f"[sector] 업종 디스크 캐시 실패: {type(e).__name__}: {e}")
        return None


def save_industry_to_disk(mapping: dict) -> None:
    """KIND가 성공했을 때만 시드를 갱신. 코드순 정렬 — 안 하면 내용이 같아도 매번 전체 diff."""
    try:
        os.makedirs(os.path.dirname(INDUSTRY_CACHE), exist_ok=True)
        (pd.DataFrame(sorted(mapping.items()), columns=["Code", "업종명"])
           .to_csv(INDUSTRY_CACHE, index=False, encoding="utf-8"))
    except Exception as e:
        print(f"[sector] 업종 캐시 저장 실패(무시): {type(e).__name__}: {e}")


# ── 집계 ─────────────────────────────────────────────────────────────────────
def compute_sector_table(quotes: pd.DataFrame, industry: dict) -> list:
    """[[업종, 등락률%, 종목수, 상승, 보합, 하락], ...] 등락률 높은 순. quotes는 Code/Name/ChagesRatio/Marcap."""
    df = quotes.copy()
    df["업종명"] = df["Code"].map(industry)
    df = df.dropna(subset=["업종명"])
    df = df[~df["Name"].astype(str).str.contains("스팩", na=False)]
    for c in ("ChagesRatio", "Marcap"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["ChagesRatio", "Marcap"])
    rows = []
    for name, g in df.groupby("업종명"):
        if len(g) < MIN_MEMBERS:
            continue
        w = g["Marcap"].sum()
        # 시총가중 평균 — 업종 지수가 실제로 움직인 정도에 가깝다. 단순평균이면 소형주가 순위를 좌우한다.
        rate = float((g["ChagesRatio"] * g["Marcap"]).sum() / w) if w else 0.0
        rows.append([name, round(rate, 2), int(len(g)), int((g["ChagesRatio"] > 0).sum()),
                     int((g["ChagesRatio"] == 0).sum()), int((g["ChagesRatio"] < 0).sum())])
    rows.sort(key=lambda r: -r[1])
    return rows


# ── 저장된 날 ────────────────────────────────────────────────────────────────
def saved_days() -> list:
    """저장된 거래일 목록, 최신순."""
    if not os.path.isdir(DAILY_DIR):
        return []
    return sorted((f[:-5] for f in os.listdir(DAILY_DIR) if f.endswith(".json")), reverse=True)


def load_day(day: str) -> list | None:
    try:
        with open(os.path.join(DAILY_DIR, f"{day}.json"), encoding="utf-8") as f:
            return json.load(f)["rows"]
    except Exception:
        return None


def _save_day(day: str, rows: list) -> None:
    os.makedirs(DAILY_DIR, exist_ok=True)
    path = os.path.join(DAILY_DIR, f"{day}.json")
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        f.write('{"date": "%s", "rows": [\n' % day)
        f.write(",\n".join(json.dumps(r, ensure_ascii=False) for r in rows))   # 한 줄에 업종 하나
        f.write("\n]}\n")
    os.replace(path + ".tmp", path)


def _quotes_for(day: str) -> pd.DataFrame | None:
    """그날 캐시 CSV(시세가 있는 거래일만). 없거나 휴일이면 None."""
    try:
        r = requests.get(_FDR_CACHE_REPO.format(date=day), timeout=20)
        if r.status_code != 200:
            return None
        norm = _normalize(pd.read_csv(io.StringIO(r.text), dtype={"Code": str}))
        if norm is None or not _has_prices(norm):
            return None
        return norm
    except Exception as e:
        print(f"[sector] {day} 시세 조회 실패: {type(e).__name__}: {e}")
        return None


def update(since: date) -> list:
    """since부터 오늘까지 빠진 평일을 채운다. 새로 쓴 날짜 목록."""
    have = set(saved_days())
    today = datetime.now(KST).date()
    industry = None
    written = []
    d = since
    while d <= today:
        day = d.isoformat()
        if d.weekday() < 5 and day not in have:
            q = _quotes_for(day)
            if q is not None:
                if industry is None:
                    industry = industry_from_kind()
                    if industry:
                        save_industry_to_disk(industry)
                    else:
                        industry = industry_from_disk()
                    if not industry:
                        raise RuntimeError("업종 분류를 받지 못했다 (KIND / 디스크 모두 실패)")
                rows = compute_sector_table(q, industry)
                if rows:
                    _save_day(day, rows)
                    written.append(day)
                    print(f"[sector] {day}: 업종 {len(rows)}개 저장 (1위 {rows[0][0]} {rows[0][1]:+.2f}%)")
        d += timedelta(days=1)
    return written


if __name__ == "__main__":
    since = datetime.now(KST).date() - timedelta(days=10)
    if "--since" in sys.argv:
        since = date.fromisoformat(sys.argv[sys.argv.index("--since") + 1])
    out = update(since)
    print(f"[sector] 새로 저장 {len(out)}일")
