"""
kofia_flow.py — 투자자예탁금 · 신용거래융자 잔고 · 반대매매 (금융투자협회 통계).

data.go.kr "금융위원회_금융투자협회종합통계정보"(15094809)의 두 오퍼레이션을 읽는다.
수출입 탭과 **같은 `DATA_GO_KR_KEY`**를 쓰지만, data.go.kr은 API마다 활용신청이
따로라 이 API를 신청해야 비로소 200이 떨어진다(신청 전에는 403 코드 30).

왜 쓰는가 — "반대매매가 어디까지 났고 그래서 저점일 수 있나"를 보기 위한 것이다.
다만 **반대매매 금액 하나로는 판단할 수 없다**:
  · 이 통계의 반대매매는 **미수(위탁매매 미수금) 반대매매만** 집계한다.
    규모가 훨씬 큰 '신용융자 담보부족 반대매매'는 어느 공식 통계에도 없다.
  · 후행 지표다 — 이미 청산된 금액이라 '저점 확인'에는 쓰지만 예측에는 못 쓴다.
  · 금액이 작아(보통 수백억) 하루치 변동이 노이즈에 묻힌다.
그래서 화면은 ① 신용거래융자 잔고가 고점 대비 얼마나 줄었나(실제 디레버리징 규모)
② 신용잔고 ÷ 투자자예탁금(과열도) ③ 반대매매 금액·비중 — 셋을 같이 본다.

⚠️ 오퍼레이션 영문명은 포털 화면에도, 상세기능 표에도 적혀 있지 않다. 페이지 HTML에
묻혀 있는 Swagger 정의에서 꺼냈고(2026-11-08), 추측한 이름 50여 개는 전부
"해당 오픈API 서비스가 없거나 폐기됨"(코드 12)이 떴다. 바꾸지 말 것.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests

BASE = "https://apis.data.go.kr/1160100/service/GetKofiaStatisticsInfoService"
OP_CREDIT = "getGrantingOfCreditBalanceInfo"      # 신용공여잔고추이
OP_FUND = "getSecuritiesMarketTotalCapitalInfo"   # 증시자금추이

# 응답 항목 → 내부 이름. 금액은 모두 '억원'으로 맞춘다(아래 _scale 참고).
_CREDIT_FIELDS = {
    "crdTrFingWhl": "crd",          # 신용거래융자 전체
    "crdTrFingScrs": "crd_kospi",   # 신용거래융자 유가증권(코스피)
    "crdTrFingKosdaq": "crd_kosdaq",
    "dpsgScrtMogFing": "mog",       # 예탁증권 담보융자
}
_FUND_FIELDS = {
    "invrDpsgAmt": "dpsg",                  # 투자자예탁금(장내파생 예수금 제외)
    "brkTrdUcolMny": "ucol",                # 위탁매매 미수금
    "brkTrdUcolMnyVsOppsTrdAmt": "opps",    # 미수금 대비 실제 반대매매금액
    "ucolMnyVsOppsTrdRlImpt": "opps_pct",   # 미수금 대비 반대매매비중(%)
}
_MONEY = {"crd", "crd_kospi", "crd_kosdaq", "mog", "dpsg", "ucol", "opps"}


class KofiaError(RuntimeError):
    """조회 실패. message를 그대로 화면에 띄워도 되는 문장으로 쓴다."""


class KofiaNotRegistered(KofiaError):
    """키는 있으나 이 API에 활용신청이 안 된 상태(403 코드 30·20)."""


def _num(v):
    if v in (None, "", "-"):
        return None
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return None


_ROWS = 1000        # 한 번에 받는 건수
_MAX_PAGES = 20     # 안전장치 — 일별 데이터라 20페이지(2만 건)면 충분하다
_WORKERS = 4        # data.go.kr은 초당 요청 한도가 있다(동시 12개는 전부 429였다)


def _get_page(op: str, api_key: str, page: int, rows: int = _ROWS,
              timeout: int = 20) -> dict:
    """한 페이지의 body. 429면 0.5/1/2초 쉬었다 재시도한다."""
    url = f"{BASE}/{op}"
    params = {"serviceKey": api_key, "numOfRows": rows, "pageNo": page,
              "resultType": "json"}
    last = None
    for wait in (0.5, 1.0, 2.0, None):
        try:
            r = requests.get(url, params=params, timeout=timeout)
        except Exception as e:
            last = KofiaError(f"금투협 통계 요청 실패: {type(e).__name__}")
        else:
            if r.status_code == 429:
                last = KofiaError("금투협 통계 요청이 한도에 걸렸습니다")
            else:
                try:
                    data = r.json()
                except ValueError:
                    raise KofiaError(
                        f"금투협 통계 응답을 읽지 못했습니다 (HTTP {r.status_code})")
                # data.go.kr 라우터 오류는 본문 모양이 아예 다르다.
                err = (data.get("OpenAPI_ServiceResponse") or {}).get("cmmMsgHeader")
                if err:
                    code = str(err.get("returnReasonCode", ""))
                    msg = (err.get("returnAuthMsg") or err.get("errMsg")
                           or "알 수 없는 오류")
                    if code in ("30", "20", "22"):
                        raise KofiaNotRegistered(msg)
                    raise KofiaError(f"{msg} (코드 {code})")
                head = ((data.get("response") or {}).get("header")) or {}
                if str(head.get("resultCode", "00")) not in ("00", "0"):
                    raise KofiaError(head.get("resultMsg") or "금투협 통계 조회 실패")
                return ((data.get("response") or {}).get("body")) or {}
        if wait is None:
            break
        time.sleep(wait)
    raise last or KofiaError("금투협 통계 조회 실패")


def _rows_of(body: dict) -> list:
    items = body.get("items") or {}
    if isinstance(items, dict):
        items = items.get("item") or []
    if isinstance(items, dict):
        items = [items]
    return items if isinstance(items, list) else []


def _collect(body: dict, fields: dict, out: dict) -> None:
    for it in _rows_of(body):
        if not isinstance(it, dict):
            continue
        d = str(it.get("basDt") or "").strip()
        if len(d) != 8 or not d.isdigit():
            continue
        row = out.setdefault(d, {})
        for src, dst in fields.items():
            v = _num(it.get(src))
            if v is not None:
                row[dst] = v


def _scale(df: pd.DataFrame) -> pd.DataFrame:
    """금액 항목을 '억원'으로 맞춘다.

    💡 포털 어디에도 금액 단위가 적혀 있지 않다(응답 설명이 "...금액"뿐). 그래서
    투자자예탁금의 크기로 단위를 역산한다 — 실제 50~100조원이라는 사실이 고정점이다.
    원/천원/백만원/억원/조원 어느 쪽으로 와도 억원으로 떨어진다. 단위가 바뀌면
    그래프 축만 틀리는 게 아니라 '예탁금 대비 신용잔고 비율'이 통째로 망가지므로,
    추정 못 하면 환산하지 않고 그대로 둔다(화면에 비율은 안 띄우게 된다).
    """
    if "dpsg" not in df or df["dpsg"].dropna().empty:
        return df
    v = float(df["dpsg"].dropna().iloc[-1])
    for lo, div in ((1e12, 1e8), (1e9, 1e5), (1e6, 1e2), (1e4, 1.0), (1.0, 1e-4)):
        if v >= lo:
            break
    else:
        return df
    if div != 1.0:
        for c in _MONEY & set(df.columns):
            df[c] = df[c] / div
    return df


def fetch_flow(api_key: str) -> pd.DataFrame:
    """index=기준일, 금액은 억원. columns: dpsg crd crd_kospi crd_kosdaq mog ucol opps opps_pct.

    💡 날짜 범위 파라미터(beginBasDt/endBasDt)는 상세기능 표에만 있고 Swagger에는
    없어서 쓰지 않는다. 대신 1페이지로 totalCount를 읽어 **남은 페이지를 병렬로**
    받는다 — 순차로 받으면 두 오퍼레이션 합쳐 열 번이 넘어 탭 로딩이 20초가 된다.
    날짜를 키로 담으므로 페이지가 도착하는 순서는 상관없다.
    """
    if not api_key:
        raise KofiaError("DATA_GO_KR_KEY가 없습니다")
    merged: dict[str, dict] = {}
    tasks = []
    for op, fields in ((OP_FUND, _FUND_FIELDS), (OP_CREDIT, _CREDIT_FIELDS)):
        body = _get_page(op, api_key, 1)
        _collect(body, fields, merged)
        total = _num(body.get("totalCount")) or 0
        got = len(_rows_of(body))
        if got >= _ROWS and total > _ROWS:
            last = min(int(-(-total // _ROWS)), _MAX_PAGES)
            tasks += [(op, fields, p) for p in range(2, last + 1)]
    if tasks:
        with ThreadPoolExecutor(max_workers=_WORKERS) as ex:
            futs = {ex.submit(_get_page, op, api_key, p): (op, fields)
                    for op, fields, p in tasks}
            for fut in as_completed(futs):
                op, fields = futs[fut]
                _collect(fut.result(), fields, merged)
    if not merged:
        raise KofiaError("금투협 통계가 비어 있습니다")
    df = pd.DataFrame.from_dict(merged, orient="index")
    df.index = pd.to_datetime(df.index, format="%Y%m%d")
    df = df.sort_index()
    return _scale(df)


if __name__ == "__main__":
    import sys

    key = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        d = fetch_flow(key)
    except KofiaNotRegistered as e:
        print("활용신청이 필요합니다:", e)
        sys.exit(2)
    print(d.tail(10).to_string())
    print("\n기간:", d.index[0].date(), "~", d.index[-1].date(), "| 건수:", len(d))
    print("컬럼:", list(d.columns))
