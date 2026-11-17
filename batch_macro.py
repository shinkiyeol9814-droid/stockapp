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
from datetime import date, datetime, timedelta, timezone

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
_CREDIT_CACHE    = os.path.join(_MACRO_DIR, "us_credit.json")
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


def _warn(msg: str) -> None:
    """
    GitHub Actions 경고 주석. print와 달리 **공개 API로 읽히고** 실행 요약 위에
    노란 배너로 뜬다 — 러너 로그는 리포 admin 토큰이 있어야 받을 수 있다
    (DART 배치의 ::notice:: 요약과 같은 이유).
    """
    print(f"::warning title=매크로 배치::{msg}")


# 💡 "이번 실행에서 받아오기를 시도했는데 통째로 실패한 것"을 적어 두는 쪽지.
#
# ⚠️ 왜 필요한가: --audit은 **파일의 마지막 날짜**만 보므로, 허용 일수 안쪽에서
#    소스가 죽으면 조용히 넘어간다. 2026-11-17 FRED 6계열이 전멸했는데 신용
#    스프레드 파일이 5일 전(허용 6일)이라 점검은 "모든 소스가 최신"이라고 했다.
#    경고 주석은 떴지만 그건 메일이 안 간다 — 빨간불만 간다.
#
# 같은 잡 안에서만 쓰는 임시 쪽지라 커밋되면 안 된다(.gitignore에 있다).
_FAIL_MARK = os.path.join(_MACRO_DIR, ".fetch_fail.json")
_FRED_TAGS = {"credit": "신용 스프레드(FRED)", "inflation": "미국 물가(FRED)"}


def _mark_fail(name: str, msg: str | None) -> None:
    """msg가 있으면 적고, None이면 지운다(이번엔 성공했다는 뜻)."""
    try:
        cur = _read_any(_FAIL_MARK)
        cur = cur if isinstance(cur, dict) else {}
        if msg is None:
            cur.pop(name, None)
        else:
            cur[name] = msg
        os.makedirs(_MACRO_DIR, exist_ok=True)
        with open(_FAIL_MARK, "w", encoding="utf-8") as f:
            json.dump(cur, f, ensure_ascii=False)
    except OSError:
        pass          # 쪽지를 못 써도 배치 자체는 계속 돈다


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
#
# ⚠️ **fred.stlouisfed.org(웹)는 수시로 멈춘다 — api.stlouisfed.org(API)는 멀쩡하다.**
# 2026-11-17 실측. 그날 18:00 러너 실행에서 6개 계열이 **전부** 25초 read timeout이었고
# (경고 주석으로 남았다), 같은 시각 로컬에서 재보니 8초 타임아웃으로 **12번 연속 실패**였다.
# curl도 똑같이 멈추니 파이썬 클라이언트 문제가 아니고, 러너 IP 문제도 아니다.
# 그런데 **같은 순간** api.stlouisfed.org는 0.7초에 응답했다(키가 없어 400이지만 그게
# '살아 있다'는 증거다). fred.stlouisfed.org는 CSV뿐 아니라 **루트 페이지와 alfred까지**
# 같이 멈췄다 — 즉 웹 프런트엔드만 죽는다. 멀쩡한 구간과 먹통 구간이 몇 분 단위로
# 번갈아 오므로(실측: 0.5초 성공 두 번 → 12번 연속 실패) 재시도만으로는 못 메운다.
#
# 그래서 받는 순서는 **API(키가 있으면) → CSV**다. 키는 무료·즉시 발급이고
# (https://fredaccount.stlouisfed.org/apikeys) Actions Secret `FRED_API_KEY`로 넣는다.
# 키가 없으면 예전처럼 CSV만 쓰되 짧은 타임아웃으로 여러 번 두드린다 — 성공하는
# 응답은 **0.5~0.7초**에 오므로 10초를 넘긴 시도는 기다려도 안 온다.
_FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={}"
_FRED_API = ("https://api.stlouisfed.org/fred/series/observations"
             "?series_id={sid}&api_key={key}&file_type=json")
_FRED_TRIES = 4           # 시도 횟수(엔드포인트별)
_FRED_TIMEOUT = 10        # 초. 성공은 1초 안에 오므로 길게 잡아도 소득이 없다
_INFLATION_SERIES = {
    "cpi":      "CPIAUCSL",   # 소비자물가 (계절조정, 1947~)
    "core_cpi": "CPILFESL",   # 식품·에너지 제외 (1957~)
    "ppi":      "PPIFIS",     # 생산자물가 최종수요 (2009~)
    "core_pce": "PCEPILFE",   # 연준이 목표로 보는 지표 (1959~)
}
_INFLATION_KEEP_DAYS = 365 * 40


# ICE BofA 옵션조정스프레드(OAS) — 하이일드와 투자등급. 회사채 금리에서 같은
# 만기 국채 금리를 뺀 값이고, "부도 위험에 시장이 요구하는 추가 이자"다.
#
# 왜 받는가: 돈을 빌려주는 쪽이 주식 사는 쪽보다 먼저 겁을 낸다. 지수가 버티는데
# 스프레드가 벌어지는 구간이 가장 위험하고, 지수가 빠지는데 스프레드가 조용하면
# 보통 수급 조정이다 — 하락의 성격을 구분하는 데 쓴다.
#
# ⚠️ FRED CSV는 **일별 계열을 최근 3년치만** 내려준다(2026-11-08 실측: 786건,
# cosd/coed를 줘도 늘지 않는다). 월별인 물가가 40년치 전부 나오는 것과 다르다.
# 그래서 장기 이력은 "매일 받아 누적"해야만 길어진다 — keep_days를 20년으로 두고
# _merge_market이 기존 이력을 지키게 한다. 화면의 고점/저점·구간 판단도 그래서
# 당장은 최근 3년 기준이고, 배치가 도는 만큼 저절로 길어진다.
_CREDIT_SERIES = {
    "hy": "BAMLH0A0HYM2",   # 미국 하이일드(투기등급) OAS
    "ig": "BAMLC0A0CM",     # 미국 투자등급 OAS
}
_CREDIT_KEEP_DAYS = 365 * 20


def _fred_rows(pairs) -> list:
    """[(날짜문자열, 값문자열), ...] -> [[epoch_ms, 값], ...]. 결측은 버린다."""
    rows = []
    for ds, vs in pairs:
        if vs in (".", "", None):
            continue              # FRED는 결측을 "."으로 준다
        try:
            d = datetime.strptime(str(ds).strip()[:10], "%Y-%m-%d").date()
            rows.append([_date_to_ms(d), round(float(vs), 4)])
        except ValueError:
            continue
    rows.sort()
    return rows


def _fred_one(key: str, sid: str) -> tuple[list, str]:
    """한 계열을 받아 ([[ms, 값], ...], 실패사유)로 돌려준다. 성공하면 사유는 빈 문자열.

    API(키가 있을 때) -> CSV 순서로, 엔드포인트마다 _FRED_TRIES번 두드린다
    (왜 두 경로인지는 위 _FRED_CSV 머리말)."""
    import time

    key_env = (os.environ.get("FRED_API_KEY") or "").strip()
    plans = []
    if key_env:
        plans.append(("API", _FRED_API.format(sid=sid, key=key_env)))
    plans.append(("CSV", _FRED_CSV.format(sid)))

    last = "시도하지 않았습니다"
    for kind, url in plans:
        for i in range(_FRED_TRIES):
            try:
                r = requests.get(url, headers=_UA_HEADERS, timeout=_FRED_TIMEOUT)
                if r.status_code != 200:
                    last = f"{kind} HTTP {r.status_code}"
                    if r.status_code in (400, 403, 404):
                        break     # 키가 틀렸거나 계열이 없는 것 — 재시도해도 같다
                    continue
                if kind == "API":
                    obs = (r.json().get("observations") or [])
                    rows = _fred_rows([(o.get("date"), o.get("value")) for o in obs])
                else:
                    rows = _fred_rows(
                        [tuple(ln.strip().split(",")[:2])
                         for ln in r.text.splitlines()[1:] if "," in ln])
                if rows:
                    return rows, ""
                last = f"{kind} 응답에 값이 없습니다"
            except Exception as e:
                last = f"{kind} {type(e).__name__}: {e}"
            time.sleep(1.5 * (i + 1))
    return [], last


def _fetch_fred(series: dict, tag: str) -> dict:
    """{키: [[epoch_ms, 값], ...]}. 실패한 항목은 빼고 돌려준다."""
    out = {}
    for key, sid in series.items():
        rows, why = _fred_one(key, sid)
        if rows:
            out[key] = rows
            print(f"[{tag}] {key}({sid}): {len(rows)}건 (최신 {rows[-1][1]})")
        else:
            _warn(f"FRED {key}({sid}) 실패: {why} — 이 계열은 갱신되지 않습니다")
    # 💡 한 계열도 못 받았으면 쪽지를 남긴다 — --audit이 이걸 보고 빨간불을 낸다
    #    (파일 날짜가 허용 범위 안이라 조용히 넘어가던 자리. _FAIL_MARK 머리말 참고).
    _mark_fail(_FRED_TAGS.get(tag, tag),
               f"{len(series)}개 계열을 전부 못 받았습니다 — 마지막 사유: {why}"
               if series and not out else None)
    return out


def fetch_us_inflation() -> dict:
    return _fetch_fred(_INFLATION_SERIES, "inflation")


def fetch_us_credit() -> dict:
    return _fetch_fred(_CREDIT_SERIES, "credit")


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


# ─────────────────────────────────────────────────────────────────────────────
# 신선도 점검 (`python batch_macro.py --audit`)
#
# ⚠️ **왜 필요한가 — 이 배치는 소스가 죽어도 초록불로 끝난다.** 각 fetch는 실패를
# print만 하고 넘어간다(한 소스가 막혀도 나머지 이력은 지키려고 일부러 그렇게 했다).
# 그래서 워크플로우는 계속 성공인데 특정 파일만 몇 주씩 멈출 수 있다 — FRED 두 계열
# (신용 스프레드·미국 물가)이 2026-11-08 기능 추가 이후 **러너에서 단 한 번도
# 성공하지 못한 채** 8일을 그렇게 갔고, 화면 카드의 날짜(11/05)를 사람이 눈으로
# 보고서야 알았다. 같은 기간 야후·CNN·재무부 소스는 매일 멀쩡히 들어왔다.
#
# 그래서 커밋이 끝난 뒤 파일을 다시 읽어 "마지막 데이터가 며칠 전인지"를 본다.
# 문제가 있으면 ::warning:: 주석 + 종료코드 1이다. 주석은 공개 API로 읽히고,
# 단계가 빨간불이면 GitHub이 리포 주인에게 메일을 보낸다 — 사람이 알아차리는
# 경로는 그것뿐이다. 커밋 **뒤에** 돌리므로 점검이 실패해도 받아온 데이터는 남는다.
#
# ⚠️ 허용 일수는 넉넉하게 잡는다. 거짓 경보가 한 번 나면 그 뒤의 진짜 경보까지
#    같이 무시된다. 특히 DRAM·DDR4·리튬은 **값이 안 바뀌면 점을 아예 안 찍으므로**
#    (_append_if_new) 3~5일 정체가 정상이다 — 그걸 고장이라 부르면 안 된다.
_FRESHNESS = [
    ("DRAM 스팟(DDR5)",     _DRAM_CACHE,      12),
    ("DDR4 스팟",           _DDR4_CACHE,      12),
    ("리튬(TE)",            _LITHIUM_CACHE,   12),   # 중국 연휴가 길다
    ("시장 지표(야후)",      _MARKET_CACHE,     6),
    ("공포·탐욕 지수",       _FNG_CACHE,        6),
    ("미국 연방부채",        _USDEBT_CACHE,     7),
    ("신용 스프레드(FRED)",  _CREDIT_CACHE,     6),
    ("S&P 밸류에이션",       _SP500_CACHE,     45),   # PER 월간·PBR 분기
    # 월간. 넷 중 core_PCE가 가장 늦어(발표가 익월 말) 늘 70~80일이 된다 — 그 계열에
    # 맞춰 느슨하게 잡는다. FRED가 아예 막히는 건 어차피 위 _fetch_fred가 즉시 경고한다.
    ("미국 물가(FRED)",      _INFLATION_CACHE, 95),
]


def _read_any(path: str):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _latest_ms(obj) -> int | None:
    """
    [[ts, 값], ...] 또는 {계열: [[ts, 값], ...]} 에서 마지막 시각.
    ts는 epoch_ms(캐시 파일)일 수도 "YYYY-MM-DD"(compare·index_val)일 수도 있다.
    """
    if isinstance(obj, dict):
        got = [g for g in (_latest_ms(v) for v in obj.values()) if g]
        # 계열 하나만 끊겨도 잡아야 하므로 가장 뒤처진 쪽을 본다
        return min(got) if got else None
    if isinstance(obj, list) and obj:
        last = obj[-1]
        if isinstance(last, (list, tuple)) and last:
            ts = last[0]
            if isinstance(ts, str):
                try:
                    return _date_to_ms(date.fromisoformat(ts[:10]))
                except ValueError:
                    return None
            if isinstance(ts, (int, float)):
                return int(ts)
    return None


def _check(name: str, path: str, allow: int, today: date) -> str | None:
    """허용 일수를 넘었으면 사람이 읽을 한 줄, 멀쩡하면 None."""
    ms = _latest_ms(_read_any(path))
    rel = os.path.relpath(path, _DIR).replace(os.sep, "/")
    if ms is None:
        return f"{name}: 파일이 없거나 비어 있습니다 ({rel})"
    d = datetime.fromtimestamp(ms / 1000, timezone.utc).date()
    age = (today - d).days
    if age > allow:
        return f"{name}: 최신 데이터가 {d} — {age}일 전입니다 (허용 {allow}일, {rel})"
    return None


def audit_freshness() -> list[str]:
    today = datetime.now(KST).date()
    problems = [p for p in (_check(n, pa, a, today) for n, pa, a in _FRESHNESS) if p]

    # 이번 실행에서 **아예 못 받아온 것** — 파일 날짜가 허용 범위 안이어도 빨간불이다
    # (_FAIL_MARK 머리말: 날짜만 보면 소스가 죽은 날을 놓친다).
    for name, why in (_read_any(_FAIL_MARK) or {}).items():
        problems.append(f"{name}: {why}")

    # 비교 차트(S&P·미국채 10년)는 연도별 파일 — 올해 것을 본다.
    p = _check("비교 차트(S&P·금리)",
               os.path.join(_COMPARE_DIR, f"{today.year}.json"), 6, today)
    if p:
        problems.append(p)

    # ── 아래는 batch_macro가 아니라 같은 워크플로우의 다른 단계·다른 배치가 쓴다 ──
    # 둘 다 continue-on-error거나 별도 워크플로우라, 조용히 멈춰도 여기 말고는
    # 드러나는 곳이 없다. 매크로 배치가 평일 매일 도니 일일 점검 자리로 쓴다.
    sector = os.path.join(_DIR, "data", "sector", "daily")
    days = sorted(f[:-5] for f in os.listdir(sector) if f.endswith(".json"))         if os.path.isdir(sector) else []
    if not days:
        problems.append("섹터 등락률: data/sector/daily 가 비어 있습니다")
    else:
        try:
            age = (today - date.fromisoformat(days[-1])).days
            if age > 10:      # 설·추석 연휴(최대 5영업일)를 넘겨도 안 울리게
                problems.append(f"섹터 등락률: 최신 파일이 {days[-1]} — {age}일 전입니다")
        except ValueError:
            problems.append(f"섹터 등락률: 파일명이 날짜가 아닙니다 ({days[-1]})")

    iv = os.path.join(_MACRO_DIR, "index_val", f"{today.year}.json")
    p = _check("지수 PER·PBR", iv, 10, today)
    if p:
        problems.append(p)
    else:
        # ⚠️ 날짜만으로는 못 잡는 고장이 하나 있다 — 줄은 들어왔는데 PER·PBR만 0인 것.
        #    KRX가 장 마감 당일 늦은 저녁에야 올리므로 이른 시각에 받으면 이렇게 된다
        #    (2026-11-13 금요일분이 실제로 0으로 남아 월요일까지 화면이 11/12에 멈췄다).
        for mk, rows in (_read_any(iv) or {}).items():
            if rows and len(rows[-1]) >= 3 and not (rows[-1][1] and rows[-1][2]):
                problems.append(f"지수 PER·PBR: {mk} {rows[-1][0]} 줄의 PER·PBR이 0입니다"
                                " — KRX 공개 전에 받은 값입니다")

    # 종목목록 시드는 날짜 칼럼이 없어 신선도를 못 재므로 크기만 본다(최후 폴백이라
    # 조금 묵은 건 괜찮지만, 잘린 파일이 남는 건 치명적이다 — krx_listing.py 머리말).
    seed = os.path.join(_DIR, "data", "listing", "krx_listing.csv")
    try:
        with open(seed, "r", encoding="utf-8") as f:
            n = sum(1 for _ in f) - 1
        if n < 2000:
            problems.append(f"종목목록 시드: {n}행뿐입니다 (data/listing/krx_listing.csv)")
    except OSError:
        problems.append("종목목록 시드: 읽을 수 없습니다 (data/listing/krx_listing.csv)")

    return problems


if __name__ == "__main__":
    import sys

    try:
        # 윈도우 콘솔(cp949)에서 '—' 같은 글자를 못 찍어 배치가 통째로 죽는다.
        # 러너는 UTF-8이라 영향이 없지만, 로컬에서 돌려볼 때 걸린다.
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    # 💡 `--audit` — 받아오기는 하지 않고 "안 들어온 것"만 점검한다(위 주석 참고).
    #    워크플로우가 커밋 뒤에 이 모드로 한 번 더 부른다.
    if "--audit" in sys.argv:
        _problems = audit_freshness()
        for _p in _problems:
            _warn(_p)
        if _problems:
            print(f"::error title=매크로 신선도::{len(_problems)}건이 들어오지 않았습니다"
                  " — 위 경고를 보세요")
            sys.exit(1)
        print("::notice title=매크로 신선도::모든 소스가 최신입니다")
        sys.exit(0)

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
        updated["credit"] = _merge_market(_CREDIT_CACHE, fetch_us_credit(),
                                          _CREDIT_KEEP_DAYS)
        updated["compare"] = update_compare_series()

    print(f"{datetime.now(KST).date()} 갱신 결과({only or 'all'}): {updated}")
