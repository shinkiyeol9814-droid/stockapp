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

# ── AG Grid JS ─────────────────────────────────────────────────────────────────
# valueGetter: 브라우저에서 실시간 계산 (방식·배수 변경 시 즉시 반영)
def _vg_tp(sfx: str) -> JsCode:
    """목표가 valueGetter  sfx: '_c'(당해) or '_n'(내년)"""
    return JsCode(f"""
function(params) {{
    var d = params.data, m = d['평가방식'], x = +d['목표배수'], p = d['_p'];
    if (!m || !(x > 0) || !(p > 0)) return null;
    if (m.indexOf('EV') >= 0) {{
        var ev = d['_ev{sfx}']; return (ev > 0) ? p * (x / ev) : null;
    }}
    var t = m.indexOf('POR') >= 0 ? d['_por{sfx}']
          : m.indexOf('PER') >= 0 ? d['_per{sfx}']
          : m.indexOf('PBR') >= 0 ? d['_pbr{sfx}'] : null;
    return (t > 0) ? t * x : null;
}}
""")

def _vg_up(sfx: str) -> JsCode:
    """업사이드(%) valueGetter"""
    return JsCode(f"""
function(params) {{
    var d = params.data, m = d['평가방식'], x = +d['목표배수'], p = d['_p'];
    if (!m || !(x > 0) || !(p > 0)) return null;
    if (m.indexOf('EV') >= 0) {{
        var ev = d['_ev{sfx}']; return (ev > 0) ? (x / ev - 1) * 100 : null;
    }}
    var t = m.indexOf('POR') >= 0 ? d['_por{sfx}']
          : m.indexOf('PER') >= 0 ? d['_per{sfx}']
          : m.indexOf('PBR') >= 0 ? d['_pbr{sfx}'] : null;
    return (t > 0) ? (t * x / p - 1) * 100 : null;
}}
""")

_vg_curr_mult = JsCode("""
function(params) {
    var d = params.data, m = d['평가방식'], p = d['_p'];
    if (!m || !(p > 0)) return null;
    if (m.indexOf('EV') >= 0) { var ev = d['_ev_c']; return (ev > 0) ? ev : null; }
    var t = m.indexOf('POR') >= 0 ? d['_por_c']
          : m.indexOf('PER') >= 0 ? d['_per_c']
          : m.indexOf('PBR') >= 0 ? d['_pbr_c'] : null;
    return (t > 0) ? p / t : null;
}
""")

# 포매터
def _jsnull(v): return f"({v} == null || (typeof {v} === 'number' && isNaN({v})))"

_upside_style = JsCode(f"""
function(params) {{
    var v = params.value;
    if ({_jsnull('v')}) return {{}};
    if (v > 0) return {{color:'#26a69a', fontWeight:'700'}};
    if (v < 0) return {{color:'#ef5350', fontWeight:'700'}};
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
_edit_style = JsCode("function(params) { return {color:'#1565C0', cursor:'pointer'}; }")
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

    # ── 직전 그리드 편집값을 session_state에 선적용 (깜빡임 방지) ─────────────
    pending = st.session_state.pop("_wl_pending", {})
    for code, (m, x) in pending.items():
        st.session_state[f"wl_m_{code}"] = m
        st.session_state[f"wl_x_{code}"] = x

    watchlist = load_watchlist()

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
                        st.success(f"✅ {chosen.split('(')[0].strip()} 추가됨")
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
            # ── JS valueGetter 용 숨김 재무 데이터 ──
            "_p":       p,
            "_por_c":   _tp1x(fin, stocks, "영업이익",   CUR_YEAR),
            "_per_c":   _tp1x(fin, stocks, "당기순이익", CUR_YEAR),
            "_pbr_c":   _tp1x(fin, stocks, "자본총계",   CUR_YEAR),
            "_ev_c":    _ev(fin, CUR_YEAR),
            "_por_n":   _tp1x(fin, stocks, "영업이익",   NEXT_YEAR),
            "_per_n":   _tp1x(fin, stocks, "당기순이익", NEXT_YEAR),
            "_pbr_n":   _tp1x(fin, stocks, "자본총계",   NEXT_YEAR),
            "_ev_n":    _ev(fin, NEXT_YEAR),
            # ── valueGetter가 계산할 열 (초기값 무시됨) ──
            "현재배수":         None,
            f"{CY} 목표가":    None,
            f"{CY} 업사이드":  None,
            f"{NY} 목표가":    None,
            f"{NY} 업사이드":  None,
            "삭제":            "",
        })
    df = pd.DataFrame(rows)

    # ── AG Grid 설정 ──────────────────────────────────────────────────────────
    gb = GridOptionsBuilder.from_dataframe(df)
    gb.configure_default_column(
        resizable=True, filterable=False, sortable=False,
        suppressMovable=True,
        cellStyle={"fontSize": "13px", "display": "flex", "alignItems": "center"},
    )

    # 숨김 열
    for hc in ["_code", "_p",
               "_por_c", "_per_c", "_pbr_c", "_ev_c",
               "_por_n", "_per_n", "_pbr_n", "_ev_n"]:
        gb.configure_column(hc, hide=True)

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

    # valueGetter 열 (브라우저에서 실시간 계산)
    gb.configure_column("현재배수",
                        valueGetter=_vg_curr_mult,
                        valueFormatter=_mult_fmt,
                        type="numericColumn", minWidth=68, maxWidth=82,
                        cellStyle={"color": "#888", "display": "flex", "alignItems": "center"})
    gb.configure_column(f"{CY} 목표가",
                        valueGetter=_vg_tp("_c"),
                        valueFormatter=_tp_fmt,
                        type="numericColumn", minWidth=95, maxWidth=115,
                        cellStyle={"color": "#555", "display": "flex", "alignItems": "center"})
    gb.configure_column(f"{CY} 업사이드",
                        valueGetter=_vg_up("_c"),
                        valueFormatter=_upside_fmt, cellStyle=_upside_style,
                        type="numericColumn", minWidth=85, maxWidth=105)
    gb.configure_column(f"{NY} 목표가",
                        valueGetter=_vg_tp("_n"),
                        valueFormatter=_tp_fmt,
                        type="numericColumn", minWidth=95, maxWidth=115,
                        cellStyle={"color": "#555", "display": "flex", "alignItems": "center"})
    gb.configure_column(f"{NY} 업사이드",
                        valueGetter=_vg_up("_n"),
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
