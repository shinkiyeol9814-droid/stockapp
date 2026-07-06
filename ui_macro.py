"""
ui_macro.py — 매크로 지표 & 수출입 동향 탭
"""
import streamlit as st
import requests
import pandas as pd
import plotly.graph_objects as go
import concurrent.futures
import xml.etree.ElementTree as ET
from datetime import datetime

# ── 상수 ─────────────────────────────────────────────────────────────────────
MARKET_ITEMS = [
    ("원/달러 환율",  "USDKRW=X", "₩",    ",.0f"),
    ("미국채 10년",   "^TNX",      "%",    ".2f"),
    ("WTI 유가",      "CL=F",      "$",    ",.2f"),
    ("Brent 유가",    "BZ=F",      "$",    ",.2f"),
    ("금",            "GC=F",      "$",    ",.0f"),
    ("구리",          "HG=F",      "$/lb", ".3f"),
    ("리튬 ETF(LIT)", "LIT",       "$",    ",.2f"),
    ("SOX(반도체)",   "^SOX",      "pt",   ",.0f"),
]

TRADE_CATS = {
    "반도체":   ["8542"],
    "자동차":   ["8703"],
    "선박":     ["8901", "8902"],
    "2차전지":  ["8507"],
    "변압기":   ["8504"],
    "화장품":   ["3304", "3305", "3306"],
}

_API_BASE = "https://apis.data.go.kr/1220000/Itemtrade/getItemtradeList"


# ── 데이터 함수 ───────────────────────────────────────────────────────────────
@st.cache_data(ttl=300, show_spinner=False)
def _get_price_history(ticker: str, period: str = "1y") -> pd.DataFrame | None:
    try:
        import yfinance as yf
        hist = yf.Ticker(ticker).history(period=period)
        if hist.empty:
            return None
        df = hist[["Close"]].rename(columns={"Close": "price"})
        if df.index.tz is not None:
            df.index = df.index.tz_convert(None)
        return df
    except Exception:
        return None



def _api_call(api_key: str, hs_codes: list, year: int, month: int) -> float:
    """
    관세청_품목별 수출입실적(GW) 호출.
    strtYymm/endYymm = YYYYMM, hsCd = HS 코드 앞 4자리.
    응답은 XML 전용. 수출금액 필드: expDlr (달러).
    여러 HS코드 합산 반환 (백만$).
    """
    yymm = f"{year}{month:02d}"
    total = 0.0
    for hs in hs_codes:
        try:
            r = requests.get(
                _API_BASE,
                params={
                    "serviceKey": api_key,
                    "strtYymm": yymm,
                    "endYymm": yymm,
                    "hsCd": hs,
                    "numOfRows": 9999,
                    "pageNo": 1,
                },
                timeout=15,
            )
            if r.status_code != 200:
                continue
            root = ET.fromstring(r.text)
            for item in root.findall(".//item"):
                v = item.findtext("expDlr") or "0"
                total += float(v.replace(",", "") or 0)
        except Exception:
            pass
    return total / 1_000_000


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
        return year, month, i, _api_call(api_key, hs_codes, year, month)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(_fetch, month_list))

    rows = []
    for year, month, i, total_M in sorted(results, key=lambda x: (x[0], x[1])):
        is_cur = (year == now.year and month == now.month)
        rows.append(dict(
            label=f"{str(year)[2:]}년{month:02d}월",
            year=year, month=month,
            d10=0.0, d20=0.0, d30=0.0,
            total=total_M, is_partial=is_cur,
        ))

    return pd.DataFrame(rows)


# ── 스파크라인 헬퍼 ─────────────────────────────────────────────────────────
def _make_sparkline(hist: pd.DataFrame, unit: str, fmt: str, period: str) -> go.Figure:
    prices = hist["price"]
    last  = float(prices.iloc[-1])
    first = float(prices.iloc[0])
    is_up = last >= first
    color  = "#ef5350" if is_up else "#1565C0"
    fill_c = "rgba(239,83,80,0.12)" if is_up else "rgba(21,101,192,0.12)"

    min_val = float(prices.min())
    max_val = float(prices.max())
    rng = (max_val - min_val) or abs(max_val) * 0.02 or 1.0
    base_y = min_val - rng * 0.1
    top_y  = max_val + rng * 0.1

    shapes: list = []
    seen_ym: set = set()
    for dt in pd.DatetimeIndex(hist.index):
        ym = (dt.year, dt.month)
        if ym not in seen_ym:
            seen_ym.add(ym)
            if len(seen_ym) > 1:
                shapes.append(dict(
                    type="line", xref="x", yref="paper",
                    x0=dt, x1=dt, y0=0, y1=1,
                    line=dict(color="rgba(150,150,150,0.22)", width=1, dash="dot"),
                ))

    dtick = {"1mo": "W1", "3mo": "M1", "6mo": "M1", "1y": "M2"}.get(period, "M1")
    tfmt  = "%d일" if period == "1mo" else "%m월"

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=hist.index, y=[base_y] * len(hist),
        mode="lines", line=dict(width=0),
        showlegend=False, hoverinfo="skip",
    ))
    fig.add_trace(go.Scatter(
        x=hist.index, y=prices,
        mode="lines",
        line=dict(color=color, width=1.5),
        fill="tonexty", fillcolor=fill_c,
        showlegend=False,
        hovertemplate=f"%{{x|%m/%d}}<br>%{{y:{fmt}}} {unit}<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=[hist.index[-1]], y=[last],
        mode="markers", marker=dict(color=color, size=4),
        showlegend=False, hoverinfo="skip",
    ))

    fig.update_layout(
        height=90,
        margin=dict(l=0, r=2, t=2, b=18),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        dragmode=False,
        shapes=shapes,
        hovermode="x",
        xaxis=dict(showticklabels=True, tickformat=tfmt, dtick=dtick,
                   tickfont=dict(size=7, color="#aaa"), ticklen=0,
                   showgrid=False, zeroline=False),
        yaxis=dict(range=[base_y, top_y], showticklabels=False,
                   showgrid=False, zeroline=False),
    )
    return fig


# ── 차트 헬퍼 ────────────────────────────────────────────────────────────────
def _render_monthly_chart(trend_df: pd.DataFrame, cat_sel: str, _: bool, now: datetime):
    hist_mask  = (trend_df["d10"] == 0) & (~trend_df["is_partial"])
    brkdn_mask = trend_df["d10"] > 0

    fig = go.Figure()

    if hist_mask.any():
        hdf = trend_df[hist_mask]
        fig.add_trace(go.Bar(
            x=hdf["label"], y=hdf["total"],
            name="수출 확정치", marker_color="#90CAF9",
            hovertemplate="%{x}<br><b>$%{y:,.1f}M</b><extra>확정</extra>",
        ))

    if brkdn_mask.any():
        bdf = trend_df[brkdn_mask]
        fig.add_trace(go.Bar(
            x=bdf["label"], y=bdf["d10"],
            name="1-10일", marker_color="#66BB6A",
            hovertemplate="%{x} 1-10일<br><b>$%{y:,.1f}M</b><extra></extra>",
        ))
        fig.add_trace(go.Bar(
            x=bdf["label"], y=bdf["d20"].where(bdf["d20"] > 0, other=None),
            name="11-20일", marker_color="#FFA726",
            hovertemplate="%{x} 11-20일<br><b>$%{y:,.1f}M</b><extra></extra>",
        ))
        fig.add_trace(go.Bar(
            x=bdf["label"], y=bdf["d30"].where(bdf["d30"] > 0, other=None),
            name="21-말일", marker_color="#26A69A",
            hovertemplate="%{x} 21-말일<br><b>$%{y:,.1f}M</b><extra></extra>",
        ))

    td = {(r["year"], r["month"]): r["total"] for _, r in trend_df.iterrows()}
    for _, row in trend_df.iterrows():
        prev = td.get((row["year"] - 1, row["month"]), 0)
        if prev <= 0 or row["total"] <= 0:
            continue
        yoy = (row["total"] / prev - 1) * 100
        clr = "#ef5350" if yoy > 0 else "#1565C0"
        fig.add_annotation(
            x=row["label"], y=row["total"],
            text=f"{yoy:+.0f}%", showarrow=False,
            font=dict(size=8, color=clr), yanchor="bottom", yshift=2,
        )

    cur_rows = trend_df[trend_df["is_partial"]]
    if not cur_rows.empty:
        fig.add_annotation(
            x=cur_rows.iloc[-1]["label"], y=cur_rows.iloc[-1]["total"],
            text=f"({now.month}월 {now.day}일까지)", showarrow=False,
            font=dict(size=8, color="#999"), yanchor="bottom", yshift=14,
        )

    fig.update_layout(
        barmode="stack", height=420,
        title=dict(text=f"{cat_sel} 월별 수출", font=dict(size=13), x=0),
        xaxis=dict(tickfont=dict(size=9), tickangle=-45, showgrid=False),
        yaxis=dict(title="수출금액 (백만$)", tickformat=",.0f",
                   showgrid=True, gridcolor="rgba(200,200,200,0.25)"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1, font=dict(size=10)),
        margin=dict(l=0, r=10, t=50, b=80),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", dragmode=False,
    )
    st.plotly_chart(fig, use_container_width=True,
                    config={"displayModeBar": False, "scrollZoom": True})


def _render_quarterly_chart(trend_df: pd.DataFrame, cat_sel: str, _: bool, now: datetime):
    df = trend_df.copy()
    df["quarter"] = ((df["month"] - 1) // 3 + 1).astype(int)
    df["q_label"] = df.apply(lambda r: f"{str(r['year'])[2:]}년Q{r['quarter']}", axis=1)

    qdf = (
        df.groupby(["year", "quarter", "q_label"], as_index=False)
        .agg(total=("total", "sum"), is_partial=("is_partial", "any"),
             d10=("d10", "sum"), d20=("d20", "sum"), d30=("d30", "sum"))
        .sort_values(["year", "quarter"])
        .reset_index(drop=True)
    )

    hist_mask  = (qdf["d10"] == 0) & (~qdf["is_partial"])
    brkdn_mask = qdf["d10"] > 0

    fig = go.Figure()

    if hist_mask.any():
        hdf = qdf[hist_mask]
        fig.add_trace(go.Bar(
            x=hdf["q_label"], y=hdf["total"],
            name="수출 확정치", marker_color="#90CAF9",
            hovertemplate="%{x}<br><b>$%{y:,.1f}M</b><extra>확정</extra>",
        ))

    if brkdn_mask.any():
        bdf = qdf[brkdn_mask]
        fig.add_trace(go.Bar(
            x=bdf["q_label"], y=bdf["d10"],
            name="1-10일 누계", marker_color="#66BB6A",
            hovertemplate="%{x} 1-10일<br><b>$%{y:,.1f}M</b><extra></extra>",
        ))
        fig.add_trace(go.Bar(
            x=bdf["q_label"], y=bdf["d20"].where(bdf["d20"] > 0, other=None),
            name="11-20일 누계", marker_color="#FFA726",
            hovertemplate="%{x} 11-20일<br><b>$%{y:,.1f}M</b><extra></extra>",
        ))
        fig.add_trace(go.Bar(
            x=bdf["q_label"], y=bdf["d30"].where(bdf["d30"] > 0, other=None),
            name="21-말일 누계", marker_color="#26A69A",
            hovertemplate="%{x} 21-말일<br><b>$%{y:,.1f}M</b><extra></extra>",
        ))

    # YoY% 분기 기준
    qtd = {(r["year"], r["quarter"]): r["total"] for _, r in qdf.iterrows()}
    for _, row in qdf.iterrows():
        prev = qtd.get((row["year"] - 1, row["quarter"]), 0)
        if prev <= 0 or row["total"] <= 0:
            continue
        yoy = (row["total"] / prev - 1) * 100
        clr = "#ef5350" if yoy > 0 else "#1565C0"
        fig.add_annotation(
            x=row["q_label"], y=row["total"],
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
        title=dict(text=f"{cat_sel} 분기별 수출", font=dict(size=13), x=0),
        xaxis=dict(tickfont=dict(size=10), tickangle=-30, showgrid=False),
        yaxis=dict(title="수출금액 (백만$)", tickformat=",.0f",
                   showgrid=True, gridcolor="rgba(200,200,200,0.25)"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1, font=dict(size=10)),
        margin=dict(l=0, r=10, t=50, b=60),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", dragmode=False,
    )
    st.plotly_chart(fig, use_container_width=True,
                    config={"displayModeBar": False, "scrollZoom": True})


# ── 렌더링 ────────────────────────────────────────────────────────────────────
def render_macro():
    st.markdown(
        "<div style='font-size:1.4rem;font-weight:bold;margin-bottom:4px;'>🌐 매크로 지표</div>",
        unsafe_allow_html=True,
    )
    st.caption("시장 지표 5분 자동갱신 · 수출 데이터 1시간 캐시")

    tab_mkt, tab_trade = st.tabs(["📊 시장 지표", "🚢 수출 동향"])

    # ══════════════════════════════════════════════════════════════════════════
    # TAB 1 — 시장 지표
    # ══════════════════════════════════════════════════════════════════════════
    with tab_mkt:
        period_map = {"1개월": "1mo", "3개월": "3mo", "6개월": "6mo", "1년": "1y"}
        sel_p = st.radio("기간", list(period_map.keys()), index=3,
                          horizontal=True, key="macro_period")
        period = period_map[sel_p]

        with st.spinner("시장 데이터 로딩 중..."):
            hists = {name: _get_price_history(ticker, period)
                     for name, ticker, *_ in MARKET_ITEMS}

        for row_start in (0, 4):
            cols = st.columns(4)
            for ci, (name, ticker, unit, fmt) in enumerate(MARKET_ITEMS[row_start:row_start + 4]):
                hist = hists[name]
                with cols[ci]:
                    if hist is None or hist.empty:
                        st.markdown(
                            f"<div style='padding:4px 0 8px;'>"
                            f"<div style='font-size:11px;color:#888;'>{name}</div>"
                            f"<div style='font-size:18px;font-weight:700;color:#ccc;'>N/A</div>"
                            f"</div>",
                            unsafe_allow_html=True,
                        )
                        continue
                    last  = float(hist["price"].iloc[-1])
                    prev  = float(hist["price"].iloc[-2]) if len(hist) > 1 else last
                    chg_p = (last / prev - 1) * 100 if prev else 0
                    try:
                        val_str = f"{last:{fmt}} {unit}"
                    except Exception:
                        val_str = f"{last:.2f} {unit}"
                    clr   = "#ef5350" if chg_p > 0 else "#1565C0" if chg_p < 0 else "#888"
                    arrow = "▲" if chg_p > 0 else "▼" if chg_p < 0 else "─"
                    st.markdown(
                        f"<div style='padding:4px 0 2px;'>"
                        f"<div style='font-size:11px;color:#888;margin-bottom:1px;'>{name}</div>"
                        f"<div style='font-size:18px;font-weight:700;line-height:1.2;'>{val_str}</div>"
                        f"<div style='font-size:12px;color:{clr};margin-top:2px;'>"
                        f"{arrow} {chg_p:+.2f}% 전일</div>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
                    st.plotly_chart(
                        _make_sparkline(hist, unit, fmt, period),
                        use_container_width=True,
                        config={"displayModeBar": False, "scrollZoom": True, "staticPlot": False},
                    )

        _, cr = st.columns([9, 1.5])
        with cr:
            if st.button("🔄 새로고침", key="macro_mkt_refresh", use_container_width=True):
                _get_price_history.clear()
                st.rerun()

    # ══════════════════════════════════════════════════════════════════════════
    # TAB 2 — 수출 동향
    # ══════════════════════════════════════════════════════════════════════════
    with tab_trade:
        has_key = bool(st.secrets.get("DATA_GO_KR_KEY", ""))

        if not has_key:
            st.warning(
                "수출 데이터를 표시하려면 **관세청 공공데이터포털 API 키**가 필요합니다.\n\n"
                "1. [data.go.kr](https://www.data.go.kr) 에서 **관세청_통관기준 수출입 실적** API 신청\n"
                "2. 발급된 키를 Streamlit Cloud 시크릿에 추가: `DATA_GO_KR_KEY = \"발급받은키\"`"
            )
            st.stop()

        st.markdown("#### 📈 품목별 수출 추세")

        cc1, cc2, cc3 = st.columns([2, 2, 2])
        with cc1:
            cat_sel = st.selectbox("품목", list(TRADE_CATS.keys()), key="export_cat")
        with cc2:
            n_mo = st.selectbox("기간", [12, 18, 24], index=1, key="export_nmo")
        with cc3:
            view_mode = st.radio("단위", ["월별", "분기별"], horizontal=True, key="export_view")

        with st.spinner(f"{cat_sel} 추세 데이터 로딩 중..."):
            trend_df = _get_export_trend(cat_sel, n_mo)

        now = datetime.today()

        if trend_df is None or trend_df.empty:
            st.error("API에서 데이터를 가져오지 못했습니다. API 키와 네트워크 상태를 확인해주세요.")
        elif view_mode == "월별":
            _render_monthly_chart(trend_df, cat_sel, False, now)
        else:
            _render_quarterly_chart(trend_df, cat_sel, False, now)
