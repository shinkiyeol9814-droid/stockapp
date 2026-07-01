"""
ui_watchlist.py — 밸류 워치리스트 tab (AG Grid 테이블 + 실시간 계산).
"""
import streamlit as st
import requests
import json
import base64
import pandas as pd
import concurrent.futures
from datetime import datetime

from st_aggrid import AgGrid, GridOptionsBuilder, JsCode, GridUpdateMode, DataReturnMode

from valuation import (
    get_hybrid_financials, get_ticker_listing, get_stocks_count,
    UNIT, API_HEADERS, GITHUB_REPO, GITHUB_BRANCH,
)

WATCHLIST_FILE = "data/watchlist/watchlist.json"
METHODS   = ["POR(영업익)", "PER(순이익)", "PBR(자본총계)", "EV/EBITDA"]
COL_MAP   = {
    "POR(영업익)":   "영업이익",
    "PER(순이익)":   "당기순이익",
    "PBR(자본총계)": "자본총계",
    "EV/EBITDA":    "EV/EBITDA",
}
CUR_YEAR  = datetime.today().year
NEXT_YEAR = CUR_YEAR + 1

# ── GitHub ─────────────────────────────────────────────────────────────────────
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

# ── 데이터 ────────────────────────────────────────────────────────────────────
@st.cache_data(ttl=60, show_spinner=False)
def get_live_price(code: str):
    try:
        data = requests.get(
            f"https://m.stock.naver.com/api/stock/{code}/basic",
            headers=API_HEADERS, timeout=5
        ).json()
        price = 0.0
        # 비정규장에도 유효한 가격 필드를 순서대로 시도
        for field in ["closePrice", "stockEndPrice", "endPrice", "basePrice", "prevClosePrice"]:
            v = data.get(field)
            if v:
                p = float(str(v).replace(",", ""))
                if p > 0:
                    price = p
                    break
        change = float(str(data.get("fluctuationsRatio") or "0").replace(",", ""))
        name   = data.get("stockName") or data.get("corporateName", code)
        return (price if price > 0 else None), change, name
    except:
        return None, None, code

@st.cache_data(ttl=3600, show_spinner=False)
def get_watch_financials(code: str):
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

# ── 재무 계산 헬퍼 (JS valueGetter용 사전 계산) ────────────────────────────────
def _tp1x(fin_df, stocks, col_key, year):
    """1배수 기준 주가 = (재무값 * UNIT) / 주식수"""
    if fin_df is None or stocks == 0:
        return None
    row = fin_df[fin_df["Year"] == year]
    if row.empty:
        return None
    val = row[col_key].values[0]
    if pd.isna(val) or val <= 0:
        return None
    return float(val) * UNIT / stocks

def _ev(fin_df, year):
    if fin_df is None:
        return None
    row = fin_df[fin_df["Year"] == year]
    if row.empty:
        return None
    val = row["EV/EBITDA"].values[0]
    if pd.isna(val) or val <= 0:
        return None
    return float(val)

# ── Python 재무 계산 헬퍼 ──────────────────────────────────────────────────────
def _tp1x_best(fin_df, stocks, col_key, years):
    """여러 연도 순서로 시도해 첫 번째 유효한 1배수 주가를 반환"""
    for yr in years:
        v = _tp1x(fin_df, stocks, col_key, yr)
        if v is not None:
            return v
    return None

def _ev_best(fin_df, years):
    for yr in years:
        v = _ev(fin_df, yr)
        if v is not None:
            return v
    return None

def _py_curr_mult(fin_df, stocks, method, price, years):
    """시장 현재 배수"""
    if price is None or price <= 0:
        return None
    if "EV" in method:
        ev = _ev_best(fin_df, years)
        return ev if ev else None
    col = COL_MAP.get(method)
    if col is None:
        return None
    tp1x = _tp1x_best(fin_df, stocks, col, years)
    return (price / tp1x) if tp1x and tp1x > 0 else None

def _py_target(fin_df, stocks, method, mult, price, years):
    """목표 주가"""
    if mult <= 0:
        return None
    if "EV" in method:
        ev = _ev_best(fin_df, years)
        if ev and ev > 0 and price and price > 0:
            return price * (mult / ev)
        return None
    col = COL_MAP.get(method)
    if col is None:
        return None
    tp1x = _tp1x_best(fin_df, stocks, col, years)
    return (tp1x * mult) if tp1x else None

def _py_upside(fin_df, stocks, method, mult, price, years):
    """업사이드 (%)"""
    tp = _py_target(fin_df, stocks, method, mult, price, years)
    if tp is None or price is None or price <= 0:
        return None
    return (tp / price - 1) * 100

# 포매터
def _jsnull(v): return f"({v} == null || (typeof {v} === 'number' && isNaN({v})))"

_upside_style = JsCode(f"""
function(params) {{
    var v = params.value;
    if ({_jsnull('v')}) return {{}};
    if (v > 0) return {{color:'#ef5350', fontWeight:'700'}};
    if (v < 0) return {{color:'#1565C0', fontWeight:'700'}};
    return {{color:'#888'}};
}}
""")
_upside_fmt = JsCode(f"""
function(params) {{
    var v = params.value;
    if ({_jsnull('v')}) return 'N/A';
    return (v > 0 ? '▲ +' : v < 0 ? '▼ ' : '') + v.toFixed(1) + '%';
}}
""")
_price_fmt = JsCode(f"""
function(params) {{
    var v = params.value;
    if ({_jsnull('v')} || v === 0) return '-';
    return Math.round(v).toLocaleString('ko-KR') + '원';
}}
""")
_change_fmt = JsCode(f"""
function(params) {{
    var v = params.value;
    if ({_jsnull('v')}) return '-';
    return (v > 0 ? '+' : '') + v.toFixed(2) + '%';
}}
""")
_change_style = JsCode("""
function(params) {
    var v = params.value;
    if (v > 0) return {color:'#ef5350'};
    if (v < 0) return {color:'#1565C0'};
    return {color:'#888'};
}
""")
_tp_fmt = JsCode(f"""
function(params) {{
    var v = params.value;
    if ({_jsnull('v')} || v <= 0) return 'N/A';
    return Math.round(v).toLocaleString('ko-KR') + '원';
}}
""")
_mult_fmt = JsCode(f"""
function(params) {{
    var v = params.value;
    if ({_jsnull('v')}) return 'N/A';
    return v.toFixed(1) + 'x';
}}
""")
_edit_style = JsCode("function(params) { return {color:'#1565C0', backgroundColor:'#eef4ff', cursor:'pointer', display:'flex', alignItems:'center'}; }")
_del_btn = JsCode("""
class DelBtn {
    init(params) {
        this.eGui = document.createElement('div');
        this.eGui.style.cssText = 'display:flex;align-items:center;justify-content:center;height:100%;';
        this.eGui.innerHTML = '<button style="border:none;background:transparent;color:#cc3333;font-size:15px;cursor:pointer;padding:0 4px;" title="삭제">✕</button>';
        this.eGui.querySelector('button').addEventListener('click', () => {
            params.api.applyTransaction({remove: [params.data]});
        });
    }
    getGui() { return this.eGui; }
    refresh() { return true; }
}
""")


# ── 렌더링 ─────────────────────────────────────────────────────────────────────
def render_watchlist():
    st.markdown(
        "<div style='font-size:1.4rem;font-weight:bold;margin-bottom:4px;'>📋 밸류 워치리스트</div>",
        unsafe_allow_html=True,
    )
    st.caption("≡ 핸들 드래그로 순서 이동 · 파란 셀 클릭 편집 → 업사이드 즉시 반영 · 자동 저장")

    # ── 직전 편집값 선적용 + watchlist 즉시 반영 (GitHub 캐시 지연 우회) ────
    pending  = st.session_state.pop("_wl_pending", {})
    wl_fresh = st.session_state.pop("_wl_fresh", None)
    # pending이 있거나 fresh watchlist가 있을 때만 그리드 데이터 강제 갱신
    force_reload = bool(pending) or (wl_fresh is not None)
    for code, (m, x) in pending.items():
        st.session_state[f"wl_m_{code}"] = m
        st.session_state[f"wl_x_{code}"] = x

    watchlist = wl_fresh or load_watchlist()

    # ── 종목 추가 ─────────────────────────────────────────────────────────────
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
                        st.session_state["_wl_fresh"] = updated  # 즉시 반영
                        st.session_state.pop(f"_wlc_{code}", None)
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

    # ── 병렬 데이터 로딩 ─────────────────────────────────────────────────────
    uncached = [c for c in codes if f"_wlc_{c}" not in st.session_state]
    if uncached:
        get_ticker_listing()  # main thread 캐시 워밍
        def _load_one(code):
            get_watch_financials(code)
            get_live_price(code)
            return code
        with st.spinner(f"데이터 로딩 중... ({len(uncached)}개 종목)"):
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=min(5, len(uncached))
            ) as executor:
                for code in executor.map(_load_one, uncached):
                    st.session_state[f"_wlc_{code}"] = True

    # ── DataFrame 구성 ────────────────────────────────────────────────────────
    CY, NY = f"{CUR_YEAR}E", f"{NEXT_YEAR}E"
    CY_YRS = [CUR_YEAR, CUR_YEAR - 1]    # 2026 → 2025 폴백
    NY_YRS = [NEXT_YEAR, CUR_YEAR]       # 2027 → 2026 폴백
    rows = []
    for code in codes:
        fin, stocks     = get_watch_financials(code)
        price, chg, nm  = get_live_price(code)
        method = st.session_state.get(f"wl_m_{code}", "POR(영업익)")
        mult   = float(st.session_state.get(f"wl_x_{code}", 12.0))
        p = float(price) if price else None
        rows.append({
            "_code":    code,
            "종목명":   nm or code,
            "현재가":   p,
            "등락률":   float(chg) if chg is not None else None,
            "평가방식": method,
            "목표배수": mult,
            "현재배수":        _py_curr_mult(fin, stocks, method, p, CY_YRS),
            f"{CY} 목표가":   _py_target(fin, stocks, method, mult, p, CY_YRS),
            f"{CY} 업사이드": _py_upside(fin, stocks, method, mult, p, CY_YRS),
            f"{NY} 목표가":   _py_target(fin, stocks, method, mult, p, NY_YRS),
            f"{NY} 업사이드": _py_upside(fin, stocks, method, mult, p, NY_YRS),
            "삭제":            "",
        })
    df = pd.DataFrame(rows)
    # float64 dtype 보장 (None → NaN, aggrid numeric 변환 오류 방지)
    for _c in ["현재배수", f"{CY} 목표가", f"{CY} 업사이드", f"{NY} 목표가", f"{NY} 업사이드"]:
        df[_c] = pd.to_numeric(df[_c], errors="coerce")

    # ── AG Grid 설정 ──────────────────────────────────────────────────────────
    gb = GridOptionsBuilder.from_dataframe(df)
    gb.configure_default_column(
        resizable=True, filterable=False, sortable=False,
        suppressMovable=True,
        cellStyle={"fontSize": "13px", "display": "flex", "alignItems": "center"},
    )

    # 숨김 열
    gb.configure_column("_code", hide=True)

    # 종목명 (드래그 핸들)
    gb.configure_column("종목명", rowDrag=True, minWidth=110, maxWidth=160,
                        cellStyle={"fontWeight": "700", "fontSize": "13px",
                                   "display": "flex", "alignItems": "center"})

    gb.configure_column("현재가",  valueFormatter=_price_fmt,  type="numericColumn",
                        minWidth=95, maxWidth=120)
    gb.configure_column("등락률",  valueFormatter=_change_fmt, cellStyle=_change_style,
                        type="numericColumn", minWidth=70, maxWidth=82)

    # 편집 가능 열 (파란색)
    gb.configure_column("평가방식", editable=True,
                        cellEditor="agSelectCellEditor",
                        cellEditorParams={"values": METHODS},
                        cellStyle=_edit_style, minWidth=128, maxWidth=148)
    gb.configure_column("목표배수", editable=True,
                        type=["numericColumn"],
                        valueFormatter=_mult_fmt,
                        cellEditorParams={"step": 0.5},
                        cellStyle=_edit_style, minWidth=68, maxWidth=82)

    # 계산 열 (Python에서 사전 계산, rerun 시 갱신)
    _right = {"textAlign": "right", "display": "flex", "alignItems": "center", "justifyContent": "flex-end"}
    gb.configure_column("현재배수",
                        valueFormatter=_mult_fmt, type="numericColumn",
                        minWidth=68, maxWidth=82,
                        cellStyle={**_right, "color": "#888"})
    gb.configure_column(f"{CY} 목표가",
                        valueFormatter=_tp_fmt, type="numericColumn",
                        minWidth=95, maxWidth=115,
                        cellStyle={**_right, "color": "#555"})
    gb.configure_column(f"{CY} 업사이드",
                        valueFormatter=_upside_fmt, cellStyle=_upside_style,
                        type="numericColumn", minWidth=85, maxWidth=105)
    gb.configure_column(f"{NY} 목표가",
                        valueFormatter=_tp_fmt, type="numericColumn",
                        minWidth=95, maxWidth=115,
                        cellStyle={**_right, "color": "#555"})
    gb.configure_column(f"{NY} 업사이드",
                        valueFormatter=_upside_fmt, cellStyle=_upside_style,
                        type="numericColumn", minWidth=85, maxWidth=105)

    gb.configure_column("삭제", cellRenderer=_del_btn,
                        headerName="", width=48, maxWidth=48,
                        suppressMovable=True, editable=False)

    gb.configure_grid_options(
        rowDragManaged=True,
        animateRows=True,
        suppressRowClickSelection=True,
        rowHeight=42,
        headerHeight=38,
        domLayout="autoHeight",
    )

    grid_resp = AgGrid(
        df,
        gridOptions=gb.build(),
        update_mode=GridUpdateMode.MODEL_CHANGED,
        data_return_mode=DataReturnMode.FILTERED_AND_SORTED,
        allow_unsafe_jscode=True,
        reload_data=force_reload,   # 편집 중 False → 그리드가 편집값 보존; 변경 적용 시만 True
        theme="alpine",
        fit_columns_on_grid_load=False,
        key="wl_aggrid",
    )

    # ── 변경 감지 & 자동 저장 ─────────────────────────────────────────────────
    ret: pd.DataFrame = grid_resp.data
    if ret is None or ret.empty or "_code" not in ret.columns:
        _render_bottom(watchlist)
        return

    current_codes = [
        c for c in ret["_code"].tolist()
        if c and not (isinstance(c, float) and pd.isna(c))
    ]

    # 삭제 감지
    deleted = [c for c in codes if c not in current_codes]
    if deleted:
        for c in deleted:
            for k in [f"wl_m_{c}", f"wl_x_{c}", f"_wlc_{c}"]:
                st.session_state.pop(k, None)
        new_wl = {
            c: {
                "method":   st.session_state.get(f"wl_m_{c}", watchlist[c].get("method", "POR(영업익)")),
                "multiple": float(st.session_state.get(f"wl_x_{c}", watchlist[c].get("multiple", 12.0))),
            }
            for c in current_codes if c in watchlist
        }
        save_watchlist(new_wl)
        st.session_state["_wl_fresh"] = new_wl  # 즉시 반영
        st.rerun()

    # 방식·배수 변경 감지
    settings_changed = False
    new_settings: dict[str, tuple] = {}
    for _, row in ret.iterrows():
        c = row.get("_code", "")
        if not c or c not in watchlist:
            continue
        new_m = str(row.get("평가방식") or "POR(영업익)")
        try:
            new_x = float(row.get("목표배수") or 12.0)
        except (TypeError, ValueError):
            new_x = 12.0
        if (st.session_state.get(f"wl_m_{c}") != new_m or
                abs(float(st.session_state.get(f"wl_x_{c}", 12.0)) - new_x) > 0.001):
            new_settings[c] = (new_m, new_x)
            settings_changed = True

    # 순서 변경 감지
    order_changed = current_codes != codes

    if settings_changed or order_changed:
        new_wl = {
            c: {
                "method":   new_settings[c][0] if c in new_settings
                            else st.session_state.get(f"wl_m_{c}", watchlist.get(c, {}).get("method", "POR(영업익)")),
                "multiple": new_settings[c][1] if c in new_settings
                            else float(st.session_state.get(f"wl_x_{c}", watchlist.get(c, {}).get("multiple", 12.0))),
            }
            for c in current_codes if c in watchlist
        }
        save_watchlist(new_wl)

        if settings_changed:
            # 다음 런에서 df를 올바르게 빌드하도록 pending에 저장 후 재런
            st.session_state["_wl_pending"] = new_settings
            st.toast("저장됨", icon="✅")
            st.rerun()
        else:
            st.toast("순서 저장됨", icon="✅")

    _render_bottom(watchlist)


def _render_bottom(watchlist: dict = None):
    st.write("")
    _, col_r = st.columns([8, 1.5])
    with col_r:
        if st.button("🔄 새로고침", use_container_width=True):
            get_live_price.clear()
            if watchlist:
                for code in list(watchlist.keys()):
                    st.session_state.pop(f"_wlc_{code}", None)
            st.rerun()
