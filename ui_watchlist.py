import streamlit as st
import requests
import json
import base64
import pandas as pd
import numpy as np

GITHUB_REPO   = "shinkiyeol9814-droid/stockapp"
GITHUB_BRANCH = "main"
WATCHLIST_FILE = "data/watchlist/watchlist.json"
HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

METHODS = ["PER", "PBR", "PSR", "EV/EBITDA"]
DEFAULT_MULT = {"PER": 12.0, "PBR": 1.5, "PSR": 1.0, "EV/EBITDA": 8.0}
METRIC_LABEL = {"PER": "EPS", "PBR": "BPS", "PSR": "SPS", "EV/EBITDA": "EBIT/주"}

# ── GitHub 저장소 ────────────────────────────────────────
def _gh_hdrs():
    tok = st.secrets.get("GH_PAT") or st.secrets.get("GITHUB_TOKEN", "")
    return {"Authorization": f"token {tok}", "Accept": "application/vnd.github.v3+json"}

@st.cache_data(ttl=60, show_spinner=False)
def load_watchlist() -> dict:
    try:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{WATCHLIST_FILE}?ref={GITHUB_BRANCH}"
        res = requests.get(url, headers=_gh_hdrs(), timeout=7)
        if res.status_code == 200:
            return json.loads(base64.b64decode(res.json()["content"]).decode("utf-8"))
    except:
        pass
    return {}

def save_watchlist(data: dict) -> bool:
    load_watchlist.clear()
    content = json.dumps(data, ensure_ascii=False, indent=2)
    b64 = base64.b64encode(content.encode()).decode()
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{WATCHLIST_FILE}"
    hdrs = _gh_hdrs()
    res = requests.get(url, headers=hdrs, params={"ref": GITHUB_BRANCH}, timeout=7)
    sha = res.json().get("sha") if res.status_code == 200 else None
    payload = {"message": "Update watchlist", "content": b64, "branch": GITHUB_BRANCH}
    if sha:
        payload["sha"] = sha
    r = requests.put(url, headers=hdrs, json=payload, timeout=10)
    return r.status_code in [200, 201]

# ── 주가/재무 데이터 수집 ─────────────────────────────────
@st.cache_data(ttl=60, show_spinner=False)
def fetch_metrics(code: str) -> dict:
    def p(s):
        try:
            v = float(str(s or "").replace(",", "").strip())
            return v if v != 0 else None
        except:
            return None

    m = {"price": None, "eps": None, "bps": None,
         "sps": None, "ebitda_ps": None, "name": ""}

    # 현재가 · EPS · BPS
    try:
        data = requests.get(
            f"https://m.stock.naver.com/api/stock/{code}/basic",
            headers=HDR, timeout=5
        ).json()
        m["price"] = p(data.get("closePrice"))
        m["eps"]   = p(data.get("eps"))
        m["bps"]   = p(data.get("bps"))
        m["name"]  = data.get("stockName") or data.get("corporateName", "")
    except:
        pass

    # 발행주식수 (SPS · EV/EBITDA 계산용)
    shares = None
    try:
        data = requests.get(
            f"https://m.stock.naver.com/api/stock/{code}/integration",
            headers=HDR, timeout=5
        ).json()
        sc = data.get("stockEndType", {}).get("totalInfo", {}).get("stockCount")
        if sc:
            shares = int(str(sc).replace(",", ""))
    except:
        pass

    # 매출액 · 영업이익 (억원 단위) → 주당으로 변환
    if shares and shares > 0:
        try:
            data = requests.get(
                f"https://m.stock.naver.com/api/stock/{code}/finance/annual",
                headers=HDR, timeout=5
            ).json()
            rows = {}
            for item in data.get("financeInfo", []):
                vals = [v for v in (item.get("value") or []) if v is not None]
                if vals:
                    rows[item["financeName"]] = vals[-1]  # 가장 최근 실적

            rev = rows.get("매출액")    # 억원
            oi  = rows.get("영업이익")  # 억원
            if rev:
                m["sps"]       = rev * 1e8 / shares   # 주당매출
            if oi:
                m["ebitda_ps"] = oi  * 1e8 / shares   # 주당영업이익(EBIT 근사)
        except:
            pass

    return m

# ── 목표주가 계산 ─────────────────────────────────────────
def calc_target(method: str, multiple: float, m: dict):
    try:
        key = {"PER": "eps", "PBR": "bps", "PSR": "sps", "EV/EBITDA": "ebitda_ps"}[method]
        base = m.get(key)
        if base and base > 0:
            return base * multiple
    except:
        pass
    return None

# ── 메인 렌더링 ───────────────────────────────────────────
def render_watchlist():
    st.markdown(
        "<div style='font-size:1.4rem;font-weight:bold;margin-bottom:12px;'>📋 밸류 워치리스트</div>",
        unsafe_allow_html=True,
    )

    watchlist = load_watchlist()

    # ── 종목 추가 ──────────────────────────────────────────
    from valuation import get_ticker_listing
    listing = get_ticker_listing()

    with st.expander("➕ 종목 추가", expanded=len(watchlist) == 0):
        c1, c2, c3 = st.columns([3, 2, 1])
        with c1:
            name_q = st.text_input("종목명 검색", key="wl_q", placeholder="삼성전자, SK하이닉스...")
        filtered = (
            listing[listing["Name"].str.contains(name_q, case=False, na=False)]
            if name_q else listing.iloc[:0]
        )
        opts = [f"{r['Name']} ({r['Code']})" for _, r in filtered.head(10).iterrows()]
        with c2:
            chosen = st.selectbox("종목 선택", [""] + opts, key="wl_pick")
        with c3:
            st.write("")
            st.write("")
            add_clicked = st.button("추가", type="primary", key="wl_add", use_container_width=True)

        if add_clicked and chosen:
            code = chosen.split("(")[-1].rstrip(")")
            if code not in watchlist:
                watchlist[code] = {"method": "PER", "multiple": DEFAULT_MULT["PER"]}
                if save_watchlist(watchlist):
                    st.success(f"✅ {chosen.split('(')[0].strip()} 추가됨")
                    st.rerun()
                else:
                    st.error("저장 실패 (GH_PAT 확인)")
            else:
                st.warning("이미 추가된 종목입니다.")

    if not watchlist:
        st.info("종목을 추가해주세요.")
        return

    # ── 실시간 데이터 수집 ─────────────────────────────────
    with st.spinner(f"{len(watchlist)}개 종목 데이터 수집 중..."):
        metrics = {code: fetch_metrics(code) for code in watchlist}

    # ── DataFrame 구성 ─────────────────────────────────────
    rows = []
    for code, cfg in watchlist.items():
        m        = metrics.get(code, {})
        method   = cfg.get("method", "PER")
        multiple = float(cfg.get("multiple", DEFAULT_MULT.get(method, 10.0)))
        price    = m.get("price")

        # 기준값: 저장된 값 우선, 없으면 API 자동
        base_key    = {"PER": "eps", "PBR": "bps", "PSR": "sps", "EV/EBITDA": "ebitda_ps"}[method]
        api_base    = m.get(base_key)
        stored_base = cfg.get("base_val")
        base_val    = stored_base if stored_base else api_base

        # 목표주가 / 업사이드
        target = (base_val * multiple) if (base_val and base_val > 0) else None
        upside = ((target / price - 1) * 100) if (target and price and price > 0) else None

        # 현재 배수 (참고용)
        cur_mult = (price / base_val) if (price and base_val and base_val > 0) else None

        rows.append({
            "_code":       code,
            "종목명":      m.get("name") or code,
            "현재가":      int(price)         if price    else None,
            "평가방식":    method,
            "현재배수":    round(cur_mult, 1)  if cur_mult else None,
            "목표배수":    multiple,
            "기준값":      float(round(base_val)) if base_val else None,
            "목표주가":    int(target)         if target   else None,
            "업사이드(%)": round(upside, 1)    if upside is not None else None,
            "_del":        False,
        })

    df = pd.DataFrame(rows)

    st.caption(
        "기준값(EPS/BPS/SPS/EBITDA)은 직접 입력하거나 API 자동값을 사용합니다. "
        "평가방식·목표배수·기준값 수정 후 💾 저장을 눌러주세요."
    )

    edited = st.data_editor(
        df,
        column_config={
            "_code":       None,
            "종목명":      st.column_config.TextColumn("종목명",    width="small",  disabled=True),
            "현재가":      st.column_config.NumberColumn("현재가",  format="%d원",  disabled=True),
            "평가방식":    st.column_config.SelectboxColumn("평가방식", options=METHODS, width="small"),
            "현재배수":    st.column_config.NumberColumn("현재배수", format="%.1f×", disabled=True,
                                                        help="현재 주가 ÷ 기준값"),
            "목표배수":    st.column_config.NumberColumn("목표배수", format="%.1f×",
                                                        min_value=0.1, max_value=200.0, step=0.5),
            "기준값":      st.column_config.NumberColumn(
                                "기준값 ✏️",
                                format="%d원",
                                min_value=0.0,
                                help="직접 입력 — PER:EPS  PBR:BPS  PSR:주당매출  EV/EBITDA:주당영업이익(원 단위)",
                            ),
            "목표주가":    st.column_config.NumberColumn("목표주가", format="%d원",  disabled=True),
            "업사이드(%)": st.column_config.NumberColumn("업사이드", format="%.1f%%", disabled=True),
            "_del":        st.column_config.CheckboxColumn("삭제",  width="small"),
        },
        hide_index=True,
        use_container_width=True,
        key="wl_editor",
    )

    # 편집된 기준값·배수로 목표주가/업사이드 즉시 재계산해 아래에 표시
    recalc_rows = []
    for _, row in edited.iterrows():
        bv     = row["기준값"]
        mult   = row["목표배수"]
        price  = row["현재가"]
        target = (bv * mult) if (bv and bv > 0 and mult) else None
        upside = ((target / price - 1) * 100) if (target and price and price > 0) else None
        recalc_rows.append({
            "종목명":      row["종목명"],
            "현재가":      price,
            "평가방식":    row["평가방식"],
            "기준값":      round(bv)      if bv     else None,
            "목표배수":    mult,
            "목표주가":    int(target)    if target else None,
            "업사이드(%)": round(upside, 1) if upside is not None else None,
        })
    recalc_df = pd.DataFrame(recalc_rows)

    if recalc_df["목표주가"].notna().any():
        st.markdown("**실시간 계산 결과** (저장 전 미리보기)")
        st.dataframe(
            recalc_df,
            column_config={
                "현재가":      st.column_config.NumberColumn(format="%d원"),
                "기준값":      st.column_config.NumberColumn(format="%d원"),
                "목표배수":    st.column_config.NumberColumn(format="%.1f×"),
                "목표주가":    st.column_config.NumberColumn(format="%d원"),
                "업사이드(%)": st.column_config.NumberColumn(format="%.1f%%"),
            },
            hide_index=True,
            use_container_width=True,
        )

    # ── 저장 / 삭제 ────────────────────────────────────────
    c_save, c_rfr, _ = st.columns([1.5, 1.5, 5])

    with c_save:
        if st.button("💾 저장", type="primary", use_container_width=True):
            new_wl = {}
            for _, row in edited.iterrows():
                if not row["_del"]:
                    entry = {
                        "method":   row["평가방식"],
                        "multiple": float(row["목표배수"]),
                    }
                    # 기준값이 입력된 경우 저장 (0이나 None이면 저장 안 함)
                    bv = row.get("기준값")
                    if bv and float(bv) > 0:
                        entry["base_val"] = float(bv)
                    new_wl[row["_code"]] = entry
            if save_watchlist(new_wl):
                st.success("저장됐습니다.")
                st.rerun()
            else:
                st.error("저장 실패")

    with c_rfr:
        if st.button("🔄 새로고침", use_container_width=True):
            fetch_metrics.clear()
            st.rerun()

    # ── 업사이드 요약 차트 ─────────────────────────────────
    disp = edited[edited["업사이드(%)"].notna()].copy()
    if not disp.empty:
        st.divider()
        st.markdown("#### 업사이드 요약")
        disp_sorted = disp.sort_values("업사이드(%)", ascending=True)
        colors = ["#ef5350" if v < 0 else "#26a69a" for v in disp_sorted["업사이드(%)"]]

        import plotly.graph_objects as go
        fig = go.Figure(go.Bar(
            x=disp_sorted["업사이드(%)"],
            y=disp_sorted["종목명"],
            orientation="h",
            marker_color=colors,
            text=[f"{v:+.1f}%" for v in disp_sorted["업사이드(%)"]],
            textposition="outside",
        ))
        fig.update_layout(
            height=max(200, len(disp_sorted) * 40 + 60),
            margin=dict(l=0, r=60, t=20, b=20),
            xaxis_title="업사이드 (%)",
            yaxis_title="",
            plot_bgcolor="white",
            xaxis=dict(zeroline=True, zerolinecolor="#999"),
        )
        st.plotly_chart(fig, use_container_width=True)
