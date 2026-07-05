"""
ui_macro.py — 매크로 지표 & 수출입 동향 탭
"""
import streamlit as st
import requests
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime

# ── 상수 ─────────────────────────────────────────────────────────────────────
# (표시명, yfinance ticker, 단위, format_spec)
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

# HS코드 기준 수출 품목
TRADE_CATS = {
    "반도체":   ["8542"],
    "자동차":   ["8703"],
    "선박":     ["8901", "8902"],
    "2차전지":  ["8507"],
    "변압기":   ["8504"],
    "화장품":   ["3304", "3305", "3306"],
}

# 실데이터 없을 때 표시할 예시 (2024년 연간 평균 월별 수출 추정치, 백만$)
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
def _get_price_history(ticker: str, period: str = "3mo") -> pd.DataFrame | None:
    """yfinance 가격 이력 (5분 캐시)"""
    try:
        import yfinance as yf
        hist = yf.Ticker(ticker).history(period=period)
        if hist.empty:
            return None
        return hist[["Close"]].rename(columns={"Close": "price"})
    except Exception:
        return None


@st.cache_data(ttl=3600, show_spinner=False)
def _get_trade_data(year: int, month: int) -> tuple[dict, bool]:
    """
    관세청 공공데이터포털 API 수출 실적 조회.
    Returns (data_dict {품목: 백만달러}, is_real)
    DATA_GO_KR_KEY 가 secrets에 없으면 예시 데이터 반환.
    """
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
        results[cat] = total / 1_000_000  # 달러 → 백만달러

    if all(v == 0 for v in results.values()):
        return _DEMO_EXPORT, False
    return results, True


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
        sel_p = st.radio("기간", list(period_map.keys()), index=1,
                          horizontal=True, key="macro_period")
        period = period_map[sel_p]

        # 전체 history 일괄 fetch
        with st.spinner("시장 데이터 로딩 중..."):
            hists = {name: _get_price_history(ticker, period)
                     for name, ticker, *_ in MARKET_ITEMS}

        # ── Metric grid (4열 × 2행) — 한국 색상 규칙: +상승=빨강, -하락=파랑 ──
        cols = st.columns(4)
        for i, (name, ticker, unit, fmt) in enumerate(MARKET_ITEMS):
            hist = hists[name]
            with cols[i % 4]:
                if hist is None or hist.empty:
                    st.markdown(
                        f"<div style='padding:4px 0;'>"
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
                    f"<div style='padding:4px 0;'>"
                    f"<div style='font-size:11px;color:#888;margin-bottom:2px;'>{name}</div>"
                    f"<div style='font-size:18px;font-weight:700;line-height:1.3;'>{val_str}</div>"
                    f"<div style='font-size:12px;color:{clr};margin-top:3px;'>"
                    f"{arrow} {chg_p:+.2f}% 전일</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )

        st.write("")

        # ── Sparkline 서브플롯 (2×4) ────────────────────────────────────────
        fig = make_subplots(
            rows=2, cols=4,
            subplot_titles=[m[0] for m in MARKET_ITEMS],
            vertical_spacing=0.22,
            horizontal_spacing=0.07,
        )
        for i, (name, ticker, unit, fmt) in enumerate(MARKET_ITEMS):
            row, col = i // 4 + 1, i % 4 + 1
            hist = hists[name]
            if hist is None or hist.empty:
                continue
            first = float(hist["price"].iloc[0])
            last  = float(hist["price"].iloc[-1])
            is_up = last >= first
            color = "#ef5350" if is_up else "#1565C0"
            fill  = "rgba(239,83,80,0.10)" if is_up else "rgba(21,101,192,0.10)"

            fig.add_trace(go.Scatter(
                x=hist.index, y=hist["price"],
                mode="lines",
                line=dict(color=color, width=1.5),
                fill="tozeroy", fillcolor=fill,
                showlegend=False,
                hovertemplate=f"%{{x|%m/%d}}<br>%{{y:{fmt}}} {unit}<extra>{name}</extra>",
            ), row=row, col=col)
            # 마지막 포인트 마커
            fig.add_trace(go.Scatter(
                x=[hist.index[-1]], y=[last],
                mode="markers",
                marker=dict(color=color, size=5),
                showlegend=False, hoverinfo="skip",
            ), row=row, col=col)

        fig.update_xaxes(showticklabels=False, showgrid=False, zeroline=False)
        fig.update_yaxes(showgrid=False, tickfont=dict(size=9), zeroline=False)
        for ann in fig.layout.annotations:
            ann.font.size = 11
        fig.update_layout(
            height=420,
            margin=dict(l=0, r=10, t=45, b=5),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig, use_container_width=True,
                        config={"displayModeBar": False, "staticPlot": False,
                                "scrollZoom": False})

        # 새로고침
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

        # ── 차트 ────────────────────────────────────────────────────────────
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
                                "modeBarButtonsToRemove": ["select2d","lasso2d","zoom2d"]})

        # ── 테이블 ──────────────────────────────────────────────────────────
        df_show = df.copy()
        df_show.index = df_show.index + 1
        df_show["수출금액 (백만$)"] = df_show["수출금액 (백만$)"].map(lambda x: f"${x:,.0f}M")
        st.dataframe(df_show, use_container_width=True)

        # ── 안내 ────────────────────────────────────────────────────────────
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
