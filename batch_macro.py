"""
batch_macro.py — DRAM(DDR5/DDR4) & 리튬 탄산염 스팟 가격을 매일 캐시 파일에 누적.

ui_macro.py의 _get_*_price_history()는 화면 표시용으로 오늘자 현재가를
스크래핑해도 파일에 저장하지 않으므로, 과거 데이터가 전혀 쌓이지 않는다.
이 스크립트는 GitHub Actions에서 매일 실행되어 오늘자 값을 각 캐시 JSON에
append하고 커밋되도록 한다 (커밋/푸시는 워크플로우 쪽에서 처리).
"""
import html as html_mod
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
_MARKET_CACHE  = os.path.join(_MACRO_DIR, "market_cache.json")
_FNG_CACHE     = os.path.join(_MACRO_DIR, "fng_cache.json")
_SP500_CACHE   = os.path.join(_MACRO_DIR, "sp500_val.json")
_INFLATION_CACHE = os.path.join(_MACRO_DIR, "us_inflation.json")
_COMPARE_DIR     = os.path.join(_MACRO_DIR, "compare")

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
    # 💡 값이 직전과 같으면 새 점을 만들지 않는다. 이 스팟 가격들은 소스가
    # 움직였을 때만 갱신되는데, 매일 같은 값을 찍으면 화면의 "전일대비"가
    # 계속 0.00%로 나와 마지막 실제 변동이 묻힌다. 커밋도 불필요하게 쌓인다.
    if raw and price == raw[-1][1]:
        return False
    raw.append([today_ms, price])
    _save(path, raw)
    return True


def update_lithium() -> bool:
    """
    TE 리튬 현재가를 "그 값의 실제 날짜"(오늘 이전 마지막 GFEX 거래일)로 저장한다.

    💡 예전엔 _append_if_new로 긁은 날(KST)에 찍어 모든 점이 하루씩 밀렸다 —
    TE 현재가는 그 시각에 아직 직전 거래일 값이다. 자세한 근거는 lithium_source 주석.
    """
    import lithium_source as ls
    price = ls.fetch_te_value()
    if price is None:
        return False
    d = ls.te_value_date(datetime.now(KST).date(), ls.gfex_trading_days())
    ts = _date_to_ms(d)
    raw = _load(_LITHIUM_CACHE)
    if raw and raw[-1][0] >= ts:
        return False   # 그 날짜 값이 이미 있다 (같은 날 재실행 등)
    if raw and price == raw[-1][1]:
        return False   # TE가 아직 새 값을 안 냈다 — 같은 값을 다음 날짜로 찍지 않는다
    raw.append([ts, price])
    _save(_LITHIUM_CACHE, raw)
    return True


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


# ── CNN 공포·탐욕 지수 ───────────────────────────────────────────────────────
# CNN이 매일 계산하는 0~100 심리 지표(7개 하위지표 합성). 공개 엔드포인트지만
# 평범한 요청은 418로 막힌다 — Referer/Origin까지 붙여야 200이 온다.
# 한 번에 최근 1년치 일별 이력을 주므로 us_debt처럼 "받아서 합치기"로 쌓는다
# (그냥 덮어쓰면 1년이 지난 과거가 매번 잘려 나간다).
_FNG_API = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
_FNG_HEADERS = {
    **_UA_HEADERS,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://edition.cnn.com/markets/fear-and-greed",
    "Origin": "https://edition.cnn.com",
}
_FNG_KEEP_DAYS = 365 * 3


def fetch_fng_series() -> list:
    """[[epoch_ms(UTC 자정), 점수], ...]. 실패하면 빈 리스트."""
    try:
        r = requests.get(_FNG_API, headers=_FNG_HEADERS, timeout=20)
        if r.status_code != 200:
            print(f"[fng] HTTP {r.status_code}")
            return []
        data = (r.json().get("fear_and_greed_historical") or {}).get("data") or []
        out = []
        for row in data:
            try:
                d = datetime.fromtimestamp(float(row["x"]) / 1000, timezone.utc).date()
                out.append([_date_to_ms(d), round(float(row["y"]), 2)])
            except Exception:
                continue
        print(f"[fng] {len(out)}건 (최신 {out[-1][1] if out else '-'})")
        return out
    except Exception as e:
        print(f"[fng] 실패: {type(e).__name__}: {e}")
        return []


def _merge_list(path: str, rows: list, keep_days: int) -> bool:
    """[[ms, 값]] 시계열을 기존 파일과 날짜 기준으로 합쳐 저장. 바뀌었으면 True."""
    if not rows:
        return False
    try:
        with open(path, "r", encoding="utf-8") as f:
            cur = json.load(f)
        if not isinstance(cur, list):
            cur = []
    except Exception:
        cur = []

    by_ts = {int(ts): v for ts, v in cur if isinstance(ts, (int, float))}
    by_ts.update({int(ts): v for ts, v in rows})
    cutoff = _date_to_ms(datetime.now(KST).date() - timedelta(days=keep_days))
    merged = [[ts, by_ts[ts]] for ts in sorted(by_ts) if ts >= cutoff]
    if merged == cur:
        return False
    _save(path, merged)
    return True


# ── 야후 시세(환율·금리·원자재) 스냅샷 ──────────────────────────────────────
# 앱(ui_macro)은 야후를 직접 부르지만 Streamlit Cloud에서는 그 호출이 통째로
# 실패해 카드가 전부 N/A로 떨어진 적이 있다(공유 IP 대역이 레이트리밋에 걸린
# 것으로 보인다). DART와 같은 처방: 러너가 대신 받아 파일로 남기고, 앱은
# 라이브가 실패했을 때 이 파일로 폴백한다. 장이 쉬는 날에도 마지막 값이
# 남아 있으므로 화면이 비지 않는다.
_MARKET_TICKERS = ["USDKRW=X", "JPY=X", "^TNX", "CL=F", "GC=F", "SI=F", "HG=F", "BTC-USD"]
_MARKET_KEEP_DAYS = 400   # 화면 차트가 최대 1년치 — 여유분만 남긴다


def fetch_market_series() -> dict:
    """티커 → [[epoch_ms(UTC 자정), 종가], ...]. 실패한 티커는 빼고 돌려준다."""
    try:
        import yfinance as yf
    except ImportError:
        print("[market] yfinance 미설치 — 건너뜀")
        return {}

    out = {}
    for tk in _MARKET_TICKERS:
        try:
            hist = yf.Ticker(tk).history(period="2y")
            if hist.empty:
                print(f"[market] {tk}: 빈 응답")
                continue
            rows = []
            for ts, close in hist["Close"].dropna().items():
                d = ts.date()
                rows.append([_date_to_ms(d), round(float(close), 6)])
            if rows:
                out[tk] = rows
                print(f"[market] {tk}: {len(rows)}건 (최신 {rows[-1][1]})")
        except Exception as e:
            print(f"[market] {tk} 실패: {type(e).__name__}: {e}")
    return out


# ── S&P 500 밸류에이션 (multpl) ──────────────────────────────────────────────
# 코스피·코스닥 밴드와 같은 화면을 미국 지수로도 보려는 것. KRX처럼 일별을 주는
# 공개 소스가 없어 multpl.com을 쓴다 — PER은 월간(1871~), PBR은 분기(1999~)다.
# 나스닥·다우는 무료 이력 소스가 없어 넣지 않는다(현재값만 있고 과거가 없다).
#
# ⚠️ 값 칸에 `&#x2002;`(엔스페이스) 같은 엔티티가 섞여 온다. 태그만 걷어내고
# 숫자를 찾으면 그 안의 "2002"를 값으로 집는다(실제로 PBR 106행 중 다수가
# 2002.0으로 들어왔다). 엔티티를 먼저 푼 다음 숫자를 찾아야 한다.
_SP500_PAGES = {
    "per": "s-p-500-pe-ratio/table/by-month",
    "pbr": "s-p-500-price-to-book/table/by-quarter",
}
_SP500_ROW = re.compile(
    r"<td[^>]*>\s*([A-Z][a-z]{2}\s+\d{1,2},\s*\d{4})\s*</td>\s*<td[^>]*>(.*?)</td>", re.S)
_SP500_NUM = re.compile(r"-?\d+(?:\.\d+)?")
_SP500_KEEP_DAYS = 365 * 40


def fetch_sp500_valuation() -> dict:
    """{"per": [[epoch_ms, 값], ...], "pbr": [...]}. 실패한 항목은 빼고 돌려준다."""
    out = {}
    for key, path in _SP500_PAGES.items():
        try:
            r = requests.get(f"https://www.multpl.com/{path}", headers=_UA_HEADERS, timeout=25)
            if r.status_code != 200:
                print(f"[sp500] {key}: HTTP {r.status_code}")
                continue
            i = r.text.find("<table")
            body = r.text[i:r.text.find("</table>", i)] if i >= 0 else ""
            rows = []
            for d, cell in _SP500_ROW.findall(body):
                txt = html_mod.unescape(re.sub(r"<[^>]+>", " ", cell)).replace(",", "")
                m = _SP500_NUM.search(txt)
                if not m:
                    continue
                try:
                    dt = datetime.strptime(re.sub(r"\s+", " ", d), "%b %d, %Y").date()
                except ValueError:
                    continue
                rows.append([_date_to_ms(dt), round(float(m.group()), 4)])
            if rows:
                rows.sort()
                out[key] = rows
                print(f"[sp500] {key}: {len(rows)}건 (최신 {rows[-1][1]})")
        except Exception as e:
            print(f"[sp500] {key} 실패: {type(e).__name__}: {e}")
    return out


# 미국 물가 — FRED의 CSV 엔드포인트. API 키가 필요 없고(fredgraph.csv는 공개),
# 1947년부터 전체 이력이 한 번에 나온다. yfinance처럼 Streamlit Cloud에서
# 막힐 위험을 피하려고 여기서 받아 파일로 커밋하고, 앱은 그 파일만 읽는다.
#
# ⚠️ PMI는 여기 없다 — 무료로 제대로 받을 경로가 없다. ISM은 2016년 6월 FRED에서
# 22개 계열이 API까지 삭제됐고(NAPM·NAPMPMI 모두 404 실측), S&P Global PMI는
# PMI®가 등록상표로 보도자료만 공개돼 시계열 재배포가 안 된다. DBnomics의
# ISM/pmi는 2025-09부터 값이 11.1·10.0처럼 깨져 있고(50 기준 지수에 불가능한 값)
# indexed_at이 2026-01-07에서 멈춘 방치된 피드다. 제조업 설문이 필요하면
# 필라델피아(GACDFSA066MSFRBPHI)·댈러스(BACTSAMFRBDAL) 연준 지수가 무료로
# 살아 있지만 50이 아니라 0 기준 확산지수라 PMI처럼 읽을 수 없다.
#
# 레벨(지수값)을 그대로 저장한다 — YoY 계산은 앱에서 한다. 정의를 바꿔도
# 다시 받을 필요가 없고, 레벨이 원본이라 검증도 쉽다.
_FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={}"
_INFLATION_SERIES = {
    "cpi":      "CPIAUCSL",   # 소비자물가 (계절조정, 1947~)
    "core_cpi": "CPILFESL",   # 식품·에너지 제외 (1957~)
    "ppi":      "PPIFIS",     # 생산자물가 최종수요 (2009~)
    "core_pce": "PCEPILFE",   # 연준이 목표로 보는 지표 (1959~)
}
_INFLATION_KEEP_DAYS = 365 * 40


def fetch_us_inflation() -> dict:
    """{"cpi": [[epoch_ms, 지수], ...], ...}. 실패한 항목은 빼고 돌려준다."""
    out = {}
    for key, sid in _INFLATION_SERIES.items():
        try:
            r = requests.get(_FRED_CSV.format(sid), headers=_UA_HEADERS, timeout=25)
            if r.status_code != 200:
                print(f"[inflation] {key}({sid}): HTTP {r.status_code}")
                continue
            rows = []
            for line in r.text.splitlines()[1:]:
                parts = line.strip().split(",")
                if len(parts) < 2 or parts[1] in (".", ""):
                    continue          # FRED는 결측을 "."으로 준다
                try:
                    d = datetime.strptime(parts[0].strip(), "%Y-%m-%d").date()
                except ValueError:
                    continue
                try:
                    rows.append([_date_to_ms(d), round(float(parts[1]), 4)])
                except ValueError:
                    continue
            if rows:
                rows.sort()
                out[key] = rows
                print(f"[inflation] {key}({sid}): {len(rows)}건 (최신 {rows[-1][1]})")
        except Exception as e:
            print(f"[inflation] {key} 실패: {type(e).__name__}: {e}")
    return out


# S&P 500 지수 · 미국채 10년 금리 일별 종가 — 매크로 탭의 비교 차트용.
#
# 왜 market_cache.json에 안 넣는가: 그 파일은 400일만 보관하는데(화면 차트가
# 최대 1년) 비교 차트는 10년까지 본다. 8개 티커 전부를 10년으로 늘리면 한 파일이
# 매일 전부 바뀌어 커밋마다 수백 KB가 쌓인다.
#
# 그래서 index_val과 같은 **연도별 파일**로 쪼갠다 — 올해 파일만 바뀌므로 커밋
# 증가분이 하루 수십 KB가 아니라 거의 0이다. 날짜도 index_val처럼 문자열로 둔다
# (epoch_ms UTC 자정 규칙을 또 신경 쓸 필요가 없다).
#
# 코스피는 여기서 받지 않는다 — index_val이 2006년부터 일별 종가를 이미 갖고 있다.
_COMPARE_TICKERS = {"sp500": "^GSPC", "ust10y": "^TNX"}


def update_compare_series(period: str = "2y") -> bool:
    """
    연도별 파일에 누적. 내용이 바뀐 연도 파일만 다시 쓴다.

    period는 기본 2y — 정기 실행은 최근 구간만 메우면 된다. 전체 이력을 처음
    심을 때만 "15y"로 한 번 돌린다(값은 확정된 과거라 다시 받을 일이 없다).
    """
    try:
        import yfinance as yf
    except ImportError:
        print("[compare] yfinance 미설치 — 건너뜀")
        return False

    fresh = {}
    for key, tk in _COMPARE_TICKERS.items():
        try:
            hist = yf.Ticker(tk).history(period=period)
            c = hist["Close"].dropna() if not hist.empty else None
            if c is None or c.empty:
                print(f"[compare] {tk}: 빈 응답")
                continue
            fresh[key] = {ts.date().isoformat(): round(float(v), 4) for ts, v in c.items()}
            print(f"[compare] {tk}: {len(fresh[key])}건 (최신 {c.iloc[-1]:.3f})")
        except Exception as e:
            print(f"[compare] {tk} 실패: {type(e).__name__}: {e}")
    if not fresh:
        return False

    years = {d[:4] for rows in fresh.values() for d in rows}
    os.makedirs(_COMPARE_DIR, exist_ok=True)
    changed = False
    for y in sorted(years):
        path = os.path.join(_COMPARE_DIR, f"{y}.json")
        try:
            with open(path, "r", encoding="utf-8") as f:
                cur = json.load(f)
            if not isinstance(cur, dict):
                cur = {}
        except Exception:
            cur = {}
        merged = {}
        for key in _COMPARE_TICKERS:
            by_date = {d: v for d, v in cur.get(key, [])}
            by_date.update({d: v for d, v in fresh.get(key, {}).items() if d[:4] == y})
            if by_date:
                merged[key] = [[d, by_date[d]] for d in sorted(by_date)]
        if not merged or merged == cur:
            continue
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, path)
        changed = True
        print(f"[compare] {y}.json 갱신 ({sum(len(v) for v in merged.values())}건)")
    return changed


def _merge_market(path: str, fresh: dict, keep_days: int = _MARKET_KEEP_DAYS) -> bool:
    """
    기존 파일과 병합해 저장. 일부 티커만 실패해도 나머지 이력은 지키고,
    받아온 날짜는 덮어쓴다(장중에 받은 값이 종가로 정정될 수 있다).
    """
    if not fresh:
        return False
    try:
        with open(path, "r", encoding="utf-8") as f:
            cur = json.load(f)
        if not isinstance(cur, dict):
            cur = {}
    except Exception:
        cur = {}

    cutoff = _date_to_ms(datetime.now(KST).date() - timedelta(days=keep_days))
    merged = dict(cur)
    for tk, rows in fresh.items():
        by_ts = {int(ts): v for ts, v in cur.get(tk, []) if isinstance(ts, (int, float))}
        by_ts.update({int(ts): v for ts, v in rows})
        merged[tk] = [[ts, by_ts[ts]] for ts in sorted(by_ts) if ts >= cutoff]

    if merged == cur:
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)
    return True


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
    import sys

    # 💡 `--only us_debt` — 미 연방부채만 갱신하는 모드.
    #
    # 왜 필요한가: 재무부는 record_date D의 부채를 D+1 영업일 오후 4시(ET)쯤
    # 공개한다. 그런데 이 배치의 정규 실행은 09:00 UTC(= 05:00 ET)라 그날
    # 공개분을 언제나 놓치고, 결과적으로 화면이 실제보다 하루이틀 더 뒤처졌다
    # (2026-09-09에 돌았는데도 09-04까지만 쌓인 이유가 이것이다).
    # 그래서 미국이 공개한 뒤인 저녁(23:00 UTC)에 한 번 더 돌린다.
    #
    # 그 저녁 실행에서 스팟 가격까지 같이 건드리면 안 된다. _today_ms()가
    # KST 날짜를 쓰는데 23:00 UTC는 이미 한국 시각으로 다음 날이라,
    # 오늘 긁은 DRAM/리튬 값이 내일자로 기록되어 하루씩 밀린다.
    only = None
    if "--only" in sys.argv:
        i = sys.argv.index("--only")
        only = sys.argv[i + 1] if i + 1 < len(sys.argv) else None

    updated = {}
    if only in (None, "us_debt"):
        updated["us_debt"] = _replace_if_changed(_USDEBT_CACHE, fetch_us_debt_series())
    if only is None:
        updated["lithium"] = update_lithium()
        updated["dram"] = _append_if_new(_DRAM_CACHE, fetch_dram_price())
        updated["ddr4"] = _append_if_new(_DDR4_CACHE, fetch_ddr4_price())
        updated["market"] = _merge_market(_MARKET_CACHE, fetch_market_series())
        updated["fng"] = _merge_list(_FNG_CACHE, fetch_fng_series(), _FNG_KEEP_DAYS)
        updated["sp500"] = _merge_market(_SP500_CACHE, fetch_sp500_valuation(),
                                         _SP500_KEEP_DAYS)
        updated["inflation"] = _merge_market(_INFLATION_CACHE, fetch_us_inflation(),
                                             _INFLATION_KEEP_DAYS)
        updated["compare"] = update_compare_series()

    print(f"{datetime.now(KST).date()} 갱신 결과({only or 'all'}): {updated}")
