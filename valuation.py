import streamlit as st
import FinanceDataReader as fdr
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import io
import re
import requests
import plotly.graph_objects as go

# 공통 헤더 설정
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
}

def get_stocks_count(ticker_row, ticker):
    try:
        sc = pd.to_numeric(ticker_row['Stocks'], errors='coerce').fillna(0).values[0]
        if sc > 0: return sc
    except: pass
    try:
        marcap = pd.to_numeric(ticker_row['Marcap'], errors='coerce').fillna(0).values[0]
        close_p = pd.to_numeric(ticker_row['Close'], errors='coerce').fillna(0).values[0]
        if marcap > 0 and close_p > 0: return int(marcap / close_p)
    except: pass
    try:
        res_m = requests.get(f"https://m.stock.naver.com/api/stock/{ticker}/integration", headers=HEADERS, timeout=5).json()
        return int(res_m['stockEndType']['totalInfo']['stockCount'])
    except: return 1

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
        
        ajax_headers = HEADERS.copy()
        ajax_headers["Referer"] = main_url
        
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
                            r = get_v(r'^(매출액|영업수익)')
                            o = get_v(r'^영업이익\(발표기준\)') or get_v(r'^영업이익$')
                            n = get_v(r'^당기순이익')
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
    if is_zero:
        color = "#888888"
        bg_color = "#f4f4f4"
    else:
        color = "#ff4b4b" if is_up else "#0068c9"
        bg_color = f"{color}15" 

    return f"""
    <div style="background-color: #ffffff; padding: 12px; border-radius: 8px; border: 1px solid #e0e0e0; text-align: center; box-shadow: 0 2px 4px rgba(0,0,0,0.05); margin-bottom: 10px;">
        <div style="font-size: 13px; color: #777; font-weight: 600; margin-bottom: 4px;">{title}</div>
        <div style="font-size: 22px; font-weight: 900; color: #222; margin-bottom: 2px;">{price_str}</div>
        <div style="font-size: 12px; color: #888; margin-bottom: 10px;">시총: {marcap_str}</div>
        <div style="display: inline-block; font-size: 14px; font-weight: 800; color: {color}; background-color: {bg_color}; padding: 4px 10px; border-radius: 6px;">
            {rate_str}
        </div>
    </div>
    """

def render_valuation_menu():
    st.markdown("<div class='main-title'>📈 가치평가 시뮬레이터</div>", unsafe_allow_html=True)
    
    col_input, col_btn = st.columns([1, 0.25])
    with col_input:
        st.markdown("<div class='search-container'><div class='search-label'>종목명:</div><div class='search-input-wrap'>", unsafe_allow_html=True)
        corp_name = st.text_input("종목명", value=st.session_state.search_corp_name, placeholder="예: 삼성전자", label_visibility="collapsed").strip()
        st.session_state.search_corp_name = corp_name
        st.markdown("</div></div>", unsafe_allow_html=True)
    with col_btn:
        st.markdown("<div style='margin-top:2px;'></div>", unsafe_allow_html=True)
        search_clicked = st.button("갱신", use_container_width=True)

    st.markdown("<div class='search-container'><div class='search-label'>평가방식:</div><div class='search-input-wrap'>", unsafe_allow_html=True)
    val_type = st.selectbox("평가방식", ["PER(순이익)", "POR(영업익)"], label_visibility="collapsed")
    st.markdown("</div></div>", unsafe_allow_html=True)
    
    if corp_name:
        listing = get_ticker_listing()
        ticker_row = listing[listing['Name'].str.upper() == corp_name.upper()]
        if not ticker_row.empty:
            ticker = ticker_row['Code'].values[0]
            if st.session_state.last_ticker != ticker:
                temp_fin = get_hybrid_financials(ticker)
                temp_price = get_stock_price_data(ticker, "2021-01-01", datetime.today().strftime('%Y-%m-%d'))
                
                if not temp_price.empty:
                    col_p = '영업이익' if "POR" in val_type else '당기순이익'
                    stocks_count = get_stocks_count(ticker_row, ticker)
                    
                    raw_m = pd.to_numeric(temp_fin[col_p], errors='coerce').values
                    cur_m = pd.Series(raw_m).ffill().bfill().values * 100_000_000 / stocks_count
                    
                    band_dates_ts = temp_fin['Plot_Date'].map(datetime.timestamp).values
                    ext_interp = np.interp(temp_price.index.map(datetime.timestamp).values, band_dates_ts, cur_m)
                    
                    valid_idx = ext_interp > 0
                    daily_val = np.full(len(temp_price), np.nan)
                    daily_val[valid_idx] = temp_price['Close'].values[valid_idx] / ext_interp[valid_idx]
                    
                    valid_hist_mult = daily_val[~np.isnan(daily_val)]
                    if len(valid_hist_mult) > 0:
                        realistic_mults = valid_hist_mult[(valid_hist_mult > 0) & (valid_hist_mult < 300)]
                        if len(realistic_mults) > 0:
                            q_min = np.percentile(realistic_mults, 5)
                            q_max = np.percentile(realistic_mults, 95)
                            filtered_mult = realistic_mults[(realistic_mults >= q_min) & (realistic_mults <= q_max)]
                            if len(filtered_mult) > 0:
                                avg_m = np.mean(filtered_mult)
                                st.session_state.target_mult = max(1, int(round(avg_m)))
                st.session_state.last_ticker = ticker

    st.markdown("<div class='search-container'><div class='search-label'>목표배수:</div><div class='search-input-wrap'>", unsafe_allow_html=True)
    target_mult = st.number_input("목표배수", value=st.session_state.target_mult, step=1, format="%d", label_visibility="collapsed")
    st.markdown("</div></div>", unsafe_allow_html=True)

    if corp_name:
        with st.spinner("데이터 분석 중..."):
            listing = get_ticker_listing()
            ticker_row = listing[listing['Name'].str.upper() == corp_name.upper()]
            
            if ticker_row.empty:
                st.error("❌ 종목을 찾을 수 없습니다. 종목명을 정확히 입력해주세요.")
            else:
                ticker = ticker_row['Code'].values[0]
                stocks_count = get_stocks_count(ticker_row, ticker)

                fin_df = get_hybrid_financials(ticker)
                df_price = get_stock_price_data(ticker, "2021-01-01", datetime.today().strftime('%Y-%m-%d'))
                
                if not df_price.empty:
                    curr_p = df_price.iloc[-1]['Close']
                    prev_p = df_price.iloc[-2]['Close'] if len(df_price) > 1 else curr_p
                    curr_marcap = (curr_p * stocks_count) / 100_000_000
                    updown = ((curr_p / prev_p) - 1) * 100
                    
                    st.markdown(f"<div class='sub-header'>📊 {corp_name} ({ticker})</div>", unsafe_allow_html=True)
                    
                    st.markdown("<div class='sub-header' style='margin-top:10px; font-size:15px !important;'>📝 연도별 재무 상세 <span style='color:red; font-size:12px; font-weight:normal;'>(※ 값 수정 시 하단 밸류 즉시 재측정)</span></div>", unsafe_allow_html=True)
                    edited_df = st.data_editor(
                        fin_df[['Label', '매출액', '영업이익', '당기순이익']], 
                        hide_index=True, 
                        use_container_width=True, 
                        key=f"editor_{ticker}"
                    )
                    
                    fin_df['매출액'] = edited_df['매출액'].values
                    fin_df['영업이익'] = edited_df['영업이익'].values
                    fin_df['당기순이익'] = edited_df['당기순이익'].values
                    
                    y1, y2 = datetime.today().year, datetime.today().year + 1
                    col_p = '영업이익' if "POR" in val_type else '당기순이익'
                    band_name = "POR" if "POR" in val_type else "PER"
                    
                    def get_t(y):
                        v = fin_df[fin_df['Year'] == y][col_p].values
                        if len(v) > 0 and pd.notna(v[0]) and v[0] > 0:
                            tp = float((v[0] * 100_000_000 / stocks_count) * target_mult)
                            return tp, float(((tp/curr_p)-1)*100), float((tp*stocks_count)/100_000_000)
                        return 0, 0, 0
                    
                    tp1, up1, tm1 = get_t(y1); tp2, up2, tm2 = get_t(y2)
                    last_date_str = df_price.index[-1].strftime('%m.%d')

                    st.markdown("<div style='height: 10px;'></div>", unsafe_allow_html=True)
                    col1, col2, col3 = st.columns(3)
                    
                    with col1:
                        rate_str = f"{updown:+.2f}%"
                        st.markdown(make_card_ui(f"현재가 ({last_date_str})", f"{curr_p:,.0f}원", f"{curr_marcap:,.0f}억", rate_str, updown > 0, is_zero=(updown==0)), unsafe_allow_html=True)
                    
                    with col2:
                        if tp1 > 0:
                            rate_str = f"목표대비 {up1:+.1f}%"
                            st.markdown(make_card_ui(f"목표가 ({str(y1)[-2:]}년)", f"{tp1:,.0f}원", f"{tm1:,.0f}억", rate_str, up1 > 0), unsafe_allow_html=True)
                        else:
                            st.markdown(make_card_ui(f"목표가 ({str(y1)[-2:]}년)", "N/A", "-", "데이터 없음", False, is_zero=True), unsafe_allow_html=True)

                    with col3:
                        if tp2 > 0:
                            rate_str = f"목표대비 {up2:+.1f}%"
                            st.markdown(make_card_ui(f"목표가 ({str(y2)[-2:]}년)", f"{tp2:,.0f}원", f"{tm2:,.0f}억", rate_str, up2 > 0), unsafe_allow_html=True)
                        else:
                            st.markdown(make_card_ui(f"목표가 ({str(y2)[-2:]}년)", "N/A", "-", "데이터 없음", False, is_zero=True), unsafe_allow_html=True)

                    st.markdown("<div class='sub-header' style='margin-top:20px;'>📉 밸류에이션 차트</div>", unsafe_allow_html=True)
                    
                    chart_period = st.radio("Choose a date range", ["1년", "3년", "5년", "전체"], index=3, horizontal=True)
                    
                    end_date_dt = df_price.index[-1]
                    if chart_period == "1년": start_date_chart = end_date_dt - pd.DateOffset(years=1)
                    elif chart_period == "3년": start_date_chart = end_date_dt - pd.DateOffset(years=3)
                    elif chart_period == "5년": start_date_chart = end_date_dt - pd.DateOffset(years=5)
                    else: start_date_chart = pd.to_datetime("2021-01-01")

                    future_dates = pd.date_range(start=df_price.index[-1], end=pd.to_datetime('2028-02-28'), freq='D')
                    extended_dates = df_price.index.append(future_dates[1:])
                    
                    raw_metrics = pd.to_numeric(fin_df[col_p], errors='coerce').values
                    cur_metrics = pd.Series(raw_metrics).ffill().bfill().values * 100_000_000 / stocks_count
                    cur_metrics = np.where(cur_metrics <= 0, 0.1, cur_metrics)
                    
                    band_dates_ts = fin_df['Plot_Date'].map(datetime.timestamp).values
                    ext_interp = np.interp(extended_dates.map(datetime.timestamp).values, band_dates_ts, cur_metrics)
                    
                    today_metric = ext_interp[len(df_price)-1]
                    today_m = float(curr_p / today_metric) if today_metric > 0 else 0
                    
                    interp_history = ext_interp[:len(df_price)]
                    daily_val = np.full(len(df_price), np.nan)
                    valid_idx = interp_history > 0
                    daily_val[valid_idx] = df_price['Close'].values[valid_idx] / interp_history[valid_idx]
                    
                    valid_hist_mult = daily_val[~np.isnan(daily_val)]
                    bands = []
                    avg_m_val = 0
                    if len(valid_hist_mult) > 0:
                        realistic_mults = valid_hist_mult[(valid_hist_mult > 0) & (valid_hist_mult < 300)]
                        if len(realistic_mults) > 0:
                            q_min = np.percentile(realistic_mults, 5)
                            q_max = np.percentile(realistic_mults, 95)
                            filtered_mult = realistic_mults[(realistic_mults >= q_min) & (realistic_mults <= q_max)]
                            
                            if len(filtered_mult) > 0:
                                avg_m_val = np.mean(filtered_mult)
                                mn, mx = np.min(filtered_mult), np.max(filtered_mult)
                                if mx <= mn: mx = mn + 5
                                stp = (mx - mn) / 3
                                bands = sorted(list(set([round(mn + (stp * i), 1) for i in range(4) if mn+(stp*i) > 0])))

                    x_range = [start_date_chart, end_date_dt + timedelta(days=120)]
                    cols = ['#1f77b4', '#ff7f0e', '#2ca02c', '#9467bd']

                    # --- 1. 상단 차트 (주가 vs 밴드) ---
                    fig1 = go.Figure()
                    fig1.add_trace(go.Scatter(x=df_price.index, y=df_price['Close'], mode='lines', name='주가', line=dict(color='var(--text-color)', width=1.5)))
                    
                    for i, b in enumerate(bands):
                        if pd.notna(b):
                            band_y = np.where(ext_interp > 0, ext_interp * float(b), np.nan)
                            fig1.add_trace(go.Scatter(x=extended_dates, y=band_y, mode='lines', name=f'{b}x', line=dict(color=cols[i%4], width=1, dash='dot')))
                    
                    if avg_m_val > 0:
                        avg_y = np.where(ext_interp > 0, ext_interp * avg_m_val, np.nan)
                        fig1.add_trace(go.Scatter(x=extended_dates, y=avg_y, mode='lines', name='AvgVal', line=dict(color='green', width=1.5)))
                        avg_y_curr = ext_interp[len(df_price)-1] * avg_m_val
                        if avg_y_curr > 0:
                            fig1.add_annotation(x=df_price.index[-1], y=avg_y_curr, text=f"Avg: {avg_m_val:.1f}x", showarrow=True, arrowhead=2, ax=-40, ay=30, font=dict(size=11, color="white", weight="bold"), bgcolor="rgba(0,128,0,0.8)", bordercolor="green", borderwidth=1, borderpad=4)

                    if today_m > 0 and today_m < 300:
                        today_line_y = np.where(ext_interp > 0, ext_interp * today_m, np.nan)
                        fig1.add_trace(go.Scatter(x=extended_dates, y=today_line_y, mode='lines', name='현재Val', line=dict(color='red', width=1.5)))
                        fig1.add_annotation(x=df_price.index[-1], y=curr_p, text=f"현재: {today_m:.1f}x", showarrow=True, arrowhead=2, ax=-40, ay=-30, font=dict(size=11, color="white", weight="bold"), bgcolor="rgba(255,0,0,0.8)", bordercolor="red", borderwidth=1, borderpad=4)

                    target_line_y = np.where(ext_interp > 0, ext_interp * target_mult, np.nan)
                    fig1.add_trace(go.Scatter(x=extended_dates, y=target_line_y, mode='lines', name='목표Val', line=dict(color='blue', width=1.5)))
                    if tp1 > 0:
                        fig1.add_annotation(x=fin_df[fin_df['Year'] == y1]['Plot_Date'].iloc[0], y=tp1, text=f"목표: {target_mult}x", showarrow=True, arrowhead=2, ax=-40, ay=-30, font=dict(size=11, color="white", weight="bold"), bgcolor="rgba(0,0,255,0.8)", bordercolor="blue", borderwidth=1, borderpad=4)

                    df_filtered_price = df_price[df_price.index >= start_date_chart]
                    y_min = df_filtered_price['Close'].min() * 0.85 if not df_filtered_price.empty else df_price['Close'].min() * 0.85
                    y_max_cands = [df_filtered_price['Close'].max() if not df_filtered_price.empty else curr_p]
                    if tp1 > 0: y_max_cands.append(tp1)
                    if tp2 > 0: y_max_cands.append(tp2)
                    y_max = max(y_max_cands) * 1.15
                    
                    fig1.update_yaxes(range=[y_min * 0.8, y_max])
                    fig1.update_xaxes(range=x_range, tickmode='array', tickvals=fin_df['Plot_Date'], ticktext=[f"{str(y)[-2:]}년" for y in fin_df['Year']], showticklabels=True)
                    
                    fig1.update_layout(
                        height=400, 
                        margin=dict(l=0, r=50, t=100, b=10), 
                        title=dict(text=f"[{band_name} 밴드]", x=0.01, y=0.99, font=dict(size=14)),
                        legend=dict(orientation="h", yanchor="bottom", y=1.15, xanchor="left", x=0, font=dict(size=10)),
                        hovermode="x unified", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)"
                    )
                    st.plotly_chart(fig1, use_container_width=True, config={'staticPlot': True})

                    # --- 2. 하단 차트 (과거 밸류에이션 추이) ---
                    st.write("")
                    fig2 = go.Figure()
                    fig2.add_trace(go.Scatter(x=df_price.index, y=daily_val, mode='lines', name='당일Val', line=dict(color='var(--text-color)', width=1.5)))
                    
                    x_start, x_end = df_price.index[0], extended_dates[-1]
                    
                    # 💡 [핵심 UI] 라벨이 서로 겹치지 않게 가로로 살짝 엇갈리게 배치하는 꼼수 좌표
                    x_pos_avg = start_date_chart + timedelta(days=10)
                    x_pos_today = start_date_chart + timedelta(days=60)
                    x_pos_target = start_date_chart + timedelta(days=110)

                    for i, b in enumerate(bands):
                        if pd.notna(b):
                            fig2.add_trace(go.Scatter(x=[x_start, x_end], y=[float(b), float(b)], mode='lines', name=f'{b}x', line=dict(color=cols[i%4], width=1, dash='dash')))
                            # 밴드 숫자(점선 우측)는 그대로 유지
                            fig2.add_annotation(x=extended_dates[-1] + timedelta(days=15), y=float(b), text=f"{b}x", showarrow=False, xanchor="left", yanchor="middle", font=dict(size=11, color=cols[i%4], weight="bold"))
                    
                    if avg_m_val > 0:
                        fig2.add_trace(go.Scatter(x=[x_start, x_end], y=[avg_m_val, avg_m_val], mode='lines', name=f'Avg {avg_m_val:.1f}x', line=dict(color='green', width=2)))
                        # 💡 차트 첫 부분(좌측)에 위 그래프와 동일한 뱃지 적용
                        fig2.add_annotation(x=x_pos_avg, y=avg_m_val, text=f"Avg: {avg_m_val:.1f}x", showarrow=False, xanchor="left", yanchor="bottom", font=dict(size=11, color="white", weight="bold"), bgcolor="rgba(0,128,0,0.8)", bordercolor="green", borderwidth=1, borderpad=4)
                    
                    y2_max = max([bands[-1]*1.1 if bands else 30, target_mult*1.2])
                    if today_m > 0 and today_m < 300: 
                        fig2.add_trace(go.Scatter(x=[x_start, x_end], y=[today_m, today_m], mode='lines', name='현재Val', line=dict(color='red', width=1.5)))
                        # 💡 차트 첫 부분(좌측)에 뱃지 적용 (기존 우측 텍스트 삭제)
                        fig2.add_annotation(x=x_pos_today, y=today_m, text=f"현재: {today_m:.1f}x", showarrow=False, xanchor="left", yanchor="bottom", font=dict(size=11, color="white", weight="bold"), bgcolor="rgba(255,0,0,0.8)", bordercolor="red", borderwidth=1, borderpad=4)
                        y2_max = max(y2_max, today_m * 1.2)
                        
                    fig2.add_trace(go.Scatter(x=[x_start, x_end], y=[target_mult, target_mult], mode='lines', name='목표Val', line=dict(color='blue', width=1.5)))
                    # 💡 차트 첫 부분(좌측)에 뱃지 적용
                    fig2.add_annotation(x=x_pos_target, y=target_mult, text=f"목표: {target_mult}x", showarrow=False, xanchor="left", yanchor="bottom", font=dict(size=11, color="white", weight="bold"), bgcolor="rgba(0,0,255,0.8)", bordercolor="blue", borderwidth=1, borderpad=4)
                    
                    fig2.update_yaxes(range=[0, y2_max])
                    
                    bottom_x_labels = [f"{str(row['Year'])[-2:]}년<br>{row.get(col_p, 0):,.0f}억" for _, row in fin_df.iterrows()]
                    fig2.update_xaxes(range=x_range, tickmode='array', tickvals=fin_df['Plot_Date'], ticktext=bottom_x_labels, showticklabels=True)
                    
                    fig2.update_layout(
                        height=300, 
                        margin=dict(l=0, r=50, t=100, b=50), 
                        title=dict(text=f"[평균 {band_name} 밴드]", x=0.01, y=0.99, font=dict(size=14)),
                        showlegend=False, # 💡 [수정] 하단 차트 범례 완전 제거
                        hovermode="x unified", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)"
                    )
                    st.plotly_chart(fig2, use_container_width=True, config={'staticPlot': True})

                else:
                    st.error("❌ 주가 데이터를 불러오는 데 실패했습니다. 종목명을 확인하거나 잠시 후 다시 시도해주세요.")

    else:
        st.info("👆 상단에 종목명을 입력하고 갱신 버튼을 눌러주세요!")
        
    st.markdown("<div style='height: 50px;'></div>", unsafe_allow_html=True)
