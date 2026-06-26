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
METHODS        = ["POR(영업익)", "PER(순이익)", "PBR(자본총계)", "EV/EBITDA"]
DEFAULT_MULT   = {"POR(영업익)": 12.0, "PER(순이익)": 12.0, "PBR(자본총계)": 1.5, "EV/EBITDA": 8.0}
COL_MAP        = {"POR(영업익)": "영업이익", "PER(순이익)": "당기순이익",
                  "PBR(자본총계)": "자본총계", "EV/EBITDA": "EV/EBITDA"}
CUR_YEAR       = datetime.today().year
NEXT_YEAR      = 2027

# ── GitHub 저장소 ─────────────────────────────────────────────────────────────
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
    content = json.dumps(data, ensure_ascii=False, indent=2)
    b64     = base64.b64encode(content.encode()).decode()
    url     = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{WATCHLIST_FILE}"
    hdrs    = _gh_hdrs()
    res     = requests.get(url, headers=hdrs, params={"ref": GITHUB_BRANCH}, timeout=7)
    sha     = res.json().get("sha") if res.status_code == 200 else None
    payload = {"message": "Update watchlist", "content": b64, "branch": GITHUB_BRANCH}
    if sha:
        payload["sha"] = sha
    r = requests.put(url, headers=hdrs, json=payload, timeout=10)
    return r.status_code in [200, 201]

# ── 데이터 수집 ───────────────────────────────────────────────────────────────
@st.cache_data(ttl=60, show_spinner=False)
def get_live_price(code: str):
    """현재가, 등락률, 종목명 (NAVER Finance, 60초 캐시)"""
    try:
        data   = requests.get(
            f"https://m.stock.naver.com/api/stock/{code}/basic",
            headers=API_HEADERS, timeout=5
        ).json()
        price  = float(str(data.get("closePrice")       or "0").replace(",", ""))
        change = float(str(data.get("fluctuationsRatio") or "0").replace(",", ""))
        name   = data.get("stockName") or data.get("corporateName", code)
        return (price or None), change, name
    except:
        return None, None, code

@st.cache_data(ttl=3600, show_spinner=False)
def get_watch_financials(code: str):
    """재무 데이터(억원) + 발행주식수 반환 (1시간 캐시)"""
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

# ── 목표주가 계산 (valuation.py get_t() 동일 로직) ────────────────────────────
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
        if "EBITDA" in method:
            tp = curr_price * (multiple / float(val))
        else:
            tp = float(val) * UNIT * multiple / stocks
        if tp <= 0:
            return None, None
        return tp, (tp / curr_price - 1) * 100
    except:
        return None, None

# ── 헬퍼 HTML ─────────────────────────────────────────────────────────────────
def _up_html(upside):
    if upside is None:
        return "<span style='color:#bbb;font-size:12px;'>N/A</span>"
    color = "#ef5350" if upside < 0 else "#26a69a"
    arrow = "▼" if upside < 0 else "▲"
    return (f"<span style='color:{color};font-weight:700;font-size:14px;'>"
            f"{arrow} {upside:+.1f}%</span>")

def _price_html(tp):
    if tp is None:
        return "<span style='color:#bbb;font-size:12px;'>N/A</span>"
    return f"<span style='font-size:13px;'>{tp:,.0f}원</span>"

# ── 메인 렌더링 ───────────────────────────────────────────────────────────────
def render_watchlist():
    st.markdown(
        "<div style='font-size:1.4rem;font-weight:bold;margin-bottom:4px;'>📋 밸류 워치리스트</div>",
        unsafe_allow_html=True,
    )
    st.caption("방식·배수 변경 즉시 재계산 · 현재가 60초 캐시 · 재무데이터 1시간 캐시")

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
                    # 현재 session_state의 method/multiple을 watchlist에 반영한 뒤 추가
                    updated_wl = {}
                    for c, cfg in watchlist.items():
                        updated_wl[c] = {
                            "method":   st.session_state.get(f"wl_m_{c}", cfg.get("method", "POR(영업익)")),
                            "multiple": float(st.session_state.get(f"wl_x_{c}", cfg.get("multiple", 12.0))),
                        }
                    updated_wl[code] = {"method": "POR(영업익)", "multiple": 12.0}
                    if save_watchlist(updated_wl):
                        st.success(f"✅ {chosen.split('(')[0].strip()} 추가")
                        st.rerun()
                    else:
                        st.error("저장 실패 (GH_PAT 확인)")
                else:
                    st.warning("이미 추가된 종목입니다.")

    if not watchlist:
        st.info("종목을 추가해주세요.")
        return

    # ── 세션 상태 초기화 (최초 1회, 키가 없을 때만) ───────────────────────────
    for code, cfg in watchlist.items():
        if f"wl_m_{code}" not in st.session_state:
            st.session_state[f"wl_m_{code}"] = cfg.get("method", "POR(영업익)")
        if f"wl_x_{code}" not in st.session_state:
            st.session_state[f"wl_x_{code}"] = float(cfg.get("multiple", 12.0))

    # ── 테이블 헤더 ───────────────────────────────────────────────────────────
    # 컬럼: 순서 | 종목명 | 현재가 | 방식 | 배수 | 26E목표 | 26E업사이드 | 27E목표 | 27E업사이드 | 삭제
    W = [0.55, 1.9, 1.4, 1.6, 0.95, 1.3, 1.1, 1.3, 1.1, 0.5]
    hdr_labels = ["↕", "종목명", "현재가", "평가방식", "목표배수",
                  f"{CUR_YEAR}E 목표가", f"{CUR_YEAR}E 업사이드",
                  "27E 목표가", "27E 업사이드", ""]
    h_cols = st.columns(W)
    for c, label in zip(h_cols, hdr_labels):
        c.markdown(
            f"<div style='font-size:11px;font-weight:700;color:#555;"
            f"padding:3px 0;border-bottom:2px solid #e0e0e0;'>{label}</div>",
            unsafe_allow_html=True,
        )

    # ── 종목 행 ───────────────────────────────────────────────────────────────
    codes        = list(watchlist.keys())
    codes_to_del = []
    swap_action  = None   # (idx_a, idx_b)

    for i, code in enumerate(codes):
        price, change, name = get_live_price(code)
        fin_df, stocks      = get_watch_financials(code)

        # 세션 상태에서 현재 method/multiple 읽기 (위젯 렌더 전)
        cur_method   = st.session_state.get(f"wl_m_{code}", "POR(영업익)")
        cur_multiple = float(st.session_state.get(f"wl_x_{code}", 12.0))
        is_float     = "PBR" in cur_method or "EBITDA" in cur_method

        tp_c, up_c = calc_target(fin_df, stocks, cur_method, cur_multiple, price, CUR_YEAR)
        tp_n, up_n = calc_target(fin_df, stocks, cur_method, cur_multiple, price, NEXT_YEAR)

        cols = st.columns(W)

        # ↑ / ↓ 순서 버튼
        with cols[0]:
            st.write("")
            b_up, b_dn = st.columns(2)
            with b_up:
                if st.button("↑", key=f"wl_up_{code}", disabled=(i == 0),
                             help="위로"):
                    swap_action = (i, i - 1)
            with b_dn:
                if st.button("↓", key=f"wl_dn_{code}", disabled=(i == len(codes) - 1),
                             help="아래로"):
                    swap_action = (i, i + 1)

        # 종목명
        cols[1].markdown(
            f"<div style='font-size:13px;font-weight:600;padding:8px 0 2px;'>{name}</div>",
            unsafe_allow_html=True,
        )

        # 현재가
        if price:
            chg_color = "#ef5350" if change and change < 0 else "#26a69a"
            chg_txt   = (f"<span style='font-size:11px;color:{chg_color};'>{change:+.2f}%</span>"
                         if change is not None else "")
            cols[2].markdown(
                f"<div style='padding:6px 0 2px;'>"
                f"<span style='font-size:14px;font-weight:700;'>{price:,.0f}</span><br>{chg_txt}</div>",
                unsafe_allow_html=True,
            )
        else:
            cols[2].markdown("<span style='color:#999;font-size:12px;'>조회중…</span>",
                             unsafe_allow_html=True)

        # 평가방식 selectbox — index= 미전달, key로만 상태 관리
        with cols[3]:
            st.selectbox(
                " ", METHODS,
                key=f"wl_m_{code}",
                label_visibility="collapsed",
            )

        # 목표배수 number_input
        with cols[4]:
            st.number_input(
                " ",
                min_value=0.1, max_value=200.0,
                step=0.1 if is_float else 0.5,
                format="%.1f",
                key=f"wl_x_{code}",
                label_visibility="collapsed",
            )

        # 현재년도 목표가 / 업사이드
        cols[5].markdown(f"<div style='padding:8px 0 2px;'>{_price_html(tp_c)}</div>",
                         unsafe_allow_html=True)
        cols[6].markdown(f"<div style='padding:8px 0 2px;'>{_up_html(up_c)}</div>",
                         unsafe_allow_html=True)

        # 2027E 목표가 / 업사이드
        cols[7].markdown(f"<div style='padding:8px 0 2px;'>{_price_html(tp_n)}</div>",
                         unsafe_allow_html=True)
        cols[8].markdown(f"<div style='padding:8px 0 2px;'>{_up_html(up_n)}</div>",
                         unsafe_allow_html=True)

        # 삭제
        with cols[9]:
            st.write("")
            if st.button("✕", key=f"wl_del_{code}", help=f"{name} 삭제"):
                codes_to_del.append(code)

    # ── 순서 변경 처리 ────────────────────────────────────────────────────────
    if swap_action:
        a, b           = swap_action
        codes[a], codes[b] = codes[b], codes[a]
        # 현재 session_state 값을 반영해서 순서 바꾼 뒤 저장
        new_wl = {
            c: {
                "method":   st.session_state.get(f"wl_m_{c}", watchlist[c].get("method", "POR(영업익)")),
                "multiple": float(st.session_state.get(f"wl_x_{c}", watchlist[c].get("multiple", 12.0))),
            }
            for c in codes
        }
        save_watchlist(new_wl)
        st.rerun()

    # ── 삭제 처리 ─────────────────────────────────────────────────────────────
    if codes_to_del:
        for code in codes_to_del:
            watchlist.pop(code, None)
            for k in [f"wl_m_{code}", f"wl_x_{code}"]:
                st.session_state.pop(k, None)
        save_watchlist(watchlist)
        st.rerun()

    st.divider()

    # ── 하단 버튼 ─────────────────────────────────────────────────────────────
    b1, b2, _ = st.columns([1.5, 1.5, 6])
    with b1:
        if st.button("💾 설정 저장", type="primary", use_container_width=True):
            new_wl = {
                code: {
                    "method":   st.session_state.get(f"wl_m_{code}", "POR(영업익)"),
                    "multiple": float(st.session_state.get(f"wl_x_{code}", 12.0)),
                }
                for code in watchlist
            }
            if save_watchlist(new_wl):
                st.success("저장됐습니다.")
            else:
                st.error("저장 실패")
    with b2:
        if st.button("🔄 가격 새로고침", use_container_width=True):
            get_live_price.clear()
            st.rerun()
