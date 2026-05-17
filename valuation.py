import streamlit as st
import FinanceDataReader as fdr
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import io
import re
import requests
import json
import os
import base64
import time
import plotly.graph_objects as go

# --- 설정 및 상수 ---
GITHUB_REPO = "shinkiyeol9814-droid/stockapp"
GITHUB_BRANCH = "main"
ESTIMATES_FILE = "data/valuation/user_estimates.json"
UNIT = 100_000_000

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
}
API_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

@st.cache_data(ttl=300)
def load_user_estimates():
    try:
        github_token = st.secrets.get("GH_PAT")
        if not github_token: return {}
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{ESTIMATES_FILE}?ref={GITHUB_BRANCH}"
        headers = {"Authorization": f"token {github_token}", "Accept": "application/vnd.github.v3+json"}
        res = requests.get(url, headers=headers, timeout=7)
        if res.status_code == 200:
            content = base64.b64decode(res.json()['content']).decode('utf-8')
            return json.loads(content)
        return {}
    except: return {}

def save_to_github(file_path, content, message):
    try:
        github_token = st.secrets.get("GH_PAT")
        if not github_token: return False, "Streamlit Secrets에 GH_PAT가 없습니다."
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{file_path}"
        headers = {"Authorization": f"token {github_token}", "Accept": "application/vnd.github.v3+json"}
        res = requests.get(url, headers=headers, params={"ref": GITHUB_BRANCH}, timeout=7)
        sha = res.json().get('sha') if res.status_code == 200 else None
        payload = {
            "message": message,
            "content": base64.b64encode(content.encode("utf-8")).decode("utf-8"),
            "branch": GITHUB_BRANCH
        }
        if sha: payload["sha"] = sha
        put_res = requests.put(url, headers=headers, json=payload, timeout=7)
        if put_res.status_code in [200, 201]: return True, "성공"
        return False, put_res.text
    except Exception as e:
        return False, f"통신 에러: {str(e)}"

@st.cache_data(ttl=86400)
def get_ticker_listing():
    for _ in range(3):
        try:
            df = fdr.StockListing('KRX')
            if not df.empty and 'Name' in df.columns: return df
        except: pass
    try:
        url = 'http://kind.krx.co.kr/corpgeneral/corpList.do?method=download&searchType=13'
        res = requests.get(url, headers=HEADERS, timeout=10)
        df = pd.read_html(io.StringIO(res.text), header=0)[0]
        df = df.rename(columns={'회사명': 'Name', '종목코드': 'Code'})
        df['Code'] = df['Code'].astype(str).str.zfill(6)
        return df
    except: return pd.DataFrame(columns=['Code', 'Name'])

def get_stocks_count(ticker_row, ticker):
    try:
        if 'Stocks' in ticker_row.columns:
            sc = pd.to_numeric(ticker_row['Stocks'].values[0], errors='coerce')
            if pd.notna(sc) and sc > 0: return sc
    except: pass
    try:
        url = f"https://m.stock.naver.com/api/stock/{ticker}/integration"
        res = requests.get(url, headers=API_HEADERS, timeout=5).json()
        return int(res['stockEndType']['totalInfo']['stockCount'])
    except: pass
    try:
        url = f"https://finance.naver.com/item/main.naver?code={ticker}"
        res = requests.get(url, headers=API_HEADERS, timeout=5)
        match = re.search(r'상장주식수<.*?<em>([\d,]+)</em>', res.text, re.DOTALL)
        if match: return int(match.group(1).replace(',', ''))
    except: pass
    try:
        if 'Marcap' in ticker_row.columns and 'Close' in ticker_row.columns:
            marcap  = pd.to_numeric(ticker_row['Marcap'].values[0], errors='coerce')
            close_p = pd.to_numeric(ticker_row['Close'].values[0],  errors='coerce')
            if pd.notna(marcap) and pd.notna(close_p) and marcap > 0 and close_p > 0:
                return int(marcap / close_p)
    except: pass
    return 1

def get_stock_price_data(ticker, start_date, end_date):
    try: return fdr.DataReader(ticker, start_date, end_date)
    except: return pd.DataFrame()

# 💡 [핵심 해결 1] MultiIndex 구조 평탄화 및 확실한 인덱싱 로직 교체
def parse_all_tables(html):
    try:
        dfs = pd.read_html(io.StringIO(html))
        parsed_tables = []
        for df in dfs:
            try:
                # MultiIndex flatten (다중 헤더를 하나로 합침)
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = [
                        "_".join([str(x) for x in col if str(x) != 'nan'])
                        for col in df.columns
                    ]

                df = df.copy()

                # 첫 컬럼 index 지정 및 공백 제거
                first_col = df.columns[0]
                df[first_col] = (
                    df[first_col]
                    .astype(str)
                    .str.replace(" ", "")
                    .str.strip()
                )

                df = df.set_index(first_col)
                parsed_tables.append(df)
            except:
                continue
        return parsed_tables
    except:
        return []

@st.cache_data(ttl=3600)
def get_hybrid_financials(ticker):
    target_years = [2021, 2022, 2023, 2024, 2025, 2026, 2027]
    master_dict = {
        y: {'매출액': np.nan, '영업이익': np.nan, '당기순이익': np.nan,
            '자본총계': np.nan, 'EBITDA': np.nan, '순차입금': np.nan, 'EV/EBITDA': np.nan}
        for y in target_years
    }
    try:
        main_url = f"https://navercomp.wisereport.co.kr/v2/company/c1010001.aspx?cmp_cd={ticker}"
        main_res = requests.get(main_url, headers=HEADERS, timeout=7)
        encparam = ""
        match = re.search(r"encparam\s*:\s*'([^']+)'", main_res.text)
        if match: encparam = match.group(1)
        ajax_headers = HEADERS.copy()
        ajax_headers["Referer"] = main_url
        
        htmls = [main_res.text]
        
        # 💡 [핵심 해결 2] 컨센서스 및 투자지표 탭 명시적 전체 순회
        urls = [
            f"https://navercomp.wisereport.co.kr/v2/company/c1050001.aspx?cmp_cd={ticker}", # 컨센서스 탭
            f"https://navercomp.wisereport.co.kr/v2/company/c1030001.aspx?cmp_cd={ticker}", # 투자지표 탭
            f"https://navercomp.wisereport.co.kr/v2/company/ajax/cF1001.aspx?cmp_cd={ticker}&fin_typ=0&freq_typ=Y&encparam={encparam}",
            f"https://navercomp.wisereport.co.kr/v2/company/ajax/cF2001.aspx?cmp_cd={ticker}&fin_typ=0&freq_typ=Y&encparam={encparam}",
            f"https://navercomp.wisereport.co.kr/v2/company/ajax/cF4002.aspx?cmp_cd={ticker}&fin_typ=0&freq_typ=Y&encparam={encparam}",
            f"https://navercomp.wisereport.co.kr/v2/company/ajax/cF3002.aspx?cmp_cd={ticker}&fin_typ=0&freq_typ=Y&encparam={encparam}",
            f"https://navercomp.wisereport.co.kr/v2/company/ajax/c1050001_data.aspx?cmp_cd={ticker}&fin_typ=0&freq_typ=Y&encparam={encparam}",
            f"https://navercomp.wisereport.co.kr/v2/company/ajax/c1030001_data.aspx?cmp_cd={ticker}&fin_typ=0&freq_typ=Y&encparam={encparam}"
        ]
        
        for url in urls:
            try:
                res = requests.get(url, headers=ajax_headers, timeout=7)
                res.encoding = 'utf-8'
                htmls.append(res.text)
            except: pass
            
        for html_text in htmls:
            dfs_parsed = parse_all_tables(html_text)
            for df_parsed in dfs_parsed:
                for c in df_parsed.columns:
                    # 💡 [핵심 해결 3] 연도 매칭 정규식 강화
                    c_str = str(c)
                    match_yr = re.search(r'(20\d{2})', c_str)
                    if match_yr:
                        y = int(match_yr.group(1))
                        if y in target_years:
                            def get_v(p):
                                for k in df_parsed.index:
                                    k_str = str(k).replace(" ", "").upper()
                                    if re.search(p, k_str):
                                        val = df_parsed.loc[k, c]
                                        if isinstance(val, pd.Series): 
                                            val = val.dropna()
                                            if len(val) == 0: continue
                                            val = val.iloc[0]
                                        try:
                                            # 숫자, 소수점, 마이너스만 남김
                                            cleaned = re.sub(r'[^\d\.-]', '', str(val))
                                            if cleaned in ["", "-", ".", "-.", "NaN", "nan"]: continue
                                            return float(cleaned)
                                        except: continue
                                return np.nan
                            
                            r        = get_v(r'^(매출액|영업수익)')
                            o        = get_v(r'^영업이익$')
                            if pd.isna(o): o = get_v(r'^영업이익\(발표기준\)')
                            n        = get_v(r'^(당기순이익|지배주주순이익)')
                            cap      = get_v(r'^(자본총계|지배주주지분)')
                            ebitda   = get_v(r'^EBITDA(?!마진|비율|/)')
                            net_debt = get_v(r'^순차입금(?!비율|/)')
                            
                            # 💡 [핵심 해결 4] 정규식 무적 방어
                            ev_ebitda_mult = get_v(r'EV.*EBITDA')
                            
                            if pd.isna(master_dict[y]['매출액'])   and pd.notna(r):        master_dict[y]['매출액']   = r
                            if pd.isna(master_dict[y]['영업이익']) and pd.notna(o):        master_dict[y]['영업이익'] = o
                            if pd.isna(master_dict[y]['당기순이익']) and pd.notna(n):      master_dict[y]['당기순이익'] = n
                            if pd.isna(master_dict[y]['자본총계']) and pd.notna(cap):      master_dict[y]['자본총계'] = cap
                            if pd.isna(master_dict[y]['EBITDA'])   and pd.notna(ebitda):   master_dict[y]['EBITDA']   = ebitda
                            if pd.isna(master_dict[y]['순차입금']) and pd.notna(net_debt): master_dict[y]['순차입금'] = net_debt
                            if pd.isna(master_dict[y]['EV/EBITDA']) and pd.notna(ev_ebitda_mult): master_dict[y]['EV/EBITDA'] = ev_ebitda_mult
    except: pass
    rows = []
    for y in target_years:
        row = master_dict[y].copy()
        row['Year']      = y
        row['Plot_Date'] = pd.to_datetime(f"{y}-12-28")
        row['Label']     = f"{y}년"
        rows.append(row)
    return pd.DataFrame(rows)

def make_card_ui(title, price_str, marcap_str, rate_str, is_up, is_zero=False):
    if is_zero: color, bg_color = "#888888", "#f4f4f4"
    else: color, bg_color = ("#ff4b4b" if is_up else "#0068c9"), ("#ff4b4b15" if is_up else "#0068c915")
    return f"""
    <div style="background-color: #ffffff; padding: 12px; border-radius: 8px; border: 1px solid #e0e0e0; text-align: center; box-shadow: 0 2px 4px rgba(0,0,0,0.05); margin-bottom: 10px;">
        <div style="font-size: 13px; color: #777; font-weight: 600; margin-bottom: 4px;">{title}</div>
        <div style="font-size: 22px; font-weight: 900; color: #222; margin-bottom: 2px;">{price_str}</div>
        <div style="font-size: 12px; color: #888; margin-bottom: 10px;">시총: {marcap_str}</div>
        <div style="display: inline-block; font-size: 14px; font-weight: 800; color: {color}; background-color: {bg_color}; padding: 4px 10px; border-radius: 6px;">{rate_str}</div>
    </div>
    """

def extract_number_or_nan(val):
    if pd.isna(val) or str(val).strip() == "": return np.nan
    s = str(val).replace(',', '').replace('✅', '').strip()
    m = re.search(r'-?\d+\.?\d*', s)
    return float(m.group()) if m else np.nan

def extract_number(val):
    if pd.isna(val) or str(val).strip() == "": return 0.0
    s = str(val).replace(',', '').replace('✅', '').strip()
    m = re.search(r'-?\d+\.?\d*', s)
    return float(m.group()) if m else 0.0

def apply_search():
    new_name = st.session_state.get("ui_corp_name", "").strip()
    if new_name:
        st.session_state.active_corp_name = new_name

    new_val_type = st.session_state.get("ui_val_type", "POR(영업익)")
    prev_val_type = st.session_state.get("active_val_type", "POR(영업익)")
    st.session_state.active_val_type = new_val_type

    # 💡 PBR과 EV/EBITDA 모두 소수점 첫째 자리 입력 지원
    new_is_float = "PBR" in new_val_type or "EBITDA" in new_val_type
    prev_is_float = "PBR" in prev_val_type or "EBITDA" in prev_val_type
    type_changed = new_is_float != prev_is_float

    if new_is_float:
        if type_changed: st.session_state.active_target_mult = 1.0
        else: st.session_state.active_target_mult = float(st.session_state.get("ui_target_mult_float", 1.0))
    else:
        if type_changed: st.session_state.active_target_mult = 10.0
        else: st.session_state.active_target_mult = float(int(st.session_state.get("ui_target_mult_int", 10)))

def render_valuation_menu():
    if 'app_init_done' not in st.session_state:
        st.session_state.app_init_done = True
        q_code = st.query_params.get("stock_code", "")
        q_val  = st.query_params.get("val_type", "")
        q_mult = st.query_params.get("mult", "")
        if q_code:
            listing = get_ticker_listing()
            matched = listing[listing['Code'] == str(q_code).zfill(6)]
            if not matched.empty:
                restored_name = matched['Name'].values[0]
                st.session_state.active_corp_name = restored_name
                st.session_state.ui_corp_name     = restored_name
                if q_val:
                    st.session_state.active_val_type = q_val
                    st.session_state.ui_val_type     = q_val
                if q_mult:
                    mult = float(q_mult)
                    st.session_state.active_target_mult = mult
                    if q_val and ("PBR" in q_val or "EBITDA" in q_val):
                        st.session_state.ui_target_mult_float = mult
                        st.session_state.ui_target_mult_int   = 10
                    else:
                        st.session_state.ui_target_mult_int   = int(mult)
                        st.session_state.ui_target_mult_float = 1.0

    if 'active_corp_name'   not in st.session_state: st.session_state.active_corp_name   = ""
    if 'active_val_type'    not in st.session_state: st.session_state.active_val_type    = "POR(영업익)"
    if 'active_target_mult' not in st.session_state: st.session_state.active_target_mult = 10.0

    is_float_type = "PBR" in st.session_state.active_val_type or "EBITDA" in st.session_state.active_val_type
    
    prev_is_float = st.session_state.get('_prev_is_float', is_float_type)
    if is_float_type != prev_is_float:
        st.session_state.pop('ui_target_mult_float', None)
        st.session_state.pop('ui_target_mult_int', None)
        st.session_state.active_target_mult = 1.0 if is_float_type else 10.0
    
    st.session_state['_prev_is_float'] = is_float_type
    
    if is_float_type and 'ui_target_mult_float' not in st.session_state:
        st.session_state['ui_target_mult_float'] = float(st.session_state.active_target_mult)
    elif not is_float_type and 'ui_target_mult_int' not in st.session_state:
        st.session_state['ui_target_mult_int'] = int(st.session_state.active_target_mult)
    
    if 'ui_val_type' not in st.session_state:
        st.session_state['ui_val_type'] = st.session_state.active_val_type
    
    st.markdown("""
        <style>
        .stButton > button, [data-testid="stFormSubmitButton"] > button { background-color: #ffe6e6 !important; border-color: #ffcccc !important; }
        .stButton > button p, [data-testid="stFormSubmitButton"] > button p { color: #d63031 !important; font-weight: 600 !important; }
        .stButton > button:hover, [data-testid="stFormSubmitButton"] > button:hover { background-color: #ffcccc !important; }
        </style>
    """, unsafe_allow_html=True)
    st.markdown("<div class='main-title'>📈 가치평가 시뮬레이터</div>", unsafe_allow_html=True)

    val_options = ["PER(순이익)", "POR(영업익)", "PBR(자본총계)", "EV/EBITDA"]

    with st.form("search_form", border=False):
        col1, col2, col3, col4 = st.columns([2, 1.5, 1.2, 1])
        with col1:
            st.text_input("종목명", key="ui_corp_name", placeholder="예: 삼성전자")
        with col2:
            idx = val_options.index(st.session_state.ui_val_type) if st.session_state.ui_val_type in val_options else 1
            st.selectbox("평가방식", val_options, index=idx, key="ui_val_type")
        with col3:
            if is_float_type: st.number_input("목표배수", step=0.1, format="%.1f", key="ui_target_mult_float")
            else: st.number_input("목표배수", step=1, format="%d", key="ui_target_mult_int")
        with col4:
            st.markdown("<div style='margin-top: 28px;'></div>", unsafe_allow_html=True)
            submitted = st.form_submit_button("갱신", type="primary", use_container_width=True)

    if submitted:
        apply_search()
        st.rerun()

    if "EBIT
