import streamlit as st
import FinanceDataReader as fdr
import matplotlib.pyplot as plt
import pandas as pd
import yfinance as yf
import urllib.request
import json
import numpy as np
from datetime import datetime, timedelta
import platform
import io
import re

# --- 한글 폰트 설정 ---
system_name = platform.system()
if system_name == 'Windows':
    plt.rcParams['font.family'] = 'Malgun Gothic'
elif system_name == 'Darwin': 
    plt.rcParams['font.family'] = 'AppleGothic'
else: 
    plt.rcParams['font.family'] = 'NanumGothic'
plt.rcParams['axes.unicode_minus'] = False

# --- 데이터 캐싱 ---
@st.cache_data 
def get_ticker_listing():
    return fdr.StockListing('KRX')

@st.cache_data 
def get_stock_price_data(ticker, start_date, end_date):
    return fdr.DataReader(ticker, start_date, end_date)

@st.cache_data
def get_hybrid_financials(ticker):
    all_dfs = []
    # 1. 야후 파이낸스
    try:
        listing = get_ticker_listing()
        market = listing[listing['Code'] == ticker]['Market'].values[0]
        yf_ticker = f"{ticker}.KS" if market in ['KOSPI', 'KOSPI200'] else f"{ticker}.KQ"
        stock = yf.Ticker(yf_ticker)
        financials = stock.financials
        if financials is not None and not financials.empty:
            df = pd.DataFrame()
            try: df['매출액'] = financials.loc['Total Revenue']
            except: df['매출액'] = np.nan
            try: df['영업이익'] = financials.loc['Operating Income']
            except: df['영업이익'] = np.nan
            try: df['당기순이익'] = financials.loc['Net Income']
            except: df['당기순이익'] = np.nan
            df = df / 100_000_000 
            df['Year'] = df.index.year
            df['Is_Estimate'] = False
            df['Source'] = 'Yahoo'
            df['Priority'] = 3
            all_dfs.append(df.dropna(subset=['Year']))
    except: pass

    # 2. 네이버 API
    try:
        url = f"https://m.stock.naver.com/api/stock/{ticker}/finance/annual"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        res = urllib.request.urlopen(req, timeout=5).read()
        data = json.loads(res)
        if 'annualDate' in data:
            dates = data['annualDate']
            df = pd.DataFrame()
            df['Year'] = [int(re.search(r'(\d{4})', d).group(1)) if re.search(r'(\d{4})', d) else np.nan for d in dates]
            df['Is_Estimate'] = ['(E)' in d or '(P)' in d for d in dates]
            for ko, en in {'매출액': 'sales', '영업이익': 'operatingProfit', '당기순이익': 'netIncome'}.items():
                df[ko] = [float(str(v).replace(',', '')) if v else np.nan for v in data.get(en, [])]
            df['Source'] = 'Naver_API'
            df['Priority'] = 2
            all_dfs.append(df.dropna(subset=['Year']))
    except: pass

    # 3. FnGuide
    try:
        url = f"https://comp.fnguide.com/SVO2/ASP/SVD_Main.asp?pGB=1&gdtb=S&cID=&MenuYn=Y&ReportGB=&NewMenuID=101&stkGb=701&gicode=A{ticker}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        res = urllib.request.urlopen(req, timeout=10).read()
        dfs = pd.read_html(io.StringIO(res.decode('utf-8', 'replace')))
        for df in dfs:
            cols = [str(c[-1]) if isinstance(df.columns, pd.MultiIndex) else str(c) for c in df.columns]
            df.columns = cols
            if not df.empty and '매출액' in str(df.iloc[:,0]) and '당기순이익' in str(df.iloc[:,0]):
                df.set_index(df.columns[0], inplace=True)
                res_data = {}
                for ko in ['매출액', '영업이익', '당기순이익']:
                    clean_idx = df.index.astype(str).str.replace(' ', '')
                    matches = [i for i, idx in enumerate(clean_idx) if ko in idx]
                    res_data[ko] = df.iloc[matches[0]] if matches else pd.Series([np.nan]*len(df.columns))
                fg_df = pd.DataFrame(res_data).T
                years, is_ests, valid_idx = [], [], []
                for c in fg_df.index:
                    m = re.search(r'(\d{4})', str(c))
                    if m:
                        years.append(int(m.group(1))); is_ests.append('(E)' in str(c)); valid_idx.append(c)
                fg_df = fg_df.loc[valid_idx].copy()
                fg_df['Year'] = years; fg_df['Is_Estimate'] = is_ests
                for col in ['매출액', '영업이익', '당기순이익']:
                    fg_df[col] = pd.to_numeric(fg_df[col].astype(str).str.replace(r'[^\d\.-]', '', regex=True), errors='coerce')
                fg_df['Source'] = 'FnGuide'; fg_df['Priority'] = 0
                all_dfs.append(fg_df); break
    except: pass

    if not all_dfs: return None
    fin_df = pd.concat(all_dfs, ignore_index=True)
    fin_df[['매출액', '영업이익', '당기순이익']] = fin_df[['매출액', '영업이익', '당기순이익']].replace({0: np.nan})
    fin_df = fin_df.sort_values(['Year', 'Priority']).drop_duplicates(subset=['Year'], keep='first')
    fin_df['Plot_Date'] = pd.to_datetime(fin_df['Year'].astype(int).astype(str) + '-12-28')
    fin_df['Label'] = fin_df['Year'].astype(int).astype(str) + '년 12월'
    return fin_df.sort_values('Plot_Date').reset_index(drop=True)

# --- UI 세팅 ---
st.set_page_config(page_title="주식 가치평가 시뮬레이터", layout="wide")
st.sidebar.header("🔍 종목 검색")
corp_name_input = st.sidebar.text_input("종목명 입력", value="").strip()
val_type = st.sidebar.radio("가치평가 기준", ["PER (순이익 기반)", "POR (영업이익 기반)"])

if corp_name_input:
    listing = get_ticker_listing()
    ticker_row = listing[listing['Name'].str.upper() == corp_name_input.upper()]
    
    if not ticker_row.empty:
        ticker = ticker_row['Code'].values[0]
        official_name = ticker_row['Name'].values[0]
        stocks_count = ticker_row['Stocks'].values[0]
        marcap = ticker_row['Marcap'].values[0]

        # 1. 초기 데이터 로드
        fin_df = get_hybrid_financials(ticker)
        df_price = get_stock_price_data(ticker, datetime.today() - timedelta(days=365*6), datetime.today())

        if fin_df is not None and not df_price.empty:
            st.header(f"📊 {official_name} ({ticker}) 가치평가 시뮬레이션")
            
            # --- 수기 수정을 위한 데이터 에디터 ---
            st.markdown("### 📋 연도별 재무 현황 상세 (수정 시 차트 즉시 반영)")
            display_df = fin_df[['Label', '매출액', '영업이익', '당기순이익', 'Source']].copy()
            display_df.columns = ['연도', '매출액(억)', '영업이익(억)', '당기순익(억)', '출처']
            
            # 가운데 정렬 스타일 적용
            st.markdown("""<style> [data-testid="stTable"] td {text-align: center !important;} </style>""", unsafe_allow_html=True)
            
            # 💡 수정 1: 재무제표 수기 입력 반영
            edited_df = st.data_editor(display_df, use_container_width=True, hide_index=True)
            
            # 수정된 값 반영
            fin_df['영업이익'] = edited_df['영업이익(억)']
            fin_df['당기순이익'] = edited_df['당기순익(억)']
            fin_df['매출액'] = edited_df['매출액(억)']

            # --- 가치평가 계산 로직 ---
            is_por = "POR" in val_type
            band_name = "POR" if is_por else "PER"
            profit_col = '영업이익' if is_por else '당기순이익'
            
            fin_df['Metric_Per_Share'] = (fin_df[profit_col] * 100_000_000) / stocks_count
            fin_df['Plot_Metric'] = fin_df['Metric_Per_Share'].apply(lambda x: 0.001 if pd.isna(x) or x <= 0 else x)

            # 역사적 밴드 계산
            fin_df_hist = fin_df[fin_df['Plot_Date'] <= pd.Timestamp(datetime.today())].copy()
            historical_metric_dict = {row['Year']: row['Metric_Per_Share'] for _, row in fin_df_hist.iterrows()}
            df_hist_daily = df_price.copy()
            df_hist_daily['Year'] = df_hist_daily.index.year
            df_hist_daily['Metric'] = df_hist_daily['Year'].map(historical_metric_dict).ffill().bfill()
            
            valid_hist = df_hist_daily[df_hist_daily['Metric'] > 0.01].copy()
            valid_hist['Multiple'] = valid_hist['Close'] / valid_hist['Metric']
            
            min_mult, max_mult = valid_hist['Multiple'].min(), valid_hist['Multiple'].max()
            avg_mult = valid_hist['Multiple'].mean()
            step = (max_mult - min_mult) / 5
            bands = [round(min_mult + (step * i), 1) for i in range(6)]

            # 미래 밴드 연장 데이터 생성
            future_dates = pd.date_range(start=df_price.index[-1], end=fin_df['Plot_Date'].max() + timedelta(days=60), freq='D')
            extended_dates = df_price.index.append(future_dates[1:])
            band_metrics = fin_df['Plot_Metric'].values
            band_dates_ts = fin_df['Plot_Date'].map(datetime.timestamp).values
            extended_metrics_interp = np.interp(extended_dates.map(datetime.timestamp).values, band_dates_ts, band_metrics)
            
            # 실시간 가치 계산
            today_est_metric = np.interp(datetime.timestamp(df_price.index[-1]), band_dates_ts, band_metrics)
            today_mult = df_price.iloc[-1]['Close'] / today_est_metric if today_est_metric > 0.002 else np.nan

            # --- 차트 그리기 ---
            fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 10), gridspec_kw={'height_ratios': [2, 1]})
            
            # 상단: 주가 & 밴드
            ax1.plot(df_price.index, df_price['Close'], label='현재 주가', color='black', linewidth=1.5)
            colors = ['#1f77b4', '#ff7f0e', '#2ca02c', '#9467bd', '#8c564b', '#e377c2']
            for i, b in enumerate(bands):
                ax1.plot(extended_dates, extended_metrics_interp * b, color=colors[i%len(colors)], linestyle='-', linewidth=0.7, alpha=0.5, label=f'{b:.1f}x')
            
            if pd.notna(today_mult):
                ax1.plot(extended_dates, extended_metrics_interp * today_mult, color='red', linewidth=2.5, label=f'실시간 가치({today_mult:.1f}x)')

            ax1.set_title(f"[{band_name} 밴드] 및 실시간 가치 시뮬레이션", fontsize=15, fontweight='bold')
            ax1.legend(loc='upper left', ncol=2, fontsize=9)
            ax1.grid(True, alpha=0.3)

            # 하단: 배수 추이
            ax2.plot(valid_hist.index, valid_hist['Multiple'], color='blue', linewidth=1.2, label=f'당일 {band_name}')
            
            # 💡 수정 2: 평균 밴드 Avg 수치 초록색 실선 표기
            ax2.axhline(avg_mult, color='green', linestyle='-', linewidth=2.0, label=f'Avg ({avg_mult:.1f}x)')
            ax2.text(valid_hist.index[0], avg_mult, f'  Avg: {avg_mult:.1f}x', color='green', va='bottom', fontweight='bold')
            
            if pd.notna(today_mult):
                ax2.axhline(today_mult, color='red', linestyle='--', alpha=0.7, label=f'Current ({today_mult:.1f}x)')

            ax2.set_ylabel(f"{band_name} 배수")
            ax2.legend(loc='upper right', fontsize=9)
            ax2.grid(True, alpha=0.3)
            
            # X축 라벨 세팅
            labels_final = [f"{row['Year']}년\n{row[profit_col]:,.0f}억" for _, row in fin_df.iterrows()]
            ax1.set_xticks(fin_df['Plot_Date']); ax1.set_xticklabels(labels_final, fontsize=9)
            ax2.set_xticks(fin_df['Plot_Date']); ax2.set_xticklabels(labels_final, fontsize=9)

            st.pyplot(fig)

            # --- 상단 메트릭 표시 (차트 아래 배치하여 수정 결과 확인 용이) ---
            m_col1, m_col2, m_col3 = st.columns(3)
            with m_col1: st.metric("현재 주가", f"{df_price.iloc[-1]['Close']:,.0f} 원")
            
            # 목표주가 계산 (올해/내년)
            for i, year in enumerate([datetime.today().year, datetime.today().year+1]):
                target_row = fin_df[fin_df['Year'] == year]
                if not target_row.empty and target_row.iloc[0]['Metric_Per_Share'] > 0:
                    t_price = target_row.iloc[0]['Metric_Per_Share'] * (today_mult if pd.notna(today_mult) else avg_mult)
                    upside = (t_price / df_price.iloc[-1]['Close'] - 1) * 100
                    with [m_col2, m_col3][i]:
                        st.metric(f"{year}년 목표가", f"{t_price:,.0f} 원", f"{upside:+.2f}%")

    else:
        st.error("종목명을 정확히 입력해주세요.")
