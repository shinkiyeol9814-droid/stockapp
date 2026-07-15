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
from datetime import datetime

import requests

_DIR = os.path.dirname(os.path.abspath(__file__))
_LITHIUM_CACHE = os.path.join(_DIR, "lithium_cache.json")
_DRAM_CACHE    = os.path.join(_DIR, "dram_cache.json")
_DDR4_CACHE    = os.path.join(_DIR, "ddr4_cache.json")

_UA_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def _today_ms() -> int:
    return int(datetime.combine(datetime.today().date(), datetime.min.time()).timestamp() * 1000)


def _load(path: str) -> list:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save(path: str, raw: list) -> None:
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
