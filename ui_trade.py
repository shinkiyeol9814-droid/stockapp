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


@st.cache_data(ttl=3600, show_spinner=False)
def _get_export_trend(hs_codes: tuple, n_months: int = 18) -> pd.DataFrame | None:
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
def _trend_note(series, metric: str):
    """
    "역대 최대 / N개월 만에 최대" 판정. 텔레그램 리서치 채널들이 붙이는
    코멘트와 같은 개념이고, 매크로 탭의 마일스톤 배지와 같은 방식이다:
    현재값을 넘어선 값이 마지막으로 나온 시점부터의 경과 기간을 센다.

    ⚠️ 보유 구간이 곧 판정 범위다. 18개월만 받아왔다면 "역대"라고 쓸 수 없으니
    조회 기간을 그대로 문구에 담는다("18개월 내 최대").
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
        return f"{span}개월 내 최대 {metric}", "#ef5350"
    gap = span - 1 - higher[-1]
    if gap >= 3:
        return f"{gap}개월 만에 최대 {metric}", "#ef5350"
    lower = [i for i, v in enumerate(vals[:-1]) if v < last]
    if not lower:
        return f"{span}개월 내 최소 {metric}", "#1565C0"
    gap = span - 1 - lower[-1]
    if gap >= 3:
        return f"{gap}개월 만에 최소 {metric}", "#1565C0"
    return None


def render_trade():
    st.markdown(
        "<div style='font-size:1.4rem;font-weight:bold;margin-bottom:4px;'>🚢 수출입 동향</div>",
        unsafe_allow_html=True,
    )
    st.caption(f"관세청 품목별 수출입실적 · 세부품목 {item_count()}개 · 1시간 캐시")

    if not st.secrets.get("DATA_GO_KR_KEY", ""):
        st.warning(
            "수출 데이터를 표시하려면 **관세청 공공데이터포털 API 키**가 필요합니다.\n\n"
            "1. [data.go.kr](https://www.data.go.kr) 에서 **관세청_통관기준 수출입 실적** API 신청\n"
            "2. 발급된 키를 Streamlit Cloud 시크릿에 추가: `DATA_GO_KR_KEY = \"발급받은키\"`"
        )
        st.stop()

    # 💡 테마 → 세부품목 2단 선택. 예전엔 HS 4자리 대분류 6개뿐이라
    # "반도체가 늘었다"까지만 알 수 있었다 — 디램/낸드/MLCC처럼 갈라서
    # 봐야 어느 종목에 걸리는지가 보인다.
    c1, c2 = st.columns([1.4, 3])
    with c1:
        theme = st.selectbox("테마", themes(), key="trade_theme")
    with c2:
        item = st.selectbox("세부품목", items_of(theme), key=f"trade_item_{theme}")

    hs_codes, stocks = lookup(theme, item)

    c3, c4, c5 = st.columns([1.4, 2.4, 1.8])
    with c3:
        n_mo = st.selectbox("기간(개월)", [12, 18, 24, 36], index=1, key="export_nmo")
    with c4:
        metric = st.radio("지표", list(_TRADE_METRICS.keys()),
                          horizontal=True, key="export_metric")
    with c5:
        view_mode = st.radio("단위", ["월별", "분기별"], horizontal=True, key="export_view")

    # 관련 상장종목 — 이 품목이 어느 종목에 걸리는지가 이 탭의 핵심이다
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
        trend_df = _get_export_trend(tuple(hs_codes), n_mo)

    if trend_df is None or trend_df.empty:
        st.error("API에서 데이터를 가져오지 못했습니다. API 키와 네트워크 상태를 확인해주세요.")
        return

    now = datetime.today()
    col, _ = _TRADE_METRICS[metric]
    note = _trend_note(list(trend_df[col]), metric)

    # 확정 통계 지연(약 2개월)을 화면에서도 알 수 있게 최신 데이터 월을 표기
    latest = trend_df.iloc[-1]
    badge = ""
    if note:
        txt, clr = note
        badge = (f"<span style='background:{clr};color:#fff;font-size:11px;font-weight:700;"
                 f"padding:2px 7px;border-radius:3px;margin-left:8px;'>{txt}</span>")
    st.markdown(
        f"<div style='font-size:12px;color:#888;margin:2px 0 6px;'>"
        f"최신 데이터 <b style='color:#555;'>{latest['label']}</b> "
        f"· {metric} <b style='color:#555;'>${latest[col]:,.1f}M</b>"
        f" · 관세청 확정 통계는 통상 1~2개월 지연 공표{badge}</div>",
        unsafe_allow_html=True,
    )

    if view_mode == "월별":
        _render_monthly_chart(trend_df, item, metric, now)
    else:
        _render_quarterly_chart(trend_df, item, metric, now)
