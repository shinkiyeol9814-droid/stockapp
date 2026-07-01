import streamlit as st
import requests
import json
import base64
import pandas as pd
from datetime import datetime

from valuation import (
    get_hybrid_financials, get_ticker_listing, get_stocks_count,
    UNIT, API_HEADERS, GITHUB_REPO, GITHUB_BRANCH,
)

WATCHLIST_FILE = "data/watchlist/watchlist.json"
METHODS   = ["POR(영업익)", "PER(순이익)", "PBR(자본총계)", "EV/EBITDA"]
COL_MAP   = {
    "POR(영업익)": "영업이익",
    "PER(순이익)": "당기순이익",
    "PBR(자본총계)": "자본총계",
    "EV/EBITDA": "EV/EBITDA",
}
CUR_YEAR  = datetime.today().year
NEXT_YEAR = 2027

# ── GitHub ───────────────────────────────────────────────────────────────────
def _gh_hdrs():
    tok = st.secrets.get("GH_PAT") or st.secrets.get("GITHUB_TOKEN", "")
    return {"Authorization": f"token {tok}", "Accept": "application/vnd.github.v3+json"}

@st.cache_data(ttl=60, show_spinner=False)
def load_watchlist() -> dict:
    try:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{WATCHLIST_FILE}?ref={GITHUB_BRANCH}"
        res = requests.get(url, headers=_gh_hdrs(), timeout=7)
        if res.status_code == 200:
            return json.loads(base64.b64decode(res.json()["content"]).decode())
    except:
        pass
    return {}

def save_watchlist(data: dict) -> bool:
    load_watchlist.clear()
    b64 = base64.b64encode(json.dumps(data, ensure_ascii=False, indent=2).encode()).decode()
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{WATCHLIST_FILE}"
    hdrs = _gh_hdrs()
    res  = requests.get(url, headers=hdrs, params={"ref": GITHUB_BRANCH}, timeout=7)
    sha  = res.json().get("sha") if res.status_code == 200 else None
    payload = {"message": "Update watchlist", "content": b64, "branch": GITHUB_BRANCH}
    if sha:
        payload["sha"] = sha
    r = requests.put(url, headers=hdrs, json=payload, timeout=10)
    return r.status_code in [200, 201]

# ── 데이터 ───────────────────────────────────────────────────────────────────
@st.cache_data(ttl=60, show_spinner=False)
def get_live_price(code: str):
    try:
        data   = requests.get(
            f"https://m.stock.naver.com/api/stock/{code}/basic",
            headers=API_HEADERS, timeout=5
        ).json()
        price  = float(str(data.get("closePrice")  or "0").replace(",", ""))
        if price == 0:
            price = float(str(data.get("stockEndPrice") or "0").replace(",", ""))
        change = float(str(data.get("fluctuationsRatio") or "0").replace(",", ""))
        name   = data.get("stockName") or data.get("corporateName", code)
        return (price if price > 0 else None), change, name
    except:
        return None, None, code

@st.cache_data(ttl=3600, show_spinner=False)
def get_watch_financials(code: str):
    # 반드시 Streamlit 메인 스레드에서 호출 — ThreadPoolExecutor 금지
    try:
        listing    = get_ticker_listing()
        ticker_row = listing[listing["Code"].astype(str).str.zfill(6) == code.zfill(6)]
        if ticker_row.empty:
            return None, 0
        stocks = get_stocks_count(ticker_row, code)
        fin_df = get_hybrid_financials(code)
        return fin_df, int(stocks)
    except:
        return None, 0

# ── 계산 ─────────────────────────────────────────────────────────────────────
def calc_target(fin_df, stocks, method, multiple, curr_price, year):
    if fin_df is None or stocks == 0 or not curr_price:
        return None, None
    col_p = COL_MAP.get(method, "영업이익")
    row   = fin_df[fin_df["Year"] == year]
    if row.empty:
        return None, None
    val = row[col_p].values[0]
    if pd.isna(val) or val <= 0:
        return None, None
    try:
        tp = (curr_price * (multiple / float(val))
              if "EBITDA" in method
              else float(val) * UNIT * multiple / stocks)
        return (tp, (tp / curr_price - 1) * 100) if tp > 0 else (None, None)
    except:
        return None, None

def calc_current_mult(fin_df, stocks, method, curr_price, year):
    if fin_df is None or not curr_price:
        return None
    col_p = COL_MAP.get(method, "영업이익")
    row   = fin_df[fin_df["Year"] == year]
    if row.empty:
        return None
    val = row[col_p].values[0]
    if pd.isna(val) or val <= 0:
        return None
    try:
        if "EBITDA" in method:
            return float(val)
        return (curr_price * stocks) / (float(val) * UNIT)
    except:
        return None

# ── HTML 헬퍼 ─────────────────────────────────────────────────────────────────
def _up_html(upside):
    if upside is None:
        return "<span style='color:#bbb;font-size:12px;'>N/A</span>"
    color = "#ef5350" if upside < 0 else "#26a69a"
    arrow = "▼" if upside < 0 else "▲"
    return (f"<span style='color:{color};font-weight:700;font-size:14px;'>"
            f"{arrow} {upside:+.1f}%</span>")

def _year_html(tp, upside):
    """목표가(회색) + 업사이드(색상)"""
    if tp is None:
        return "<span style='color:#bbb;font-size:12px;'>N/A</span>"
    return (f"<div style='font-size:12px;color:#666;'>{tp:,.0f}원</div>"
            f"<div style='margin-top:2px;'>{_up_html(upside)}</div>")

# ── 렌더링 ───────────────────────────────────────────────────────────────────
def render_watchlist():
    st.markdown(
        "<div style='font-size:1.4rem;font-weight:bold;margin-bottom:4px;'>📋 밸류 워치리스트</div>",
        unsafe_allow_html=True,
    )
    st.caption("↑↓ 핸들로 순서 변경 · 방식·배수 변경 즉시 재계산 · 현재가 60초 · 재무 1시간 캐시")

    watchlist = load_watchlist()

    # ── 종목 추가 ──────────────────────────────────────────────────────────────
    with st.expander("➕ 종목 추가", expanded=len(watchlist) == 0):
        listing = get_ticker_listing()
        c1, c2, c3 = st.columns([3, 2, 1])
        with c1:
            q = st.text_input("종목명", key="wl_q", placeholder="삼성전자, SK하이닉스...")
        filtered = (
            listing[listing["Name"].str.contains(q, case=False, na=False)]
            if q else listing.iloc[:0]
        )
        opts = [f"{r['Name']} ({r['Code']})" for _, r in filtered.head(10).iterrows()]
        with c2:
            chosen = st.selectbox("종목 선택", [""] + opts, key="wl_pick")
        with c3:
            st.write(""); st.write("")
            if st.button("추가", type="primary", use_container_width=True, key="wl_add") and chosen:
                code = chosen.split("(")[-1].rstrip(")")
                if code not in watchlist:
                    updated = {
                        c: {
                            "method":   st.session_state.get(f"wl_m_{c}", cfg.get("method", "POR(영업익)")),
                            "multiple": float(st.session_state.get(f"wl_x_{c}", cfg.get("multiple", 12.0))),
                        }
                        for c, cfg in watchlist.items()
                    }
                    updated[code] = {"method": "POR(영업익)", "multiple": 12.0}
                    if save_watchlist(updated):
                        st.success(f"✅ {chosen.split('(')[0].strip()} 추가")
                        st.rerun()
                    else:
                        st.error("저장 실패 (GH_PAT 확인)")
                else:
                    st.warning("이미 추가된 종목입니다.")

    if not watchlist:
        st.info("종목을 추가해주세요.")
        return

    codes = list(watchlist.keys())

    # ── 세션 상태 초기화 ─────────────────────────────────────────────────────
    for code, cfg in watchlist.items():
        if f"wl_m_{code}" not in st.session_state:
            st.session_state[f"wl_m_{code}"] = cfg.get("method", "POR(영업익)")
        if f"wl_x_{code}" not in st.session_state:
            st.session_state[f"wl_x_{code}"] = float(cfg.get("multiple", 12.0))

    # ── 순차 로딩 (미캐시 종목만 스피너) ──────────────────────────────────────
    uncached = [c for c in codes if f"_wlc_{c}" not in st.session_state]
    if uncached:
        with st.spinner(f"재무 데이터 로딩 중... (신규 {len(uncached)}개 종목)"):
            for code in uncached:
                get_watch_financials(code)
                get_live_price(code)
                st.session_state[f"_wlc_{code}"] = True

    all_data = {}
    for code in codes:
        fin, stocks = get_watch_financials(code)
        price, change, name = get_live_price(code)
        all_data[code] = (fin, stocks, price, change, name)

    # ── 밸류 계산 ────────────────────────────────────────────────────────────
    def _calc(code):
        fin, stocks, price, change, name = all_data[code]
        method = st.session_state.get(f"wl_m_{code}", "POR(영업익)")
        mult   = float(st.session_state.get(f"wl_x_{code}", 12.0))
        tp_c, up_c = calc_target(fin, stocks, method, mult, price, CUR_YEAR)
        tp_n, up_n = calc_target(fin, stocks, method, mult, price, NEXT_YEAR)
        curr_m     = calc_current_mult(fin, stocks, method, price, CUR_YEAR)
        return dict(name=name, price=price, change=change,
                    method=method, mult=mult,
                    tp_c=tp_c, up_c=up_c, tp_n=tp_n, up_n=up_n, curr_m=curr_m)

    vals = {c: _calc(c) for c in codes}

    # ── 컬럼 헤더 (카드 밖, 상단 1회) ────────────────────────────────────────
    W = [0.38, 1.7, 1.25, 1.6, 0.85, 0.9, 1.35, 1.35, 0.38]
    h_cols = st.columns(W)
    for col, label in zip(h_cols, [
        "", "종목명", "현재가", "평가방식", "목표배수", "현재배수",
        f"{CUR_YEAR}E 목표·업사이드", "27E 목표·업사이드", ""
    ]):
        col.markdown(
            f"<div style='font-size:11px;font-weight:700;color:#555;"
            f"padding:2px 0 4px;border-bottom:2px solid #e0e0e0;'>{label}</div>",
            unsafe_allow_html=True,
        )

    # ── 종목 카드 행 ──────────────────────────────────────────────────────────
    swap_action  = None
    codes_to_del = []

    for i, code in enumerate(codes):
        v = vals.get(code, {})
        is_float = "PBR" in v.get("method", "") or "EBITDA" in v.get("method", "")

        with st.container(border=True):
            cols = st.columns(W)

            # ↑/↓ 핸들
            with cols[0]:
                up_btn, dn_btn = st.columns(2)
                with up_btn:
                    if st.button("↑", key=f"wl_up_{code}",
                                 disabled=(i == 0), use_container_width=True):
                        swap_action = (i, i - 1)
                with dn_btn:
                    if st.button("↓", key=f"wl_dn_{code}",
                                 disabled=(i == len(codes) - 1), use_container_width=True):
                        swap_action = (i, i + 1)

            # 종목명
            cols[1].markdown(
                f"<div style='font-size:14px;font-weight:700;padding:6px 0 2px;'>"
                f"{v.get('name', code)}</div>",
                unsafe_allow_html=True,
            )

            # 현재가 + 등락률
            price  = v.get("price")
            change = v.get("change")
            if price:
                chg_color = "#ef5350" if change and change < 0 else "#26a69a"
                chg_html  = (f"<span style='font-size:11px;color:{chg_color};'>"
                             f"{change:+.2f}%</span>") if change is not None else ""
                cols[2].markdown(
                    f"<div style='padding:4px 0;'>"
                    f"<span style='font-size:14px;font-weight:600;'>{price:,.0f}</span>"
                    f"<br>{chg_html}</div>",
                    unsafe_allow_html=True,
                )
            else:
                cols[2].markdown(
                    "<span style='color:#999;font-size:12px;'>조회중…</span>",
                    unsafe_allow_html=True,
                )

            # 평가방식 selectbox
            with cols[3]:
                st.selectbox(" ", METHODS, key=f"wl_m_{code}",
                             label_visibility="collapsed")

            # 목표배수 number_input
            with cols[4]:
                st.number_input(
                    " ", min_value=0.1, max_value=200.0,
                    step=0.1 if is_float else 0.5, format="%.1f",
                    key=f"wl_x_{code}", label_visibility="collapsed",
                )

            # 현재배수
            curr_m = v.get("curr_m")
            cols[5].markdown(
                f"<div style='padding:6px 0;font-size:13px;color:#555;'>"
                f"{'%.1f' % curr_m + 'x' if curr_m is not None else 'N/A'}</div>",
                unsafe_allow_html=True,
            )

            # 26E 목표가 + 업사이드 (색상)
            cols[6].markdown(
                f"<div style='padding:4px 0;'>"
                f"{_year_html(v.get('tp_c'), v.get('up_c'))}</div>",
                unsafe_allow_html=True,
            )

            # 27E 목표가 + 업사이드 (색상)
            cols[7].markdown(
                f"<div style='padding:4px 0;'>"
                f"{_year_html(v.get('tp_n'), v.get('up_n'))}</div>",
                unsafe_allow_html=True,
            )

            # 삭제 버튼
            with cols[8]:
                st.write("")
                if st.button("✕", key=f"wl_del_{code}",
                             help=f"{v.get('name', code)} 삭제"):
                    codes_to_del.append(code)

    # ── 순서 변경 ────────────────────────────────────────────────────────────
    if swap_action:
        a, b = swap_action
        cl = list(codes)
        cl[a], cl[b] = cl[b], cl[a]
        new_wl = {
            c: {
                "method":   st.session_state.get(f"wl_m_{c}", watchlist[c].get("method", "POR(영업익)")),
                "multiple": float(st.session_state.get(f"wl_x_{c}", watchlist[c].get("multiple", 12.0))),
            }
            for c in cl
        }
        save_watchlist(new_wl)
        st.rerun()

    # ── 삭제 ────────────────────────────────────────────────────────────────
    if codes_to_del:
        for code in codes_to_del:
            watchlist.pop(code, None)
            for k in [f"wl_m_{code}", f"wl_x_{code}", f"_wlc_{code}"]:
                st.session_state.pop(k, None)
        save_watchlist(watchlist)
        st.rerun()

    # ── 방식·배수 변경 시 자동 저장 ──────────────────────────────────────────
    if not swap_action and not codes_to_del:
        changed = any(
            st.session_state.get(f"wl_m_{c}") != watchlist[c].get("method", "POR(영업익)") or
            abs(float(st.session_state.get(f"wl_x_{c}", 12.0)) - float(watchlist[c].get("multiple", 12.0))) > 0.001
            for c in codes
        )
        if changed:
            new_wl = {
                c: {
                    "method":   st.session_state.get(f"wl_m_{c}", watchlist[c].get("method", "POR(영업익)")),
                    "multiple": float(st.session_state.get(f"wl_x_{c}", watchlist[c].get("multiple", 12.0))),
                }
                for c in codes
            }
            if save_watchlist(new_wl):
                st.toast("저장됨", icon="✅")

    st.divider()

    # ── 하단 버튼 ─────────────────────────────────────────────────────────────
    _, b2 = st.columns([8, 1.5])
    with b2:
        if st.button("🔄 새로고침", use_container_width=True):
            get_live_price.clear()
            for code in list(watchlist.keys()):
                st.session_state.pop(f"_wlc_{code}", None)
            st.rerun()
