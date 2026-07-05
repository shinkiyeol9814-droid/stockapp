"""
ui_macro.py — 매크로 지표 & 수출입 동향 탭
"""
import streamlit as st
import requests
import pandas as pd
import plotly.graph_objects as go
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

_DEMO_EXPORT = {
    "반도체":  10_200,
    "자동차":   6_100,
    "선박":     1_800,
    "2차전지":    650,
    "화장품":     810,
    "변압기":     180,
}

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


@st.cache_data(ttl=3600, show_spinner=False)
def _get_trade_data(year: int, month: int) -> tuple[dict, bool]:
    api_key = st.secrets.get("DATA_GO_KR_KEY", "")
    if not api_key:
        return _DEMO_EXPORT, False

    results: dict[str, float] = {}
    base = "https://apis.data.go.kr/1220000/mtitm3/getExptRtm"

    for cat, hs_list in TRADE_CATS.items():
        total = 0.0
        for hs in hs_list:
            try:
                r = requests.get(
                    base,
                    params={
                        "serviceKey": api_key,
                        "year": str(year),
                        "month": f"{month:02d}",
                        "smitm": hs,
                        "numOfRows": 100,
                        "pageNo": 1,
                        "_type": "json",
                    },
                    timeout=10,
                )
                if r.status_code == 200:
                    body = r.json().get("response", {}).get("body", {})
                    items = body.get("items", {}).get("item", [])
                    if isinstance(items, dict):
                        items = [items]
                    for it in items:
                        v = it.get("exptUsd") or it.get("expUsd") or 0
                        total += float(str(v).replace(",", "") or 0)
            except Exception:
                pass
        results[cat] = total / 1_000_000

    if all(v == 0 for v in results.values()):
        return _DEMO_EXPORT, False
    return results, True


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

    # 월 경계 세로선
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

    # 기간별 x축 월 눈금 간격
    dtick = {"1mo": "W1", "3mo": "M1", "6mo": "M1", "1y": "M2"}.get(period, "M1")
    tfmt  = "%d일" if period == "1mo" else "%m월"

    fig = go.Figure()
    # 동적 Y 범위 — 0 고정 대신 데이터 범위에 맞는 베이스라인으로 tonexty 채움
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
        dragmode=False,       # 드래그 비활성
        shapes=shapes,
        hovermode="x",
        xaxis=dict(
            showticklabels=True,
            tickformat=tfmt,
            dtick=dtick,
            tickfont=dict(size=7, color="#aaa"),
            ticklen=0,
            showgrid=False,
            zeroline=False,
        ),
        yaxis=dict(
            range=[base_y, top_y],   # 동적 Y 범위
            showticklabels=False,
            showgrid=False,
            zeroline=False,
        ),
    )
    return fig


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
        sel_p = st.radio("기간", list(period_map.keys()), index=3,   # 1년 기본
                          horizontal=True, key="macro_period")
        period = period_map[sel_p]

        with st.spinner("시장 데이터 로딩 중..."):
            hists = {name: _get_price_history(ticker, period)
                     for name, ticker, *_ in MARKET_ITEMS}

        # ── 4열 × 2행 카드: 수치 바로 아래에 스파크라인 ─────────────────────
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
                        config={
                            "displayModeBar": False,
                            "scrollZoom": True,   # 휠 확대 허용
                            "staticPlot": False,
                        },
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
        now = datetime.today()
        col_y, col_m, col_note = st.columns([1, 1, 6])
        with col_y:
            sel_year = st.selectbox("연도", [now.year, now.year - 1, now.year - 2],
                                    key="macro_year")
        with col_m:
            default_month = now.month - 1 if now.month > 1 else 12
            sel_month = st.selectbox("월", list(range(1, 13)),
                                     index=default_month - 1,
                                     key="macro_month")

        with st.spinner("수출 데이터 로딩 중..."):
            trade_data, is_real = _get_trade_data(sel_year, sel_month)

        with col_note:
            st.write("")
            if is_real:
                st.success("✅ 공공데이터포털 실시간 데이터")
            elif st.secrets.get("DATA_GO_KR_KEY", ""):
                st.warning("⚠️ API 응답 없음 — 예시 데이터 표시 중")
            else:
                st.info("📌 예시 데이터 · `DATA_GO_KR_KEY` 설정 시 실데이터로 전환")

        df = (
            pd.DataFrame([{"품목": k, "수출금액 (백만$)": v}
                           for k, v in trade_data.items()])
            .sort_values("수출금액 (백만$)", ascending=False)
            .reset_index(drop=True)
        )
        median_v = df["수출금액 (백만$)"].median()
        bar_colors = ["#1565C0" if v >= median_v else "#90CAF9"
                      for v in df["수출금액 (백만$)"]]

        fig_t = go.Figure(go.Bar(
            x=df["품목"],
            y=df["수출금액 (백만$)"],
            marker_color=bar_colors,
            text=[f"${v:,.0f}M" for v in df["수출금액 (백만$)"]],
            textposition="outside",
        ))
        fig_t.update_layout(
            height=360,
            title=dict(
                text=f"{sel_year}년 {sel_month}월 품목별 수출{'  (예시)' if not is_real else ''}",
                font=dict(size=14), x=0, y=0.98,
            ),
            yaxis_title="백만 달러 (USD)",
            yaxis=dict(tickformat=",.0f"),
            margin=dict(l=0, r=10, t=55, b=10),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            dragmode="pan",
        )
        st.plotly_chart(fig_t, use_container_width=True,
                        config={"scrollZoom": True, "displayModeBar": "hover",
                                "modeBarButtonsToRemove": ["select2d", "lasso2d", "zoom2d"]})

        df_show = df.copy()
        df_show.index = df_show.index + 1
        df_show["수출금액 (백만$)"] = df_show["수출금액 (백만$)"].map(lambda x: f"${x:,.0f}M")
        st.dataframe(df_show, use_container_width=True)

        with st.expander("📋 데이터 출처 & 15일 단위 수출 현황 안내"):
            col_a, col_b = st.columns(2)
            with col_a:
                st.markdown("""
**🔑 실데이터 설정 방법**

1. [data.go.kr](https://www.data.go.kr) 회원가입 후 로그인
2. **관세청_통관기준 수출입 실적** API 신청
3. 발급받은 `serviceKey`를 Streamlit Cloud 시크릿에 추가:
```
DATA_GO_KR_KEY = "발급받은키..."
```
4. 앱 재시작 후 자동 전환

**품목 기준 (HS코드)**
- 반도체: 8542 (집적회로)
- 2차전지: 8507 (축전지)
- 변압기: 8504
- 화장품: 3304·3305·3306
- 자동차: 8703
- 선박: 8901·8902
""")
            with col_b:
                st.markdown("""
**📅 관세청 수출 현황 발표 주기**

| 발표 시점 | 집계 기간 |
|-----------|-----------|
| 매월 ~5일 | 전월 1–25일 |
| 매월 11일 | 당월 **1–10일** |
| 매월 16일 | 당월 **1–15일** |
| 매월 21일 | 당월 **1–20일** |
| 익월 초   | 전월 전체 |

15일 단위 API 파라미터:
```
year=2025&month=07
→ 당월 누적 수출 반환
```
관세청 보도자료:
[customs.go.kr](https://www.customs.go.kr)
""")
