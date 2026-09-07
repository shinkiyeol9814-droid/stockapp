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

_UA_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

KST = timezone(timedelta(hours=9))


def _today_ms() -> int:
    """
    오늘 자정(KST)의 epoch ms.
    💡 예전엔 datetime.today()를 썼는데, GitHub Actions 러너는 UTC라서
    이 배치가 찍는 날짜가 KST 기준 날짜와 어긋날 수 있었다(cron이 09:00 UTC
    = 18:00 KST라 지금은 우연히 같은 날이지만, 스케줄을 조금만 옮기면
    하루씩 밀린 데이터가 쌓인다). 한국 장 기준 데이터이므로 KST로 고정한다.
    """
    now_kst = datetime.now(KST)
    midnight_kst = now_kst.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(midnight_kst.timestamp() * 1000)


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


if __name__ == "__main__":
    updated = {
        "lithium": _append_if_new(_LITHIUM_CACHE, fetch_lithium_price()),
        "dram":    _append_if_new(_DRAM_CACHE, fetch_dram_price()),
        "ddr4":    _append_if_new(_DDR4_CACHE, fetch_ddr4_price()),
    }
    print(f"{datetime.today().date()} 갱신 결과: {updated}")
