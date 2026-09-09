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
import concurrent.futures
import xml.etree.ElementTree as ET
from datetime import datetime



TRADE_CATS = {
    "반도체":   ["8542"],
    "자동차":   ["8703"],
    "선박":     ["8901", "8902"],
    "2차전지":  ["8507"],
    "변압기":   ["8504"],
    "화장품":   ["3304", "3305", "3306"],
}

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


@st.cache_data(ttl=3600, show_spinner=False)
def _get_export_trend(cat: str, n_months: int = 18) -> pd.DataFrame | None:
    """
    관세청 API 기반 월간 수출 추세.
    최근 3개월은 searchDt로 순별(10일/20일/말일) 누계를 분해.
    API 키 없으면 None 반환.
    """
    api_key = st.secrets.get("DATA_GO_KR_KEY", "")
    if not api_key:
        return None

    hs_codes = TRADE_CATS.get(cat, [])
    now = datetime.today()

    month_list = []
    for i in range(n_months - 1, -1, -1):
        total_m = now.year * 12 + (now.month - 1) - i
        month_list.append((total_m // 12, total_m % 12 + 1, i))

    def _fetch(ym):
        year, month, i = ym
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

    return pd.DataFrame(rows)



# ── 차트 헬퍼 ────────────────────────────────────────────────────────────────
# 수출입 차트에서 고를 수 있는 지표. (컬럼, 막대색) — 색이 None이면 부호별로
# 칠한다(무역수지는 적자 구간이 있어 한 색으로 두면 흑/적자 구분이 안 된다).
_TRADE_METRICS = {
    "수출":     ("total",   "#90CAF9"),
    "수입":     ("imports", "#B39DDB"),
    "무역수지": ("balance", None),
}


def _bar_colors(values):
    """무역수지용 — 흑자 빨강 / 적자 파랑 (한국 시장 색상 규칙)."""
    return ["#ef5350" if v >= 0 else "#1565C0" for v in values]


def _render_monthly_chart(trend_df: pd.DataFrame, cat_sel: str, metric: str, now: datetime):
    col, base_color = _TRADE_METRICS[metric]
    is_balance = base_color is None

    fig = go.Figure()

    hdf = trend_df
    fig.add_trace(go.Bar(
        x=hdf["label"], y=hdf[col],
        name=metric,
        marker_color=_bar_colors(hdf[col]) if is_balance else base_color,
        hovertemplate="%{x}<br><b>$%{y:,.1f}M</b><extra>" + metric + "</extra>",
    ))

    # 💡 예전엔 여기서 d10/d20/d30(순별 누계)을 쌓아 올렸는데, _get_export_trend가
    # 그 값을 항상 0으로 채워서 한 번도 그려지지 않는 죽은 코드였다 — 제거.

    # 무역수지는 흑/적자로 부호가 뒤집혀 전년대비 "비율"이 의미를 잃는다 → 생략
    td = {} if is_balance else {(r["year"], r["month"]): r[col] for _, r in trend_df.iterrows()}
    for _, row in trend_df.iterrows():
        prev = td.get((row["year"] - 1, row["month"]), 0)
        if prev <= 0 or row[col] <= 0:
            continue
        yoy = (row[col] / prev - 1) * 100
        clr = "#ef5350" if yoy > 0 else "#1565C0"
        fig.add_annotation(
            x=row["label"], y=row[col],
            text=f"{yoy:+.0f}%", showarrow=False,
            font=dict(size=8, color=clr), yanchor="bottom", yshift=2,
        )

    cur_rows = trend_df[trend_df["is_partial"]]
    if not cur_rows.empty:
        fig.add_annotation(
            x=cur_rows.iloc[-1]["label"], y=cur_rows.iloc[-1][col],
            text=f"({now.month}월 {now.day}일까지)", showarrow=False,
            font=dict(size=8, color="#999"), yanchor="bottom", yshift=14,
        )

    fig.update_layout(
        barmode="stack", height=420,
        title=dict(text=f"{cat_sel} 월별 {metric}", font=dict(size=13), x=0),
        xaxis=dict(tickfont=dict(size=9), tickangle=-45, showgrid=False, fixedrange=True),
        yaxis=dict(title=f"{metric} (백만$)", tickformat=",.0f",
                   showgrid=True, gridcolor="rgba(200,200,200,0.25)", fixedrange=True),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1, font=dict(size=10)),
        margin=dict(l=0, r=10, t=50, b=80),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", dragmode=False,
    )
    # 축을 fixedrange로 잠갔으니 모드바의 줌/이동 버튼은 전부 무동작 —
    # 혼란만 주므로 모드바 자체를 숨긴다. staticPlot은 두지 않는다(툴팁 유지).
    st.plotly_chart(fig, use_container_width=True,
                    config={
                        "scrollZoom": False,
                        "displayModeBar": False,
                        "displaylogo": False,
                    })


def _render_quarterly_chart(trend_df: pd.DataFrame, cat_sel: str, metric: str, now: datetime):
    df = trend_df.copy()
    df["quarter"] = ((df["month"] - 1) // 3 + 1).astype(int)
    df["q_label"] = df.apply(lambda r: f"{str(r['year'])[2:]}년Q{r['quarter']}", axis=1)

    qdf = (
        df.groupby(["year", "quarter", "q_label"], as_index=False)
        .agg(total=("total", "sum"), imports=("imports", "sum"),
             balance=("balance", "sum"), is_partial=("is_partial", "any"))
        .sort_values(["year", "quarter"])
        .reset_index(drop=True)
    )

    col, base_color = _TRADE_METRICS[metric]
    is_balance = base_color is None

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=qdf["q_label"], y=qdf[col],
        name=metric,
        marker_color=_bar_colors(qdf[col]) if is_balance else base_color,
        hovertemplate="%{x}<br><b>$%{y:,.1f}M</b><extra>" + metric + "</extra>",
    ))

    # 💡 순별 누계(d10/d20/d30) 블록 제거 — 값이 항상 0이라 그려지지 않던 죽은 코드.
    # YoY% 분기 기준
    qtd = {} if is_balance else {(r["year"], r["quarter"]): r[col] for _, r in qdf.iterrows()}
    for _, row in qdf.iterrows():
        prev = qtd.get((row["year"] - 1, row["quarter"]), 0)
        if prev <= 0 or row[col] <= 0:
            continue
        yoy = (row[col] / prev - 1) * 100
        clr = "#ef5350" if yoy > 0 else "#1565C0"
        fig.add_annotation(
            x=row["q_label"], y=row[col],
            text=f"{yoy:+.0f}%", showarrow=False,
            font=dict(size=9, color=clr), yanchor="bottom", yshift=2,
        )

    cur_rows = qdf[qdf["is_partial"]]
    if not cur_rows.empty:
        cur_q = cur_rows.iloc[-1]
        fig.add_annotation(
            x=cur_q["q_label"], y=cur_q["total"],
            text=f"({now.month}월 {now.day}일까지)", showarrow=False,
            font=dict(size=8, color="#999"), yanchor="bottom", yshift=14,
        )

    fig.update_layout(
        barmode="stack", height=420,
        title=dict(text=f"{cat_sel} 분기별 {metric}", font=dict(size=13), x=0),
        xaxis=dict(tickfont=dict(size=10), tickangle=-30, showgrid=False, fixedrange=True),
        yaxis=dict(title=f"{metric} (백만$)", tickformat=",.0f",
                   showgrid=True, gridcolor="rgba(200,200,200,0.25)", fixedrange=True),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1, font=dict(size=10)),
        margin=dict(l=0, r=10, t=50, b=60),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", dragmode=False,
    )
    # 축을 fixedrange로 잠갔으니 모드바의 줌/이동 버튼은 전부 무동작 —
    # 혼란만 주므로 모드바 자체를 숨긴다. staticPlot은 두지 않는다(툴팁 유지).
    st.plotly_chart(fig, use_container_width=True,
                    config={
                        "scrollZoom": False,
                        "displayModeBar": False,
                        "displaylogo": False,
                    })




# ── 렌더링 ────────────────────────────────────────────────────────────────────
def render_trade():
    st.markdown(
        "<div style='font-size:1.4rem;font-weight:bold;margin-bottom:4px;'>🚢 수출입 동향</div>",
        unsafe_allow_html=True,
    )
    st.caption("관세청 품목별 수출입실적 · 1시간 캐시")

    has_key = bool(st.secrets.get("DATA_GO_KR_KEY", ""))

    if not has_key:
        st.warning(
            "수출 데이터를 표시하려면 **관세청 공공데이터포털 API 키**가 필요합니다.\n\n"
            "1. [data.go.kr](https://www.data.go.kr) 에서 **관세청_통관기준 수출입 실적** API 신청\n"
            "2. 발급된 키를 Streamlit Cloud 시크릿에 추가: `DATA_GO_KR_KEY = \"발급받은키\"`"
        )
        st.stop()

    st.markdown("#### 📈 품목별 수출입 추세")

    cc1, cc2, cc3, cc4 = st.columns([2, 1.6, 2.2, 1.8])
    with cc1:
        cat_sel = st.selectbox("품목", list(TRADE_CATS.keys()), key="export_cat")
    with cc2:
        n_mo = st.selectbox("기간", [12, 18, 24], index=1, key="export_nmo")
    with cc3:
        metric = st.radio("지표", list(_TRADE_METRICS.keys()),
                          horizontal=True, key="export_metric")
    with cc4:
        view_mode = st.radio("단위", ["월별", "분기별"], horizontal=True, key="export_view")

    with st.spinner(f"{cat_sel} 추세 데이터 로딩 중..."):
        trend_df = _get_export_trend(cat_sel, n_mo)

    now = datetime.today()

    if trend_df is None or trend_df.empty:
        st.error("API에서 데이터를 가져오지 못했습니다. API 키와 네트워크 상태를 확인해주세요.")
    elif view_mode == "월별":
        _render_monthly_chart(trend_df, cat_sel, metric, now)
    else:
        _render_quarterly_chart(trend_df, cat_sel, metric, now)
