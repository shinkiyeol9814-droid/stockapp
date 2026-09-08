"""
batch_macro.py — DRAM(DDR5/DDR4) & 리튬 탄산염 스팟 가격을 매일 캐시 파일에 누적.

ui_macro.py의 _get_*_price_history()는 화면 표시용으로 오늘자 현재가를
스크래핑해도 파일에 저장하지 않으므로, 과거 데이터가 전혀 쌓이지 않는다.
이 스크립트는 GitHub Actions에서 매일 실행되어 오늘자 값을 각 캐시 JSON에
append하고 커밋되도록 한다 (커밋/푸시는 워크플로우 쪽에서 처리).
"""
import json
import os
import re
from datetime import datetime, timedelta, timezone

import requests

# 캐시 JSON은 data/macro/ 아래로 모았다 (예전엔 리포지토리 루트에 흩어져 있었음).
_DIR = os.path.dirname(os.path.abspath(__file__))
_MACRO_DIR     = os.path.join(_DIR, "data", "macro")
_LITHIUM_CACHE = os.path.join(_MACRO_DIR, "lithium_cache.json")
_DRAM_CACHE    = os.path.join(_MACRO_DIR, "dram_cache.json")
_DDR4_CACHE    = os.path.join(_MACRO_DIR, "ddr4_cache.json")
_USDEBT_CACHE  = os.path.join(_MACRO_DIR, "us_debt_cache.json")

# 미국 연방부채는 스크래핑이 아니라 재무부 공식 API로 받는다 — 키가 필요 없고
# 1993년부터 일별 전체 이력이 나온다. 그래서 리튬/DRAM처럼 "오늘 값만 긁어
# 하루씩 append"하는 방식이 아니라, 매번 시계열 전체를 받아 덮어쓴다.
_USDEBT_API = (
    "https://api.fiscaldata.treasury.gov/services/api/fiscal_service"
    "/v2/accounting/od/debt_to_penny"
)
# 화면 차트가 최대 1년치만 쓰므로 3년만 보관한다. 8,386건 전체를 커밋하면
# 매일 파일 전체가 바뀌어 리포지토리가 불필요하게 커진다.
_USDEBT_KEEP_DAYS = 365 * 3

_UA_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

KST = timezone(timedelta(hours=9))


def _date_to_ms(d) -> int:
    """
    날짜(date)를 'UTC 자정' epoch ms로 변환한다.

    💡 왜 UTC 자정인가 — ui_macro는 이 값을 pd.to_datetime(ts, unit="ms")로
    읽는데, 그 결과는 tz 정보가 없는 UTC 기준 시각이다. 그래서 KST 자정으로
    저장하면 같은 값이 화면에서 '전날 15:00'으로 표시된다(실제로 그랬다).
    날짜를 그대로 왕복시키려면 UTC 자정으로 저장해야 한다.
    """
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000)


def _today_ms() -> int:
    """
    오늘 날짜(KST 기준)를 UTC 자정 epoch ms로.

    💡 날짜 자체는 KST로 정한다 — GitHub Actions 러너는 UTC라서 그냥
    datetime.today()를 쓰면 한국 기준 날짜와 어긋날 수 있다(cron이 09:00 UTC
    = 18:00 KST라 지금은 우연히 같지만, 스케줄을 조금만 옮기면 하루씩 밀린다).
    저장 형식은 위 _date_to_ms 설명대로 UTC 자정.
    """
    return _date_to_ms(datetime.now(KST).date())


def _load(path: str) -> list:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save(path: str, raw: list) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(raw, f)


def _append_if_new(path: str, price: float | None) -> bool:
    if price is None:
        return False
    raw = _load(path)
    today_ms = _today_ms()
    if raw and raw[-1][0] >= today_ms:
        return False  # 오늘자 이미 있음 (재실행 시 중복 방지)
    raw.append([today_ms, price])
    _save(path, raw)
    return True


def fetch_lithium_price() -> float | None:
    try:
        r = requests.get(
            "https://ko.tradingeconomics.com/commodity/lithium",
            headers={**_UA_HEADERS, "Accept-Language": "ko-KR,ko;q=0.9"},
            timeout=15,
        )
        m = re.search(r'TEChartsMeta\s*=\s*\[{"value"\s*:\s*([\d.]+)', r.text)
        return float(m.group(1)) if m else None
    except Exception:
        return None


def _fetch_dramexchange(pattern: str) -> float | None:
    try:
        r = requests.get(
            "https://www.dramexchange.com/",
            headers={**_UA_HEADERS, "Accept-Language": "en-US,en;q=0.9",
                     "Referer": "https://www.dramexchange.com/"},
            timeout=15,
        )
        m = re.search(pattern, r.text, re.S)
        return float(m.group(1)) if m else None
    except Exception:
        return None


def fetch_dram_price() -> float | None:
    # DDR5 16Gb 행에서 5번째 td값 = Spot 평균가
    return _fetch_dramexchange(
        r'DDR5 16Gb[^<]*</a>'
        r'(?:.*?<td[^>]*>[\d.]+</td>){4}'
        r'.*?<td[^>]*>([\d.]+)</td>'
    )


def fetch_ddr4_price() -> float | None:
    # DDR4 16Gb (2Gx8) 3200 행에서 5번째 td값 = Spot 평균가
    return _fetch_dramexchange(
        r'DDR4 16Gb[^<]*3200.*?</a>'
        r'(?:.*?<td[^>]*>[\d.]+</td>){4}'
        r'.*?<td[^>]*>([\d.]+)</td>'
    )


def fetch_us_debt_series() -> list | None:
    """
    미국 연방부채 총액(Total Public Debt Outstanding) 시계열.
    반환 형식은 다른 캐시와 동일한 [[epoch_ms, 값], ...] (값 단위: 조 달러).
    """
    try:
        r = requests.get(
            _USDEBT_API,
            params={
                "fields": "record_date,tot_pub_debt_out_amt",
                "sort": "-record_date",
                # 3년치 영업일(~780) 여유 있게
                "page[size]": 1200,
                "format": "json",
            },
            headers=_UA_HEADERS,
            timeout=30,
        )
        if r.status_code != 200:
            print(f"  us_debt: HTTP {r.status_code}")
            return None
        rows = r.json().get("data", [])
        cutoff_ms = _today_ms() - _USDEBT_KEEP_DAYS * 86400 * 1000
        out = []
        for d in rows:
            try:
                ts = _date_to_ms(datetime.strptime(d["record_date"], "%Y-%m-%d").date())
                if ts < cutoff_ms:
                    continue
                # 달러 → 조 달러
                out.append([ts, round(float(d["tot_pub_debt_out_amt"]) / 1e12, 4)])
            except (KeyError, TypeError, ValueError):
                continue
        out.sort(key=lambda x: x[0])
        return out or None
    except Exception as e:
        print(f"  us_debt 실패: {type(e).__name__}: {e}")
        return None


def _replace_if_changed(path: str, series: list | None) -> bool:
    """
    시계열 전체를 덮어쓴다. 내용이 같으면 파일을 건드리지 않는다 —
    배치가 하루에 여러 번 돌 때 같은 내용으로 커밋이 쌓이는 것을 막는다.
    """
    if not series:
        return False
    if _load(path) == series:
        return False
    _save(path, series)
    return True


if __name__ == "__main__":
    updated = {
        "lithium": _append_if_new(_LITHIUM_CACHE, fetch_lithium_price()),
        "dram":    _append_if_new(_DRAM_CACHE, fetch_dram_price()),
        "ddr4":    _append_if_new(_DDR4_CACHE, fetch_ddr4_price()),
        "us_debt": _replace_if_changed(_USDEBT_CACHE, fetch_us_debt_series()),
    }
    print(f"{datetime.today().date()} 갱신 결과: {updated}")
