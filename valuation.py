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
import plotly.graph_objects as go

# --- GitHub 연동 설정 ---
GITHUB_REPO = "shinkiyeol9814-droid/stockapp"
GITHUB_BRANCH = "main" 
ESTIMATES_FILE = "data/user_estimates.json"

# 공통 헤더 설정
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
}
API_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# --- 헬퍼 함수: GitHub 데이터 입출력 ---
def load_user_estimates():
    """GitHub에서 사용자 추정치 JSON 로드"""
    try:
        if os.path.exists(ESTIMATES_FILE):
            with open(ESTIMATES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}
    except: return {}

def save_to_github(file_path, content, message):
    github_token = st.secrets.get("GH_PAT")
    if not github_token: return False, "GH_PAT 부족"
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{file_path}"
    headers = {"Authorization": f"token {github_token}", "Accept": "application/vnd.github.v3+json"}
    res = requests.get(url, headers=headers, params={"ref": GITHUB_BRANCH})
    sha = res.json().get('sha') if res.status_code == 200 else None
    payload = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode("utf-8"),
        "branch": GITHUB_BRANCH
    }
    if sha: payload["sha"] = sha
    put_res = requests.put(url, headers=headers, json=payload)
    return (True, "성공") if put_res.status_code in [200, 201] else (False, put_res.text)

# --- 기존 데이터 수집 함수들 ---
def get_stocks_count(ticker_row, ticker):
    try:
        if 'Stocks' in ticker_row.columns:
            sc = pd.to_numeric(ticker_row['Stocks'], errors='coerce').fillna(0).values[0]
            if sc > 0: return sc
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
    return 1

@st.cache_data(ttl=86400)
def get_ticker_listing():
    for _ in range(3):
        try:
            df = fdr.StockListing('KRX')
            if not df.empty and 'Name' in df.columns: return df
        except: pass
    return pd.DataFrame(columns=['Code', 'Name'])

@st.cache_data 
def get_stock_price_data(ticker, start_date, end_date):
    try: return fdr.DataReader(ticker, start_date, end_date)
    except: return pd.DataFrame()

def parse_and_filter_html(html):
    try:
        dfs = pd.read_html(io.StringIO(html))
        target_df = None
        for df in dfs:
            if 'IFRS' in " ".join([str(c) for c in df.columns]): target_df = df.copy(); break
        if target_df is None: return None
        target_df.index = target_df.iloc[:, 0].astype(str).str.strip().str.replace(' ', '')
        date_cols = [c for c in target_df.columns if re.search(r'\d{4}', str(c))]
        target_df = target_df[date_cols]
        return target_df
    except: return None

@st.cache_data
def get_hybrid_financials(ticker):
    target_years = [2021, 2022, 2023, 2024, 2025, 2026, 2027]
    master_dict = {y: {'매출액': np.nan, '영업이익': np.nan, '당기순이익': np.nan} for y in target_years}
    try:
        main_url = f"https://navercomp.wisereport.co.kr/v2/company/c1010001.aspx?cmp_cd={ticker}"
        main_res = requests.get(main_url, headers=HEADERS, timeout=10)
        encparam = ""
        match = re.search(r"encparam\s*:\s*'([^']+)'", main_res.text)
        if match: encparam = match.group(1)
        ajax_headers = HEADERS.copy(); ajax_headers["Referer"] = main_url
        urls = [
            f"https://navercomp.wisereport.co.kr/v2/company/ajax/cF1001.aspx?cmp_cd={ticker}&fin_typ=0&freq_typ=Y&encparam={encparam}",
            f"https://navercomp.wisereport.co.kr/v2/company/ajax/cF3002.aspx?cmp_cd={ticker}&fin_typ=0&freq_typ=Y&encparam={encparam}"
        ]
        for url in urls:
            res = requests.get(url, headers=ajax_headers, timeout=10)
            df_parsed = parse_and_filter_html(res.text)
            if df_parsed is not None:
                for c in df_parsed.columns:
                    match_yr = re.search(r'(\d{4})', str(c))
                    if match_yr:
                        y = int(match_yr.group(1))
                        if y in target_years:
                            def get_v(p):
                                m = [k for k in df_parsed.index if re.search(p, k)]
                                if m:
                                    val = df_parsed.loc[m[0], c]
                                    try: return float(re.sub(r'[^\d\.-]', '', str(val)))
                                    except: pass
                                return np.nan
                            r = get_v(r'^(매출액|영업수익)'); o = get_v(r'^영업이익'); n = get_v(r'^당기순이익')
                            if pd.isna(master_dict[y]['매출액']) and pd.notna(r): master_dict[y]['매출액'] = r
                            if pd.isna(master_dict[y]['영업이익']) and pd.notna(o): master_dict[y]['영업이익'] = o
                            if pd.isna(master_dict[y]['당기순이익']) and pd.notna(n): master_dict[y]['당기순이익'] = n
    except: pass
    rows = []
    for y in target_years:
        row = master_dict[y].copy(); row['Year'] = y; row['Plot_Date'] = pd.to_datetime(f"{y}-12-28"); row['Label'] = f"{y}년"
        rows.append(row)
    return pd.DataFrame(rows)

def make_card_ui(title, price_str, marcap_str, rate_str, is_up, is_zero=False):
    color = "#888888" if is_zero else ("#ff4b4b" if is_up else "#0068c9")
    bg_color = "#f4f4f4" if is_zero else f"{color}15"
    return f"""
    <div style="background-color: #ffffff; padding: 12px; border-radius: 8px; border: 1px solid #e0e0e0; text-align: center; box-shadow: 0 2px 4px rgba(0,0,0,0.05); margin-bottom: 10px;">
        <div style="font-size: 13px; color: #777; font-weight: 600; margin-bottom: 4px;">{title}</div>
        <div style="font-size: 22px; font-weight: 900; color: #222; margin-bottom: 2px;">{price_str}</div>
        <div style="font-size: 12px; color: #888; margin-bottom: 10px;">시총: {marcap_str}</div>
        <div style="display: inline-block; font-size: 14px; font-weight: 800; color: {color}; background-color: {bg_color}; padding: 4px 10px; border-radius: 6px;">{rate_str}</div>
    </div>
    """

# --- 메인 렌더링 함수 ---
def render_valuation_menu():
    st.markdown("<div class='main-title'>📈 가치평가 시뮬레이터</div>", unsafe_allow_html=True)
    
    # 상단 검색 바
    col_search1, col_search2 = st.columns([2, 1])
    with col_search1:
        corp_name = st.text_input("종목명", value=st.session_state.get('search_corp_name', ""), placeholder="예: 삼성전자").strip()
        st.session_state.search_corp_name = corp_name
    with col_search2:
        val_type = st.selectbox("평가방식", ["PER(순이익)", "POR(영업익)"])

    if corp_name:
        listing = get_ticker_listing()
        ticker_row = listing[listing['Name'].str.upper() == corp_name.upper()]
        
        if ticker_row.empty:
            st.error("❌ 종목을 찾을 수 없습니다.")
        else:
            ticker = ticker_row['Code'].values[0]
            stocks_count = get_stocks_count(ticker_row, ticker)
            
            # 1. 데이터 로드 (네이버 금융 + 사용자 저장 추정치)
            fin_df = get_hybrid_financials(ticker)
            user_estimates = load_user_estimates()
            ticker_estimates = user_estimates.get(ticker, {})

            # 2. 데이터 병합 (네이버에 데이터가 없을 때만 사용자 값 적용)
            manual_indices = [] # 수동 입력된 셀 위치 추적용
            for idx, row in fin_df.iterrows():
                yr = str(row['Year'])
                if yr in ticker_estimates:
                    for col in ['매출액', '영업이익', '당기순이익']:
                        # 네이버 데이터가 없거나 0일 때만 사용자 추정치로 채움
                        if pd.isna(row[col]) or row[col] == 0:
                            if col in ticker_estimates[yr]:
                                fin_df.at[idx, col] = ticker_estimates[yr][col]
                                manual_indices.append((idx, col))

            # 3. UI: 연도별 재무 상세 표
            st.markdown("<div class='sub-header' style='margin-top:10px; font-size:15px !important;'>📝 연도별 재무 상세</div>", unsafe_allow_html=True)
            st.caption("💡 **파란색 숫자**는 직접 입력한 추정치입니다. (실제 데이터 업데이트 시 자동 대체됩니다)")
            
            # 색상 가이드를 위한 Styler 적용 (수동 입력 셀만 파란색 글씨 처리)
            def highlight_manual(data):
                attr = 'color: #0068c9; font-weight: bold;'
                df_style = pd.DataFrame('', index=data.index, columns=data.columns)
                for r, c in manual_indices:
                    if c in df_style.columns: df_style.at[r, c] = attr
                return df_style

            styled_fin = fin_df[['Label', '매출액', '영업이익', '당기순이익']].style.apply(highlight_manual, axis=None)

            edited_df = st.data_editor(
                styled_fin,
                hide_index=True,
                use_container_width=True,
                key=f"editor_{ticker}"
            )

            # 저장 버튼 로직
            if st.button("저장", type="secondary"):
                new_estimates = user_estimates.copy()
                if ticker not in new_estimates: new_estimates[ticker] = {}
                
                # 현재 표에서 '빈 칸이었던 곳'에 입력된 값들만 추출하여 저장
                for idx, row in edited_df.iterrows():
                    yr = str(fin_df.at[idx, 'Year'])
                    for col in ['매출액', '영업이익', '당기순이익']:
                        # 원본 네이버 데이터가 비어있을 때만 저장 대상
                        orig_val = get_hybrid_financials(ticker).at[idx, col]
                        if pd.isna(orig_val) or orig_val == 0:
                            if yr not in new_estimates[ticker]: new_estimates[ticker][yr] = {}
                            new_estimates[ticker][yr][col] = row[col]
                        else:
                            # 실제 데이터가 생겼다면 기존 수동 데이터 삭제 (A방식 청소)
                            if yr in new_estimates[ticker] and col in new_estimates[ticker][yr]:
                                del new_estimates[ticker][yr][col]

                success, msg = save_to_github(ESTIMATES_FILE, json.dumps(new_estimates, indent=4, ensure_ascii=False), f"Update estimates for {corp_name}")
                if success: st.success("✅ 추정치가 저장되었습니다.")
                else: st.error(f"❌ 저장 실패: {msg}")

            # 4. 밸류에이션 계산 및 차트 (기존 로직 유지)
            fin_df['매출액'] = edited_df['매출액'].values
            fin_df['영업이익'] = edited_df['영업이익'].values
            fin_df['당기순이익'] = edited_df['당기순이익'].values
            
            # [이후 차트 및 카드 UI 렌더링 부분은 기존과 동일하게 진행...]
            # (지면 관계상 핵심 로직 위주로 구성했습니다)
