"""
ui_macro.py — 매크로 지표 & 수출입 동향 탭
"""
import streamlit as st
import requests
import pandas as pd
import plotly.graph_objects as go
import math
import random
import concurrent.futures
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

_DEMO_BASE = {
    "반도체": 10500, "자동차": 6300, "선박": 1900,
    "2차전지": 680,  "화장품": 870,  "변압기": 190,
}

_DEMO_EXPORT = {
    "반도체":  10_200, "자동차": 6_100, "선박": 1_800,
    "2차전지":    650, "화장품":   810, "변압기":  180,
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
                r = requests.get(base, params={
                    "serviceKey": api_key, "year": str(year),
                    "month": f"{month:02d}", "smitm": hs,
                    "numOfRows": 100, "pageNo": 1, "_type": "json",
                }, timeout=10)
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


@st.cache_data(ttl=3600, show_spinner=False)
def _get_export_trend(cat: str, n_months: int = 18) -> pd.DataFrame:
    """
    품목별 월간 수출 추세 (최근 3개월은 10일/20일/말일 구분).
    d10/d20/d30: 각 10일 구간 수출금액 (백만$)
    """
    api_key = st.secrets.get("DATA_GO_KR_KEY", "")
    hs_codes = TRADE_CATS.get(cat, [])
    now = datetime.today()

    # 월 목록 생성 (오래된 순)
    month_list = []
    for i in range(n_months - 1, -1, -1):
        total_m = now.year * 12 + (now.month - 1) - i
        month_list.append((total_m // 12, total_m % 12 + 1, i))

    rows = []

    # ── 예시 데이터 경로 ─────────────────────────────────────────────────────
    if not api_key:
        for year, month, i in month_list:
            is_cur = (i == 0)
            rng = random.Random(abs(hash(f"{cat}{year}{month}")) % (2**31))

            base   = _DEMO_BASE.get(cat, 500)
            seas   = 1.0 + 0.18 * math.sin((month - 5) * math.pi / 6)
            yoy_f  = 1.0 + 0.08 * (year - 2024) + 0.06 * max(0, year - 2025)
            noise  = 1.0 + rng.gauss(0, 0.055)
            full_M = max(10.0, base * seas * yoy_f * noise)

            if is_cur:
                frac = min(now.day / 30, 1.0)
                part = full_M * frac
                d10 = part if now.day <= 10 else full_M * (0.33 + rng.gauss(0, 0.01))
                d20 = 0.0 if now.day <= 10 else (
                    max(0.0, part - d10) if now.day <= 20
                    else full_M * (0.34 + rng.gauss(0, 0.01))
                )
                d30 = 0.0 if now.day <= 20 else max(0.0, part - d10 - d20)
                rows.append(dict(label=f"{str(year)[2:]}년{month:02d}월",
                                 year=year, month=month,
                                 d10=d10, d20=d20, d30=d30,
                                 total=d10+d20+d30, is_partial=True))
            elif i <= 2:  # 최근 2개월: 10일 구간 표기
                d10 = full_M * (0.33 + rng.gauss(0, 0.012))
                d20 = full_M * (0.34 + rng.gauss(0, 0.012))
                d30 = max(0.0, full_M - d10 - d20)
                rows.append(dict(label=f"{str(year)[2:]}년{month:02d}월",
                                 year=year, month=month,
                                 d10=d10, d20=d20, d30=d30,
                                 total=full_M, is_partial=False))
            else:
                rows.append(dict(label=f"{str(year)[2:]}년{month:02d}월",
                                 year=year, month=month,
                                 d10=0.0, d20=0.0, d30=0.0,
                                 total=full_M, is_partial=False))
        return pd.DataFrame(rows)

    # ── 실 API 경로 (병렬 fetch) ─────────────────────────────────────────────
    def _fetch(ym):
        year, month, i = ym
        total = 0.0
        for hs in hs_codes:
            try:
                r = requests.get(
                    "https://apis.data.go.kr/1220000/mtitm3/getExptRtm",
                    params={"serviceKey": api_key, "year": str(year),
                            "month": f"{month:02d}", "smitm": hs,
                            "numOfRows": 100, "pageNo": 1, "_type": "json"},
                    timeout=8,
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
        return year, month, i, total / 1_000_000

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
        results = list(ex.map(_fetch, month_list))

    for year, month, i, total_M in sorted(results, key=lambda x: (x[0], x[1])):
        is_cur = (year == now.year and month == now.month)
        rows.append(dict(label=f"{str(year)[2:]}년{month:02d}월",
                         year=year, month=month,
                         d10=0.0, d20=0.0, d30=0.0,
                         total=total_M, is_partial=is_cur))
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
        is_demo = not bool(st.secrets.get("DATA_GO_KR_KEY", ""))

        # ── 섹션 1: 품목별 월간 추세 ──────────────────────────────────────────
        st.markdown("#### 📈 품목별 수출 추세")

        cc1, cc2, cc3 = st.columns([2, 2, 4])
        with cc1:
            cat_sel = st.selectbox("품목", list(TRADE_CATS.keys()), key="export_cat")
        with cc2:
            n_mo = st.selectbox("기간", [12, 18, 24], index=1, key="export_nmo")
        with cc3:
            st.write("")
            if is_demo:
                st.caption("📌 예시 데이터 — `DATA_GO_KR_KEY` 설정 시 실데이터")

        with st.spinner(f"{cat_sel} 추세 데이터 로딩 중..."):
            trend_df = _get_export_trend(cat_sel, n_mo)

        # 차트 데이터 준비
        # 역할별 마스크
        hist_mask  = (trend_df["d10"] == 0) & (~trend_df["is_partial"])
        brkdn_mask = trend_df["d10"] > 0  # 10일 구간 있는 월 (최근 3개월)

        fig_tr = go.Figure()

        # 확정 단색 바 (과거 월)
        if hist_mask.any():
            hdf = trend_df[hist_mask]
            fig_tr.add_trace(go.Bar(
                x=hdf["label"], y=hdf["total"],
                name="수출 확정치",
                marker_color="#90CAF9",
                hovertemplate="%{x}<br><b>$%{y:,.1f}M</b><extra>확정</extra>",
            ))

        # 스택 바 (10일/20일/말일 구분)
        if brkdn_mask.any():
            bdf = trend_df[brkdn_mask]

            # 1-10일 구간
            fig_tr.add_trace(go.Bar(
                x=bdf["label"], y=bdf["d10"],
                name="1-10일",
                marker_color="#66BB6A",
                hovertemplate="%{x} 1-10일<br><b>$%{y:,.1f}M</b><extra></extra>",
            ))
            # 11-20일 구간 (d20==0이면 None → 바 없음)
            fig_tr.add_trace(go.Bar(
                x=bdf["label"],
                y=bdf["d20"].where(bdf["d20"] > 0, other=None),
                name="11-20일",
                marker_color="#FFA726",
                hovertemplate="%{x} 11-20일<br><b>$%{y:,.1f}M</b><extra></extra>",
            ))
            # 21-말일 구간
            fig_tr.add_trace(go.Bar(
                x=bdf["label"],
                y=bdf["d30"].where(bdf["d30"] > 0, other=None),
                name="21-말일",
                marker_color="#26A69A",
                hovertemplate="%{x} 21-말일<br><b>$%{y:,.1f}M</b><extra></extra>",
            ))

        # YoY% 주석
        td = {(r["year"], r["month"]): r["total"] for _, r in trend_df.iterrows()}
        for _, row in trend_df.iterrows():
            prev = td.get((row["year"] - 1, row["month"]), 0)
            if prev <= 0 or row["total"] <= 0:
                continue
            yoy = (row["total"] / prev - 1) * 100
            clr = "#ef5350" if yoy > 0 else "#1565C0"
            fig_tr.add_annotation(
                x=row["label"], y=row["total"],
                text=f"{yoy:+.0f}%",
                showarrow=False,
                font=dict(size=8, color=clr),
                yanchor="bottom", yshift=2,
            )

        # 현재 월 "진행중" 표시
        cur_rows = trend_df[trend_df["is_partial"]]
        if not cur_rows.empty:
            now = datetime.today()
            fig_tr.add_annotation(
                x=cur_rows.iloc[-1]["label"],
                y=cur_rows.iloc[-1]["total"],
                text=f"({now.month}월 {now.day}일까지)",
                showarrow=False,
                font=dict(size=8, color="#999"),
                yanchor="bottom", yshift=14,
            )

        fig_tr.update_layout(
            barmode="stack",
            height=400,
            title=dict(
                text=f"{cat_sel} 월별 수출{'  (예시)' if is_demo else ''}",
                font=dict(size=13), x=0,
            ),
            xaxis=dict(tickfont=dict(size=9), tickangle=-45, showgrid=False),
            yaxis=dict(title="수출금액 (백만$)", tickformat=",.0f",
                       showgrid=True, gridcolor="rgba(200,200,200,0.25)"),
            legend=dict(orientation="h", yanchor="bottom", y=1.02,
                        xanchor="right", x=1, font=dict(size=10)),
            margin=dict(l=0, r=10, t=50, b=80),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            dragmode=False,
        )
        st.plotly_chart(fig_tr, use_container_width=True,
                        config={"displayModeBar": False, "scrollZoom": True})

        st.markdown("---")

        # ── 섹션 2: 월별 품목 비교 (기존) ────────────────────────────────────
        st.markdown("#### 📊 월별 품목 비교")

        now = datetime.today()
        col_y, col_m, col_note = st.columns([1, 1, 6])
        with col_y:
            sel_year = st.selectbox("연도", [now.year, now.year - 1, now.year - 2],
                                    key="macro_year")
        with col_m:
            default_month = now.month - 1 if now.month > 1 else 12
            sel_month = st.selectbox("월", list(range(1, 13)),
                                     index=default_month - 1, key="macro_month")

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

        fig_m = go.Figure(go.Bar(
            x=df["품목"], y=df["수출금액 (백만$)"],
            marker_color=bar_colors,
            text=[f"${v:,.0f}M" for v in df["수출금액 (백만$)"]],
            textposition="outside",
        ))
        fig_m.update_layout(
            height=320,
            title=dict(
                text=f"{sel_year}년 {sel_month}월 품목별 수출{'  (예시)' if not is_real else ''}",
                font=dict(size=13), x=0, y=0.98,
            ),
            yaxis_title="백만 달러 (USD)",
            yaxis=dict(tickformat=",.0f"),
            margin=dict(l=0, r=10, t=45, b=10),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            dragmode=False,
        )
        st.plotly_chart(fig_m, use_container_width=True,
                        config={"scrollZoom": True, "displayModeBar": False})

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

**품목 기준 (HS코드)**
- 반도체: 8542 · 2차전지: 8507
- 변압기: 8504 · 화장품: 3304~3306
- 자동차: 8703 · 선박: 8901~8902
""")
            with col_b:
                st.markdown("""
**📅 관세청 수출 발표 주기**

| 발표 시점 | 집계 기간 |
|-----------|-----------|
| 매월 11일 | 당월 **1–10일** |
| 매월 21일 | 당월 **1–20일** |
| 익월 초   | 전월 전체 |

**차트 구분**
- 🔵 수출 확정치: 당월 확정 월간 합계
- 🟢 1-10일 · 🟠 11-20일 · 🟦 21-말일: 순별 누적
- 진행중: 현재 월 누적 (오늘까지)
""")
