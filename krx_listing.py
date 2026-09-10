"""
krx_listing.py — KRX 전종목(종목코드/종목명) 조회. 다중 소스 폴백 + 디스크 캐시.

━━ 왜 이 모듈이 따로 있나 ━━
"장 끝나면 가치평가 검색이 안 된다"가 반복된 원인은 fdr 내부 구조다.

fdr 0.9.110의 `StockListing('KRX')`는
  ① KRX에 최종 영업일(max_work_dt)을 물어보고
  ② 서드파티 캐시 리포지토리(FinanceData/fdr_krx_data_cache)에서
     `data/listing/krx/{그 날짜}.csv` 를 내려받는다.

문제는 ①이 장 마감과 함께 당일로 넘어가는데, ②의 리포지토리에 당일 CSV가
올라오는 건 그보다 늦다는 것. 그 사이 구간에는 404 하드 실패가 되고,
fdr 쪽에는 **이전 날짜로 되돌아가는 폴백이 없다**(같은 파일의 지수목록
로더는 15일치를 되짚는데, 전종목 로더만 빠져 있다).

기존 코드는 이 실패를 즉시 3번 재시도했는데, 결정론적 404라 3번 다 같이
실패하고 KIND 폴백으로 떨어졌다. KIND까지 흔들리면 빈 DataFrame이 되어
"종목을 찾을 수 없습니다"가 떴고, 원격 소스 말고는 기댈 곳이 없어 계속
재발했다.

━━ 해결 ━━
소스를 4단으로 두고, 한 번이라도 성공하면 슬림 CSV(코드/종목명/시장)를
디스크에 남겨 **마지막 방어선**으로 쓴다. 원격이 전부 죽어도 검색은 된다.
슬림 CSV에는 주가/시총을 넣지 않는다 — 검색에 필요 없고, 매일 바뀌는 값을
리포지토리에 커밋하면 git이 계속 불어나기 때문(종목 구성은 상장/폐지 때만
바뀌므로 사실상 변화가 없다).

주가·시총·상장주식수가 필요한 쪽(`get_stocks_count`)은 이미 자체 폴백
체인을 갖고 있으므로 이 모듈은 "코드 ↔ 종목명" 해석만 책임진다.
"""
import io
import os
import time
from datetime import datetime, timedelta

import pandas as pd
import requests

# 슬림 캐시 위치 — 리포지토리에 시드로 커밋해 두고(콜드 스타트 대비),
# 조회가 성공할 때마다 최신본으로 덮어쓴다.
_DIR = os.path.dirname(os.path.abspath(__file__))
LISTING_CACHE = os.path.join(_DIR, "data", "listing", "krx_listing.csv")

_FDR_CACHE_REPO = (
    "https://raw.githubusercontent.com/FinanceData/fdr_krx_data_cache"
    "/refs/heads/master/data/listing/krx/{date}.csv"
)
_KIND_URL = "http://kind.krx.co.kr/corpgeneral/corpList.do?method=download&searchType=13"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# 조회 성공 시 남길 컬럼 (있는 것만)
_SLIM_COLS = ["Code", "Name", "Market"]
# 신고가 배치가 계산에 반드시 필요로 하는 시세 컬럼.
_PRICE_COLS = ["Marcap", "Close", "Volume", "ChagesRatio"]


def _normalize(df: pd.DataFrame) -> pd.DataFrame | None:
    """Code/Name을 갖춘 DataFrame으로 정규화. 쓸 수 없으면 None."""
    if df is None or len(df) == 0:
        return None
    if "Code" not in df.columns or "Name" not in df.columns:
        return None
    out = df.copy()
    out["Code"] = out["Code"].astype(str).str.strip().str.split(".").str[0].str.zfill(6)
    out["Name"] = out["Name"].astype(str).str.strip()
    out = out[(out["Code"].str.len() == 6) & (out["Name"] != "") & (out["Name"] != "nan")]
    out = out.drop_duplicates(subset=["Code"])
    if len(out) < 1000:
        # KRX 상장 종목은 2,800개 안팎 — 이보다 훨씬 적으면 잘린 응답으로 보고 버린다.
        return None
    return out.reset_index(drop=True)


# ── 소스 ①: fdr 기본 경로 ──────────────────────────────────────────────────────
def _from_fdr() -> pd.DataFrame | None:
    try:
        import FinanceDataReader as fdr

        return _normalize(fdr.StockListing("KRX"))
    except Exception as e:
        print(f"[krx_listing] fdr.StockListing 실패: {type(e).__name__}: {e}")
        return None


# ── 소스 ②: fdr가 쓰는 캐시 리포지토리를 직접, 날짜를 되짚어가며 ───────────────
def _from_cache_repo(max_back_days: int = 10) -> pd.DataFrame | None:
    """
    fdr이 하지 않는 '이전 날짜 되짚기'를 대신 한다. 장 마감 직후 당일 CSV가
    아직 안 올라온 구간을 이 소스가 메운다(전날 종목 구성으로도 검색은 충분).
    """
    today = datetime.now()
    for i in range(max_back_days):
        date_str = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        try:
            df = pd.read_csv(_FDR_CACHE_REPO.format(date=date_str), dtype={"Code": str})
            norm = _normalize(df)
            if norm is not None:
                print(f"[krx_listing] 캐시 리포지토리 {date_str} 사용 ({len(norm)}종목)")
                return norm
        except Exception:
            continue
    print(f"[krx_listing] 캐시 리포지토리 {max_back_days}일치 모두 실패")
    return None


# ── 소스 ③: KRX KIND (인코딩 명시) ─────────────────────────────────────────────
def _from_kind() -> pd.DataFrame | None:
    """
    KIND 응답은 EUC-KR인데 Content-Type에 charset이 없어서 requests가
    ISO-8859-1로 추정한다. 그대로 read_html하면 컬럼명이 깨져
    rename('회사명'→'Name')이 조용히 무효가 되고, 다음 줄의 df['Code']에서
    KeyError가 난다 — 인코딩을 반드시 명시해야 한다.
    """
    try:
        res = requests.get(_KIND_URL, headers=_HEADERS, timeout=10)
        res.encoding = "euc-kr"
        df = pd.read_html(io.StringIO(res.text), header=0)[0]
        df = df.rename(columns={"회사명": "Name", "종목코드": "Code"})
        norm = _normalize(df)
        if norm is None:
            print(f"[krx_listing] KIND 응답 파싱 실패 (컬럼: {list(df.columns)[:5]})")
        return norm
    except Exception as e:
        print(f"[krx_listing] KIND 실패: {type(e).__name__}: {e}")
        return None


# ── 소스 ④: 디스크 슬림 캐시 (최후의 방어선) ───────────────────────────────────
def _from_disk() -> pd.DataFrame | None:
    try:
        if not os.path.exists(LISTING_CACHE):
            return None
        df = pd.read_csv(LISTING_CACHE, dtype={"Code": str})
        norm = _normalize(df)
        if norm is not None:
            age_h = (time.time() - os.path.getmtime(LISTING_CACHE)) / 3600
            print(f"[krx_listing] 디스크 캐시 사용 ({len(norm)}종목, {age_h:.0f}시간 전)")
        return norm
    except Exception as e:
        print(f"[krx_listing] 디스크 캐시 실패: {type(e).__name__}: {e}")
        return None


_STALE_CACHE_DAYS = 7


def _save_to_disk(df: pd.DataFrame) -> None:
    """
    성공한 조회 결과를 슬림 CSV로 저장. 실패해도 조회 자체를 망치지 않는다.

    💡 단, **더 빈약한 결과로 기존 캐시를 덮어쓰지 않는다.**
    KIND(corpList.do)는 우선주(예: 삼성전자우)나 일부 종목을 빼고 주는데
    (2,759 vs fdr 2,873), fdr이 잠깐 흔들려 KIND로 폴백한 순간 더 완전했던
    캐시가 빈약한 목록으로 교체돼 버린다. 그러면 원격이 전부 죽은 뒤
    디스크 폴백으로 넘어갔을 때 우선주 검색이 안 된다.
    기존 캐시가 오래됐으면(상장/폐지 반영 필요) 크기와 무관하게 갱신한다.
    """
    try:
        cols = [c for c in _SLIM_COLS if c in df.columns]
        new_df = df[cols]

        if os.path.exists(LISTING_CACHE):
            age_days = (time.time() - os.path.getmtime(LISTING_CACHE)) / 86400
            if age_days < _STALE_CACHE_DAYS:
                try:
                    old_n = len(pd.read_csv(LISTING_CACHE, dtype={"Code": str}))
                    if len(new_df) < old_n:
                        print(
                            f"[krx_listing] 디스크 캐시 유지 "
                            f"(기존 {old_n}종목 > 신규 {len(new_df)}종목, {age_days:.1f}일 전)"
                        )
                        return
                except Exception:
                    pass  # 기존 캐시를 못 읽으면 그냥 새로 쓴다

        os.makedirs(os.path.dirname(LISTING_CACHE), exist_ok=True)
        new_df.to_csv(LISTING_CACHE, index=False, encoding="utf-8")
    except Exception as e:
        print(f"[krx_listing] 디스크 캐시 저장 실패(무시): {type(e).__name__}: {e}")


def fetch_krx_listing() -> pd.DataFrame:
    """
    KRX 전종목 목록을 반환한다. 원격 소스가 성공하면 디스크 캐시를 갱신한다.
    모든 소스가 실패하면 RuntimeError — 호출부(캐시 데코레이터)가 실패 결과를
    저장하지 않고 다음 호출에서 곧바로 재시도하게 하기 위함이다.
    """
    for source in (_from_fdr, _from_cache_repo, _from_kind):
        df = source()
        if df is not None:
            _save_to_disk(df)
            return df

    df = _from_disk()
    if df is not None:
        return df

    raise RuntimeError("KRX 종목 목록 조회 실패 (fdr/캐시리포/KIND/디스크 캐시 전부 실패)")


def fetch_krx_marcap() -> pd.DataFrame:
    """
    시세·시가총액 컬럼까지 포함한 KRX 목록.

    fetch_krx_listing()은 '종목코드/이름만 있으면 되는' 화면 검색용이라
    KIND·디스크 캐시(코드/이름만 있는 슬림 캐시)까지 폴백한다. 반면 신고가
    배치는 Marcap/Close/Volume/ChagesRatio가 없으면 계산 자체가 불가능하다.
    그래서 그 컬럼을 주는 소스(① fdr, ② 캐시 리포지토리)까지만 쓰고,
    없으면 조용히 빈손으로 넘어가지 않고 예외를 던진다.
    """
    for label, fn in (("fdr", _from_fdr), ("캐시 리포지토리", _from_cache_repo)):
        df = fn()
        if df is None:
            continue
        missing = [c for c in _PRICE_COLS if c not in df.columns]
        if missing:
            print(f"[krx_listing] {label}: 시세 컬럼 누락 {missing} — 다음 소스로")
            continue
        return df
    raise RuntimeError(
        "KRX 시세 목록을 어느 소스에서도 받지 못했습니다 "
        "(fdr / 캐시 리포지토리 모두 실패)."
    )


if __name__ == "__main__":
    # 배치에서 `python krx_listing.py`로 호출해 시드 CSV를 갱신한다.
    # 이모지 대신 ASCII만 쓴다 — Windows 콘솔(cp949)에서 직접 실행할 때
    # 이모지가 UnicodeEncodeError로 죽어서 성공한 갱신이 실패처럼 보인다.
    listing = fetch_krx_listing()
    print(f"[OK] {len(listing)} stocks -> {LISTING_CACHE}")
