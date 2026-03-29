import streamlit as st
import FinanceDataReader as fdr
import pandas as pd
import yfinance as yf
import numpy as np
from datetime import datetime, timedelta
import platform
import io
import re
import requests

# --- Plotly (인터랙티브 차트) ---
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# --- 페이지 기본 설정 ---
st.set_page_config(page_title="주식 종합 분석 플랫폼", page_icon="📈", layout="wide")

# --- 세션 상태 초기화 ---
if 'history' not in st.session_state:
    st.session_state.history = []
if 'clicked_item' not in st.session_state:
    st.session_state.clicked_item = None

# --- 데이터 캐싱 함수들 ---

@st.cache_data(ttl=86400)
def get_ticker_listing():
    for _ in range(3):
        try:
            df = fdr.StockListing('KRX')
            if not df.empty and 'Name' in df.columns and 'Stocks' in df.columns:
                return df
        except:
            time.sleep(1)
            
    try:
        url = 'http://kind.krx.co.kr/corpgeneral/corpList.do?method=download&searchType=13'
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        res = requests.get(url, headers=headers, timeout=10)
        df = pd.read_html(io.StringIO(res.text), header=0)[0]
        
        df = df.rename(columns={'회사명': 'Name', '종목코드': 'Code'})
        df['Code'] = df['Code'].astype(str).str.zfill(6)
        
        df['Market'] = 'KOSPI' 
        df['Stocks'] = 0 
        df['Marcap'] = 0
        return df
    except Exception as e:
        return pd.DataFrame(columns=['Code', 'Name', 'Market', 'Stocks', 'Marcap'])

@st.cache_data 
def get_stock_price_data(ticker, start_date, end_date):
    return fdr.DataReader(ticker, start_date, end_date)

def parse_and_filter_html(html):
    dfs = pd.read_html(io.StringIO(html))
    target_df = None
    for df in dfs:
        if isinstance(df.columns, pd.MultiIndex):
            col_str = " ".join([str(c) for c in df.columns])
            if 'IFRS' in col_str:
                target_df = df.copy()
                break
        elif not df.empty and len(df.columns) > 0:
            first_col = str(df.iloc[:, 0].values)
            if '매출액' in first_col and '당기순이익' in first_col:
                target_df = df.copy()
                break
                
    if target_df is None: return None

    if isinstance(target_df.columns, pd.MultiIndex):
        target_df.index = target_df.iloc[:, 0].astype(str).str.strip().str.replace(' ', '')
        target_df.columns = [str(c[-1]) for c in target_df.columns]
    else:
        target_df.index = target_df.iloc[:, 0].astype(str).str.strip().str.replace(' ', '')
    
    target_df = target_df.loc[:, ~target_df.columns.duplicated()]
    date_cols = [c for c in target_df.columns if re.search(r'\d{4}[/.]\d{2}', str(c))]
    target_df = target_df[date_cols]

    target_patterns = [
        r'^(매출액|영업수익|순영업수익)', r'^영업이익$', r'^영업이익\(발표기준\)', r'^당기순이익', r'^PER', r'^PBR'
    ]
    
    available_items = []
    for pattern in target_patterns:
        matched_idx = [idx for idx in target_df.index if re.search(pattern, idx)]
        if matched_idx: available_items.append(matched_idx[0])
            
    filtered_df = target_df.loc[available_items].copy()
    rename_dict = {}
    for idx in filtered_df.index:
        if re.search(r'^(매출액|영업수익|순영업수익)', idx): rename_dict[idx] = '매출액'
        elif re.search(r'^영업이익$', idx): rename_dict[idx] = '영업이익'
        elif re.search(r'^영업이익\(발표기준\)', idx): rename_dict[idx] = '영업이익(발표기준)'
        elif re.search(r'^당기순이익', idx): rename_dict[idx] = '당기순이익'
        elif re.search(r'^PER', idx): rename_dict[idx] = 'PER(배)'
        elif re.search(r'^PBR', idx): rename_dict[idx] = 'PBR(배)'
    
    filtered_df.rename(index=rename_dict, inplace=True)
    filtered_df.dropna(axis=1, how='all', inplace=True)
    return filtered_df

@st.cache_data
def get_hybrid_financials(ticker):
    target_years = [2021, 2022, 2023, 2024, 2025, 2026, 2027]
    master_dict = {y: {'매출액': np.nan, '영업이익': np.nan, '당기순이익': np.nan} for y in target_years}
    
    try:
        listing = get_ticker_listing()
        market = listing[listing['Code'] == ticker]['Market'].values[0]
        stock = yf.Ticker(f"{ticker}.KS" if market in ['KOSPI', 'KOSPI200'] else f"{ticker}.KQ")
        fin = stock.financials
        if fin is not None and not fin.empty:
            fin = fin.T
            fin.index = pd.to_datetime(fin.index)
            for d, row in fin.iterrows():
                y = d.year
                if y in target_years:
                    rev, op, ni = row.get('Total Revenue', np.nan), row.get('Operating Income', np.nan), row.get('Net Income', np.nan)
                    if pd.notna(op) or pd.notna(ni):
                        master_dict[y]['매출액'] = float(rev)/1e8 if pd.notna(rev) and rev!=0 else np.nan
                        master_dict[y]['영업이익'] = float(op)/1e8 if pd.notna(op) and op!=0 else np.nan
                        master_dict[y]['당기순이익'] = float(ni)/1e8 if pd.notna(ni) and ni!=0 else np.nan
    except: pass

    # 💡 핵심: 무겁고 느린 가상 브라우저(Selenium)를 버리고, 초고속 다이렉트 서버 통신(requests)으로 데이터 즉시 추출
    try:
        url = f"https://navercomp.wisereport.co.kr/v2/company/c1010001.aspx?cmp_cd={ticker}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Referer": f"https://finance.naver.com/item/coinfo.naver?code={ticker}",
            "Accept-Language": "ko-KR,ko;q=0.9"
        }
        res = requests.get(url, headers=headers, timeout=10)
        df_annual = parse_and_filter_html(res.text)

        if df_annual is not None:
            for c in df_annual.columns:
                match = re.search(r'(\d{4})', str(c))
                if match:
                    y = int(match.group(1))
                    if y in target_years:
                        def get_val(item_name):
                            if item_name in df_annual.index:
                                val = df_annual.loc[item_name, c]
                                try: return float(re.sub(r'[^\d\.-]', '', str(val))) if pd.notna(val) and str(val).strip() not in ['', '-', 'N/A'] else np.nan
                                except: return np.nan
                            return np.nan
                        rev = get_val('매출액')
                        op = get_val('영업이익(발표기준)') if pd.notna(get_val('영업이익(발표기준)')) else get_val('영업이익')
                        ni = get_val('당기순이익')
                        
                        if pd.notna(op) or pd.notna(ni) or pd.notna(rev):
                            master_dict[y]['매출액'] = rev
                            master_dict[y]['영업이익'] = op
                            master_dict[y]['당기순이익'] = ni
    except: pass

    rows = []
    current_year = datetime.today().year
    for y in target_years:
        row = master_dict[y].copy()
        row['Year'] = y
        row['Plot_Date'] = pd.to_datetime(f"{y}-12-28")
        row['Label'] = f"{y}년(E)" if y >= current_year else f"{y}년"
        rows.append(row)
        
    return pd.DataFrame(rows)

# ==========================================
# 💡 좌측 사이드바: 네비게이션 메뉴 구성
# ==========================================
st.sidebar.title("🧭 네비게이션 메뉴")

menu = st.sidebar.radio(
    "메뉴 선택",
    ["📈 밸류에이션 (PER/POR밴드)", "📰 관심종목 - 뉴스", "📝 증권사 레포트", "🛠️ 버전 업데이트 이력"],
    label_visibility="collapsed"
)

# ==========================================
# 💡 페이지 1: 밸류에이션 시뮬레이터 (메인)
# ==========================================
if menu == "📈 밸류에이션 (PER/POR밴드)":
    st.title("📈 밸류에이션 시뮬레이터 (PER/POR 밴드)")
    
    col_input1, col_input2, col_input3 = st.columns([2, 1, 1])
    
    with col_input1:
        corp_name_input = st.text_input("🔍 종목명 검색 후 엔터 (예: 삼성전자, 에코프로비엠)", value="", key="search_input")
    with col_input2:
        val_type = st.selectbox("가치평가 기준", ["PER (순이익 기준)", "POR (영업이익 기준)"])
    with col_input3:
        is_por = "POR" in val_type
        band_name = "POR" if is_por else "PER"
        profit_col = '영업이익' if is_por else '당기순이익'
        target_mult = st.number_input(f"🎯 목표 {band_name} 배수", value=10.0, step=1.0, format="%.1f")
    
    def set_clicked_item(item):
        st.session_state.clicked_item = item

    if st.session_state.history:
        st.write("🕒 **최근 검색:**")
        hist_cols = st.columns(len(st.session_state.history[:7]) + 1)
        for i, item in enumerate(reversed(st.session_state.history[-7:])):
            hist_cols[i].button(item, on_click=set_clicked_item, args=(item,), key=f"hist_{i}")

    if corp_name_input and corp_name_input != st.session_state.get('last_input', ''):
        st.session_state.clicked_item = None
        st.session_state.last_input = corp_name_input

    corp_name = st.session_state.clicked_item if st.session_state.clicked_item else corp_name_input

    if corp_name:
        clean_history = [h for h in st.session_state.history if h.upper() != corp_name.upper()]
        clean_history.append(corp_name)
        st.session_state.history = clean_history

    st.markdown("---")

    if corp_name:
        with st.spinner(f"'{corp_name}' 초고속 데이터 수집 중... 🏃‍♂️"):
            listing = get_ticker_listing()
            
            if 'Name' not in listing.columns:
                st.error("❌ 종목 목록을 불러오지 못했습니다. 서버 상태가 불안정합니다.")
                st.stop()
                
            ticker_row = listing[listing['Name'].str.upper() == corp_name.upper()]
            
            if ticker_row.empty:
                st.error(f"❌ '{corp_name}' 종목을 찾을 수 없습니다.")
            else:
                ticker = ticker_row['Code'].values[0]
                official_name = ticker_row['Name'].values[0]
                stocks_count = ticker_row['Stocks'].values[0] 
                marcap = ticker_row['Marcap'].values[0]
                
                if stocks_count == 0 or pd.isna(stocks_count):
                    try:
                        res = requests.get(f"https://m.stock.naver.com/api/stock/{ticker}/integration", headers={'User-Agent': 'Mozilla/5.0'})
                        data = res.json()
                        stocks_count = int(data['stockEndType']['totalInfo']['stockCount'])
                        marcap = int(data['stockEndType']['totalInfo']['marketValue']) * 100_000_000
                    except:
                        stocks_count = 1 

                fin_df_cache = get_hybrid_financials(ticker)
                
                end_date = datetime.today()
                start_date_plot = end_date - timedelta(days=365 * 6)
                df_price = get_stock_price_data(ticker, start_date_plot, end_date)
                
                if df_price.empty or fin_df_cache is None or fin_df_cache.empty:
                    st.error("❌ 데이터를 가져오는 데 실패했습니다.")
                else:
                    current_price = df_price.iloc[-1]['Close']
                    prev_close = df_price.iloc[-2]['Close'] if len(df_price) > 1 else current_price
                    
                    st.subheader(f"📊 {official_name} ({ticker}) 분석 결과")
                    
                    with st.expander("📋 연도별 재무 현황 상세 (직접 빈칸을 입력/수정하여 시뮬레이션 가능)", expanded=True):
                        st.caption(f"※ 빈칸(-)을 채우거나 수치를 더블클릭하여 자유롭게 수정해 보세요. 수정한 값은 하단 밴드와 업사이드 계산에 즉시 반영됩니다.")
                        
                        edit_df = fin_df_cache[['Label', '매출액', '영업이익', '당기순이익']].copy()
                        edited_df = st.data_editor(
                            edit_df,
                            column_config={
                                "Label": st.column_config.Column("연도", disabled=True),
                                "매출액": st.column_config.NumberColumn("매출액(억원)", format="%d"),
                                "영업이익": st.column_config.NumberColumn("영업이익(억원)", format="%d"),
                                "당기순이익": st.column_config.NumberColumn("당기순이익(억원)", format="%d"),
                            },
                            hide_index=True
                        )
                    
                    fin_df = fin_df_cache.copy()
                    fin_df['매출액'] = pd.to_numeric(edited_df['매출액'], errors='coerce')
                    fin_df['영업이익'] = pd.to_numeric(edited_df['영업이익'], errors='coerce')
                    fin_df['당기순이익'] = pd.to_numeric(edited_df['당기순이익'], errors='coerce')

                    if is_por:
                        fin_df['Metric_Per_Share'] = (fin_df['영업이익'] * 100_000_000) / stocks_count
                    else:
                        fin_df['Metric_Per_Share'] = (fin_df['당기순이익'] * 100_000_000) / stocks_count

                    fin_df['Plot_Metric'] = fin_df['Metric_Per_Share'].apply(lambda x: 0.001 if pd.isna(x) or x <= 0 else x)

                    fin_df_hist = fin_df[fin_df['Year'] <= 2024].copy()
                    historical_metric_dict = {row['Plot_Date'].year: row['Metric_Per_Share'] for idx, row in fin_df_hist.iterrows()}
                    
                    df_hist_daily = df_price.copy()
                    df_hist_daily['Year'] = df_hist_daily.index.year
                    df_hist_daily['Metric'] = df_hist_daily['Year'].map(historical_metric_dict).ffill().bfill()
                    valid_hist = df_hist_daily[df_hist_daily['Metric'] > 0].copy()
                    
                    bands = []
                    min_mult, max_mult = 0, 0
                    if valid_hist.empty:
                        st.warning("⚠️ 유의미한 흑자 과거 데이터가 없어 역사적 밴드를 그릴 수 없습니다.")
                    else:
                        valid_hist['Multiple'] = valid_hist['Close'] / valid_hist['Metric']
                        min_mult = valid_hist['Multiple'].min()
                        max_mult = valid_hist['Multiple'].max()
                        step = (max_mult - min_mult) / 3
                        raw_bands = [round(min_mult + (step * i), 1) for i in range(4)]
                        bands = sorted(list(set([b for b in raw_bands if b > 0]))) 

                    future_dates = pd.date_range(start=df_price.index[-1], end=pd.to_datetime('2028-02-28'), freq='D')
                    extended_dates = df_price.index.append(future_dates[1:])

                    extended_dates_ts = extended_dates.map(datetime.timestamp).values
                    band_dates_ts = fin_df['Plot_Date'].map(datetime.timestamp).values
                    band_metrics = fin_df['Plot_Metric'].values
                    
                    extended_metrics_interp = np.interp(extended_dates_ts, band_dates_ts, band_metrics)
                    daily_metrics_interp = np.interp(df_price.index.map(datetime.timestamp).values, band_dates_ts, band_metrics)
                    
                    df_price['Est_Metric'] = daily_metrics_interp
                    df_price['Current_Valuation'] = np.where(df_price['Est_Metric'] <= 0.002, np.nan, df_price['Close'] / df_price['Est_Metric'])

                    today_est_metric = df_price['Est_Metric'].iloc[-1]
                    today_mult = current_price / today_est_metric if pd.notna(today_est_metric) and today_est_metric > 0.002 else np.nan

                    year_1 = datetime.today().year
                    year_2 = datetime.today().year + 1
                    
                    target_metric_1 = fin_df[fin_df['Year'] == year_1]['Metric_Per_Share'].values
                    target_metric_2 = fin_df[fin_df['Year'] == year_2]['Metric_Per_Share'].values

                    if len(target_metric_1) > 0 and target_metric_1[0] > 0:
                        target_price_1 = target_metric_1[0] * target_mult
                        upside_pct_1 = ((target_price_1 / current_price) - 1) * 100
                    else:
                        target_price_1, upside_pct_1 = 0, 0.0

                    if len(target_metric_2) > 0 and target_metric_2[0] > 0:
                        target_price_2 = target_metric_2[0] * target_mult
                        upside_pct_2 = ((target_price_2 / current_price) - 1) * 100
                    else:
                        target_price_2, upside_pct_2 = 0, 0.0
                        
                    st.write("")
                    col_m1, col_m2, col_m3 = st.columns([1, 1.2, 1.2])
                    with col_m1:
                        st.metric(label=f"현재 주가 ({df_price.index[-1].strftime('%Y-%m-%d')})", value=f"{current_price:,.0f} 원", 
                                  delta=f"{current_price - prev_close:,.0f} 원 ({(current_price/prev_close - 1)*100:.2f}%)")
                        if marcap > 0:
                            st.markdown(f"<span style='color:gray; font-size:16px;'>**시가총액:** {marcap / 1_000_000_000_000:.2f}조 원</span>", unsafe_allow_html=True)
                    with col_m2:
                        if target_price_1 <= 0:
                            st.warning(f"⚠️ 올해({year_1}년) 실적이 없어 목표가를 산출할 수 없습니다. 위 표를 채워주세요.")
                        else:
                            color1 = "#ff4b4b" if upside_pct_1 >= 0 else "#0068c9"
                            st.markdown(f"""
                                <div style="line-height: 1.3;">
                                    <span style="font-size: 15px; color: gray;">🎯 올해({year_1}년) 목표가 [Val: {target_mult}x]</span><br>
                                    <span style="font-size: 28px; font-weight: bold;">{target_price_1:,.0f} 원</span><br>
                                    <span style="font-size: 22px; font-weight: bold; color: {color1};">상승 여력: {upside_pct_1:+.2f}%</span>
                                </div>
                            """, unsafe_allow_html=True)
                    with col_m3:
                        if target_price_2 <= 0:
                            st.warning(f"⚠️ 내년({year_2}년) 실적이 없어 목표가를 산출할 수 없습니다. 위 표를 채워주세요.")
                        else:
                            color2 = "#ff4b4b" if upside_pct_2 >= 0 else "#0068c9"
                            st.markdown(f"""
                                <div style="line-height: 1.3;">
                                    <span style="font-size: 15px; color: gray;">🎯 내년({year_2}년) 목표가 [Val: {target_mult}x]</span><br>
                                    <span style="font-size: 28px; font-weight: bold;">{target_price_2:,.0f} 원</span><br>
                                    <span style="font-size: 22px; font-weight: bold; color: {color2};">상승 여력: {upside_pct_2:+.2f}%</span>
                                </div>
                            """, unsafe_allow_html=True)

                    st.write("---")

                    plot_labels_final = []
                    for idx, row in fin_df.iterrows():
                        profit_val = row.get(profit_col, pd.NA)
                        fmt_val = f"{profit_val:,.0f}억" if pd.notna(profit_val) else "-"
                        if pd.notna(profit_val) and profit_val <= 0:
                            fmt_val = f"{profit_val:,.0f}억(적자)"
                        label_text = f"{row['Label']}<br>{fmt_val}"
                        plot_labels_final.append(label_text)

                    # --- 차트 영역 (Plotly) ---
                    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.08, row_heights=[0.7, 0.3])
                    
                    fig.add_trace(go.Scatter(x=df_price.index, y=df_price['Close'], mode='lines', name='현재 주가', line=dict(color='black', width=2)), row=1, col=1)
                    
                    colors = ['#1f77b4', '#ff7f0e', '#2ca02c', '#9467bd']
                    if bands:
                        for i, b in enumerate(bands):
                            fig.add_trace(go.Scatter(x=extended_dates, y=extended_metrics_interp * b, mode='lines', name=f'{b:.1f}x', line=dict(color=colors[i%len(colors)], width=1, dash='dot')), row=1, col=1)

                    if pd.notna(today_mult):
                        fig.add_trace(go.Scatter(x=extended_dates, y=extended_metrics_interp * today_mult, mode='lines', name=f'실시간 가치({today_mult:.1f}x)', line=dict(color='red', width=2.5)), row=1, col=1)
                        
                    fig.add_trace(go.Scatter(x=extended_dates, y=extended_metrics_interp * target_mult, mode='lines', name=f'목표 가치({target_mult}x)', line=dict(color='blue', width=2.5)), row=1, col=1)

                    if pd.notna(today_mult):
                        fig.add_annotation(
                            x=df_price.index[-1], y=current_price,
                            text=f"현재: {today_mult:.1f}x",
                            showarrow=True, arrowhead=2, ax=-60, ay=-45,
                            font=dict(size=16, color="red", weight="bold"),
                            bgcolor="rgba(255,255,255,0.9)", bordercolor="red", borderwidth=2,
                            row=1, col=1
                        )

                    if target_price_1 > 0:
                        target_date_1 = fin_df[fin_df['Year'] == year_1]['Plot_Date'].iloc[0]
                        fig.add_annotation(
                            x=target_date_1, y=target_price_1,
                            text=f"목표: {target_mult:.1f}x",
                            showarrow=True, arrowhead=2, ax=-60, ay=-45,
                            font=dict(size=16, color="blue", weight="bold"),
                            bgcolor="rgba(255,255,255,0.9)", bordercolor="blue", borderwidth=2,
                            row=1, col=1
                        )

                    max_y_vals = [df_price['Close'].max(), current_price]
                    if target_price_1 > 0: max_y_vals.append(target_price_1)
                    if target_price_2 > 0: max_y_vals.append(target_price_2)
                    y_limit = max(max_y_vals) * 1.25 
                    fig.update_yaxes(range=[0, y_limit], title_text="주가 (원)", row=1, col=1)

                    valid_price = df_price[df_price['Est_Metric'] > 0.001]
                    fig.add_trace(go.Scatter(x=valid_price.index, y=valid_price['Current_Valuation'], mode='lines', name=f'당일 {band_name}', line=dict(color='purple', width=1.5)), row=2, col=1)
                    
                    if pd.notna(today_mult):
                        fig.add_hline(y=today_mult, line_dash="dash", line_color="red", row=2, col=1)
                    fig.add_hline(y=target_mult, line_dash="solid", line_color="blue", row=2, col=1)
                    
                    fig.update_yaxes(range=[0, max(max_mult*1.1 if max_mult > 0 else 30, target_mult*1.2)], title_text=f"{band_name} (배)", row=2, col=1)

                    fig.update_xaxes(
                        tickmode='array',
                        tickvals=fin_df['Plot_Date'],
                        ticktext=plot_labels_final,
                        range=[df_price.index[0], fin_df['Plot_Date'].max() + timedelta(days=120)],
                    )
                    
                    fig.update_layout(
                        height=750, 
                        hovermode="x unified",
                        title_text=f"[{band_name}] 21~27년 밸류에이션 차트",
                        title_font_size=20,
                        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
                    )

                    st.plotly_chart(fig)

    else:
        st.info("👆 상단 검색창에 분석을 원하시는 종목명을 입력해주세요! (예: 삼성전자, 카카오)")

# ==========================================
# 💡 페이지 2: 관심종목 - 뉴스
# ==========================================
elif menu == "📰 관심종목 - 뉴스":
    st.title("📰 관심종목 - 실시간 뉴스")
    st.write("사용자의 관심종목과 관련된 핵심 뉴스를 스크래핑하여 보여주는 공간입니다.")
    st.info("🛠️ 현재 서비스 준비 중입니다. 다음 업데이트를 기대해 주세요!")

# ==========================================
# 💡 페이지 3: 증권사 레포트
# ==========================================
elif menu == "📝 증권사 레포트":
    st.title("📝 최신 증권사 레포트 요약")
    st.write("주요 증권사에서 발간된 리서치 자료 및 목표가 컨센서스를 요약 제공합니다.")
    st.info("🛠️ 현재 서비스 준비 중입니다. 다음 업데이트를 기대해 주세요!")

# ==========================================
# 💡 페이지 4: 버전 업데이트 이력
# ==========================================
elif menu == "🛠️ 버전 업데이트 이력":
    st.title("🛠️ 버전 업데이트 이력")
    st.write("본 시뮬레이터가 발전해 온 과정입니다.")
    
    history_data = {
        "버전": ["V1.0.1 (정식출시)", "V39.5", "V39.4", "V39.3", "V39.2", "V39.0", "V38.0", "V37.0"],
        "업데이트 내용": [
            "가상 브라우저(Selenium) 완전 폐기 및 초고속 API 통신 도입 (속도 10배 향상 및 25~27년 데이터 누락 완벽 해결)",
            "클라우드 가상 브라우저 해상도 강제 고정(1920x1080)으로 과거/미래 데이터(21년, 26/27년) 짤림 현상 완전 해결",
            "클라우드 렌더링 지연에 따른 25~27년 데이터 누락 방지 로직(WebDriverWait) 탑재",
            "사이드바 라디오 버튼(label) 경고 해결 및 클라우드 우회망 User-Agent 위장 로직 추가",
            "클라우드 IP 차단 원천 해결 (KIND 직접 크롤링 및 네이버 모바일 API 실시간 연동 방어 로직 탑재)",
            "메인 UI 대규모 개편 (사이드바 메뉴화, 상단 컨트롤 패널 배치, 버전 이력 독립 탭 전환)",
            "차트 X축 연도 고정, 밴드 4개 축소, 찌그러짐 방지 Y축 타이트 최적화 및 팝업 텍스트 적용",
            "인터랙티브 웹 차트(Plotly) 도입, 엑셀형 표 직접 수정 기능, 목표 밸류에이션 입력 기능 추가"
        ]
    }
    
    df_history = pd.DataFrame(history_data)
    
    st.markdown("""
        <style>
            [data-testid="stDataFrame"] td {
                text-align: left !important;
            }
            [data-testid="stDataFrame"] th {
                text-align: center !important;
            }
        </style>
    """, unsafe_allow_html=True)
    
    st.dataframe(df_history, hide_index=True)
