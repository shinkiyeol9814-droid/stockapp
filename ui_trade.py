"""
ui_trade.py — 수출입 동향 탭 (관세청 품목별 수출입실적).

원래 매크로 탭 안의 라디오 토글 한 칸이었는데, 시장 지표와 성격이 달라
(정부 API · 월 단위 · 품목별) 별도 메뉴로 분리했다.

데이터 소스: 관세청_품목별 수출입실적 (data.go.kr, DATA_GO_KR_KEY 필요).
수출·수입·무역수지를 지표로 골라 월별/분기별로 본다.
"""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import requests
import html
import concurrent.futures
import xml.etree.ElementTree as ET
from datetime import datetime



# 세부품목 카탈로그(HS코드 ↔ 관련 상장종목)는 trade_items.py 에 분리했다 —
# 47개 품목이라 여기 두면 화면 로직이 안 보인다.
from trade_items import TRADE_ITEMS, themes, items_of, lookup, item_count

_API_BASE = "https://apis.data.go.kr/1220000/Itemtrade/getItemtradeList"


def _api_call(api_key: str, hs_codes: list, year: int, month: int):
    """
    관세청_품목별 수출입실적 → (수출액, 수입액) 백만달러.

    💡 예전 주석에 "HS코드 파라미터 필터가 미지원"이라고 적혀 있었지만 사실이
    아니다. hsSgn 파라미터가 정상 동작한다 — 매달 전체 9,653건을 받아
    클라이언트에서 걸러내던 것을 코드당 21건으로 줄인다(0.8초 → 0.1초).
    18개월 × 카테고리를 도는 구조라 체감 차이가 크다.

    ⚠️ 함정: hsSgn을 쓰면 응답에 hsCode가 "-"인 **합계 행**이 하나 더 붙는다.
    그 행의 값이 나머지 전체의 합과 같아서, 그냥 다 더하면 정확히 2배가 된다.
    10자리 세부 코드만 합산해 기존 결과와 동일하게 맞춘다.
    """
    yymm = f"{year}{month:02d}"
    exp_total = imp_total = 0.0
    for code in hs_codes:
        try:
            r = requests.get(
                _API_BASE,
                params={
                    "serviceKey": api_key,
                    "strtYymm": yymm,
                    "endYymm": yymm,
                    "hsSgn": code,
                    "numOfRows": 999,
                    "pageNo": 1,
                },
                timeout=20,
            )
            if r.status_code != 200:
                continue
            root = ET.fromstring(r.text)
            for item in root.findall(".//item"):
                hs = (item.findtext("hsCode", "") or "").strip()
                if len(hs) != 10:
                    continue  # 합계 행("-") 등 세부 품목이 아닌 행은 제외
                exp_total += float((item.findtext("expDlr", "0") or "0").replace(",", ""))
                imp_total += float((item.findtext("impDlr", "0") or "0").replace(",", ""))
        except Exception:
            continue
    return exp_total / 1_000_000, imp_total / 1_000_000


_NITEM_BASE = "https://apis.data.go.kr/1220000/nitemtrade/getNitemtradeList"


def _api_call_country(api_key: str, hs_codes: list, year: int, month: int, country: str):
    """
    국가별 품목 수출입실적 → (수출액, 수입액) 백만달러.
    country는 국가코드(예: "CN", "US"). Itemtrade와 달리 국가 차원이 하나 더 있어
    별도 엔드포인트(nitemtrade)를 쓴다.

    ⚠️ Itemtrade와 같은 함정: 응답에 hsCd/statCd가 "-"인 합계 행이 섞여 온다.
    국가코드로 걸러내므로 자연히 제외되지만, 필드명이 hsCode가 아니라 hsCd다.
    """
    yymm = f"{year}{month:02d}"
    exp_total = imp_total = 0.0
    for code in hs_codes:
        try:
            r = requests.get(
                _NITEM_BASE,
                params={
                    "serviceKey": api_key, "strtYymm": yymm, "endYymm": yymm,
                    "hsSgn": code, "numOfRows": 999, "pageNo": 1,
                },
                timeout=20,
            )
            if r.status_code != 200:
                continue
            for item in ET.fromstring(r.text).findall(".//item"):
                if (item.findtext("statCd", "") or "").strip() != country:
                    continue
                exp_total += float((item.findtext("expDlr", "0") or "0").replace(",", ""))
                imp_total += float((item.findtext("impDlr", "0") or "0").replace(",", ""))
        except Exception:
            continue
    return exp_total / 1_000_000, imp_total / 1_000_000


@st.cache_data(ttl=3600, show_spinner=False)
def get_country_options(hs_codes: tuple, ref_months: int = 3):
    """
    이 품목의 주요 수출 대상국 목록 → [(국가코드, "중국 (74%)"), ...] 비중 내림차순.
    최근 몇 달을 합산해 뽑는다 — 한 달만 보면 선적 시점 때문에 순위가 튄다.
    """
    api_key = st.secrets.get("DATA_GO_KR_KEY", "")
    if not api_key or not hs_codes:
        return []
    now = datetime.today()
    totals: dict[str, list] = {}
    for i in range(1, ref_months + 1):
        tm = now.year * 12 + (now.month - 1) - i
        y, m = tm // 12, tm % 12 + 1
        for code in hs_codes:
            try:
                r = requests.get(
                    _NITEM_BASE,
                    params={"serviceKey": api_key, "strtYymm": f"{y}{m:02d}",
                            "endYymm": f"{y}{m:02d}", "hsSgn": code,
                            "numOfRows": 999, "pageNo": 1},
                    timeout=20,
                )
                if r.status_code != 200:
                    continue
                for item in ET.fromstring(r.text).findall(".//item"):
                    cc = (item.findtext("statCd", "") or "").strip()
                    nm = (item.findtext("statCdCntnKor1", "") or "").strip()
                    # 합계 행(코드/국가명이 "-")은 제외
                    if not cc or cc == "-" or not nm or nm == "-":
                        continue
                    v = float((item.findtext("expDlr", "0") or "0").replace(",", ""))
                    row = totals.setdefault(cc, [nm, 0.0])
                    row[1] += v
            except Exception:
                continue
    grand = sum(v[1] for v in totals.values())
    if grand <= 0:
        return []
    ranked = sorted(totals.items(), key=lambda kv: -kv[1][1])
    out = []
    for cc, (nm, v) in ranked[:15]:
        pct = v / grand * 100
        if pct < 0.5:
            break
        out.append((cc, f"{nm} ({pct:.0f}%)"))
    return out


@st.cache_data(ttl=3600, show_spinner=False)
def _get_export_trend(hs_codes: tuple, n_months: int = 36,
                      country: str | None = None) -> pd.DataFrame | None:
    """
    관세청 API 기반 월간 수출 추세.
    최근 3개월은 searchDt로 순별(10일/20일/말일) 누계를 분해.
    API 키 없으면 None 반환.
    """
    api_key = st.secrets.get("DATA_GO_KR_KEY", "")
    if not api_key:
        return None

    hs_codes = list(hs_codes)
    now = datetime.today()

    month_list = []
    for i in range(n_months - 1, -1, -1):
        total_m = now.year * 12 + (now.month - 1) - i
        month_list.append((total_m // 12, total_m % 12 + 1, i))

    def _fetch(ym):
        year, month, i = ym
        # 국가를 지정하면 국가 차원이 있는 엔드포인트로 간다
        if country:
            exp_M, imp_M = _api_call_country(api_key, hs_codes, year, month, country)
        else:
            exp_M, imp_M = _api_call(api_key, hs_codes, year, month)
        return year, month, i, exp_M, imp_M

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(_fetch, month_list))

    rows = []
    for year, month, i, exp_M, imp_M in sorted(results, key=lambda x: (x[0], x[1])):
        is_cur = (year == now.year and month == now.month)
        rows.append(dict(
            label=f"{str(year)[2:]}년{month:02d}월",
            year=year, month=month,
            # total은 기존 이름 그대로 수출액 (차트/호출부 호환).
            # 수입·무역수지를 추가로 실어 보낸다.
            total=exp_M, imports=imp_M, balance=exp_M - imp_M,
            is_partial=is_cur,
        ))

    # 💡 관세청 품목별 실적은 확정 통계라 2개월 가까이 지연된다
    # (2026-09-09 기준 최신이 2026-07). 아직 안 나온 달도 0으로 채워지는데,
    # 그대로 그리면 막대 높이가 0이 되어 "수출이 갑자기 끊긴" 것처럼 보인다.
    # 꼬리의 빈 달만 잘라낸다 — 중간의 0은 실제 이상치일 수 있으니 남긴다.
    while rows and rows[-1]["total"] == 0 and rows[-1]["imports"] == 0:
        rows.pop()

    return pd.DataFrame(rows)



# ── 차트 ─────────────────────────────────────────────────────────────────────
def _render_trend_chart(trend_df: pd.DataFrame, title: str, subtitle: str):
    """
    월별 수출 추이 꺾은선.

    💡 예전엔 누적 막대였다. 18~36개월을 막대로 세우면 폭이 얇아져 추세선이
     안 보이고, YoY 라벨이 막대 위마다 붙어 화면이 시끄러웠다. 추세를 읽는
     용도라 선이 맞다 — 대신 마지막 점만 마커로 강조하고, 수입·무역수지는
     축을 늘리지 않고 툴팁에만 담는다(지표는 수출 하나로 고정).
    """
    x = list(trend_df["label"])
    y = list(trend_df["total"])

    # 마지막 값이 시작보다 높으면 상승 → 빨강 (한국 시장 색상 규칙)
    up = len(y) > 1 and y[-1] >= y[0]
    line_c = "#ef5350" if up else "#1565C0"
    fill_c = "rgba(239,83,80,0.10)" if up else "rgba(21,101,192,0.10)"

    cust = list(zip(trend_df["imports"], trend_df["balance"]))

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=x, y=y, mode="lines", name="수출",
        line=dict(color=line_c, width=2.2),
        fill="tozeroy", fillcolor=fill_c,
        customdata=cust,
        hovertemplate=("<b>%{x}</b><br>수출 <b>$%{y:,.1f}M</b>"
                       "<br>수입 $%{customdata[0]:,.1f}M"
                       "<br>무역수지 $%{customdata[1]:,.1f}M<extra></extra>"),
    ))
    # 최근 값만 점으로 강조 — 어디가 현재인지 한눈에
    fig.add_trace(go.Scatter(
        x=[x[-1]], y=[y[-1]], mode="markers",
        marker=dict(color=line_c, size=8, line=dict(color="#fff", width=1.5)),
        showlegend=False, hoverinfo="skip",
    ))

    # 눈금이 36개면 다 찍으면 겹친다 — 3개월 간격으로만 라벨
    step = max(1, len(x) // 12)
    tickvals = x[::step]

    fig.update_layout(
        height=380, showlegend=False,
        title=dict(text=f"<b>{title}</b><br><span style='font-size:11px;color:#888;'>"
                        f"{subtitle}</span>", font=dict(size=14), x=0, y=0.97),
        xaxis=dict(tickfont=dict(size=9), tickangle=-45, showgrid=False,
                   tickmode="array", tickvals=tickvals, fixedrange=True),
        yaxis=dict(title="수출금액 (백만$)", tickformat=",.0f", showgrid=True,
                   gridcolor="rgba(200,200,200,0.22)", rangemode="tozero",
                   fixedrange=True),
        margin=dict(l=0, r=14, t=54, b=70),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        dragmode=False, hovermode="x unified",
    )
    # 축을 잠갔으니 모드바 버튼은 전부 무동작 → 숨긴다. staticPlot은 두지 않는다(툴팁 유지).
    st.plotly_chart(fig, use_container_width=True,
                    config={"scrollZoom": False, "displayModeBar": False,
                            "displaylogo": False})


# ── 렌더링 ────────────────────────────────────────────────────────────────────
def _trend_note(series):
    """
    "36개월 내 최대 / N개월 만에 최대" 배지 문구. 텔레그램 리서치 채널들이 붙이는
    "역대 최대" 코멘트와 같은 개념이고, 매크로 탭 마일스톤과 같은 방식이다:
    현재값을 넘어선 값이 마지막으로 나온 시점부터의 경과 기간을 센다.

    ⚠️ 조회 구간이 곧 판정 범위다 — "역대"라고 쓰지 않고 기간을 문구에 담는다.
    """
    vals = [v for v in series if v is not None]
    if len(vals) < 4:
        return None
    last = vals[-1]
    if last <= 0:
        return None
    span = len(vals)
    higher = [i for i, v in enumerate(vals[:-1]) if v > last]
    if not higher:
        return f"{span}개월 내 최대", "#ef5350"
    gap = span - 1 - higher[-1]
    if gap >= 3:
        return f"{gap}개월 만에 최대", "#ef5350"
    lower = [i for i, v in enumerate(vals[:-1]) if v < last]
    if not lower:
        return f"{span}개월 내 최소", "#1565C0"
    gap = span - 1 - lower[-1]
    if gap >= 3:
        return f"{gap}개월 만에 최소", "#1565C0"
    return None


# 화면 고정값 — 요청에 따라 선택 UI를 두지 않는다.
# 기간을 36개월로 잡은 이유: 마일스톤("N개월 만에 최대") 판정 범위가 곧 조회
# 구간이라, 짧으면 "최대"가 너무 쉽게 뜬다.
_MONTHS = 36


def render_trade():
    st.markdown(
        "<div style='font-size:1.4rem;font-weight:bold;margin-bottom:4px;'>🚢 수출입 동향</div>",
        unsafe_allow_html=True,
    )
    st.caption(f"관세청 품목별 수출입실적 · 세부품목 {item_count()}개 · 월별 수출 {_MONTHS}개월 · 1시간 캐시")

    if not st.secrets.get("DATA_GO_KR_KEY", ""):
        st.warning(
            "수출 데이터를 표시하려면 **관세청 공공데이터포털 API 키**가 필요합니다.\n\n"
            "1. [data.go.kr](https://www.data.go.kr) 에서 **관세청_통관기준 수출입 실적** API 신청\n"
            "2. 발급된 키를 Streamlit Cloud 시크릿에 추가: `DATA_GO_KR_KEY = \"발급받은키\"`"
        )
        st.stop()

    theme = st.radio("테마", themes(), horizontal=True, key="trade_theme")

    # 💡 세부품목은 드롭다운에 숨기지 않고 라디오로 전부 펼친다 — 테마 안에
    # 뭐가 있는지 한 번에 보이는 게 이 탭의 쓸모다(요청 사항).
    item = st.radio("세부품목", items_of(theme), horizontal=True,
                    key=f"trade_item_{theme}", label_visibility="visible")

    hs_codes, stocks = lookup(theme, item)

    # 국가 선택 — 전체(합산) 또는 주요 대상국. 비중을 라벨에 같이 보여준다.
    opts = get_country_options(tuple(hs_codes))
    labels = ["전체"] + [lb for _, lb in opts]
    codes_by_label = {lb: cc for cc, lb in opts}
    picked = st.selectbox("수출 대상국", labels, key=f"trade_country_{theme}_{item}")
    country = codes_by_label.get(picked)

    if stocks:
        st.markdown(
            f"<div style='background:#eef3fb;border-left:3px solid #1565C0;color:#1a2733;"
            f"padding:7px 11px;margin:6px 0 2px;font-size:13px;border-radius:0 5px 5px 0;'>"
            f"<b style='color:#1565C0;'>관련종목</b> &nbsp; {html.escape(stocks)}"
            f"<span style='color:#6b7a8c;margin-left:8px;font-size:11.5px;'>"
            f"HS {', '.join(hs_codes)}</span></div>",
            unsafe_allow_html=True,
        )

    with st.spinner(f"{item} 추세 데이터 로딩 중..."):
        trend_df = _get_export_trend(tuple(hs_codes), _MONTHS, country)

    if trend_df is None or trend_df.empty:
        st.error("API에서 데이터를 가져오지 못했습니다. API 키와 네트워크 상태를 확인해주세요.")
        return

    latest = trend_df.iloc[-1]
    note = _trend_note(list(trend_df["total"]))
    badge = ""
    if note:
        txt, clr = note
        badge = (f"<span style='background:{clr};color:#fff;font-size:11px;font-weight:700;"
                 f"padding:2px 7px;border-radius:3px;margin-left:8px;'>{txt}</span>")

    # 확정 통계는 1~2개월 지연 공표된다 — 최신 데이터 월을 명시해야 오해가 없다
    st.markdown(
        f"<div style='font-size:12px;color:#888;margin:2px 0 4px;'>"
        f"최신 <b style='color:#555;'>{latest['label']}</b> "
        f"· 수출 <b style='color:#555;'>${latest['total']:,.1f}M</b>"
        f" · 관세청 확정 통계는 통상 1~2개월 지연 공표{badge}</div>",
        unsafe_allow_html=True,
    )

    where = "전체" if not country else picked.split(" (")[0]
    _render_trend_chart(
        trend_df,
        f"{item} 월별 수출",
        f"대상국 {where} · {trend_df.iloc[0]['label']} ~ {latest['label']}",
    )
