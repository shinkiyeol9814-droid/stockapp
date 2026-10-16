"""
lithium_source.py — 리튬 탄산염 가격(TradingEconomics)과 "그 값이 몇 일자 값인지".

━━ 왜 따로 뺐나 ━━
TE 페이지에서 긁는 건 날짜 없는 "현재가" 하나뿐이다. 예전엔 그걸 긁은 날(KST)로
찍었는데, TE의 현재가는 그 시각에 아직 **직전 중국 거래일** 값이라 화면의 모든
점이 하루씩 밀렸다. 사용자가 TE 앱과 대조해 발견했다(2026-10-16):

    값        TE가 밝힌 날짜   예전 앱 날짜
    115,400   10/12            10/13
    111,650   10/13            10/14   ← TE 본문 "10월 14일 … 전일 대비 3.05%"로도 확인
    104,400   10/14            10/15

그래서 배치(batch_macro)와 앱(ui_macro)이 같은 규칙으로 날짜를 붙이도록 여기 모은다:
    TE 현재가의 날짜 = 오늘(KST) 이전의 마지막 광저우선물거래소(GFEX) 거래일

거래일 목록은 GFEX 탄산리튬 연속물(LC0) 일봉의 날짜를 쓴다(시나 금융 공개 엔드포인트,
akshare가 쓰는 것과 같다). 국경절·춘절 같은 중국 휴장을 그대로 반영한다. 못 받으면
직전 평일로 대신한다.

⚠️ 값 자체는 TE 그대로다. GFEX 선물 종가와는 3~8% 차이 나고 방향도 다른 날이 있어
(10/13: TE −3.3%, 선물 +2.2%) 같은 시계열이 아니다 — TE는 현물 성격으로 보인다.
TE 차트 API는 응답을 일부러 난독화해 두어 그걸 풀어 쓰지는 않는다.
"""
import json
import re
from datetime import date, timedelta

import requests

TE_URL = "https://ko.tradingeconomics.com/commodity/lithium"
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
       "Accept-Language": "ko-KR,ko;q=0.9"}
_SINA_LC0 = ("https://stock2.finance.sina.com.cn/futures/api/jsonp.php/var%20_LC0="
             "/InnerFuturesNewService.getDailyKLine?symbol=LC0")


def fetch_te_value(timeout: int = 15) -> float | None:
    """TE 리튬 탄산염 현재가(CNY/t). 날짜는 te_value_date()로 따로 정한다."""
    try:
        r = requests.get(TE_URL, headers=_UA, timeout=timeout)
        m = re.search(r'TEChartsMeta\s*=\s*\[{"value"\s*:\s*([\d.]+)', r.text)
        return float(m.group(1)) if m else None
    except Exception:
        return None


def gfex_trading_days(timeout: int = 15) -> list | None:
    """GFEX 거래일 목록(오름차순 date). 실패하면 None."""
    try:
        r = requests.get(_SINA_LC0, timeout=timeout,
                         headers={"User-Agent": "Mozilla/5.0",
                                  "Referer": "https://finance.sina.com.cn/"})
        m = re.search(r"\((\[.*\])\)", r.text, re.S)
        rows = json.loads(m.group(1)) if m else []
        days = sorted(date.fromisoformat(x["d"]) for x in rows if x.get("d"))
        return days or None
    except Exception:
        return None


def te_value_date(today: date, trading_days: list | None) -> date:
    """오늘(KST) 긁은 TE 현재가가 실제로 속한 날짜 — 오늘 이전의 마지막 GFEX 거래일."""
    if trading_days:
        before = [d for d in trading_days if d < today]
        # 목록이 오늘 근처까지 와 있을 때만 믿는다(소스가 멈춰 있으면 엉뚱하게 과거로 간다).
        if before and (today - before[-1]).days <= 15:
            return before[-1]
    d = today - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d
