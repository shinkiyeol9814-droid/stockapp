"""
ui_macro.py — 매크로 지표 탭 (환율/금리/원자재/메모리/미국부채)
"""
import streamlit as st
import requests
import pandas as pd
import plotly.graph_objects as go
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
import json
import os
import re
import html
import urllib.parse

import kofia_flow
import lithium_source
from ui_common import (page_header, section_title, section_title_select,
                       run_with_progress)

_MACRO_DIR     = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "macro")
_LITHIUM_CACHE = os.path.join(_MACRO_DIR, "lithium_cache.json")
_DRAM_CACHE    = os.path.join(_MACRO_DIR, "dram_cache.json")
_DDR4_CACHE    = os.path.join(_MACRO_DIR, "ddr4_cache.json")
_USDEBT_CACHE  = os.path.join(_MACRO_DIR, "us_debt_cache.json")
_MARKET_CACHE  = os.path.join(_MACRO_DIR, "market_cache.json")
_FNG_CACHE     = os.path.join(_MACRO_DIR, "fng_cache.json")
_SP500_CACHE   = os.path.join(_MACRO_DIR, "sp500_val.json")
_INFLATION_CACHE = os.path.join(_MACRO_DIR, "us_inflation.json")
_CREDIT_CACHE    = os.path.join(_MACRO_DIR, "us_credit.json")
_COMPARE_DIR     = os.path.join(_MACRO_DIR, "compare")

_USDEBT_API = (
    "https://api.fiscaldata.treasury.gov/services/api/fiscal_service"
    "/v2/accounting/od/debt_to_penny"
)

# ── 상수 ─────────────────────────────────────────────────────────────────────
MARKET_ITEMS = [
    # (이름, 티커, 단위, 포맷, 표시배수 — 원/100엔처럼 관례상 배수 적용해 보여줄 때 사용)
    ("원/달러 환율",   "USDKRW=X", "₩",    ",.0f", 1),
    ("엔/달러 환율",   "JPY=X",     "¥",    ",.2f", 1),
    ("미국채 10년",    "^TNX",      "%",    ".2f", 1),
    ("WTI 유가",       "CL=F",      "$",    ",.2f", 1),
    ("금",             "GC=F",      "$",    ",.0f", 1),
    ("은",             "SI=F",      "$",    ",.2f", 1),
    ("구리",           "HG=F",      "$/lb", ".3f", 1),
    ("리튬 탄산염",     "_LITHIUM_",  "CNY/t",",.0f", 1),
    # 💡 3개씩 배치되는 그리드에서 DDR5/DDR4 쌍이 줄바꿈으로 갈라지지 않도록
    # 둘을 맨 끝 줄에 함께 둔다. 은(銀)을 금 옆에 넣으면서 연방부채를 리튬 옆으로 올렸다.
    ("미국 연방부채",   "_USDEBT_",  "조 달러", ",.3f", 1),
    ("DDR5 16Gb Spot", "_DRAM_",    "$",    ",.3f", 1),
    ("DDR4 16Gb Spot", "_DDR4_",    "$",    ",.3f", 1),
    # 💡 맨 끝(DDR4 옆 빈 칸)에 붙였다 — 중간에 끼우면 DDR5/DDR4 쌍이 줄바꿈으로 갈라진다.
    # 24시간 거래라 "전일"은 야후의 직전 UTC 자정 종가 기준이다.
    ("비트코인",        "BTC-USD",   "$",    ",.0f", 1),
    ("공포·탐욕 지수",   "_FNG_",     "점",   ".0f", 1),
]

# 카드 밑에 최신 뉴스 헤드라인 1건을 붙일 티커 → (검색어, 제목 필수 키워드).
#
# 💡 "최신 1건"을 고르는 규칙은 실측으로 정했다:
#   · 구글 뉴스 RSS는 관련도순이라 그대로 1위를 쓰면 며칠 묵은 기사가 박혀
#     안 바뀐다(카드는 ▲+4.34%인데 밑에 "3.5% 급락…1개월來 최저"가 붙었다).
#   · 그렇다고 받은 100건 중 가장 새것을 고르면 WTI에서 베트남 기계번역
#     기사("오늘 연료 가격: 세계 유가 급등…")가 올라온다.
#   → 제목에 필수 키워드가 있는 것만 남기고 그중 가장 새것을 쓴다. 위 기계번역
#     기사는 "WTI"가 제목에 없어 걸러진다.
#   · 검색어에 when:7d를 붙인다. 안 붙이면 100건 풀이 관련도 위주로 채워져
#     최근 기사가 덜 들어온다 — WTI가 31.6시간 전 기사 → 10.5시간 전 기사로 당겨졌다.
#
# 모든 카드에 붙인다(급등·급락 사유를 카드에서 바로 보려고). 뉴스는 시세와 같이
# 병렬로 받아 오므로 11개를 다 붙여도 1초 남짓이다(2026-10-16 실측 1.08초).
# 헤드라인 앞에 경과("2일")가 붙어, 기사가 뜸한 카드(DDR4 등)는 오래된 게 보인다.
#
# 키워드는 카드마다 실제 결과를 보고 골랐다:
#   · 엔/달러 — "엔"만 쓰면 "원·달러 환율… 1,350원" 같은 원화 기사가 걸려 "엔화"로 좁힌다.
#   · 은      — "은" 한 글자는 거의 모든 문장에 들어 있어 "은값/은 가격/국제 은"으로만.
_NEWS_QUERY = {
    "USDKRW=X":  ("원달러 환율", ("환율",)),
    "JPY=X":     ("엔달러 환율 엔화", ("엔화", "엔/달러", "엔달러", "엔·달러")),
    "^TNX":      ("미 국채금리 10년물", ("국채", "10년")),
    "CL=F":      ("WTI 유가", ("WTI",)),
    "GC=F":      ("국제 금값", ("금값", "금 가격", "금시세", "금 시세", "국제 금")),
    "SI=F":      ("국제 은값", ("은값", "은 가격", "은시세", "은 시세", "국제 은")),
    "HG=F":      ("구리 가격", ("구리",)),
    "_LITHIUM_": ("탄산리튬 가격", ("리튬",)),
    "_USDEBT_":  ("미국 국가부채", ("부채",)),
    "_DRAM_":    ("DDR5 D램 가격", ("DDR5", "D램", "디램")),
    # 💡 DDR4만 검색어가 "DDR4 가격", 키워드가 ("DDR4",) 하나였다 — 그래서 7일치
    #    풀 8건 중 제목에 'DDR4'가 든 게 0건이라 매번 기간 제한 없는 재조회로
    #    넘어가, 7일(실측 175시간) 묵은 기사가 카드에 박혔다(2026-11-10 제보).
    #    국내 기사는 DDR4를 제목에 거의 안 쓰고 'D램'·'범용 D램'이라 부른다.
    #    DDR5와 같은 모양으로 맞추면 7일치 안에서 걸린다(실측 36시간).
    "_DDR4_":    ("DDR4 D램 가격", ("DDR4", "D램", "디램")),
    "BTC-USD":   ("비트코인", ("비트코인", "BTC")),
}
_NEWS_RECENT = " when:7d"


def _news_age(dt, now) -> str:
    """'3시간' / '2일' 같은 짧은 경과 표기 — 헤드라인이 언제 것인지 보이게."""
    h = (now - dt).total_seconds() / 3600
    if h < 1:
        return "방금"
    if h < 24:
        return f"{int(h)}시간"
    return f"{int(h // 24)}일"


def _today_ms_utc_midnight() -> int:
    """
    오늘 날짜(KST 기준)를 UTC 자정 epoch ms로. batch_macro._date_to_ms와 같은 규칙.

    💡 예전엔 각 캐시 함수가 datetime.combine(datetime.today().date(), ...)로
    직접 계산했다. 그러면 (a) 로컬(KST)에서는 KST 자정이 저장돼 화면에
    "전날 15:00"으로 표시되고, (b) Streamlit Cloud(UTC)에서는 UTC 날짜가
    쓰여 한국 기준 날짜와 하루 어긋날 수 있었다. 날짜는 KST로 정하고
    저장은 UTC 자정으로 통일해 두 문제를 함께 없앤다.
    """
    d = datetime.now(_KST).date()
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000)

def _weekday_gap(then: datetime, now: datetime) -> int:
    """
    then 다음날부터 now까지의 영업일(주말 제외) 수.
    시세 최신성 경고가 주말·휴일에 오탐을 내지 않게 하려고 쓴다.
    (공휴일 달력까지 두지는 않는다 — 휴일은 영업일 1일로 계산돼
     허용 범위 안에 들어오므로 실질적으로 충분하다.)
    """
    d0, d1 = then.date(), now.date()
    if d1 <= d0:
        return 0
    return sum(
        1
        for i in range(1, (d1 - d0).days + 1)
        if (d0 + timedelta(days=i)).weekday() < 5
    )


# ── 데이터 함수 ───────────────────────────────────────────────────────────────
# "max"는 화면 기간 선택에는 없다 — 마일스톤(N개월 최고/최저) 판정을 위해
# 잘라내지 않은 전체 이력을 받을 때 쓰는 내부용 키.
_PERIOD_DAYS = {"1mo": 30, "3mo": 90, "6mo": 180, "1y": 365, "3y": 1095, "5y": 1825, "max": 100000}
# 야후 history()는 "3y"를 받지 않는다(1y·2y·5y·10y만). 3년은 5년치를 받아 잘라 쓴다.
_YF_PERIOD = {"3y": "5y"}


@st.cache_data(ttl=600, show_spinner=False)
def _market_cache() -> dict:
    """
    batch_macro가 남긴 야후 시세 스냅샷. Streamlit Cloud에서 야후 호출이
    통째로 실패해 환율·금리·원자재 카드가 전부 N/A로 떨어진 적이 있어,
    DART와 같은 방식으로 러너가 받아둔 값을 폴백으로 쓴다.
    """
    try:
        with open(_MARKET_CACHE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _market_cache_history(ticker: str, period: str = "1y") -> pd.DataFrame | None:
    """스냅샷 파일에서 한 티커의 시계열을 꺼내 화면 포맷(DataFrame)으로."""
    raw = _market_cache().get(ticker) or []
    if not raw:
        return None
    df = pd.DataFrame(raw, columns=["ts", "price"])
    df["date"] = pd.to_datetime(df["ts"], unit="ms")
    df = df.set_index("date").drop(columns=["ts"]).sort_index()
    cutoff_days = _PERIOD_DAYS.get(period, 365)
    if cutoff_days < 100000:
        df = df[df.index >= pd.Timestamp.now() - pd.Timedelta(days=cutoff_days)]
    return df if not df.empty else None


@st.cache_data(ttl=300, show_spinner=False)
def _get_price_history(ticker: str, period: str = "1y") -> pd.DataFrame | None:
    """
    야후가 가끔 요청한 기간보다 훨씬 짧은(불완전한) 구간만 반환하는 경우가
    있어(예: 1년 요청했는데 3개월치만 옴), 그런 응답을 그대로 5분간 캐싱해
    버리면 차트가 앞부분이 잘린 것처럼 보인다. 반환된 구간이 요청 기간의
    절반에도 못 미치면 불완전한 응답으로 간주하고 재시도한다.
    """
    expected_days = _PERIOD_DAYS.get(period, 365)
    last_df = None
    for attempt in range(3):
        try:
            # import까지 try 안에 둔다 — 밖에 두면 yfinance가 없거나 깨졌을 때
            # 예외가 그대로 올라가 매크로 탭 전체가 죽는다(폴백도 못 탄다).
            import yfinance as yf
            hist = yf.Ticker(ticker).history(period=_YF_PERIOD.get(period, period))
            if hist.empty:
                continue
            df = hist[["Close"]].rename(columns={"Close": "price"})
            if df.index.tz is not None:
                df.index = df.index.tz_convert(None)
            if period in _YF_PERIOD:
                df = df[df.index >= df.index[-1] - pd.Timedelta(days=expected_days)]
            last_df = df
            span_days = (df.index[-1] - df.index[0]).days
            if span_days >= expected_days * 0.5:
                return df
        except Exception:
            continue

    # 라이브가 실패했거나 토막난 응답만 왔으면 배치 스냅샷으로 폴백한다.
    cached = _market_cache_history(ticker, period)
    if cached is not None and (last_df is None or len(cached) > len(last_df)):
        return cached
    return last_df


_KST = timezone(timedelta(hours=9))


@st.cache_data(ttl=300, show_spinner=False)
def _get_last_and_prev_close(ticker: str):
    """
    야후의 실시간 현재가/전일종가를 직접 사용.
    일봉 배열의 [-1]/[-2]로 전일종가를 추론하면 장중 진행형 봉이 계속
    갱신되거나 날짜 경계를 지날 때 기준점이 흔들려 등락률이 튈 수 있어서,
    변동 없는 전일 공식 종가를 기준으로 삼기 위함.
    regularMarketTime(최종 시세 갱신 시각)도 같이 반환 — 미국채 수익률처럼
    거래시간이 짧은 지표는 한국 시간대엔 몇 시간 전 값에서 멈춰있을 수 있어
    "언제 기준 값인지"를 화면에 같이 보여주기 위함.

    ⚠️ **전일종가는 `info["regularMarketPreviousClose"]`를 쓴다 —
    `fast_info.previous_close`가 아니다.** 이름과 달리 그 값은 전일 종가가
    아니라 "1주 1시간봉(prepost 포함)을 **달력 날짜**로 묶어 마지막 봉의
    종가를 고른 뒤 [-2]"다(yfinance 1.7.0 `scrapers/quote.py`). 거의 24시간
    도는 선물에서는 이게 전혀 다른 값이 된다 — WTI(CL=F)는 17:00 ET에 닫고
    **같은 날** 18:00 ET에 다음 세션을 여는데, 그러면 '11/09의 마지막
    1시간봉'이 23:00 ET, 즉 이미 11/10 세션으로 6시간 들어간 값이다.

    2026-11-10 실측: 유가가 86.85 → 85.75로 **-1.27%** 빠진 날 카드가
    `▲ +0.07%`로 떴다(previous_close가 85.67이라). 같은 날 금·은·구리·환율도
    0.2~1.0%p씩 어긋났고, 부호가 뒤집힌 건 WTI였다. 날짜 묶기가 구조적으로
    깨지는 것도 같이 보였다 — 글로벡스가 일요일 18:00 ET에 주를 여니까
    "11/08(일)" 행이 유령으로 하나 생긴다.
    `regularMarketPreviousClose`는 야후 사이트가 그대로 쓰는 공식 전일
    종가(선물은 정산가)라 이 문제가 없다.
    """
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        fi = t.fast_info
        last = float(fi.last_price)
        prev = float(fi.previous_close)   # info가 실패할 때만 쓰는 폴백
        if last <= 0 or prev <= 0:
            return None, None, None
        last_update = None
        try:
            info = t.get_info()
            reg_prev = info.get("regularMarketPreviousClose")
            if reg_prev and float(reg_prev) > 0:
                prev = float(reg_prev)
            rt = info.get("regularMarketTime")
            if rt:
                last_update = datetime.fromtimestamp(rt, tz=timezone.utc).astimezone(_KST)
        except Exception:
            pass
        return last, prev, last_update
    except Exception:
        return None, None, None


@st.cache_data(ttl=86400, show_spinner=False)
def _get_full_history(ticker: str) -> pd.DataFrame | None:
    """
    마일스톤(사상/N개월 최고·최저) 판정용 전체 이력.
    화면용 _get_price_history와 분리한 이유: 그쪽은 "요청 기간의 절반도 안 오면
    불완전 응답"이라는 재시도 규칙이 있어서 period='max'에는 그대로 못 쓴다.
    하루 캐시 — 과거 이력은 장중에 바뀌지 않는다.
    """
    try:
        import yfinance as yf
        hist = yf.Ticker(ticker).history(period="max")
        if hist.empty:
            return None
        df = hist[["Close"]].rename(columns={"Close": "price"}).dropna()
        if df.index.tz is not None:
            df.index = df.index.tz_convert(None)
        return df if not df.empty else None
    except Exception as e:
        print(f"[ui_macro] {ticker} 전체 이력 조회 실패: {type(e).__name__}: {e}")
        # 마일스톤 배지는 없어도 되지만, 스냅샷이 있으면 그거라도 쓴다.
        return _market_cache_history(ticker, "max")


_FNG_API = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
# CNN은 평범한 요청을 418로 막는다 — Referer/Origin까지 있어야 200이 온다.
_FNG_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://edition.cnn.com/markets/fear-and-greed",
    "Origin": "https://edition.cnn.com",
}

# CNN이 쓰는 구간. 탐욕(주가 상승 국면)을 빨강, 공포를 파랑으로 둬서
# 이 앱의 색 규칙(상승 빨강·하락 파랑)과 방향을 맞춘다.
# 경계는 CNN이 실제로 붙여 보내는 rating에서 역산했다(1년치 실측):
#   extreme fear ~24.77 · fear 25.26~44.86 · neutral 45.23~54.97 · greed 56.20~
# 흔히 보이는 0-20/21-40/41-60/61-80/81-100 구분은 다른 지수(암호화폐
# Fear & Greed)의 것이라 이 데이터에는 맞지 않는다.
# (하한, 상한, 등급, 선색, 차트 띠 색)
_FNG_BANDS = [
    (0,  25,  "극단적 공포", "#1565C0", "rgba(21,101,192,0.13)"),
    (25, 45,  "공포",        "#4A8FD6", "rgba(74,143,214,0.11)"),
    (45, 55,  "중립",        "#888888", "rgba(136,136,136,0.10)"),
    (55, 76,  "탐욕",        "#F08C8A", "rgba(240,140,138,0.11)"),
    (76, 100, "극단적 탐욕", "#ef5350", "rgba(239,83,80,0.13)"),
]


def _fng_rating(v):
    """점수 -> (등급 문구, 색)."""
    for lo, hi, label, color, _fill in _FNG_BANDS:
        if v < hi:
            return label, color
    return _FNG_BANDS[-1][2], _FNG_BANDS[-1][3]


@st.cache_data(ttl=1800, show_spinner=False)
def _get_fng_history(period: str = "1y") -> pd.DataFrame | None:
    """
    CNN 공포·탐욕 지수(0~100). 배치가 쌓아둔 캐시가 1순위.

    연방부채와 같은 모양이다 — 한 번 받을 때 1년치 전체가 오므로 '오늘 값
    덧붙이기'가 필요 없고, 캐시가 아직 없는 배포에서도 API 폴백으로 바로 뜬다.
    """
    raw = None
    try:
        with open(_FNG_CACHE, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        raw = None

    if not raw:
        try:
            r = requests.get(_FNG_API, headers=_FNG_HEADERS, timeout=15)
            data = (r.json().get("fear_and_greed_historical") or {}).get("data") or []
            # 타임스탬프 규칙은 다른 캐시와 같다 — 그날의 UTC 자정(AGENTS.md).
            raw = []
            for x in data:
                d = datetime.fromtimestamp(float(x["x"]) / 1000, timezone.utc).date()
                ms = int(datetime(d.year, d.month, d.day,
                                  tzinfo=timezone.utc).timestamp() * 1000)
                raw.append([ms, round(float(x["y"]), 2)])
        except Exception as e:
            print(f"[ui_macro] 공포탐욕지수 조회 실패: {type(e).__name__}: {e}")
            return None

    if not raw:
        return None
    df = pd.DataFrame(raw, columns=["ts", "price"])
    df["date"] = pd.to_datetime(df["ts"], unit="ms")
    df = df.set_index("date").drop(columns=["ts"]).sort_index()
    cutoff_days = _PERIOD_DAYS.get(period, 365)
    if cutoff_days < 100000:
        df = df[df.index >= pd.Timestamp.now() - pd.Timedelta(days=cutoff_days)]
    return df if not df.empty else None


def _milestone_note(full: pd.DataFrame | None, last: float):
    """
    현재값이 극단인지 판정해 (배지문구, 색) 반환. 특이사항이 없으면 None.

    판정 방식: "현재값을 넘어선 값이 마지막으로 나온 시점"부터 지금까지의 기간.
    그게 곧 "몇 개월 만의 최고"다. 한 번도 없었으면 보유 이력 전체의 최고.

    💡 "사상 최고"는 이력이 충분히 길 때만 쓴다. 리튬/DRAM 캐시는 1년 남짓,
    연방부채는 3년치뿐이라 그걸 "사상"이라고 쓰면 과장이 된다 —
    그 경우엔 실제 보유 기간을 그대로 표기한다.
    """
    if full is None or len(full) < 20:
        return None
    s = full["price"].dropna()
    if s.empty:
        return None

    span_days = (s.index[-1] - s.index[0]).days
    if span_days < 90:
        return None

    def _fmt_span(days: int) -> str:
        if days >= 365:
            years = days / 365.25
            return f"{years:.0f}년" if years >= 2 else "1년"
        return f"{max(1, round(days / 30.44))}개월"

    # 최고 판정
    higher = s[s > last]
    if higher.empty:
        label = "사상 최고" if span_days >= 365 * 10 else f"{_fmt_span(span_days)} 최고"
        return label, "#ef5350"
    gap_days = (s.index[-1] - higher.index[-1]).days
    if gap_days >= 90:
        return f"{_fmt_span(gap_days)} 만에 최고", "#ef5350"

    # 최저 판정
    lower = s[s < last]
    if lower.empty:
        label = "사상 최저" if span_days >= 365 * 10 else f"{_fmt_span(span_days)} 최저"
        return label, "#1565C0"
    gap_days = (s.index[-1] - lower.index[-1]).days
    if gap_days >= 90:
        return f"{_fmt_span(gap_days)} 만에 최저", "#1565C0"

    return None


_NAVER_BOND_API = "https://api.stock.naver.com/marketindex/bond/{code}"

# 야후 티커 → 네이버(레피니티브) 채권 코드.
# 💡 왜 필요한가 — ^TNX는 CBOE가 산출하는 "지수"라서 미국 정규장에만 틱이
# 갱신된다. 그래서 한국 낮 시간에는 값이 전날 미국 종가에 얼어붙어 있고,
# 24시간 돌아가는 현물 국채 시장을 보는 네이버와 한 세션씩 어긋났다
# (예: 네이버 4.7920 / -0.25% vs 앱 4.81 / +0.46% — 방향까지 반대).
# 등락률 기준(전일종가)도 서로 달라 혼란이 컸다. 현재값/등락률은 네이버
# 실시간(delayTime=0, 레피니티브)으로 받고, 과거 일별 시계열(스파크라인)은
# 야후 일봉을 그대로 쓴다 — 일별 종가는 두 소스가 사실상 동일하다.
_NAVER_BOND_MAP = {"^TNX": "US10YT=RR"}


@st.cache_data(ttl=300, show_spinner=False)
def _get_naver_bond_quote(reuters_code: str):
    """
    네이버 채권 금리 실시간 시세 → (현재값, 전일종가, 갱신시각).
    실패하면 (None, None, None) — 호출부가 야후로 폴백한다.
    """
    try:
        res = requests.get(
            _NAVER_BOND_API.format(code=reuters_code),
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                "Referer": "https://m.stock.naver.com/",
                "Accept": "application/json, text/plain, */*",
            },
            timeout=7,
        )
        j = res.json()
        # 💡 필드명이 closePrice지만 장중에는 "현재값"이다(종가가 아니다).
        # 전일종가는 따로 안 주므로 현재값 - 변동폭으로 되돌려 계산한다.
        last = float(j["closePrice"])
        prev = last - float(j["fluctuations"])
        if last <= 0 or prev <= 0:
            return None, None, None
        ts = datetime.fromisoformat(j["localTradedAt"]).astimezone(_KST)
        return last, prev, ts
    except Exception as e:
        print(f"[ui_macro] 네이버 채권({reuters_code}) 조회 실패: {type(e).__name__}: {e}")
        return None, None, None


def _news_rss(query: str):
    """구글 뉴스 RSS -> [(발행시각, 제목, 링크)] (관련도순 그대로)."""
    url = ("https://news.google.com/rss/search?q="
           f"{urllib.parse.quote(query)}&hl=ko&gl=KR&ceid=KR:ko")
    root = ET.fromstring(requests.get(url, timeout=5).text)
    out = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub = item.findtext("pubDate")
        if not title or not link or not pub:
            continue
        try:
            dt = parsedate_to_datetime(pub)
        except Exception:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        out.append((dt, title, link))
    return out


# 시세 캐시(_get_price_history, 300초)와 같은 주기 — 매크로 화면이 갱신될 때
# 뉴스도 같이 바뀌어야 한다(예전 30분 캐시는 시세만 움직이고 뉴스는 그대로였다).
@st.cache_data(ttl=300, show_spinner=False)
def _get_commodity_news(query: str, must: tuple):
    """
    헤드라인 후보 -> [(제목, 링크, 경과), ...] 최신순 최대 3건(없으면 빈 리스트).
    금융 뉴스 헤드라인은 보통 등락 사유가 제목에 그대로 들어있어
    (예: "WTI, OPEC+ 감산 소식에 급등") 요약 없이 헤드라인만으로 충분하다.
    고르는 규칙은 _NEWS_QUERY 위 주석 참고.

    💡 1건이 아니라 후보를 돌려주는 이유: DDR5·DDR4처럼 검색어가 겹치는 카드가
    나란히 서면 같은 기사가 두 번 걸린다. 쓰는 쪽이 '아직 안 쓴 것'을 고른다.
    """
    now = datetime.now(timezone.utc)
    pool = _news_rss(query + _NEWS_RECENT)
    hits = [r for r in pool if any(k.lower() in r[1].lower() for k in must)]
    if not hits:
        # 최근 7일에 키워드 맞는 기사가 없으면(한산한 주) 기간 제한 없이 한 번 더.
        # 이쪽엔 날짜 하한이 없어 한 달 지난 기사도 올라온다 — 그래서 경과("28일")를
        # 제목 앞에 같이 적는다. 애초에 여기까지 오지 않게 키워드를 넓히는 게 먼저다.
        pool = _news_rss(query)
        hits = [r for r in pool if any(k.lower() in r[1].lower() for k in must)]
    hits.sort(key=lambda r: r[0], reverse=True)
    return [(title, link, _news_age(dt, now)) for dt, title, link in hits[:3]]


@st.cache_data(ttl=3600, show_spinner=False)
def _get_us_debt_history(period: str = "1y") -> pd.DataFrame | None:
    """
    미국 연방부채 총액 (조 달러) — 커밋된 캐시 파일 우선, 없거나 비면 API 직접 조회.

    다른 캐시 지표(리튬/DRAM)는 스크래핑이 오늘 값만 주기 때문에 하루씩 쌓아야
    이력이 생기지만, 이건 재무부 공식 API가 일별 전체 이력을 주므로 배치가
    시계열 전체를 덮어쓴다. 그래서 여기서는 "오늘 값 덧붙이기"가 필요 없고,
    캐시 파일이 아직 없는 배포(첫 배포 등)에서도 API 폴백으로 바로 표시된다.
    """
    raw = None
    try:
        with open(_USDEBT_CACHE, "r") as f:
            raw = json.load(f)
    except Exception:
        raw = None

    if not raw:
        # 캐시가 없으면 API로 직접 (배치가 아직 안 돌았거나 파일이 없는 경우)
        try:
            r = requests.get(
                _USDEBT_API,
                params={
                    "fields": "record_date,tot_pub_debt_out_amt",
                    "sort": "-record_date",
                    "page[size]": 1200,
                    "format": "json",
                },
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
                timeout=15,
            )
            # 타임스탬프는 배치(batch_macro._date_to_ms)와 같은 규칙 — UTC 자정.
            # pd.to_datetime(unit="ms")가 tz 없는 UTC 시각을 주므로, 이렇게
            # 저장해야 record_date가 화면에 그 날짜로 그대로 나온다.
            raw = [
                [
                    int(
                        datetime.strptime(d["record_date"], "%Y-%m-%d")
                        .replace(tzinfo=timezone.utc).timestamp() * 1000
                    ),
                    float(d["tot_pub_debt_out_amt"]) / 1e12,
                ]
                for d in r.json().get("data", [])
            ]
            raw.sort(key=lambda x: x[0])
        except Exception as e:
            print(f"[ui_macro] 미국 연방부채 조회 실패: {type(e).__name__}: {e}")
            return None

    if not raw:
        return None

    df = pd.DataFrame(raw, columns=["ts", "price"])
    df["date"] = pd.to_datetime(df["ts"], unit="ms")
    df = df.set_index("date").drop(columns=["ts"])

    cutoff_days = _PERIOD_DAYS.get(period, 365)
    df = df[df.index >= pd.Timestamp.now() - pd.Timedelta(days=cutoff_days)]
    return df if not df.empty else None


@st.cache_data(ttl=21600, show_spinner=False)
def _gfex_trading_days():
    """GFEX 거래일 목록 — 리튬 현재가에 날짜를 붙이는 데 쓴다. 6시간 캐시."""
    return lithium_source.gfex_trading_days(timeout=8)


@st.cache_data(ttl=3600, show_spinner=False)
def _te_lithium_now():
    """TE 리튬 현재가 (값의 날짜 ms, 가격). 1y·max 두 기간이 같은 값을 쓰도록 한 번만 긁는다.
    실패는 예외로 — 빈 결과를 한 시간 붙잡지 않게."""
    price = lithium_source.fetch_te_value(timeout=10)
    if price is None:
        raise RuntimeError("TE 리튬 현재가 없음")
    d = lithium_source.te_value_date(datetime.now(_KST).date(), _gfex_trading_days())
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000), price


@st.cache_data(ttl=3600, show_spinner=False)
def _dramexchange_spot() -> dict:
    """DRAMeXchange 첫 페이지의 DDR5 16Gb · DDR4 16Gb 3200 Spot 평균가 {"ddr5", "ddr4"}.
    💡 예전엔 DDR5·DDR4 함수가 같은 페이지를 기간(1y·max)마다 따로 받아 콜드 로딩에
    4번(한 번 2.5~5초) 긁었다(2026-11-09 실측). 실패는 예외로."""
    r = requests.get(
        "https://www.dramexchange.com/",
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                 "Accept-Language": "en-US,en;q=0.9",
                 "Referer": "https://www.dramexchange.com/"},
        timeout=10,
    )
    r.raise_for_status()
    out = {}
    # 각 행에서 5번째 td값 = Spot 평균가
    for key, row in (("ddr5", r'DDR5 16Gb[^<]*</a>'), ("ddr4", r'DDR4 16Gb[^<]*3200.*?</a>')):
        m = re.search(row + r'(?:.*?<td[^>]*>[\d.]+</td>){4}.*?<td[^>]*>([\d.]+)</td>', r.text, re.S)
        out[key] = float(m.group(1)) if m else None
    # 💡 '확인 시각'을 값과 함께 캐시에 담는다. 캐시 적중이어도 이 시각은 그때
    # 실제로 긁은 시각이라(ttl 1시간) 카드에 그대로 쓸 수 있다. 이게 없으면
    # 카드가 마지막 '데이터 점의 날짜'를 기준시각으로 적어서, 소스가 며칠
    # 안 움직인 것과 스크래핑이 죽은 것이 화면에서 똑같이 보인다.
    out["at"] = datetime.now(_KST)
    return out


@st.cache_data(ttl=3600, show_spinner=False)
def _get_lithium_price_history(period: str = "1y") -> pd.DataFrame | None:
    """리튬 탄산염 가격 (CNY/ton) — 캐시 파일 + TE 현재가 스크래핑"""
    try:
        with open(_LITHIUM_CACHE, "r") as f:
            raw = json.load(f)
        if not raw:
            return None

        # 현재가 스크래핑 (실패해도 캐시 데이터로 진행)
        # 💡 TE 현재가는 아직 "직전 중국 거래일" 값이라 오늘 날짜로 찍으면 하루 밀린다.
        # 배치와 같은 규칙(lithium_source.te_value_date)으로 날짜를 붙인다.
        try:
            ts, current_price = _te_lithium_now()   # 값의 날짜는 lithium_source.te_value_date 규칙
            # 💡 소스가 새 값을 아직 안 냈으면 직전과 같은 값이 긁힌다. 그걸 새 점으로
            # 덧붙이면 "전일대비 0.00%"가 되어 실제 마지막 변동이 화면에서 사라진다.
            if raw[-1][0] < ts and current_price != raw[-1][1]:
                raw.append([ts, current_price])
        except Exception:
            pass

        df = pd.DataFrame(raw, columns=["ts", "price"])
        df["date"] = pd.to_datetime(df["ts"], unit="ms")
        df = df.set_index("date").drop(columns=["ts"])

        cutoff_days = _PERIOD_DAYS.get(period, 365)
        cutoff = pd.Timestamp.now() - pd.Timedelta(days=cutoff_days)
        df = df[df.index >= cutoff]
        return df if not df.empty else None
    except Exception:
        return None


@st.cache_data(ttl=3600, show_spinner=False)
def _get_dram_price_history(period: str = "1y") -> pd.DataFrame | None:
    """DDR5 16Gb Spot (USD) — 캐시 파일 + DRAMeXchange 현재가 스크래핑"""
    try:
        with open(_DRAM_CACHE, "r") as f:
            raw = json.load(f)
        if not raw:
            return None

        try:
            current_price = _dramexchange_spot().get("ddr5")
            if current_price is not None:
                today_ms = _today_ms_utc_midnight()
                # 💡 소스가 아직 오늘자를 발행하지 않으면 어제와 똑같은 값이
                # 스크래핑된다. 그걸 오늘 점으로 덧붙이면 직전값과 같아져
                # "전일대비 0.00%"가 되고, 실제로 마지막에 움직인 폭
                # (예: DDR4 93.0 → 91.0)이 화면에서 사라진다.
                # 값이 달라졌을 때만 새 점으로 인정한다.
                if raw[-1][0] < today_ms and current_price != raw[-1][1]:
                    raw.append([today_ms, current_price])
        except Exception:
            pass

        df = pd.DataFrame(raw, columns=["ts", "price"])
        df["date"] = pd.to_datetime(df["ts"], unit="ms")
        df = df.set_index("date").drop(columns=["ts"])

        cutoff_days = _PERIOD_DAYS.get(period, 365)
        cutoff = pd.Timestamp.now() - pd.Timedelta(days=cutoff_days)
        df = df[df.index >= cutoff]
        return df if not df.empty else None
    except Exception:
        return None


@st.cache_data(ttl=3600, show_spinner=False)
def _get_ddr4_price_history(period: str = "1y") -> pd.DataFrame | None:
    """DDR4 16Gb 3200 Spot (USD) — 캐시 파일 + DRAMeXchange 현재가 스크래핑"""
    try:
        with open(_DDR4_CACHE, "r") as f:
            raw = json.load(f)
        if not raw:
            return None

        try:
            current_price = _dramexchange_spot().get("ddr4")
            if current_price is not None:
                today_ms = _today_ms_utc_midnight()
                # 💡 소스가 아직 오늘자를 발행하지 않으면 어제와 똑같은 값이
                # 스크래핑된다. 그걸 오늘 점으로 덧붙이면 직전값과 같아져
                # "전일대비 0.00%"가 되고, 실제로 마지막에 움직인 폭
                # (예: DDR4 93.0 → 91.0)이 화면에서 사라진다.
                # 값이 달라졌을 때만 새 점으로 인정한다.
                if raw[-1][0] < today_ms and current_price != raw[-1][1]:
                    raw.append([today_ms, current_price])
        except Exception:
            pass

        df = pd.DataFrame(raw, columns=["ts", "price"])
        df["date"] = pd.to_datetime(df["ts"], unit="ms")
        df = df.set_index("date").drop(columns=["ts"])

        cutoff_days = _PERIOD_DAYS.get(period, 365)
        cutoff = pd.Timestamp.now() - pd.Timedelta(days=cutoff_days)
        df = df[df.index >= cutoff]
        return df if not df.empty else None
    except Exception:
        return None



# ── 스파크라인 헬퍼 ─────────────────────────────────────────────────────────
# ── 지수 밸류에이션 밴드 (코스피·코스닥 PER·PBR) ─────────────────────────────
# 데이터는 index_valuation.py(GitHub Actions, KRX 로그인)가 data/macro/index_val/{연도}.json에
# 쌓는다. 앱엔 KRX 계정이 없어 파일만 읽는다.
#
# "지금 PER이 싼가 비싼가"를 보려는 화면이라, 선택 기간의 최고·평균·최저를 가로선으로
# 깔고 현재 값이 그 사이 어디인지(백분위)를 숫자로 붙인다. 최고·최저 선 색은 다른 카드의
# "N개월 최고/최저" 배지와 같은 빨강·파랑이다.
_INDEX_VAL_DIR = os.path.join(_MACRO_DIR, "index_val")
_BAND_PERIODS = {"5년": 5, "10년": 10, "전체": None}
# valuation.py 밴드 차트의 주가·당일Val 선과 같은 색. 예전 코드엔 color="var(--text-color)"로
# 적혀 있었지만 plotly.js는 CSS 변수를 못 읽어 기본 1번 색 #636EFA(남보라)로 그리고 있었다
# (2026-10-21 브라우저 실측 rgb(99,110,250)). 실제로 보이던 색을 양쪽에 명시한다.
_VALUATION_LINE = "#636EFA"
# 매크로 탭의 모든 선은 이 한 가지 색이다(2026-11-06 사용자 요청).
# 예전엔 시세 카드만 전일대비로 빨강/파랑을 칠했는데, 같은 화면에서 지수 밴드는
# 보라, 시세는 빨강/파랑이라 "색이 뜻을 갖는 건가"로 읽혀 오히려 헷갈렸다.
# 증감은 숫자(텍스트)에서 빨강/파랑으로 계속 구분하므로 정보가 사라지지 않는다.
_LINE_FILL = "rgba(99,110,250,0.13)"


def _year_ticks(idx) -> dict:
    """
    x축 연도 눈금 — 기간 길이에 맞춰 간격을 정한다.

    💡 plotly 자동 눈금은 10년 구간에서 '20·'25 둘만 찍어 "몇 년치인지" 읽히지
    않았다(사용자 지적 2026-11-06). 폭이 좁은 카드라 2년 간격이 상한이다.
    """
    try:
        yrs = (idx[-1] - idx[0]).days / 365.25
    except Exception:
        return dict(tickformat="'%y")
    if yrs <= 1.6:
        return dict(tickformat="%m월", dtick="M2")
    if yrs <= 4:
        return dict(tickformat="'%y.%m", dtick="M6")
    if yrs <= 13:
        return dict(tickformat="'%y", dtick="M24")
    return dict(tickformat="'%y", dtick="M60")


@st.cache_data(ttl=3600, show_spinner=False)
def _get_index_valuation() -> dict:
    """{"KOSPI": DataFrame(index=date, columns=[per, pbr, close]), "KOSDAQ": ...}"""
    rows = {"KOSPI": [], "KOSDAQ": []}
    try:
        files = sorted(f for f in os.listdir(_INDEX_VAL_DIR) if f.endswith(".json"))
    except Exception:
        return {}
    for fn in files:
        try:
            with open(os.path.join(_INDEX_VAL_DIR, fn), "r", encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            continue
        for k in rows:
            rows[k].extend(d.get(k, []))
    out = {}
    for k, r in rows.items():
        if not r:
            continue
        # 줄이 4칸 → 6칸(거래량·대금) → 9칸(외국인·기관·개인 순매수)으로 늘었다. 짧은 줄은 0으로 채운다.
        r = [list(x) + [0] * (9 - len(x)) for x in r]
        df = pd.DataFrame(r, columns=["date", "per", "pbr", "close", "volume", "value",
                                      "foreign", "inst", "indiv"])
        df["date"] = pd.to_datetime(df["date"])
        out[k] = df.drop_duplicates("date").set_index("date").sort_index()
    return out


@st.cache_data(ttl=60, show_spinner=False)
def _get_index_live(idx: str):
    """
    네이버 실시간 지수 -> (지수, 전일대비, 등락률%, 시각, 장상태) 또는 None.
    밴드 카드의 PER·PBR은 전일 배치(KRX 확정치) 기준이라, 오늘 지수는 따로 받는다.
    전일대비·등락률은 부호 없이 오고 방향은 compareToPreviousPrice로 온다.
    """
    try:
        d = requests.get(f"https://m.stock.naver.com/api/index/{idx}/basic",
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=5).json()
        v = float(str(d["closePrice"]).replace(",", ""))
        chg = abs(float(str(d["compareToPreviousClosePrice"]).replace(",", "")))
        pct = abs(float(str(d["fluctuationsRatio"]).replace(",", "")))
        direction = (d.get("compareToPreviousPrice") or {}).get("name", "")
        sign = -1 if direction in ("FALLING", "LOWER_LIMIT") else 0 if direction == "UNCHANGED" else 1
        t = datetime.fromisoformat(d["localTradedAt"]) if d.get("localTradedAt") else None
        return v, sign * chg, sign * pct, t, d.get("marketStatus")
    except Exception as e:
        print(f"[ui_macro] 네이버 지수({idx}) 조회 실패: {type(e).__name__}: {e}")
        return None


def _index_today_html(idx: str, label: str, df: pd.DataFrame | None) -> str:
    """'코스피 5,711.39 ▲158.65 (+2.86%) · 10/21 09:56 장중' 한 줄."""
    live = _get_index_live(idx)
    if live:
        v, chg, pct, t, status = live
        when = (f"{t:%m/%d %H:%M} " if t else "") + ("장중" if status == "OPEN" else "종가")
    elif df is not None and len(df) >= 2:
        # 네이버가 막히면 배치가 받아 둔 최근 두 종가로 대신한다.
        v, prev = float(df["close"].iloc[-1]), float(df["close"].iloc[-2])
        chg, pct = v - prev, (v / prev - 1) * 100 if prev else 0.0
        when = f"{df.index[-1]:%m/%d} 종가 (실시간 지연)"
    else:
        return ""
    clr = "#ef5350" if chg > 0 else "#1565C0" if chg < 0 else "#888"
    arrow = "▲" if chg > 0 else "▼" if chg < 0 else "─"
    return (f"<div style='margin:10px 0 2px;font-size:14px;'><b>{label} {v:,.2f}</b> "
            f"<span style='color:{clr};font-weight:600;'>{arrow}{abs(chg):,.2f} ({pct:+.2f}%)</span> "
            f"<span style='font-size:11px;color:#aaa;'>· {when}</span></div>")


def _fmt_shares(v) -> str:
    """거래량(주) → '5.2억주' / '8,300만주'."""
    if not v or v != v:
        return "-"
    return f"{v / 1e8:,.1f}억주" if v >= 1e8 else f"{v / 1e4:,.0f}만주"


def _fmt_krw(v) -> str:
    """거래대금(원) → '12.3조' / '8,400억'."""
    if not v or v != v:
        return "-"
    return f"{v / 1e12:,.1f}조" if v >= 1e12 else f"{v / 1e8:,.0f}억"


def _fmt_krw_signed(v) -> str:
    """순매수(원) → '+1,058억' / '-3.2조' / '0'."""
    if v is None or v != v:
        return "-"
    if v == 0:
        return "0"
    sign = "+" if v > 0 else "-"
    a = abs(v)
    return f"{sign}{a / 1e12:,.1f}조" if a >= 1e12 else f"{sign}{a / 1e8:,.0f}억"


def _make_band_chart(s: pd.Series, unit: str, close: pd.Series | None = None,
                     index_name: str = "", volume: pd.Series | None = None,
                     value: pd.Series | None = None, foreign: pd.Series | None = None) -> go.Figure:
    hi, mean, lo = float(s.max()), float(s.mean()), float(s.min())
    fig = go.Figure()

    # 거래량 — 보조축으로 그래프 아래 40%에 깐다(보조축 범위를 최대의 2.5배로 잡음).
    # 처음엔 1/4이었는데 너무 낮아 흐름이 안 읽힌다는 피드백으로 키웠다.
    # 막대가 아니라 면적으로 그린다: 10년 일봉 2,451개가 444px에 몰려 막대 하나가 0.2px라
    # 브라우저에서 거의 안 보였다(2026-10-22 실측). 같은 값을 면으로 채우면 밀도와 상관없이 보인다.
    # 상승/하락 색을 쓰지 않는 이유도 같다 — 그 밀도에선 빨강·파랑이 섞여 얼룩이 된다.
    vol = volume.reindex(s.index).fillna(0) if volume is not None else None
    has_vol = vol is not None and float(vol.max()) > 0
    if has_vol:
        fig.add_trace(go.Scatter(
            x=s.index, y=vol.values, yaxis="y2", mode="lines", fill="tozeroy",
            line=dict(width=0, color="rgba(120,144,156,0)"), fillcolor="rgba(120,144,156,0.35)",
            showlegend=False, hoverinfo="skip",
        ))

    # 마우스오버에 그날 지수 종가·거래량·거래대금을 같이 띄운다 — "PER 9.6배일 때 코스피가 몇
    # 포인트였고 거래가 얼마나 붙었나"를 같이 봐야 밴드 위치를 시장 상황으로 옮겨 읽을 수 있다.
    cd, extra = None, ""
    if close is not None:
        c = close.reindex(s.index)
        v = vol if vol is not None else pd.Series(0, index=s.index)
        val = value.reindex(s.index).fillna(0) if value is not None else pd.Series(0, index=s.index)
        fo = foreign.reindex(s.index).fillna(0) if foreign is not None else pd.Series(0, index=s.index)
        cd = [[f"{a:,.2f}", _fmt_shares(b), _fmt_krw(d), _fmt_krw_signed(f)]
              for a, b, d, f in zip(c.values, v.values, val.values, fo.values)]
        extra = "<br>" + index_name + " %{customdata[0]}"
        if has_vol:
            extra += "<br>거래량 %{customdata[1]} · 대금 %{customdata[2]}"
        if float(fo.abs().max()) > 0:
            extra += "<br>외국인 %{customdata[3]}"
    # 선 아래를 옅게 채운다 — 시세 카드와 같은 모양(2026-11-06 요청).
    # "tonexty"는 바로 앞 트레이스까지 채우므로, 바닥선을 선 '직전'에 둔다
    # (거래량 트레이스가 앞에 오면 그쪽으로 채워진다).
    span_ = (hi - lo) or abs(hi) or 1.0
    base_y = lo - span_ * (0.70 if has_vol else 0.12)
    top_y = hi + span_ * 0.06
    fig.add_trace(go.Scatter(
        x=s.index, y=[base_y] * len(s), mode="lines", line=dict(width=0),
        showlegend=False, hoverinfo="skip",
    ))
    fig.add_trace(go.Scatter(
        x=s.index, y=s.values, mode="lines", customdata=cd,
        # 가치평가 탭 밴드 차트의 주가·당일Val 선과 같은 색·굵기.
        line=dict(color=_VALUATION_LINE, width=1.5), showlegend=False,
        fill="tonexty", fillcolor=_LINE_FILL,
        hovertemplate="%{x|%Y-%m-%d}<br><b>%{y:.2f}" + unit + "</b>" + extra + "<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=[s.index[-1]], y=[s.values[-1]], mode="markers",
        marker=dict(color=_VALUATION_LINE, size=7, line=dict(color="#fff", width=1)),
        showlegend=False, hoverinfo="skip",
    ))
    # 선 옆에 값을 적는다 — 매크로 카드(_make_sparkline)와 같은 방식.
    # 라벨은 xref="paper"라 축 범위 계산에 끼어들지 않는다(그래프 모양 불변).
    band_notes = []
    # 최고·최저는 선 안쪽에 붙인다(바깥에 두면 차트 밖으로 나가 잘린다).
    for y, color, dash, lbl, anchor in ((hi, "#ef5350", "dash", "최고", "top"),
                                        (mean, "#888888", "dot", "평균", "bottom"),
                                        (lo, "#1565C0", "dash", "최저", "bottom")):
        fig.add_hline(y=y, line=dict(color=color, width=1.1, dash=dash))
        band_notes.append(dict(
            xref="paper", yref="y", x=0.004, y=y, xanchor="left", yanchor=anchor,
            text=f"<b>{lbl}</b> {y:.2f}{unit}", showarrow=False,
            font=dict(size=9.5, color=color),
            bgcolor="rgba(255,255,255,0.72)", borderpad=1,
        ))
    # 크기·여백·눈금은 위 시세 카드(_make_sparkline)와 같게 — 한 화면에서 나란히 보이도록.
    fig.update_layout(
        height=96, margin=dict(l=0, r=2, t=8, b=18),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        dragmode=False, hovermode="x",
        xaxis=dict(showgrid=False, zeroline=False, fixedrange=True,
                   tickfont=dict(size=9, color="#888"), ticklen=0, **_year_ticks(s.index)),
        yaxis=dict(showgrid=False, zeroline=False, fixedrange=True, showticklabels=False),
        annotations=band_notes,
    )
    # 범위를 항상 명시한다 — 자동으로 두면 채움의 바닥이 그날 값에 따라 들쭉날쭉해진다.
    # 거래량이 있으면 선이 위 60%에 오도록 아래를 더 비운다(최저선이 면과 겹치지 않게).
    fig.update_layout(yaxis=dict(range=[base_y, top_y], showgrid=False, zeroline=False,
                                 fixedrange=True, showticklabels=False))
    if has_vol:
        fig.update_layout(
            yaxis2=dict(overlaying="y", range=[0, float(vol.max()) * 2.5], visible=False,
                        fixedrange=True),
        )
    return fig


def _render_band_card(name: str, s: pd.Series, unit: str, span_label: str,
                      close: pd.Series | None = None, index_name: str = "",
                      volume: pd.Series | None = None, value: pd.Series | None = None,
                      foreign: pd.Series | None = None) -> None:
    # KRX는 값이 없을 때 0을, 계산 불가일 때 9999.99를 넣는다(코스닥 PER: 2006~2010 합산 이익이
    # 0 근처라 753일이 0, 2007-08-14에 9999.99). 둘 다 빼야 최고·평균선이 안 틀어진다.
    # 2006~07·2014년 코스닥 PER 300배대는 실제 값(이익이 0에 가까웠던 해)이라 남긴다.
    s = s[(s > 0) & (s < 9999)].dropna()
    if len(s) < 20:
        st.markdown(f"<div style='font-size:11px;color:#888;'>{name}</div>"
                    f"<div style='font-size:18px;font-weight:700;color:#ccc;'>N/A</div>",
                    unsafe_allow_html=True)
        return
    cur = float(s.iloc[-1])
    # 백분위는 고점권/저점권 배지로만 쓴다 — 숫자까지 적으면 한 줄이 길어진다(2026-11-06 요청).
    pct = float((s < cur).mean() * 100)          # 기간 중 지금보다 낮았던 날의 비율
    vs_mean = (cur / float(s.mean()) - 1) * 100
    badge = ""
    if pct <= 20:
        badge = ("<span style='background:#1565C0;color:#fff;font-size:9.5px;font-weight:700;"
                 "padding:1px 5px;border-radius:3px;'>저점권</span>")
    elif pct >= 80:
        badge = ("<span style='background:#ef5350;color:#fff;font-size:9.5px;font-weight:700;"
                 "padding:1px 5px;border-radius:3px;'>고점권</span>")
    clr = "#ef5350" if vs_mean > 0 else "#1565C0" if vs_mean < 0 else "#888"
    # 매크로 카드와 같은 모양 — 제목·값·부가정보를 한 줄에, 아래 10px 띄움.
    # 최고·평균·최저는 차트 안 선 옆으로 옮겼다(위 _make_band_chart).
    st.markdown(
        f"<div style='padding:4px 0 10px;display:flex;align-items:baseline;"
        f"flex-wrap:wrap;column-gap:7px;row-gap:0;line-height:1.25;'>"
        f"<span style='font-size:11px;color:#888;white-space:nowrap;'>{name}</span>{badge}"
        f"<span style='font-size:18px;font-weight:700;'>{cur:.2f}{unit}</span>"
        f"<span style='font-size:11.5px;color:{clr};font-weight:600;white-space:nowrap;'>"
        f"평균 대비 {vs_mean:+.1f}%</span>"
        f"<span style='font-size:11.5px;color:#888;white-space:nowrap;'>"
        f"{s.index[-1]:%m/%d}</span></div>",
        unsafe_allow_html=True,
    )
    st.plotly_chart(_make_band_chart(s, unit, close, index_name, volume, value, foreign), use_container_width=True,
                    config={"displayModeBar": False, "scrollZoom": False, "staticPlot": False})


@st.cache_data(ttl=60, show_spinner=False)
def _get_flow_live(idx: str):
    """네이버 오늘 투자자별 순매수(억원) -> (외국인, 기관, 개인, 기준일 'YYYYMMDD') 또는 None."""
    try:
        d = requests.get(f"https://m.stock.naver.com/api/index/{idx}/integration",
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=5).json()
        t = d.get("dealTrendInfo") or {}
        num = lambda k: float(str(t.get(k, "0")).replace(",", "").replace("+", "") or 0)
        return num("foreignValue"), num("institutionalValue"), num("personalValue"), t.get("bizdate")
    except Exception as e:
        print(f"[ui_macro] 네이버 수급({idx}) 조회 실패: {type(e).__name__}: {e}")
        return None


_FOREIGN_HOLD_DIR = os.path.join(_MACRO_DIR, "foreign_hold")


@st.cache_data(ttl=3600, show_spinner=False)
def _get_foreign_holding() -> dict:
    """{"KOSPI": DataFrame(index=date, columns=[ratio, forn, cap]), "KOSDAQ": ...} — foreign_holding.py가 수집."""
    rows = {"KOSPI": [], "KOSDAQ": []}
    try:
        files = sorted(f for f in os.listdir(_FOREIGN_HOLD_DIR) if f.endswith(".json"))
    except Exception:
        return {}
    for fn in files:
        try:
            with open(os.path.join(_FOREIGN_HOLD_DIR, fn), "r", encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            continue
        for k in rows:
            rows[k].extend(d.get(k, []))
    out = {}
    for k, r in rows.items():
        if r:
            df = pd.DataFrame(r, columns=["date", "ratio", "forn", "cap"])
            df["date"] = pd.to_datetime(df["date"])
            out[k] = df.drop_duplicates("date").set_index("date").sort_index()
    return out


def _make_foreign_chart(h: pd.DataFrame, flow_m: pd.DataFrame | None) -> go.Figure:
    """
    외국인 보유 금액 선(위 60%, 아래 옅게 채움) + 월별 외국인 순매수 막대(아래 40%).

    예전엔 비중(%)을 그렸는데 사용자가 금액 추이를 원했다(2026-11-07). 같은 파일의
    'forn'(외국인 보유분 시가총액)을 조 단위로 그대로 쓰고, 비중은 마우스오버에 남긴다.
    ⚠️ 금액은 보유 주식 수뿐 아니라 주가로도 움직인다 — 지수와 닮은 모양이 나오는 건
    그 때문이고, '외국인이 더 샀다'로 읽으면 안 된다. 그걸 보는 건 아래 순매수 막대다.
    """
    s = h["forn"] / 1e12
    hi, mean, lo = float(s.max()), float(s.mean()), float(s.min())
    fig = go.Figure()
    has_flow = flow_m is not None and not flow_m.empty and float(flow_m["foreign"].abs().max()) > 0
    if has_flow:
        colors = ["#ef5350" if v > 0 else "#1565C0" if v < 0 else "#888888" for v in flow_m["foreign"]]
        fig.add_trace(go.Bar(
            x=flow_m.index, y=flow_m["foreign"], yaxis="y2",
            marker=dict(color=colors, opacity=0.55, line=dict(width=0)),
            customdata=[[_fmt_krw_signed(a)] for a in flow_m["foreign"]],
            showlegend=False, hovertemplate="%{x|%Y-%m} 외국인 순매수 <b>%{customdata[0]}</b><extra></extra>",
        ))
    # 선 아래를 옅게 채운다 — 밴드·시세 카드와 같은 모양. "tonexty"는 바로 앞
    # 트레이스까지 채우므로 바닥선을 선 '직전'에 둔다(순매수 막대가 앞에 오면 그쪽으로 채워진다).
    span = (hi - lo) or abs(hi) or 1.0
    base_y = lo - span * (0.70 if has_flow else 0.12)
    top_y = hi + span * 0.06
    fig.add_trace(go.Scatter(
        x=s.index, y=[base_y] * len(s), mode="lines", line=dict(width=0),
        showlegend=False, hoverinfo="skip",
    ))
    fig.add_trace(go.Scatter(
        x=s.index, y=s.values, mode="lines",
        line=dict(color=_VALUATION_LINE, width=1.5), showlegend=False,
        fill="tonexty", fillcolor=_LINE_FILL,
        customdata=[[f"{r:.2f}", f"{c / 1e12:,.0f}"] for r, c in zip(h["ratio"], h["cap"])],
        hovertemplate=("%{x|%Y-%m-%d}<br><b>외국인 보유 %{y:,.0f}조</b>"
                       "<br>비중 %{customdata[0]}% / 시총 %{customdata[1]}조<extra></extra>"),
    ))
    fig.add_trace(go.Scatter(
        x=[s.index[-1]], y=[s.values[-1]], mode="markers",
        marker=dict(color=_VALUATION_LINE, size=7, line=dict(color="#fff", width=1)),
        showlegend=False, hoverinfo="skip",
    ))
    # 선 옆에 값을 적는다 — 다른 카드와 같은 방식(xref="paper"라 축에 영향 없음).
    fg_notes = []
    for y, color, dash, lbl, anc in ((hi, "#ef5350", "dash", "최고", "top"),
                                     (mean, "#888888", "dot", "평균", "bottom"),
                                     (lo, "#1565C0", "dash", "최저", "bottom")):
        fig.add_hline(y=y, line=dict(color=color, width=1.1, dash=dash))
        fg_notes.append(dict(
            xref="paper", yref="y", x=0.004, y=y, xanchor="left", yanchor=anc,
            text=f"<b>{lbl}</b> {y:,.0f}조", showarrow=False,
            font=dict(size=9.5, color=color),
            bgcolor="rgba(255,255,255,0.72)", borderpad=1,
        ))
    m = float(flow_m["foreign"].abs().max()) if has_flow else 1.0
    fig.update_layout(
        height=96, margin=dict(l=0, r=2, t=8, b=18), bargap=0.15,
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        dragmode=False, hovermode="x",
        xaxis=dict(showgrid=False, zeroline=False, fixedrange=True,
                   tickfont=dict(size=9, color="#888"), ticklen=0, **_year_ticks(s.index)),
        # 선은 위 60%(거래량과 같은 배치), 막대는 0을 가운데 둔 아래 40% — 순매도도 아래로 그려진다.
        yaxis=dict(showgrid=False, zeroline=False, fixedrange=True, showticklabels=False,
                   range=[base_y, top_y]),
        yaxis2=dict(overlaying="y", visible=False, fixedrange=True, range=[-m * 1.25, m * 5.0]),
        annotations=fg_notes,
    )
    return fig


def _render_foreign_card(idx: str, label: str, val_df: pd.DataFrame | None, years: int | None,
                         span_label: str) -> None:
    """
    외국인 보유 금액(시가총액 기준) — 얼마를 들고 있고, 지금 사는지 파는지.

    선은 foreign_holding.py가 KRX 종목별 외국인 보유량을 합산한 금액(과거는 주 단위,
    최근은 일 단위). 막대는 index_val의 일별 외국인 순매수를 월 합계로 묶은 것.
    비중(%)은 숫자로만 옆에 적는다 — 그래프는 금액으로 봐 달라는 요청(2026-11-07).
    """
    h = _get_foreign_holding().get(idx)
    if h is None or h.empty:
        st.markdown(f"<div style='font-size:11px;color:#888;'>{label} 외국인 보유액</div>"
                    f"<div style='font-size:18px;font-weight:700;color:#ccc;'>N/A</div>",
                    unsafe_allow_html=True)
        return
    hs = h if not years else h[h.index >= h.index[-1] - pd.DateOffset(years=years)]
    cur = float(h["forn"].iloc[-1]) / 1e12
    cur_ratio = float(h["ratio"].iloc[-1])

    def chg_since(days):
        past = h[h.index <= h.index[-1] - pd.Timedelta(days=days)]
        return cur - float(past["forn"].iloc[-1]) / 1e12 if not past.empty else None

    def ytd():
        past = h[h.index < pd.Timestamp(h.index[-1].year, 1, 1)]
        return cur - float(past["forn"].iloc[-1]) / 1e12 if not past.empty else None

    def c(v):
        return "#ef5350" if (v or 0) > 0 else "#1565C0" if (v or 0) < 0 else "#888"

    def pp(name, v):
        return "" if v is None else f"<span style='color:{c(v)};font-weight:600;'>{name} {v:+,.0f}조</span>"

    # 오늘 순매수(네이버 실시간, 잠정) + 최근 20거래일 누적(확정)
    flow_html = ""
    if val_df is not None and float(val_df["foreign"].abs().max()) > 0:
        live = _get_flow_live(idx)
        if live:
            today, when = live[0] * 1e8, "오늘(잠정)"
        else:
            today, when = float(val_df["foreign"].iloc[-1]), f"{val_df.index[-1]:%m/%d}"
        s20 = float(val_df["foreign"].tail(20).sum())
        flow_html = (f"<div style='font-size:10px;margin-top:1px;color:#888;'>순매수 {when} "
                     f"<span style='color:{c(today)};'>{_fmt_krw_signed(today)}</span> · 20거래일 "
                     f"<span style='color:{c(s20)};'>{_fmt_krw_signed(s20)}</span></div>")
    chgs = " · ".join(x for x in (pp("1개월", chg_since(30)), pp("연초 대비", ytd())) if x)
    # 다른 카드와 같은 모양 — 제목·값·증감을 한 줄에. 최고·평균·최저는
    # 차트 안 선 옆으로 옮겼다(_make_foreign_chart). 순매수 줄은 그대로 둔다.
    st.markdown(
        f"<div style='padding:4px 0 2px;display:flex;align-items:baseline;"
        f"flex-wrap:wrap;column-gap:7px;row-gap:0;line-height:1.25;'>"
        f"<span style='font-size:11px;color:#888;white-space:nowrap;'>"
        f"{label} 외국인 보유액</span>"
        f"<span style='font-size:18px;font-weight:700;'>{cur:,.0f}조</span>"
        f"<span style='font-size:11.5px;color:#888;white-space:nowrap;'>"
        f"시총 대비 {cur_ratio:.2f}%</span>"
        f"<span style='font-size:11.5px;white-space:nowrap;'>{chgs}</span>"
        f"<span style='font-size:11.5px;color:#888;white-space:nowrap;'>"
        f"{h.index[-1]:%m/%d} 기준</span></div>"
        + flow_html
        + "<div style='height:8px;'></div>",
        unsafe_allow_html=True,
    )
    flow_m = None
    if val_df is not None:
        v = val_df[val_df.index >= hs.index[0]]
        flow_m = v[["foreign"]].resample("MS").sum()
    st.plotly_chart(_make_foreign_chart(hs, flow_m), use_container_width=True,
                    config={"displayModeBar": False, "scrollZoom": False, "staticPlot": False})


@st.cache_data(ttl=3600, show_spinner=False)
def _get_sp500_valuation() -> dict:
    """
    S&P 500 PER·PBR — batch_macro가 multpl에서 받아 둔 파일.

    코스피처럼 일별이 아니다: PER은 월간(1871~), PBR은 분기(1999~)다. 미국
    지수는 KRX 같은 일별 공개 소스가 없어서 이게 최선이고, 화면에도 그렇게 적는다.
    """
    try:
        with open(_SP500_CACHE, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return {}
    out = {}
    for key, rows in (raw or {}).items():
        if not rows:
            continue
        df = pd.DataFrame(rows, columns=["ts", "v"])
        df["date"] = pd.to_datetime(df["ts"], unit="ms")
        out[key] = df.set_index("date")["v"].sort_index()
    return out


def _render_sp500_band(years, span_label: str) -> None:
    """코스피·코스닥 아래에 같은 모양으로 한 줄. 세 번째 칸은 비운다(수급 자료 없음)."""
    data = _get_sp500_valuation()
    if not data:
        return
    st.markdown(
        "<div style='font-size:1.0rem;font-weight:700;margin:2px 0 1px;'>S&amp;P 500"
        "<span style='font-size:11px;font-weight:400;color:#888;margin-left:7px;'>"
        "PER 월간 · PBR 분기 (multpl)</span></div>",
        unsafe_allow_html=True)
    cols = st.columns(3)
    for col, (field, title) in zip(cols[:2], (("per", "PER"), ("pbr", "PBR"))):
        with col, st.container(key=f"mcard_band_sp500_{field}"):
            s_ = data.get(field)
            if s_ is None or s_.empty:
                _render_band_card(f"S&P 500 {title}", pd.Series(dtype=float), "배", span_label)
                continue
            if years:
                s_ = s_[s_.index >= s_.index[-1] - pd.DateOffset(years=years)]
            _render_band_card(f"S&P 500 {title}", s_, "배", span_label)


def _render_valuation_band() -> None:
    data = _get_index_valuation()
    if not data:
        section_title("지수 밸류에이션 밴드")
        st.caption("코스피·코스닥 PER·PBR 데이터를 아직 받지 못했습니다 (평일 18:30 배치가 수집).")
        return
    span_label = section_title_select("지수 밸류에이션 밴드", _BAND_PERIODS,
                                      key="band", index=1)
    years = _BAND_PERIODS[span_label]
    for idx, label in (("KOSPI", "코스피"), ("KOSDAQ", "코스닥")):
        df = data.get(idx)
        today_html = _index_today_html(idx, label, df)
        if today_html:
            st.markdown(today_html, unsafe_allow_html=True)
        # 시세 카드와 같은 3칸 격자로 카드 폭을 맞춘다. 지수당 한 줄: PER · PBR · 외국인 보유액.
        cols = st.columns(3)
        with cols[2], st.container(key=f"mcard_frn_{idx}"):
            _render_foreign_card(idx, label, df, years, span_label)
        for col, (field, unit, title) in zip(cols[:2], (("per", "배", "PER"), ("pbr", "배", "PBR"))):
            # 컨테이너 key가 app.py 전역 CSS의 카드 테두리를 입힌다(st-key-mcard_*).
            with col, st.container(key=f"mcard_band_{idx}_{field}"):
                if df is None or df.empty:
                    _render_band_card(f"{label} {title}", pd.Series(dtype=float), unit, span_label)
                    continue
                s = df[field]
                if years:
                    s = s[s.index >= s.index[-1] - pd.DateOffset(years=years)]
                _render_band_card(f"{label} {title}", s, unit, span_label,
                                  close=df["close"], index_name=label,
                                  volume=df["volume"], value=df["value"], foreign=df["foreign"])

    _render_sp500_band(years, span_label)


_COMPARE_PERIODS = {"1년": 1, "3년": 3, "5년": 5, "10년": 10}

# 여기서도 빨강/파랑을 쓰지 않는다 — 계열 구분으로 칠한 색이라도 이 앱에서
# 빨강·파랑은 '올랐다/내렸다'로 읽히기 때문이다(2026-11-06 요청). 코스피는 다른
# 차트와 같은 색, S&P는 그와 확실히 구분되는 호박색, 금리는 성격이 달라
# (오른쪽 축) 회색 점선으로 낮춘다.
_CMP_KOSPI = _VALUATION_LINE
_CMP_SP500 = "#E8870B"   # 지금은 안 쓴다 — S&P 차트를 뺐다(2026-11-08). 되살릴 때를 위해 남긴다.
_CMP_YIELD = "#7E8A93"


@st.cache_data(ttl=3600, show_spinner=False)
def _get_compare_series() -> dict:
    """
    {"sp500": Series, "ust10y": Series} — batch_macro가 받아 둔 연도별 파일.

    코스피는 여기서 읽지 않는다 — index_val이 일별 종가를 이미 갖고 있어서
    _get_index_valuation()을 그대로 쓴다(같은 값을 두 번 저장하지 않는다).
    """
    rows = {"sp500": {}, "ust10y": {}}
    try:
        names = sorted(os.listdir(_COMPARE_DIR))
    except Exception:
        return {}
    for fn in names:
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(_COMPARE_DIR, fn), "r", encoding="utf-8") as f:
                raw = json.load(f)
        except Exception:
            continue
        for key in rows:
            for d, v in (raw or {}).get(key, []):
                rows[key][d] = v
    out = {}
    for key, by_date in rows.items():
        if not by_date:
            continue
        s = pd.Series(by_date, dtype=float)
        s.index = pd.to_datetime(s.index)
        out[key] = s.sort_index()
    return out


_YIELD_HI = 4.6                        # 사용자가 지목한 기준선(2026-11-07)
_YIELD_BAND = "rgba(126,138,147,0.17)"  # 그 위 구간 배경 — 계열색과 안 섞이게 회색


def _yield_runs(yld: pd.Series, thr: float = _YIELD_HI, limit: int = 150):
    """금리가 thr 이상인 연속 구간 [(시작, 끝), ...]. 배경을 칠하는 데 쓴다."""
    runs, start, prev = [], None, None
    for d, v in yld.items():
        hot = bool(v >= thr)
        if hot and start is None:
            start = d
        elif not hot and start is not None:
            runs.append((start, prev))
            start = None
        prev = d
    if start is not None:
        runs.append((start, prev))
    return runs[-limit:]


def _make_rate_chart(s: pd.Series, name: str, color: str, yld: pd.Series | None) -> go.Figure:
    """
    지수 하나(왼쪽 축) + 미국채 10년 금리(오른쪽 축). 금리 4.6% 이상 구간은 배경을 칠한다.

    💡 처음엔 코스피·S&P·금리를 한 장에 겹쳐 그렸는데, 사용자가 '지수 하나당 금리 하나'로
    나눠 보길 원했다(2026-11-07). 한 장에 선이 셋이면 어느 지수가 금리에 반응했는지가
    서로 가린다 — 나누면 같은 금리 곡선을 두 번 그리는 대신 비교가 또렷해진다.
    """
    fig = go.Figure()
    # 배경 먼저(layer="below") — 선과 격자 아래에 깔린다.
    if yld is not None and not yld.empty:
        for x0, x1 in _yield_runs(yld):
            fig.add_vrect(x0=x0, x1=x1 + pd.Timedelta(days=1),
                          fillcolor=_YIELD_BAND, line_width=0, layer="below")
    fig.add_trace(go.Scatter(
        x=s.index, y=s.values, mode="lines", name=name,
        line=dict(color=color, width=1.6),
        hovertemplate=f"<b>{name}</b> %{{y:,.2f}}<extra></extra>",
    ))
    if yld is not None and not yld.empty:
        fig.add_trace(go.Scatter(
            x=yld.index, y=yld.values, mode="lines", name="미국채 10년", yaxis="y2",
            line=dict(color=_CMP_YIELD, width=1.3, dash="dot"),
            hovertemplate="<b>미국채 10년</b> %{y:.2f}%<extra></extra>",
        ))
        # 기준선 — add_hline은 기본 축(y)에 걸려서 보조축엔 shape으로 직접 넣는다.
        fig.add_shape(type="line", xref="paper", x0=0, x1=1, yref="y2",
                      y0=_YIELD_HI, y1=_YIELD_HI,
                      line=dict(color=_CMP_YIELD, width=1, dash="dash"))
        fig.add_annotation(xref="paper", x=0.997, xanchor="right", yref="y2",
                           y=_YIELD_HI, yanchor="bottom", text=f"<b>{_YIELD_HI}%</b>",
                           showarrow=False, font=dict(size=9, color=_CMP_YIELD),
                           bgcolor="rgba(255,255,255,0.72)", borderpad=1)
    # 매크로 탭 정책: 축을 fixedrange로 잠그고 툴팁만 남긴다(staticPlot 금지 —
    # 드래그와 함께 마우스오버 수치까지 죽는다). AGENTS.md '차트 설정' 참고.
    fig.update_layout(
        height=300, margin=dict(l=0, r=0, t=26, b=18),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        dragmode=False, hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="left", x=0,
                    font=dict(size=10.5), bgcolor="rgba(0,0,0,0)"),
        xaxis=dict(showgrid=False, zeroline=False, fixedrange=True,
                   tickfont=dict(size=9, color="#888"), ticklen=0, **_year_ticks(s.index)),
        yaxis=dict(title=dict(text="지수", font=dict(size=9, color="#999")),
                   showgrid=True, gridcolor="rgba(0,0,0,0.05)", zeroline=False,
                   fixedrange=True, tickfont=dict(size=9, color="#888"),
                   tickformat=",.0f"),
        yaxis2=dict(title=dict(text="금리 %", font=dict(size=9, color=_CMP_YIELD)),
                    overlaying="y", side="right", showgrid=False, zeroline=False,
                    fixedrange=True, tickfont=dict(size=9, color=_CMP_YIELD)),
    )
    return fig


def _sign_color(v) -> str:
    return "#ef5350" if (v or 0) > 0 else "#1565C0" if (v or 0) < 0 else "#888"


def _rate_summary_html(s: pd.Series, name: str, yld: pd.Series | None, span_label: str) -> str:
    """차트 아래 두 줄 — 기간 등락률, 그리고 금리 4.6% 위/아래에서의 일평균 등락."""
    bits = []
    if len(s) > 1 and float(s.iloc[0]):
        chg = (float(s.iloc[-1]) / float(s.iloc[0]) - 1) * 100
        bits.append(f"<b>{name}</b> <span style='color:{_sign_color(chg)};font-weight:600;'>"
                    f"{chg:+.1f}%</span>")
    if yld is not None and len(yld) > 1:
        d = float(yld.iloc[-1]) - float(yld.iloc[0])
        bits.append(f"<b>미국채 10년</b> {float(yld.iloc[-1]):.2f}% "
                    f"<span style='color:{_sign_color(d)};font-weight:600;'>({d:+.2f}%p)</span>")
    head = " · ".join(bits)
    out = (f"<div style='font-size:11.5px;color:#666;margin-top:-4px;'>"
           f"{span_label} · {head}</div>")
    # 금리 4.6% 위/아래를 나눠 '그 구간에서 지수가 어땠나'를 일평균으로 비교한다.
    # 누적으로 적으면 구간 길이가 달라 비교가 안 된다(42일 vs 203일).
    if yld is None or yld.empty:
        return out
    # 금리는 미국 거래일, 코스피는 한국 거래일이라 날짜가 어긋난다 — 합집합에서
    # 앞으로 채운 뒤 지수 날짜에 맞춘다(reindex 후 ffill은 NaN만 남아 안 된다).
    y = yld.reindex(s.index.union(yld.index)).ffill().reindex(s.index)
    both = pd.concat([s.pct_change() * 100, y], axis=1).dropna()
    both.columns = ["chg", "y"]
    parts = []
    for d_, lbl in ((both[both["y"] >= _YIELD_HI], f"{_YIELD_HI}% 이상"),
                    (both[both["y"] < _YIELD_HI], f"{_YIELD_HI}% 미만")):
        if d_.empty:
            continue
        m = float(d_["chg"].mean())
        parts.append(f"금리 {lbl} {len(d_)}일 <span style='color:{_sign_color(m)};"
                     f"font-weight:600;'>일평균 {m:+.2f}%</span>")
    if parts:
        out += "<div style='font-size:11.5px;color:#666;'>" + " · ".join(parts) + "</div>"
    return out


def _render_compare_section() -> None:
    data = _get_compare_series()
    val = _get_index_valuation()
    kospi_all = val.get("KOSPI", {}).get("close") if isinstance(val.get("KOSPI"), pd.DataFrame) else None
    if not data and kospi_all is None:
        section_title("지수 · 금리 비교")
        st.caption("비교용 데이터를 아직 받지 못했습니다 (매일 배치가 수집).")
        return
    span_label = section_title_select("지수 · 금리 비교", _COMPARE_PERIODS,
                                      key="cmp", index=0)
    years = _COMPARE_PERIODS[span_label]

    sp500, yld = data.get("sp500"), data.get("ust10y")
    ends = [s.index[-1] for s in (kospi_all, sp500, yld) if s is not None and not s.empty]
    if not ends:
        st.caption("비교용 데이터를 아직 받지 못했습니다.")
        return
    start = max(ends) - pd.DateOffset(years=years)
    cut = lambda s: s[s.index >= start].dropna() if s is not None else None
    kospi, sp500, yld = cut(kospi_all), cut(sp500), cut(yld)

    # 💡 S&P 500 + 금리 차트는 뺐다(2026-11-08 요청) — 코스피 한 장만 화면 폭으로.
    # 하루 전 상관은 아래에 남아 있어 S&P 계열은 계속 받는다(batch_macro.update_compare_series).
    # 되살리려면 이 블록을 st.columns(2) 루프로 되돌리면 된다 — 데이터는 그대로 있다.
    cfg = {"displayModeBar": False, "scrollZoom": False, "staticPlot": False}
    if kospi is None or kospi.empty:
        st.caption("코스피 데이터를 아직 받지 못했습니다.")
    else:
        st.plotly_chart(_make_rate_chart(kospi, "코스피", _CMP_KOSPI, yld),
                        use_container_width=True, config=cfg)
        st.markdown(_rate_summary_html(kospi, "코스피", yld, span_label),
                    unsafe_allow_html=True)

    # 일간 변동률의 상관 — 수준이 아니라 '같이 흔들리는 정도'를 본다.
    #
    # ⚠️ 같은 날짜끼리 비교하면 안 된다. 코스피는 15:30(KST)에 끝나 미국장이
    # 열리기도 전이라, 코스피 당일 등락은 미국의 '전날' 종가에 반응한 결과다.
    # 같은 날로 맞춰 재면 상관이 0.11로 나오고(실측: 3년 +0.111, 5년 +0.115),
    # S&P를 하루 밀어 맞추면 0.32로 세 배가 된다(3년 +0.320, 5년 +0.326).
    # 전자를 화면에 적으면 "두 시장은 거의 무관하다"로 잘못 읽힌다.
    if kospi is not None and sp500 is not None and not kospi.empty and not sp500.empty:
        both = pd.concat([kospi.pct_change(), sp500.pct_change().shift(1)],
                         axis=1).dropna()
        if len(both) > 30:
            st.markdown("<div style='font-size:11.5px;color:#666;margin-top:2px;'>"
                        f"코스피~S&P 전일 상관 <b>{both.corr().iloc[0, 1]:.2f}</b></div>",
                        unsafe_allow_html=True)


_INFLATION_PERIODS = {"3년": 3, "10년": 10, "전체": None}

# (키, 표시 이름, 목표치). 목표 2%는 연준의 물가 목표 — 코어 PCE가 그 대상 지표이고,
# CPI는 대중이 보는 숫자다. PPI는 목표가 없는 지표라 None.
_INFLATION_ITEMS = [
    ("cpi",      "미국 CPI",         2.0),
    ("core_cpi", "미국 코어 CPI",     2.0),
    ("ppi",      "미국 PPI 최종수요",  None),
    ("core_pce", "미국 코어 PCE",     2.0),
]


# ───────── 수급 건전성 (투자자예탁금 · 신용거래융자 · 반대매매) ─────────
#
# 목적은 "반대매매가 어디까지 났고 그래서 저점일 수 있나"다. 그런데 반대매매 금액
# 하나로는 판단이 안 된다 — 이 통계는 **미수 반대매매만** 집계하고(규모가 훨씬 큰
# 신용융자 담보부족 청산은 공식 통계가 없다), 이미 청산된 뒤의 숫자라 후행한다.
# 그래서 세 장을 같이 둔다: ① 신용융자 잔고가 고점 대비 얼마나 줄었나(실제
# 디레버리징 규모) ② 예탁금 대비 신용잔고(과열도) ③ 반대매매 금액·비중.
#
# 데이터는 앱에서 직접 받는다(배치 아님) — API가 전체 이력을 매번 주므로 쌓을
# 필요가 없고, 커밋되는 파일이 늘지 않는다. 자세한 내용은 kofia_flow.py 머리말.
_FLOW_PERIODS = {"3개월": 3, "6개월": 6, "1년": 12, "3년": 36, "전체": None}
# 예탁금 대비 신용잔고 과열선 — 40%를 넘으면 경계, 45% 이상이면 과열로 읽는다.
_CRD_WARN, _CRD_HOT = 40.0, 45.0
_FLOW_API_URL = "https://www.data.go.kr/data/15094809/openapi.do"


@st.cache_data(ttl=3600, show_spinner=False)
def _get_flow_stats():
    """(DataFrame|None, 설정오류문구). 금액 단위는 억원.

    💡 보통은 "캐시 함수는 실패를 반환하지 말고 예외로 올린다"가 규칙이다(빈 결과가
    TTL 동안 캐시되면 일시 장애가 한 시간짜리 '데이터 없음'이 된다). 여기서는
    **활용신청이 안 된 상태만** 예외로 반환값에 담는다 — 그건 일시 장애가 아니라
    사람이 신청해야 풀리는 설정 문제인데, 예외로 올리면 캐시가 안 돼서 5분마다
    돌아오는 자동 새로고침이 그때마다 쓸데없는 요청을 쏘기 때문이다. 나머지
    오류(네트워크·429·포털 장애)는 그대로 올려 다음 새로고침에 다시 시도한다.
    """
    key = st.secrets.get("DATA_GO_KR_KEY", "")
    if not key:
        return None, "DATA_GO_KR_KEY가 설정돼 있지 않습니다."
    try:
        return kofia_flow.fetch_flow(key), ""
    except kofia_flow.KofiaNotRegistered:
        return None, (
            "이 키로는 아직 조회할 수 없습니다 — 공공데이터포털에서 "
            f"[금융투자협회 종합통계정보]({_FLOW_API_URL}) 활용신청을 한 번 하면 "
            "지금 쓰는 키 그대로 열립니다."
        )


def _make_flow_chart(s: pd.Series, fmt: str, warn: float | None = None,
                     bars: bool = False, overlay: pd.Series | None = None,
                     overlay_fmt: str = ",.1f", overlay_unit: str = "") -> go.Figure:
    """수급 카드용 작은 차트. warn을 주면 그 이상 구간 배경을 칠하고 기준선을 긋는다.

    overlay는 오른쪽 축에 겹치는 보조 계열이다(2026-11-14 요청: 예탁금 대비 신용잔고
    카드에 고객예탁금을 같이). 신용잔고 카드가 지수를 겹치는 것과 **같은 회색 점선** —
    매크로 탭에서 빨강·파랑은 '올랐다/내렸다'라 계열 구분에 쓰지 않는다.
    비율만 보면 분모(예탁금)가 줄어 올라간 건지 분자(잔고)가 늘어 올라간 건지 모른다.
    """
    hi, lo = float(s.max()), float(s.min())
    if bars:
        base_y, top_y = 0.0, hi * 1.18 or 1.0
    else:
        span = (hi - lo) or abs(hi) or 1.0
        base_y, top_y = lo - span * 0.14, hi + span * 0.10
    fig = go.Figure()
    if warn is not None:
        for x0, x1 in _yield_runs(s, thr=warn):
            fig.add_vrect(x0=x0, x1=x1 + pd.Timedelta(days=1),
                          fillcolor=_SPREAD_BAND, line_width=0, layer="below")
    if bars:
        # 막대도 한 가지 색으로 — 매크로 탭은 선 색에 뜻을 담지 않는다(AGENTS.md).
        fig.add_trace(go.Bar(
            x=s.index, y=s.values, marker=dict(color=_VALUATION_LINE), showlegend=False,
            hovertemplate=f"%{{x|%Y-%m-%d}}<br><b>%{{y:{fmt}}}</b><extra></extra>",
        ))
        avg = s.rolling(20, min_periods=5).mean()
        fig.add_trace(go.Scatter(
            x=avg.index, y=avg.values, mode="lines",
            line=dict(color="#78909C", width=1.1, dash="dot"), showlegend=False,
            hovertemplate=f"20일 평균 %{{y:{fmt}}}<extra></extra>",
        ))
    else:
        # 채움을 쓰려면 폭 0인 바닥 선이 바로 앞에 있어야 한다(fill="tonexty").
        fig.add_trace(go.Scatter(x=s.index, y=[base_y] * len(s), mode="lines",
                                 line=dict(width=0), showlegend=False, hoverinfo="skip"))
        fig.add_trace(go.Scatter(
            x=s.index, y=s.values, mode="lines",
            line=dict(color=_VALUATION_LINE, width=1.5), showlegend=False,
            fill="tonexty", fillcolor=_LINE_FILL,
            hovertemplate=f"%{{x|%Y-%m-%d}}<br><b>%{{y:{fmt}}}</b><extra></extra>",
        ))
        fig.add_trace(go.Scatter(
            x=[s.index[-1]], y=[float(s.iloc[-1])], mode="markers",
            marker=dict(color=_VALUATION_LINE, size=7, line=dict(color="#fff", width=1)),
            showlegend=False, hoverinfo="skip",
        ))
    notes = []
    lines = [(hi, "#ef5350", "dash", "최고", "top"), (lo, "#1565C0", "dash", "최저", "bottom")]
    if warn is not None:
        lines.append((warn, "#78909C", "dot", "경계", "bottom"))
    for y, color, dash, lbl, anchor_ in lines:
        if bars and lbl == "최저":
            continue                     # 막대 차트의 최저는 늘 바닥이라 뜻이 없다
        if y > top_y or y < base_y:
            continue
        fig.add_hline(y=y, line=dict(color=color, width=1.1, dash=dash))
        notes.append(dict(
            xref="paper", yref="y", x=0.004, y=y, xanchor="left", yanchor=anchor_,
            text=f"<b>{lbl}</b> {format(y, fmt)}", showarrow=False,
            font=dict(size=9.5, color=color),
            bgcolor="rgba(255,255,255,0.72)", borderpad=1,
        ))
    y2 = dict(overlaying="y", visible=False, fixedrange=True)
    if overlay is not None and not overlay.dropna().empty:
        o = overlay.dropna()
        ohi, olo = float(o.max()), float(o.min())
        ospan = (ohi - olo) or abs(ohi) or 1.0
        # 두 선이 서로를 가리지 않게 보조 계열 쪽을 조금 더 넓게 잡는다(신용잔고 카드와 같게).
        y2["range"] = [olo - ospan * 0.22, ohi + ospan * 0.16]
        fig.add_trace(go.Scatter(
            x=o.index, y=o.values, mode="lines", yaxis="y2",
            line=dict(color=_CRD_IDX_LINE, width=1.2, dash="dot"), showlegend=False,
            hovertemplate=f"예탁금 <b>%{{y:{overlay_fmt}}}{overlay_unit}</b><extra></extra>",
        ))
    fig.update_layout(
        height=110, margin=dict(l=0, r=2, t=8, b=18), bargap=0.15,
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        dragmode=False, hovermode="x",
        xaxis=dict(showgrid=False, zeroline=False, fixedrange=True,
                   tickfont=dict(size=9, color="#888"), ticklen=0, **_year_ticks(s.index)),
        yaxis=dict(range=[base_y, top_y], showgrid=False, zeroline=False,
                   fixedrange=True, showticklabels=False),
        yaxis2=y2,
        annotations=notes,
    )
    return fig


# ───────── 시장별 신용잔고 (코스피 · 코스닥) ─────────
#
# 합계만 보면 어느 쪽이 레버리지를 지고 있는지 안 보인다 — 코스닥은 개인·신용 비중이
# 구조적으로 높아 같은 1조가 코스피의 1조보다 훨씬 무겁다(2026-11-08 요청).
# 지수를 같은 장에 겹치는 이유는 수준이 아니라 **벌어지는 자리**를 보기 위해서다:
# 지수가 빠지는데 잔고가 안 줄면 청산이 아직 남았고, 지수가 멈췄는데 잔고만 계속
# 줄면 디레버리징이 끝나가는 국면이다. 잔고 자체는 지수를 따라가므로 겹치는 구간은
# 아무 정보도 아니다.
#
# 지수는 보조 계열이라 비교 차트의 금리와 같은 회색 점선으로 낮춘다 — 매크로 탭에서
# 빨강·파랑은 '올랐다/내렸다'라 계열 구분에 쓰지 않는다(AGENTS.md 색상 규칙).
_CRD_IDX_LINE = _CMP_YIELD


def _make_crd_idx_chart(crd: pd.Series, idx: pd.Series | None) -> go.Figure:
    hi, lo = float(crd.max()), float(crd.min())
    span = (hi - lo) or abs(hi) or 1.0
    base_y, top_y = lo - span * 0.14, hi + span * 0.10
    fig = go.Figure()
    # 채움을 쓰려면 폭 0인 바닥 선이 바로 앞에 있어야 한다(fill="tonexty").
    fig.add_trace(go.Scatter(x=crd.index, y=[base_y] * len(crd), mode="lines",
                             line=dict(width=0), showlegend=False, hoverinfo="skip"))
    fig.add_trace(go.Scatter(
        x=crd.index, y=crd.values, mode="lines",
        line=dict(color=_VALUATION_LINE, width=1.5), showlegend=False,
        fill="tonexty", fillcolor=_LINE_FILL,
        hovertemplate="%{x|%Y-%m-%d}<br>잔고 <b>%{y:,.1f}조</b><extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=[crd.index[-1]], y=[float(crd.iloc[-1])], mode="markers",
        marker=dict(color=_VALUATION_LINE, size=7, line=dict(color="#fff", width=1)),
        showlegend=False, hoverinfo="skip",
    ))
    notes = [dict(xref="paper", yref="y", x=0.004, y=hi, xanchor="left", yanchor="top",
                  text=f"<b>최고</b> {hi:,.1f}조", showarrow=False,
                  font=dict(size=9.5, color="#ef5350"),
                  bgcolor="rgba(255,255,255,0.72)", borderpad=1)]
    fig.add_hline(y=hi, line=dict(color="#ef5350", width=1.1, dash="dash"))
    y2 = dict(overlaying="y", visible=False, fixedrange=True)
    if idx is not None and not idx.empty:
        ihi, ilo = float(idx.max()), float(idx.min())
        ispan = (ihi - ilo) or abs(ihi) or 1.0
        # 잔고 선과 지수 선이 서로를 가리지 않게 지수 쪽을 조금 더 넓게 잡는다.
        y2["range"] = [ilo - ispan * 0.22, ihi + ispan * 0.16]
        fig.add_trace(go.Scatter(
            x=idx.index, y=idx.values, mode="lines", yaxis="y2",
            line=dict(color=_CRD_IDX_LINE, width=1.2, dash="dot"), showlegend=False,
            hovertemplate="지수 <b>%{y:,.0f}</b><extra></extra>",
        ))
    fig.update_layout(
        height=110, margin=dict(l=0, r=2, t=8, b=18),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        dragmode=False, hovermode="x",
        xaxis=dict(showgrid=False, zeroline=False, fixedrange=True,
                   tickfont=dict(size=9, color="#888"), ticklen=0, **_year_ticks(crd.index)),
        yaxis=dict(range=[base_y, top_y], showgrid=False, zeroline=False,
                   fixedrange=True, showticklabels=False),
        yaxis2=y2,
        annotations=notes,
    )
    return fig


def _crd_market_card(key: str, name: str, full: pd.Series | None,
                     win: pd.Series | None, idx: pd.Series | None, idx_label: str) -> None:
    with st.container(key=f"mcard_flow_{key}"):
        _crd_market_body(name, full, win, idx, idx_label)


def _crd_market_body(name: str, full: pd.Series | None, win: pd.Series | None,
                     idx: pd.Series | None, idx_label: str) -> None:
    if win is None or win.dropna().empty or full is None or full.dropna().empty:
        st.markdown(f"<div style='font-size:11px;color:#888;'>{name}</div>"
                    f"<div style='font-size:18px;font-weight:700;color:#ccc;'>N/A</div>",
                    unsafe_allow_html=True)
        return
    win, full = win.dropna(), full.dropna()
    cur = float(win.iloc[-1])
    d1 = cur - (float(win.iloc[-2]) if len(win) > 1 else cur)
    # 배지·고점 낙폭은 **전체 이력** 기준 — 기간을 좁혀도 뜻이 바뀌지 않게.
    pk, pk_i = float(full.max()), full.idxmax()
    drop = (float(full.iloc[-1]) / pk - 1) * 100
    badge_html = ""
    if drop <= -15:
        txt, bclr = "청산 진행", "#1565C0"
    elif drop >= -2:
        txt, bclr = "고점권", "#ef5350"
    else:
        txt, bclr = "", ""
    if txt:
        badge_html = (f"<span style='background:{bclr};color:#fff;font-size:9.5px;"
                      f"font-weight:700;padding:1px 5px;border-radius:3px;'>{txt}</span>")
    st.markdown(
        f"<div style='padding:4px 0 10px;display:flex;align-items:baseline;"
        f"flex-wrap:wrap;column-gap:7px;row-gap:0;line-height:1.25;'>"
        f"<span style='font-size:11px;color:#888;white-space:nowrap;'>{name}</span>{badge_html}"
        f"<span style='font-size:18px;font-weight:700;'>{cur:,.1f}조</span>"
        f"<span style='font-size:11.5px;color:{_sign_color(d1)};white-space:nowrap;'>"
        f"전일 {d1:+,.2f}조</span>"
        f"<span style='font-size:11.5px;color:#888;white-space:nowrap;'>"
        f"{win.index[-1]:%m/%d}</span></div>",
        unsafe_allow_html=True,
    )
    st.plotly_chart(_make_crd_idx_chart(win, idx), use_container_width=True,
                    config={"displayModeBar": False, "scrollZoom": False, "staticPlot": False})
    st.markdown(
        f"<div style='font-size:10.5px;color:#9aa5b1;margin-top:1px;'>"
        f"고점 {pk:,.1f}조({pk_i:%y.%m}) 대비 "
        f"<span style='color:{_sign_color(drop)};font-weight:600;'>{drop:+.1f}%</span>"
        f" · 회색 점선 {idx_label}</div>",
        unsafe_allow_html=True,
    )


def _flow_card(key: str, name: str, s: pd.Series, unit: str, fmt: str, sub: str,
               badge: tuple | None = None, warn: float | None = None,
               bars: bool = False, overlay: pd.Series | None = None,
               overlay_fmt: str = ",.1f", overlay_unit: str = "") -> None:
    with st.container(key=f"mcard_flow_{key}"):
        _flow_card_body(name, s, unit, fmt, sub, badge, warn, bars,
                        overlay, overlay_fmt, overlay_unit)


def _flow_card_body(name: str, s: pd.Series, unit: str, fmt: str, sub: str,
                    badge: tuple | None = None, warn: float | None = None,
                    bars: bool = False, overlay: pd.Series | None = None,
                    overlay_fmt: str = ",.1f", overlay_unit: str = "") -> None:
    if s is None or s.dropna().empty:
        st.markdown(f"<div style='font-size:11px;color:#888;'>{name}</div>"
                    f"<div style='font-size:18px;font-weight:700;color:#ccc;'>N/A</div>",
                    unsafe_allow_html=True)
        return
    s = s.dropna()
    cur = float(s.iloc[-1])
    prev = float(s.iloc[-2]) if len(s) > 1 else cur
    d1 = cur - prev
    badge_html = ""
    if badge:
        txt, bclr = badge
        badge_html = (f"<span style='background:{bclr};color:#fff;font-size:9.5px;"
                      f"font-weight:700;padding:1px 5px;border-radius:3px;'>{txt}</span>")
    clr = "#ef5350" if d1 > 0 else "#1565C0" if d1 < 0 else "#888"
    st.markdown(
        f"<div style='padding:4px 0 10px;display:flex;align-items:baseline;"
        f"flex-wrap:wrap;column-gap:7px;row-gap:0;line-height:1.25;'>"
        f"<span style='font-size:11px;color:#888;white-space:nowrap;'>{name}</span>{badge_html}"
        f"<span style='font-size:18px;font-weight:700;'>{format(cur, fmt)}{unit}</span>"
        f"<span style='font-size:11.5px;color:{clr};white-space:nowrap;'>"
        f"전일 {format(d1, '+' + fmt)}{unit}</span>"
        f"<span style='font-size:11.5px;color:#888;white-space:nowrap;'>"
        f"{s.index[-1]:%m/%d}</span></div>",
        unsafe_allow_html=True,
    )
    st.plotly_chart(_make_flow_chart(s, fmt, warn, bars, overlay, overlay_fmt, overlay_unit),
                    use_container_width=True,
                    config={"displayModeBar": False, "scrollZoom": False, "staticPlot": False})
    if sub:
        st.markdown(f"<div style='font-size:10.5px;color:#9aa5b1;margin-top:1px;'>{sub}</div>",
                    unsafe_allow_html=True)


def _flow_summary_html(df: pd.DataFrame) -> str:
    """'청산이 어디까지 났나'를 한 줄로. 카드 셋을 따로 읽지 않아도 되게."""
    parts = []
    crd = df.get("crd")
    if crd is not None and crd.dropna().size > 20:
        crd = crd.dropna()
        pk_i = crd.idxmax()
        pk, cur = float(crd.max()), float(crd.iloc[-1])
        drop = (cur / pk - 1) * 100
        parts.append(
            f"신용잔고 고점 {pk/1e4:,.1f}조({pk_i:%y.%m}) 대비 "
            f"<span style='color:{_sign_color(drop)};font-weight:600;'>{drop:+.1f}%"
            f"({(cur-pk)/1e4:+,.1f}조)</span>")
    opps = df.get("opps")
    if opps is not None and opps.dropna().size > 30:
        opps = opps.dropna()
        r5 = float(opps.tail(5).mean())
        base = float(opps.tail(250).mean()) or 1.0
        parts.append(f"최근 5일 반대매매 평균 {r5:,.0f}억 "
                     f"(1년 평균의 <b>{r5/base:.1f}배</b>)")
    dpsg = df.get("dpsg")
    if dpsg is not None and dpsg.dropna().size > 20:
        dpsg = dpsg.dropna()
        m1 = dpsg.asof(dpsg.index[-1] - pd.Timedelta(days=30))
        if pd.notna(m1):
            chg = (float(dpsg.iloc[-1]) - float(m1)) / 1e4
            parts.append(f"예탁금 1개월 <span style='color:{_sign_color(chg)};"
                         f"font-weight:600;'>{chg:+,.1f}조</span>")
    if not parts:
        return ""
    return ("<div style='font-size:11.5px;color:#555;margin:2px 0 4px;"
            "line-height:1.5;'>" + " · ".join(parts) + "</div>")


# 해석은 펼침 안에만 둔다 — 늘 펼쳐 두면 카드보다 글이 길어진다. 숫자 기준선은
# 차트 안에 이미 그려져 있으니 여기서는 '그래서 어떻게 읽나'만 적는다.
#
# ⚠️ 볼드 닫는 `**`를 `%` 바로 뒤에 두지 말 것 — 강조로 안 읽히고 별표가 그대로
# 화면에 나온다(실측: "…대비 -15~20%**까지"). 볼드는 단어에만 건다.
_FLOW_HELP = """
- **신용잔고**가 고점 대비 15~20% 줄면 디레버리징이 상당히 진행된 것으로 본다.
  그 전까지는 '아직 남았다'로 읽는다.
- **반대매매**는 평소의 3~5배로 치솟았다가 며칠 안에 꺾이는 자리가 저점 근처인 경우가 많다.
  평균 수준이면 강제 청산이 아니라 **자발적 상환**이다 — 같은 감소라도 뜻이 전혀 다르다.
- **예탁금 대비 신용잔고**가 40%를 넘으면 경계, 45% 이상이면 과열. 30%대 초반까지
  내려오면 부담이 꽤 풀린 상태다. 같은 장의 **회색 점선이 고객예탁금**이다 — 비율만
  보면 분모(예탁금)가 줄어 오른 것인지 분자(잔고)가 늘어 오른 것인지 갈리지 않는다.
  둘이 같이 줄면 디레버리징이고, 예탁금만 빠지면 자금 이탈이다.
- **예탁금**이 줄다 멈추고 늘기 시작하면 대기 자금이 돌아오는 신호다.
- **시장별 잔고**는 지수와 **벌어지는 자리**를 본다. 지수가 빠지는데 잔고가 안 줄면
  청산이 남았고, 지수가 멈췄는데 잔고만 계속 줄면 끝나가는 국면이다.
- ⚠️ 전부 **후행 지표**다. 저점 '확인'에는 쓰되 예측에는 못 쓴다. 숫자가 들어오는 것도
  2~3영업일 늦어 급락 당일의 청산은 아직 보이지 않는다 — 금투협이 내놓은 것을
  공공데이터포털이 한 번 더 받아 여는 구조라, 금투협 사이트보다 하루쯤 더 늦다.
"""


def _render_flow_section() -> None:
    try:
        df, cfg_err = _get_flow_stats()
    except Exception as e:                      # 네트워크·429·포털 장애 — 다음 새로고침에 재시도
        section_title("수급 건전성")
        st.caption(f"금융투자협회 통계를 불러오지 못했습니다 ({type(e).__name__}). "
                   "잠시 후 새로고침하면 다시 시도합니다.")
        return
    if df is None:
        section_title("수급 건전성")
        st.caption(cfg_err)
        return

    span_label = section_title_select("수급 건전성", _FLOW_PERIODS, key="flow", index=2)
    months = _FLOW_PERIODS[span_label]
    w = df if not months else df[df.index >= df.index[-1] - pd.DateOffset(months=months)]
    st.markdown(_flow_summary_html(df), unsafe_allow_html=True)

    crd = w.get("crd")
    dpsg = w.get("dpsg")
    ratio = None
    if crd is not None and dpsg is not None:
        ratio = (crd / dpsg * 100).dropna()
    ratio_badge = None
    if ratio is not None and not ratio.empty:
        cur = float(ratio.iloc[-1])
        if cur >= _CRD_HOT:
            ratio_badge = ("과열", "#ef5350")
        elif cur >= _CRD_WARN:
            ratio_badge = ("경계", "#ef5350")

    # 시장별로 나눠 지수와 겹친다 — 합계 한 장으로는 어느 쪽이 레버리지를 지고
    # 있는지 안 보인다. 2026-11-09에 '신용거래융자 잔고(합계)' 카드를 빼고 이 둘로
    # 대신했다. 합계 수치는 위 요약 줄의 '고점 대비 −N%(−N조)'가 계속 말한다.
    idx_data = _get_index_valuation() or {}
    mcols = st.columns(2)
    for col, (ikey, label, mkey) in zip(
            mcols, (("KOSPI", "코스피", "crd_kospi"), ("KOSDAQ", "코스닥", "crd_kosdaq"))):
        full = df.get(mkey)
        win = w.get(mkey)
        idf = idx_data.get(ikey)
        idx = None
        if idf is not None and not idf.empty and win is not None and not win.dropna().empty:
            idx = idf["close"]
            idx = idx[idx.index >= win.dropna().index[0]].dropna()
        with col:
            _crd_market_card(
                f"crd_{ikey.lower()}", f"{label} 신용잔고",
                None if full is None else full / 1e4,
                None if win is None else win / 1e4, idx, label)

    cols = st.columns(2)
    with cols[0]:
        # 비율만 보면 분모(예탁금)가 줄어 오른 건지 분자(잔고)가 늘어 오른 건지 갈리지
        # 않는다 — 고객예탁금을 회색 점선으로 같이 겹친다(2026-11-14 요청).
        dep = None if dpsg is None else (dpsg / 1e4).dropna()      # 억원 -> 조원
        # 1개월 증감은 맨 위 요약 줄이 이미 말한다 — 여기는 현재값만.
        dep_sub = ("" if dep is None or dep.empty else
                   f"회색 점선 고객예탁금 <b>{float(dep.iloc[-1]):,.1f}조</b>")
        # 경계선은 차트 안에 '경계 40.0'으로 이미 그려진다 — 밑에 또 적지 않는다.
        _flow_card("ratio", "예탁금 대비 신용잔고", ratio, "%", ".1f", dep_sub,
                   ratio_badge, warn=_CRD_WARN,
                   overlay=dep, overlay_fmt=",.1f", overlay_unit="조")
    with cols[1]:
        # ⚠️ 이 통계는 '미수금 반대매매'만이다 — 규모가 훨씬 큰 신용융자 담보부족
        # 청산은 어느 공식 통계에도 없다. 그 한계를 카드 밑 한 줄로 못박아 둔다.
        # (섹션 캡션으로 따로 적었더니 카드 설명 세 줄과 뭉쳐 읽혔다.)
        _flow_card("opps", "반대매매", w.get("opps"), "억", ",.0f",
                   "미수금 반대매매만 · 점선 20일 평균", bars=True)

    with st.expander("읽는 법"):
        st.markdown(_FLOW_HELP)


# ───────── 신용 스프레드 (하이일드 · 투자등급 OAS) ─────────
#
# batch_macro가 FRED에서 받아 누적한다. 읽는 법은 절대 수준보다 **방향과 속도**다:
# 지수가 빠지는데 스프레드가 조용하면 수급 조정이고, 지수가 버티는데 스프레드가
# 벌어지면 그게 가장 위험한 조합이다. 하이일드만 벌어지면 저신용·한계기업 문제,
# 투자등급까지 벌어지면 시스템 문제 — 그래서 두 장을 나란히 둔다.
#
# ⚠️ 이력이 3년뿐이다. FRED CSV가 일별 계열을 최근 3년만 주기 때문이고(batch_macro
# 쪽 주석), 배치가 매일 누적하는 만큼 저절로 길어진다. 그래서 '전체'를 골라도
# 지금은 3년이고, 고점/저점 선도 그 범위 안의 값이다.
_CREDIT_PERIODS = {"1년": 1, "3년": 3, "전체": None}
_CREDIT_ITEMS = [("hy", "하이일드 스프레드"), ("ig", "투자등급 스프레드")]
# 구간 기준선. 하이일드는 4%를 넘으면 경계, 5% 이상이면 침체를 가격에 넣은 수준으로
# 읽는 것이 통상이다. 투자등급은 수준 자체가 1/4 규모라 기준도 따로 둔다.
_SPREAD_LEVELS = {"hy": (3.0, 4.0, 5.0), "ig": (0.9, 1.3, 1.8)}
_SPREAD_BAND = _YIELD_BAND   # 경계선 위 구간 배경 — 금리 4.6% 구간과 같은 회색


def _spread_badge(key: str, cur: float):
    """(문구, 색) 또는 None.

    💡 색은 앱 전체 규칙을 그대로 쓴다 — 낮으면 파랑, 높으면 빨강(밴드 카드의
    저점권/고점권과 같다). '올라가면 나쁜' 지표라고 초록/빨강으로 바꾸면 이 앱에서
    빨강·파랑이 가진 뜻(올랐다/내렸다)과 한 화면에서 충돌한다. 좋고 나쁨은 색이
    아니라 문구가 말한다.
    """
    loose, warn, risk = _SPREAD_LEVELS.get(key, (0, 0, 0))
    if cur >= risk:
        return "위험", "#ef5350"
    if cur >= warn:
        return "경계", "#ef5350"
    if cur < loose:
        return "느슨", "#1565C0"
    return None


@st.cache_data(ttl=3600, show_spinner=False)
def _get_us_credit() -> dict:
    """batch_macro가 받아 둔 OAS. {키: Series}."""
    try:
        with open(_CREDIT_CACHE, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return {}
    out = {}
    for key, rows in (raw or {}).items():
        if not rows:
            continue
        df = pd.DataFrame(rows, columns=["ts", "v"])
        df["date"] = pd.to_datetime(df["ts"], unit="ms")
        out[key] = df.set_index("date")["v"].sort_index()
    return out


def _make_spread_chart(s: pd.Series, warn: float) -> go.Figure:
    """스프레드 선 + 경계선 위 구간 배경. 모양은 물가 카드 차트와 같게 맞춘다."""
    hi, lo = float(s.max()), float(s.min())
    span = (hi - lo) or abs(hi) or 1.0
    base_y, top_y = lo - span * 0.14, hi + span * 0.10
    fig = go.Figure()
    # 경계선 이상 구간 배경 먼저(layer="below") — 금리 4.6% 구간과 같은 방식.
    for x0, x1 in _yield_runs(s, thr=warn):
        fig.add_vrect(x0=x0, x1=x1 + pd.Timedelta(days=1),
                      fillcolor=_SPREAD_BAND, line_width=0, layer="below")
    # 채움을 쓰려면 폭 0인 바닥 선이 **바로 앞에** 있어야 한다(fill="tonexty").
    fig.add_trace(go.Scatter(x=s.index, y=[base_y] * len(s), mode="lines",
                             line=dict(width=0), showlegend=False, hoverinfo="skip"))
    fig.add_trace(go.Scatter(
        x=s.index, y=s.values, mode="lines",
        line=dict(color=_VALUATION_LINE, width=1.5), showlegend=False,
        fill="tonexty", fillcolor=_LINE_FILL,
        hovertemplate="%{x|%Y-%m-%d}<br><b>%{y:.2f}%</b><extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=[s.index[-1]], y=[float(s.iloc[-1])], mode="markers",
        marker=dict(color=_VALUATION_LINE, size=7, line=dict(color="#fff", width=1)),
        showlegend=False, hoverinfo="skip",
    ))
    notes = []
    for y, color, dash, lbl, anchor_ in ((hi, "#ef5350", "dash", "최고", "top"),
                                         (lo, "#1565C0", "dash", "최저", "bottom"),
                                         (warn, "#78909C", "dot", "경계", "bottom")):
        if y > top_y or y < base_y:
            continue
        fig.add_hline(y=y, line=dict(color=color, width=1.1, dash=dash))
        notes.append(dict(
            xref="paper", yref="y", x=0.004, y=y, xanchor="left", yanchor=anchor_,
            text=f"<b>{lbl}</b> {y:.2f}%", showarrow=False,
            font=dict(size=9.5, color=color),
            bgcolor="rgba(255,255,255,0.72)", borderpad=1,
        ))
    fig.update_layout(
        height=110, margin=dict(l=0, r=2, t=8, b=18),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        dragmode=False, hovermode="x",
        xaxis=dict(showgrid=False, zeroline=False, fixedrange=True,
                   tickfont=dict(size=9, color="#888"), ticklen=0, **_year_ticks(s.index)),
        yaxis=dict(range=[base_y, top_y], showgrid=False, zeroline=False,
                   fixedrange=True, showticklabels=False),
        annotations=notes,
    )
    return fig


def _render_credit_card(key: str, name: str, s: pd.Series) -> None:
    with st.container(key=f"mcard_crd_{key}"):
        _credit_card_body(key, name, s)


def _credit_card_body(key: str, name: str, s: pd.Series) -> None:
    if s is None or len(s) < 5:
        st.markdown(f"<div style='font-size:11px;color:#888;'>{name}</div>"
                    f"<div style='font-size:18px;font-weight:700;color:#ccc;'>N/A</div>",
                    unsafe_allow_html=True)
        return
    cur = float(s.iloc[-1])
    d1 = cur - float(s.iloc[-2]) if len(s) > 1 else 0.0
    # 한 달 전 값 — 영업일이 비는 날이 있어 '직전 영업일로 되짚기'(asof)로 잡는다.
    m1 = s.asof(s.index[-1] - pd.Timedelta(days=30))
    mom = cur - float(m1) if pd.notna(m1) else None
    warn = _SPREAD_LEVELS.get(key, (0, 0, 0))[1]
    badge = ""
    b = _spread_badge(key, cur)
    if b:
        txt, bclr = b
        badge = (f"<span style='background:{bclr};color:#fff;font-size:9.5px;font-weight:700;"
                 f"padding:1px 5px;border-radius:3px;'>{txt}</span>")
    clr = lambda v: "#ef5350" if v > 0 else "#1565C0" if v < 0 else "#888"
    mom_html = ""
    if mom is not None:
        mom_html = (f"<span style='font-size:11.5px;color:{clr(mom)};font-weight:600;"
                    f"white-space:nowrap;'>1개월 {mom:+.2f}%p</span>")
    st.markdown(
        f"<div style='padding:4px 0 10px;display:flex;align-items:baseline;"
        f"flex-wrap:wrap;column-gap:7px;row-gap:0;line-height:1.25;'>"
        f"<span style='font-size:11px;color:#888;white-space:nowrap;'>{name}</span>{badge}"
        f"<span style='font-size:18px;font-weight:700;'>{cur:.2f}%</span>"
        f"<span style='font-size:11.5px;color:{clr(d1)};white-space:nowrap;'>"
        f"전일 {d1:+.2f}%p</span>{mom_html}"
        f"<span style='font-size:11.5px;color:#888;white-space:nowrap;'>"
        f"{s.index[-1]:%m/%d}</span></div>",
        unsafe_allow_html=True,
    )
    # 카드 밑 설명("투기등급 회사채 − 국채")은 뺐다 — 섹션 캡션이 이미 OAS가
    # 무엇인지 말하고 카드 제목이 등급을 말해, 같은 얘기를 세 번 하고 있었다.
    st.plotly_chart(_make_spread_chart(s, warn), use_container_width=True,
                    config={"displayModeBar": False, "scrollZoom": False, "staticPlot": False})


_CREDIT_HELP = """
- 절대 수준보다 **방향과 속도**를 본다. 같은 4%라도 벌어지는 중인지 좁혀지는 중인지가 다르다.
- **지수 하락 + 스프레드 조용** → 증시 안쪽 수급 조정. 신용 쪽은 아직 멀쩡하다는 뜻이라
  낙폭이 과하면 되돌림 쪽에 무게가 실린다.
- **지수 버팀 + 스프레드 확대** → 가장 위험한 조합. 채권이 먼저 경고하는 경우가 많다.
- **하이일드만 확대** → 저신용·한계기업 문제. **투자등급까지 확대** → 시스템 문제로 번진 것이라
  두 장을 나란히 둔다.
- 배지 색은 앱 규칙대로 낮으면 파랑·높으면 빨강이다 — **좋고 나쁨이 아니라 올랐다/내렸다**를
  말한다. 좋고 나쁨은 위 문장이 말한다.
"""


def _render_credit_section() -> None:
    data = _get_us_credit()
    if not data:
        section_title("신용 스프레드")
        st.caption("신용 스프레드를 아직 받지 못했습니다 (매일 배치가 FRED에서 수집).")
        return
    span_label = section_title_select("신용 스프레드", _CREDIT_PERIODS, key="crd", index=1)
    years = _CREDIT_PERIODS[span_label]
    # 값이 "3.48%"라 무엇인지는 한 줄 남긴다 — 회색 구간의 뜻도 같이.
    st.caption("회사채 − 국채 금리차 · 회색은 경계선 이상")
    cols = st.columns(2)
    for i, (key, name) in enumerate(_CREDIT_ITEMS):
        with cols[i % 2]:
            s = data.get(key)
            if s is not None and years:
                s = s[s.index >= s.index[-1] - pd.DateOffset(years=years)]
            _render_credit_card(key, name, s)
    with st.expander("읽는 법"):
        st.markdown(_CREDIT_HELP)


@st.cache_data(ttl=3600, show_spinner=False)
def _get_us_inflation() -> dict:
    """
    batch_macro가 FRED에서 받아 둔 물가 지수(레벨). {키: Series}.

    💡 파일에는 지수 레벨만 들어 있고 YoY는 여기서 계산한다 — 정의를 바꿔도
    다시 받을 필요가 없다. 월 단위라 '1개월' 같은 짧은 기간은 의미가 없어서
    시세 카드의 기간 라디오를 쓰지 않고 자체 기간(3년/10년/전체)을 둔다.
    """
    try:
        with open(_INFLATION_CACHE, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return {}
    out = {}
    for key, rows in (raw or {}).items():
        if not rows:
            continue
        df = pd.DataFrame(rows, columns=["ts", "v"])
        df["date"] = pd.to_datetime(df["ts"], unit="ms")
        out[key] = df.set_index("date")["v"].sort_index()
    return out


def _yoy(s: pd.Series) -> pd.Series:
    """
    전년 동월 대비 %.

    ⚠️ s.shift(12)를 그냥 쓰면 안 된다 — shift는 12개월이 아니라 12'행'을 민다.
    CPI·코어 CPI는 2025년 10월분이 미국 연방정부 셧다운으로 아예 발표되지 않아
    공식 계열에 그 달이 없고(실측: 478개월 중 2025-10 결측), 그대로 shift하면
    2026-09가 2025-09이 아니라 2025-08과 비교되어 CPI YoY가 3.72% 대신
    4.02%로 나온다. 그래서 빠진 달을 NaN으로 채운 월 인덱스에 맞춘 뒤 민다 —
    비교 대상이 없는 달은 NaN이 되어 화면에서 조용히 빠진다.
    """
    full = s.reindex(pd.date_range(s.index[0], s.index[-1], freq="MS"))
    return ((full / full.shift(12) - 1) * 100).dropna()


def _make_inflation_chart(s: pd.Series, target: float | None) -> go.Figure:
    hi, lo = float(s.max()), float(s.min())
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=s.index, y=s.values, mode="lines",
        line=dict(color=_VALUATION_LINE, width=1.5), showlegend=False,
        hovertemplate="%{x|%Y-%m}<br><b>%{y:+.2f}%</b><extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=[s.index[-1]], y=[s.values[-1]], mode="markers",
        marker=dict(color=_VALUATION_LINE, size=7, line=dict(color="#fff", width=1)),
        showlegend=False, hoverinfo="skip",
    ))
    # 라벨은 xref="paper"라 축 범위 계산에 끼어들지 않는다(밴드 차트와 같은 방식).
    # 최고는 선 아래, 최저는 선 위에 붙인다 — 바깥에 두면 차트 밖으로 나가 잘린다.
    notes = []
    lines = [(hi, "#ef5350", "dash", "최고", "top"),
             (lo, "#1565C0", "dash", "최저", "bottom")]
    if target is not None:
        lines.append((target, "#2E7D32", "dot", "목표", "bottom"))
    for y, color, dash, lbl, anchor_ in lines:
        fig.add_hline(y=y, line=dict(color=color, width=1.1, dash=dash))
        notes.append(dict(
            xref="paper", yref="y", x=0.004, y=y, xanchor="left", yanchor=anchor_,
            text=f"<b>{lbl}</b> {y:+.1f}%", showarrow=False,
            font=dict(size=9.5, color=color),
            bgcolor="rgba(255,255,255,0.72)", borderpad=1,
        ))
    # 0% 선 — 마이너스(디플레) 구간을 눈으로 구분할 수 있게.
    if lo < 0:
        fig.add_hline(y=0, line=dict(color="#ccc", width=1))
    # 크기·여백은 시세 카드(_make_sparkline)·밴드 카드와 같게 맞춘다.
    fig.update_layout(
        height=96, margin=dict(l=0, r=2, t=8, b=18),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        dragmode=False, hovermode="x",
        xaxis=dict(showgrid=False, zeroline=False, fixedrange=True,
                   tickfont=dict(size=9, color="#888"), ticklen=0, **_year_ticks(s.index)),
        yaxis=dict(showgrid=False, zeroline=False, fixedrange=True, showticklabels=False),
        annotations=notes,
    )
    return fig


def _render_inflation_card(name: str, yoy: pd.Series, target: float | None,
                           span_label: str) -> None:
    if len(yoy) < 13:
        st.markdown(f"<div style='font-size:11px;color:#888;'>{name}</div>"
                    f"<div style='font-size:18px;font-weight:700;color:#ccc;'>N/A</div>",
                    unsafe_allow_html=True)
        return
    cur = float(yoy.iloc[-1])
    mom = cur - float(yoy.iloc[-2])          # 전월 발표치 대비 가속/감속(%p)
    # 색 규칙(AGENTS.md): 상승·양수 = 빨강, 하락·음수 = 파랑. 물가에서는
    # "가속"이 빨강이 되는데, 한국 시장 색 규칙을 지표마다 뒤집지 않는다.
    clr = "#ef5350" if mom > 0 else "#1565C0" if mom < 0 else "#888"
    badge = ""
    if target is not None:
        gap = cur - target
        if gap >= 0.5:
            badge = ("<span style='background:#ef5350;color:#fff;font-size:9.5px;font-weight:700;"
                     f"padding:1px 5px;border-radius:3px;'>목표 +{gap:.1f}%p</span>")
        elif gap <= -0.5:
            badge = ("<span style='background:#1565C0;color:#fff;font-size:9.5px;font-weight:700;"
                     f"padding:1px 5px;border-radius:3px;'>목표 {gap:.1f}%p</span>")
        else:
            badge = ("<span style='background:#2E7D32;color:#fff;font-size:9.5px;font-weight:700;"
                     "padding:1px 5px;border-radius:3px;'>목표권</span>")
    st.markdown(
        f"<div style='padding:4px 0 10px;display:flex;align-items:baseline;"
        f"flex-wrap:wrap;column-gap:7px;row-gap:0;line-height:1.25;'>"
        f"<span style='font-size:11px;color:#888;white-space:nowrap;'>{name}</span>{badge}"
        f"<span style='font-size:18px;font-weight:700;'>{cur:+.2f}%</span>"
        f"<span style='font-size:11.5px;color:{clr};font-weight:600;white-space:nowrap;'>"
        f"전월 {mom:+.2f}%p</span>"
        f"<span style='font-size:11.5px;color:#888;white-space:nowrap;'>"
        f"{yoy.index[-1]:%Y-%m}</span></div>",
        unsafe_allow_html=True,
    )
    st.plotly_chart(_make_inflation_chart(yoy, target), use_container_width=True,
                    config={"displayModeBar": False, "scrollZoom": False, "staticPlot": False})


def _render_inflation_section() -> None:
    data = _get_us_inflation()
    if not data:
        section_title("경제 지표")
        st.caption("미국 물가 데이터를 아직 받지 못했습니다 (매일 배치가 FRED에서 수집).")
        return
    span_label = section_title_select("경제 지표", _INFLATION_PERIODS, key="infl", index=1)
    years = _INFLATION_PERIODS[span_label]
    # 값이 "+3.72%"라 무엇 대비인지는 한 줄 남긴다 — 이것까지 빼면 수치가 모호해진다.
    st.caption("전년 동월 대비 · 초록 점선은 연준 목표 2%")
    # ⚠️ PMI는 없다 — ISM은 2016년 FRED에서 API까지 삭제됐고(NAPM 404 실측),
    # S&P Global PMI는 PMI®가 등록상표로 시계열 재배포가 안 되며, DBnomics의
    # ISM/pmi는 2025-09부터 값이 깨진 채 갱신이 멈췄다. batch_macro 쪽 주석 참고.
    cols = st.columns(3)
    for i, (key, name, target) in enumerate(_INFLATION_ITEMS):
        with cols[i % 3], st.container(key=f"mcard_infl_{key}"):
            s = data.get(key)
            if s is None or s.empty:
                _render_inflation_card(name, pd.Series(dtype=float), target, span_label)
                continue
            yoy = _yoy(s)
            if years:
                yoy = yoy[yoy.index >= yoy.index[-1] - pd.DateOffset(years=years)]
            _render_inflation_card(name, yoy, target, span_label)


def _extremes_html(hist: pd.DataFrame, unit: str, fmt: str, mult: float = 1) -> str:
    """표시 구간의 최고·최저와 그 날짜를 한 줄로. 차트의 점선 두 개와 짝이다."""
    p = hist["price"] * mult
    if len(p) < 2 or float(p.max()) == float(p.min()):
        return ""
    hi_i, lo_i = p.idxmax(), p.idxmin()
    f = lambda v: format(float(v), fmt)
    return (
        f"<div style='font-size:10.5px;color:#9aa5b1;margin-top:1px;"
        f"white-space:nowrap;overflow:hidden;text-overflow:ellipsis;'>"
        f"<span style='color:#ef5350;'>최고 {f(p.max())}{unit}</span>"
        f" {hi_i:%y.%m.%d} · "
        f"<span style='color:#1565C0;'>최저 {f(p.min())}{unit}</span>"
        f" {lo_i:%y.%m.%d}</div>"
    )


def _fng_legend_html() -> str:
    """공포·탐욕 띠가 무슨 뜻인지 — 차트 밑에 색칩 + 구간."""
    chips = "".join(
        f"<span style='white-space:nowrap;margin-right:7px;'>"
        f"<span style='display:inline-block;width:7px;height:7px;border-radius:2px;"
        f"background:{color};margin-right:3px;vertical-align:middle;'></span>"
        f"{label} {lo}~{hi}</span>"
        for lo, hi, label, color, _fill in _FNG_BANDS)
    return (f"<div style='font-size:9.5px;color:#9aa5b1;line-height:1.5;"
            f"margin-top:1px;'>{chips}</div>")


def _make_sparkline(hist: pd.DataFrame, unit: str, fmt: str, period: str,
                    note: tuple | None = None, chg: float | None = None,
                    bands: list | None = None) -> go.Figure:
    """
    chg: 전일대비 등락률(%). 지금은 선 색에 쓰지 않는다(아래 색 주석 참고).
    호출부 호환과 과거 맥락을 위해 인자는 남겨 둔다.
    """
    prices = hist["price"]
    last  = float(prices.iloc[-1])
    if chg is None:      # 호출부가 전일대비를 못 넘긴 경우의 보수적 기본값
        chg = last - float(prices.iloc[-2]) if len(prices) > 1 else 0.0
    # 선 색은 지수 밴드와 같은 한 가지 색으로 고정한다(2026-11-06 요청).
    # 예전엔 전일대비로 빨강/파랑을 칠했는데, 바로 위 지수 밴드는 보라라서
    # 한 화면에 기준이 둘이 됐다. 증감 방향은 카드 헤더 숫자가 색으로 말한다.
    color, fill_c = _VALUATION_LINE, _LINE_FILL

    if bands:
        # 공포·탐욕처럼 눈금 자체에 의미가 있는 지표는 축을 0~100으로 고정한다.
        # 자동 범위로 두면 띠의 두께가 그날 값에 따라 달라져 구간을 못 읽는다.
        base_y, top_y = bands[0][0], bands[-1][1]
    else:
        min_val = float(prices.min())
        max_val = float(prices.max())
        rng = (max_val - min_val) or abs(max_val) * 0.02 or 1.0
        base_y = min_val - rng * 0.1
        top_y  = max_val + rng * 0.1

    shapes: list = []
    seen_ym: set = set()
    for dt in pd.DatetimeIndex(hist.index):
        ym = (dt.year, dt.month)
        if ym not in seen_ym:
            seen_ym.add(ym)
            if len(seen_ym) > 1:
                shapes.append(dict(
                    type="line", xref="x", yref="paper",
                    x0=dt, x1=dt, y0=0, y1=1,
                    line=dict(color="rgba(150,150,150,0.22)", width=1, dash="dot"),
                ))

    # 표시 구간의 최고·최저를 점선으로 긋는다. 값과 날짜는 차트에 적지 않고
    # 카드 글줄로 뺀다 — 90px 높이에 라벨까지 넣으면 선과 겹친다(지수 밴드
    # 차트에서 같은 이유로 내린 결정이다). 색·굵기도 그 차트와 맞춘다.
    hi_v, lo_v = float(prices.max()), float(prices.min())
    extra_notes = []
    if hi_v != lo_v:
        hi_d, lo_d = prices.idxmax(), prices.idxmin()
        for yv, dt_, c_, lbl in ((hi_v, hi_d, "#ef5350", "최고"),
                                 (lo_v, lo_d, "#1565C0", "최저")):
            shapes.append(dict(type="line", xref="paper", yref="y", layer="below",
                               x0=0, x1=1, y0=yv, y1=yv,
                               line=dict(color=c_, width=1, dash="dash")))
            # 라벨은 xref="paper"로 왼쪽 끝에 고정한다 — 데이터 좌표로 두면
            # 축 범위 계산에 끼어들어 그래프 모양이 바뀐다. 선 바로 위/아래에
            # 붙여 선과 겹치지 않게 한다(최고는 위, 최저는 아래).
            # 라벨은 선 '안쪽'에 붙인다 — 최고를 선 위에 두면 축 위쪽 여백(10%)을
            # 넘어 차트 밖으로 나가 잘렸다(사용자 제보). 안쪽이면 범위와 무관하게
            # 항상 보인다. 배경이 반투명이라 선·면과 겹쳐도 읽힌다.
            extra_notes.append(dict(
                xref="paper", yref="y", x=0.004, y=yv,
                xanchor="left", yanchor="top" if lbl == "최고" else "bottom",
                text=f"<b>{lbl}</b> {format(yv, fmt)}{unit} {dt_:%y.%m.%d}",
                showarrow=False, font=dict(size=9.5, color=c_),
                bgcolor="rgba(255,255,255,0.72)", borderpad=1,
            ))

    if bands:
        # 띠는 월 구분선보다 먼저(=아래) 깔아야 선·격자가 그 위로 보인다.
        shapes = [dict(type="rect", xref="paper", yref="y", layer="below",
                       x0=0, x1=1, y0=lo, y1=hi,
                       fillcolor=fill, line=dict(width=0))
                  for lo, hi, _lbl, _c, fill in bands] + shapes

    dtick = {"1mo": "W1", "3mo": "M1", "6mo": "M1", "1y": "M2", "3y": "M6", "5y": "M12"}.get(period, "M1")
    # 여러 해에 걸치면 "03월"만으로는 어느 해인지 몰라 연도를 넣는다
    tfmt  = {"1mo": "%d일", "3y": "%y.%m", "5y": "%Y"}.get(period, "%m월")

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=hist.index, y=[base_y] * len(hist),
        mode="lines", line=dict(width=0),
        showlegend=False, hoverinfo="skip",
    ))
    fig.add_trace(go.Scatter(
        x=hist.index, y=prices,
        mode="lines",
        line=dict(color=color, width=1.5),
        fill=None if bands else "tonexty", fillcolor=fill_c,
        showlegend=False,
        hovertemplate=f"%{{x|%m/%d}}<br>%{{y:{fmt}}} {unit}<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=[hist.index[-1]], y=[last],
        mode="markers", marker=dict(color=color, size=4),
        showlegend=False, hoverinfo="skip",
    ))

    # 💡 드래그(이동)/줌 비활성 — 축을 fixedrange로 잠그는 게 핵심이다.
    # dragmode=False만으로는 모드바나 휠로 여전히 확대/이동이 가능하다.
    # hovermode는 그대로 둬서 일자별 수치 툴팁은 계속 보인다
    # (staticPlot=True로 막으면 툴팁까지 죽으므로 절대 쓰지 않는다).
    fig.update_layout(
        height=96,
        margin=dict(l=0, r=2, t=8, b=18),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        dragmode=False,
        shapes=shapes,
        hovermode="x",
        xaxis=dict(showticklabels=True, tickformat=tfmt, dtick=dtick,
                   tickfont=dict(size=9, color="#888"), ticklen=0,
                   showgrid=False, zeroline=False, fixedrange=True),
        yaxis=dict(range=[base_y, top_y], showticklabels=False,
                   showgrid=False, zeroline=False, fixedrange=True),
        annotations=extra_notes,
    )

    # 💡 특이사항 배지 — 그래프 우측 상단에 paper 좌표로 띄운다.
    # 데이터 좌표를 쓰면 값 범위에 따라 위치가 흔들리고 선과 겹친다.
    # 💡 특이사항 배지는 차트 안에 넣지 않는다 — 최근이 고점이면 선이 바로
    # 우상단으로 올라와 배지와 겹친다. 카드 헤더(HTML)에서 그린다.
    # (note 인자는 호출부 호환을 위해 남겨두되 여기서는 쓰지 않는다.)

    return fig


# ── 렌더링 ────────────────────────────────────────────────────────────────────
def render_macro():
    # 새로고침은 원래 탭 맨 아래에 화면 폭 막대로 있었다 — 제목 옆 작은 버튼으로 옮겼다.
    # 제목 줄은 지웠다(2026-11-07 요청) — 상단 메뉴가 이미 어느 탭인지 알려준다.
    # 새로고침 버튼은 남겨야 해서 빈 제목으로 같은 줄만 그린다.
    if page_header("", "macro"):
        _get_price_history.clear()
        _get_lithium_price_history.clear()
        _get_dram_price_history.clear()
        _get_ddr4_price_history.clear()
        _dramexchange_spot.clear()
        _te_lithium_now.clear()
        _get_commodity_news.clear()
        _get_index_valuation.clear()
        _get_index_live.clear()
        _get_flow_live.clear()
        _get_foreign_holding.clear()
        _get_flow_stats.clear()
        _get_us_credit.clear()
        st.rerun()

    # 💡 예전엔 여기서 라디오로 "시장 지표 / 수출 동향"을 토글했다.
    # 수출입은 성격이 달라(정부 API · 월 단위 · 품목별) 별도 메뉴
    # (ui_trade.render_trade)로 분리했고, 이 탭은 시장 지표만 담당한다.
    # 2026-10-26: 3개월을 빼고 3년·5년 추가(사용자 요청). 리튬·DRAM·DDR4·공포탐욕처럼 자체 캐시로
    # 쌓는 지표는 쌓인 만큼만 보인다(연방부채는 3년치). 키를 바꾼 건 예전 세션에 남은 "3개월"이
    # 새 선택지에 없어서다.
    # 코스피·코스닥 밴드를 맨 위로 — 국내 지수가 먼저 눈에 들어와야 한다는
    # 요청(2026-11-06). 밴드는 자체 기간 선택(밴드 기간)을 쓰므로 아래 '기간'
    # 라디오와 무관하고, 그 라디오는 '시장 지표' 카드에만 걸리니 소제목 뒤에 둔다.
    period_map = {"1개월": "1mo", "6개월": "6mo", "1년": "1y", "3년": "3y", "5년": "5y"}
    # '시장 지표' 기간 상자는 밴드 아래에 그려지지만 고른 값은 세션에 남아 있어 미리 읽을 수 있다
    # — 아래 run_with_progress가 밴드보다 먼저 돌아야 해서다.
    period = period_map.get(st.session_state.get("secsel_mkt"), "1y")

    # 💡 예전엔 티커 6개(+캐시파일 3개)를 하나씩 순차적으로 가져왔다 —
    # 티커당 차트용 history() + 실시간가 fast_info/get_info()까지 최대
    # 2~3번 왕복이라, 콜드 캐시일 때 다 더하면 꽤 오래 걸렸다. 워치리스트와
    # 같은 패턴으로 전부 스레드풀에서 동시에 가져오도록 바꾼다.
    hists = {}
    last_prev_map = {}
    full_map = {}   # 마일스톤 배지용 전체 이력

    def _fetch_hist(item):
        nm, tk = item[0], item[1]
        if tk == "_LITHIUM_":
            return nm, _get_lithium_price_history(period)
        elif tk == "_DRAM_":
            return nm, _get_dram_price_history(period)
        elif tk == "_DDR4_":
            return nm, _get_ddr4_price_history(period)
        elif tk == "_USDEBT_":
            return nm, _get_us_debt_history(period)
        elif tk == "_FNG_":
            return nm, _get_fng_history(period)
        else:
            return nm, _get_price_history(tk, period)

    def _fetch_full(item):
        """마일스톤 판정용 전체 이력. 캐시 기반은 잘라내지 않은 원본."""
        nm, tk = item[0], item[1]
        if tk == "_LITHIUM_":
            return nm, _get_lithium_price_history("max")
        elif tk == "_DRAM_":
            return nm, _get_dram_price_history("max")
        elif tk == "_DDR4_":
            return nm, _get_ddr4_price_history("max")
        elif tk == "_USDEBT_":
            return nm, _get_us_debt_history("max")
        elif tk == "_FNG_":
            return nm, _get_fng_history("max")
        return nm, _get_full_history(tk)

    def _fetch_last_prev(item):
        nm, tk = item[0], item[1]
        if tk.startswith("_"):
            return nm, (None, None, None)
        # 채권 금리는 네이버 실시간 우선 (^TNX는 미국 정규장만 갱신됨).
        # 네이버가 실패하면 기존 야후 경로로 폴백한다.
        if tk in _NAVER_BOND_MAP:
            q = _get_naver_bond_quote(_NAVER_BOND_MAP[tk])
            if q[0] is not None:
                return nm, q
        return nm, _get_last_and_prev_close(tk)

    # 시세·실시간가·전체이력·뉴스를 한 번에 병렬로 — 오래 걸리면 0~100% 진행률을 띄운다.
    # 뉴스도 같은 풀에서 받는다(카드 그리면서 하나씩 받으면 카드 수만큼 직렬로 쌓인다).
    jobs = {}
    for item in MARKET_ITEMS:
        jobs[("hist", item[0])] = lambda it=item: _fetch_hist(it)[1]
        jobs[("lp", item[0])] = lambda it=item: _fetch_last_prev(it)[1]
        jobs[("full", item[0])] = lambda it=item: _fetch_full(it)[1]
    for tk, (q, must) in _NEWS_QUERY.items():
        jobs[("news", tk)] = lambda q=q, must=must: _get_commodity_news(q, must)
    # 💡 탭 맨 위에서 한 번에 받는다(2026-11-09). 예전엔 밴드·수급 섹션이 네이버 실시간
    # 지수·순매수, 금투협 통계를 그리면서 하나씩 받아(진행률 없이 약 3초) 그 뒤에야 이 막대가
    # 떴다. 여기서 같이 받아 두면 아래 섹션은 캐시만 읽는다 — 인자가 같아야 캐시 키가 같다.
    for idx in ("KOSPI", "KOSDAQ"):
        jobs[("live", idx)] = lambda i=idx: _get_index_live(i)
        jobs[("flowlive", idx)] = lambda i=idx: _get_flow_live(i)
    jobs[("flowstats", "")] = _get_flow_stats
    res = run_with_progress(jobs, label="매크로 데이터 불러오는 중", max_workers=len(MARKET_ITEMS) * 2)
    ok = lambda v, default=None: default if isinstance(v, Exception) else v
    # 후보 중 다른 카드가 아직 안 쓴 것을 고른다 — DDR5·DDR4는 'D램'을 같이
    # 키워드로 쓰므로 그냥 1위를 박으면 나란한 두 카드에 같은 기사가 걸린다.
    # MARKET_ITEMS 순서대로 집어 가니 위 카드가 우선이다(= 화면에 그리는 순서).
    news_raw = {k[1]: ok(v, []) for k, v in res.items() if k[0] == "news"}
    news_map, _used = {}, set()
    for item in MARKET_ITEMS:
        for title, link, age in (news_raw.get(item[1]) or []):
            if title in _used:
                continue
            _used.add(title)
            news_map[item[1]] = (title, link, age)
            break
    for item in MARKET_ITEMS:
        nm = item[0]
        hists[nm] = ok(res[("hist", nm)])
        last_prev_map[nm] = ok(res[("lp", nm)], (None, None, None))
        full_map[nm] = ok(res[("full", nm)])

    _render_valuation_band()
    sel_p = section_title_select("시장 지표", period_map, key="mkt", index=2)
    if period_map[sel_p] != period:      # 세션 값이 없던 첫 실행 등 — 고른 기간으로 다시 받는다
        st.rerun()


    for row_start in range(0, len(MARKET_ITEMS), 3):
        if row_start:
            # 예전엔 22px을 띄웠다 — 붙여 두면 위 카드의 뉴스 줄과 아래 카드의
            # 지표명이 한 덩어리로 읽혔기 때문이다. 이제 테두리가 그 경계를
            # 말하므로 줄 사이는 최소만 남긴다(2026-11-08 요청).
            st.markdown("<div style='height:5px;'></div>", unsafe_allow_html=True)
        cols = st.columns(3)
        for ci, (name, ticker, unit, fmt, mult) in enumerate(MARKET_ITEMS[row_start:row_start + 3]):
            hist = hists[name]
            with cols[ci], st.container(key=f"mcard_mkt_{row_start + ci}"):
                if hist is None or hist.empty:
                    st.markdown(
                        f"<div style='padding:4px 0 8px;'>"
                        f"<div style='font-size:11px;color:#888;'>{name}</div>"
                        f"<div style='font-size:18px;font-weight:700;color:#ccc;'>N/A</div>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
                    continue
                if mult != 1:
                    hist = hist.copy()
                    hist["price"] = hist["price"] * mult
                last  = float(hist["price"].iloc[-1])
                prev  = float(hist["price"].iloc[-2]) if len(hist) > 1 else last
                last_update = None
                is_cache_based = ticker.startswith("_")
                # 라이브 시세가 안 잡히면(=배치 스냅샷으로 그린 차트) 언제 기준
                # 값인지 반드시 알려준다. 그냥 두면 멈춘 값이 현재가로 읽힌다.
                is_stale_snapshot = False
                if not is_cache_based:
                    # 야후 실시간 필드가 있으면 그걸 우선 사용 (더 안정적인 전일종가 기준)
                    # 이미 위에서 병렬로 가져와둔 값을 그대로 조회만 한다 (재요청 없음)
                    fi_last, fi_prev, fi_time = last_prev_map.get(name, (None, None, None))
                    if fi_last is not None and fi_prev is not None:
                        last, prev = fi_last * mult, fi_prev * mult
                        last_update = fi_time
                        # 일부 티커(예: ^TNX)는 차트용 일별 시계열이 fast_info보다
                        # 며칠 뒤처져 올 수 있다. "현재가"와 차트가 다른 날을
                        # 보여주는 어긋남이 없도록, 더 최신이면 이어붙인다.
                        # 💡 get_info()가 실패하면 fi_time이 None으로 온다
                        # (fast_info는 성공한 경우) — 그대로 .date()를 부르면
                        # AttributeError로 매크로 탭 전체가 죽으므로 먼저 방어한다.
                        if last_update is not None and last_update.date() > hist.index[-1].date():
                            new_row = pd.DataFrame(
                                {"price": [last]}, index=[pd.Timestamp(last_update.date())]
                            )
                            hist = pd.concat([hist, new_row])
                    else:
                        is_stale_snapshot = True
                spot_at = None
                if is_cache_based:
                    # 캐시 파일 기반(리튬/DDR5/DDR4) — 방금 확인한 값이므로 age 경고 없이 시각만 표시
                    last_update = hist.index[-1].to_pydatetime()
                    # 💡 DRAM 스팟은 '값이 달라졌을 때만' 새 점을 만든다(배치의
                    # _append_if_new, 앱의 덧붙이기 조건 둘 다). 그래서 소스가 며칠
                    # 가만히 있으면 마지막 점의 날짜가 그대로 머물고, 카드가 그 날짜를
                    # 기준시각으로 적어 "갱신이 멈췄다"로 읽힌다 — 2026-11-10 제보:
                    # DDR5가 11/07에 멈춰 보였는데 DRAMeXchange 자체가 Session
                    # Change 0.00%였다. 긁은 시각을 같이 적어 둘을 구분한다.
                    # 스크래핑이 실패하면 None으로 둬서 '확인'이라 말하지 않는다.
                    if ticker in ("_DRAM_", "_DDR4_"):
                        try:
                            spot_at = _dramexchange_spot().get("at")
                        except Exception:
                            spot_at = None
                chg_p = (last / prev - 1) * 100 if prev else 0
                try:
                    val_str = f"{last:{fmt}} {unit}"
                except Exception:
                    val_str = f"{last:.2f} {unit}"
                clr   = "#ef5350" if chg_p > 0 else "#1565C0" if chg_p < 0 else "#888"
                arrow = "▲" if chg_p > 0 else "▼" if chg_p < 0 else "─"
                # 값·증감·기준시각을 한 줄에 놓는다(5.5.0) — 예전엔 세 줄이라
                # 카드 머리가 차트보다 길었다. 여기서는 문구와 색만 정하고
                # 조립은 아래 markdown에서 한 번에 한다.
                update_txt, update_clr, update_bold = "", "#aaa", False
                if is_stale_snapshot:
                    update_txt = f"{hist.index[-1]:%m/%d} 종가 기준 (지연)"
                elif is_cache_based and spot_at is not None:
                    # 값이 바뀐 날만 점이 생기므로, 마지막 점이 어제 이전이면
                    # 그 사이는 '무변동'이 맞다 — 방금 확인한 사실과 같이 적는다.
                    if last_update and last_update.date() < spot_at.date():
                        update_txt = (f"{spot_at:%m/%d %H:%M} 확인 · "
                                      f"{last_update:%m/%d} 이후 무변동")
                    else:
                        update_txt = f"{spot_at:%m/%d %H:%M} 확인"
                elif is_cache_based and last_update:
                    update_txt = f"{last_update:%m/%d %H:%M} 기준"
                elif last_update:
                    age_hours = (datetime.now(_KST) - last_update).total_seconds() / 3600
                    # 💡 예전엔 age_hours >= 24 만 보고 경고를 띄웠다. 그러면 금요일
                    # 종가를 보는 월요일마다, 그리고 미국 휴일 다음 날마다 무조건
                    # "⚠️ 2일 전"이 떴다 — 데이터는 정상인데 오탐이 나는 것.
                    # (실제로 2026-09-07 미국 노동절 다음 날 이 경고가 떴다.)
                    # 주말/휴일을 자연히 걸러내려면 달력 시간이 아니라 영업일
                    # 간격으로 판단해야 한다. 영업일 2일까지는 정상으로 본다
                    # (금→월 1일, 연휴 끼면 2일).
                    if _weekday_gap(last_update, datetime.now(_KST)) > 2:
                        age_str = f"{age_hours/24:.0f}일 전" if age_hours >= 48 else f"{age_hours:.0f}시간 전"
                        update_txt = f"⚠️ {last_update:%m/%d %H:%M} 기준 ({age_str})"
                        update_clr, update_bold = "#e65100", True
                    else:
                        update_txt = f"{last_update:%m/%d %H:%M} 기준"
                # 💡 특이사항 배지를 차트 안(paper 우상단)에 뒀더니, 최근이 고점이면
                # 선이 바로 그 자리로 올라와 겹쳤다 — 카드 헤더로 빼서 지표명 옆에 붙인다.
                # 공포·탐욕 지수는 "몇 개월 만의 최저"보다 지금이 어느 구간인지가
                # 먼저 읽혀야 해서, 마일스톤 대신 등급을 배지로 쓴다.
                note = (_fng_rating(last) if ticker == "_FNG_"
                        else _milestone_note(full_map.get(name), last))
                badge_html = ""
                if note:
                    _txt, _clr = note
                    badge_html = (
                        f"<span style='background:{_clr};color:#fff;font-size:9.5px;"
                        f"font-weight:700;padding:1px 5px;border-radius:3px;"
                        f"vertical-align:middle;'>{_txt}</span>"
                    )
                # 값 · 증감 · 기준시각을 한 줄에. baseline 정렬이라 글자 크기가
                # 달라도 밑선이 맞는다. 좁은 화면에서는 뒤(기준시각)부터 줄바꿈된다.
                meta = ""
                if update_txt:
                    meta = (f"<span style='font-size:10px;color:{update_clr};"
                            f"{'font-weight:600;' if update_bold else ''}"
                            f"margin-left:7px;white-space:nowrap;'>{update_txt}</span>")
                # 지표명까지 같은 줄에 둔다 — 두 줄일 때 카드 머리가 차트보다
                # 길어 보였다. 아래 여백(10px)은 글자와 차트가 붙어 보이던 것.
                st.markdown(
                    f"<div style='padding:4px 0 10px;display:flex;align-items:baseline;"
                    f"flex-wrap:wrap;column-gap:7px;row-gap:0;line-height:1.25;'>"
                    f"<span style='font-size:11px;color:#888;white-space:nowrap;'>"
                    f"{name}</span>{badge_html}"
                    f"<span style='font-size:18px;font-weight:700;'>{val_str}</span>"
                    f"<span style='font-size:12px;color:{clr};white-space:nowrap;'>"
                    f"{arrow} {chg_p:+.2f}% 전일</span>"
                    f"{meta}</div>",
                    unsafe_allow_html=True,
                )
                st.plotly_chart(
                    _make_sparkline(hist, unit, fmt, period, note, chg_p,
                                    bands=_FNG_BANDS if ticker == "_FNG_" else None),
                    use_container_width=True,
                    # staticPlot은 False 유지 — True로 하면 마우스오버 툴팁까지 사라진다.
                    config={"displayModeBar": False, "scrollZoom": False, "staticPlot": False},
                )
                # 최고·최저 값은 차트 안 점선 옆에 적는다(아래 _make_sparkline).
                # 여기엔 공포·탐욕 띠의 뜻만 남긴다.
                if ticker == "_FNG_":
                    st.markdown(_fng_legend_html(), unsafe_allow_html=True)

                news = news_map.get(ticker)
                if news:
                    title, link, age = news
                    # 구글 뉴스는 외부 입력이므로 HTML 이스케이프 후 삽입 (XSS 방지)
                    st.markdown(
                        f"<div style='font-size:11px;color:#555;margin-top:2px;overflow:hidden;"
                        f"text-overflow:ellipsis;white-space:nowrap;'>"
                        f"📰 <span style='color:#aaa;'>{html.escape(age)}</span> "
                        f"<a href='{html.escape(link)}' target='_blank' rel='noopener noreferrer' "
                        f"style='color:#555;text-decoration:none;'>{html.escape(title)}</a></div>",
                        unsafe_allow_html=True,
                    )

    # 비교·물가는 월/다년 단위라 위 시세 카드의 기간 라디오(1개월~5년)와 성격이
    # 다르다 — 각자 기간 선택을 가진 별도 섹션으로 아래에 둔다. 위를 밀어내면
    # 매일 보는 환율·DRAM 카드가 내려가서 맨 아래가 맞다.
    # 국내 수급(예탁금·신용융자·반대매매) → 미국 신용 스프레드 → 장기 비교 순.
    # 매일 보는 환율·DRAM 카드를 밀어내지 않으려고 시세 카드 아래에 둔다.
    _render_flow_section()
    # 신용 스프레드는 "지금 위험한가"를 보는 지표라 장기 맥락인 비교 차트보다 먼저 둔다.
    _render_credit_section()
    _render_compare_section()
    # 경제 지표(미국 물가)는 화면에서 뺐다(2026-11-07 요청). 수집(batch_macro)과
    # _render_inflation_section()은 그대로 둔다 — 다시 넣을 땐 이 줄만 되살리면 된다.



