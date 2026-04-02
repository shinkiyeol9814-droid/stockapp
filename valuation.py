import streamlit as st
import FinanceDataReader as fdr
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import io
import re
import requests
import plotly.graph_objects as go

# 공통 헤더 설정 (네이버 봇 차단 우회용)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
}

# --- 데이터 캐싱 함수들 ---
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
    try:
        return fdr.DataReader(ticker, start_date, end_date)
    except:
        return pd.DataFrame()

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
    except:
        return None

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

# 💡 [추가] 모바일 대응 깔쌈한 카드 UI 생성 함수
def make_card_ui(title, price_str, marcap_str, rate_str, is_up, is_zero=False):
    # 한국 증시 기준: 상승은 빨간색, 하락은 파란색
    if is_zero:
        color = "#888888"
        bg_color = "#f4f4f4"
    else:
        color = "#ff4b4b" if is_up else "#0068c9"
        bg_color = f"{color}15" # 투명도 15% 배경

    return f"""
    <div style="background-color: #ffffff; padding: 16px; border-radius: 12px; border: 1px solid #e0e0e0; text-align: center; box-shadow: 0 4px 6px rgba(0,0,0,0.05); margin-bottom: 15px;">
        <div style="font-size: 14px; color: #777; font-weight: 600; margin-bottom: 6px;">{title}</div>
        <div style="font-size: 24px; font-weight: 900; color: #222; margin-bottom: 4px;">{price_str}</div>
        <div style="font-size: 13px; color: #888; margin-bottom: 12px;">시총: {marcap_str}</div>
        <div style="display: inline-block; font-size: 15px; font-weight: 800; color: {color}; background-color: {bg_color}; padding: 4px 12px; border-radius: 8px;">
            {rate_str}
        </div>
    </div>
    """

# --- 가치평가 메뉴 UI 렌더링 함수 ---
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
                    sc = ticker_row['Stocks'].values[0] if 'Stocks' in ticker_row.columns and ticker_row['Stocks'].values[0] > 0 else 0
                    if sc <= 0:
                        try:
                            res_m = requests.get(f"https://m.stock.naver.com/api/stock/{ticker}/integration", headers=HEADERS, timeout=5).json()
                            sc = int(res_m['stockEndType']['totalInfo']['stockCount'])
                        except: sc = 1
                    
                    current_year = datetime.today().year
                    h_dict = {row['Plot_Date'].year: float(row[col_p]) * 100_000_000 / sc for idx, row in temp_fin[temp_fin['Year'] <= current_year].iterrows() if pd.notna(row[col_p])}
                    
                    temp_p = temp_price.copy()
                    metric_series = pd.Series(temp_p.index.year.map(h_dict), index=temp_p.index)
                    temp_p['Metric'] = metric_series.ffill().bfill()
                    
                    valid_m = temp_p[pd.to_numeric(temp_p['Metric'], errors='coerce') > 0].copy()
                    if not valid_m.empty:
                        avg_m = (valid_m['Close'] / valid_m['Metric']).mean()
                        if not np.isnan(avg_m) and not np.isinf(avg_m):
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
                
                stocks_count = ticker_row['Stocks'].values[0] if 'Stocks' in ticker_row.columns and ticker_row['Stocks'].values[0] > 0 else 0
                if stocks_count <= 0:
                    try:
                        res = requests.get(f"https://m.stock.naver.com/api/stock/{ticker}/integration", headers=HEADERS, timeout=5).json()
                        stocks_count = int(res['stockEndType']['totalInfo']['stockCount'])
                    except: stocks_count = 1

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

                    # 💡 [핵심 반영 1] 모바일 친화적인 3분할 카드 UI 적용
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


                    # 💡 [핵심 반영 2] 차트 기간 설정 필터 추가
                    st.markdown("<div class='sub-header' style='margin-top:15px;'>📉 밸류에이션 차트</div>", unsafe_allow_html=True)
                    chart_period = st.radio("조회 기간", ["1년", "3년", "5년", "전체"], index=3, horizontal=True, label_visibility="collapsed")
                    
                    end_date_dt = df_price.index[-1]
                    if chart_period == "1년":
                        start_date_chart = end_date_dt - pd.DateOffset(years=1)
                    elif chart_period == "3년":
                        start_date_chart = end_date_dt - pd.DateOffset(years=3)
                    elif chart_period == "5년":
                        start_date_chart = end_date_dt - pd.DateOffset(years=5)
                    else:
                        start_date_chart = pd.to_datetime("2021-01-01")

                    # --- 차트 데이터 로직 ---
                    current_year = datetime.today().year
                    historical_metric_dict = {row['Plot_Date'].year: float(row[col_p]) * 100_000_000 / stocks_count for idx, row in fin_df[fin_df['Year'] <= current_year].iterrows() if pd.notna(row[col_p])}
                    df_hist_daily = df_price.copy()
                    df_hist_daily['Year'] = df_hist_daily.index.year
                    
                    metric_series2 = pd.Series(df_hist_daily['Year'].map(historical_metric_dict), index=df_hist_daily.index)
                    df_hist_daily['Metric'] = metric_series2.ffill().bfill().values
                    
                    bands = []
                    avg_m_val = 0
                    valid_hist = df_hist_daily[pd.to_numeric(df_hist_daily['Metric'], errors='coerce') > 0].copy()
                    if not valid_hist.empty:
                        valid_hist['Mult'] = valid_hist['Close'] / valid_hist['Metric']
                        avg_m_val = valid_hist['Mult'].mean()
                        mn, mx = valid_hist['Mult'].min(), valid_hist['Mult'].max(); stp = (mx-mn)/3
                        bands = sorted(list(set([round(mn + (stp * i), 1) for i in range(4) if mn+(stp*i) > 0])))

                    future_dates = pd.date_range(start=df_price.index[-1], end=pd.to_datetime('2028-02-28'), freq='D')
                    extended_dates = df_price.index.append(future_dates[1:])
                    band_dates_ts = fin_df['Plot_Date'].map(datetime.timestamp).values
                    raw_metrics = pd.to_numeric(fin_df[col_p], errors='coerce').values
                    cur_metrics = np.nan_to_num(raw_metrics, nan=0.001) * 100_000_000 / stocks_count
                    cur_metrics = np.where(cur_metrics <= 0, 0.001, cur_metrics)
                    ext_interp = np.interp(extended_dates.map(datetime.timestamp).values, band_dates_ts, cur_metrics)
                    today_m = float(curr_p / ext_interp[len(df_price)-1]) if ext_interp[len(df_price)-1] > 0.1 else 0
                    
                    # 💡 X축 범위를 필터 선택값으로 제한 (미래 예측 밴드는 유지)
                    x_range = [start_date_chart, fin_df['Plot_Date'].max() + timedelta(days=90)]
                    cols = ['#1f77b4', '#ff7f0e', '#2ca02c', '#9467bd']

                    fig1 = go.Figure()
                    fig1.add_trace(go.Scatter(x=df_price.index, y=df_price['Close'], mode='lines', name='주가', line=dict(color='var(--text-color)', width=1.5)))
                    
                    for i, b in enumerate(bands):
                        if pd.notna(b):
                            fig1.add_trace(go.Scatter(x=extended_dates, y=ext_interp * float(b), mode='lines', name=f'{b}x', line=dict(color=cols[i%4], width=1, dash='dot')))
                    
                    if avg_m_val > 0:
                        fig1.add_trace(go.Scatter(x=extended_dates, y=ext_interp * avg_m_val, mode='lines', name='AvgVal', line=dict(color='green', width=1.5)))
                        avg_y_curr = ext_interp[len(df_price)-1] * avg_m_val
                        fig1.add_annotation(x=df_price.index[-1], y=avg_y_curr, text=f"Avg: {avg_m_val:.1f}x", showarrow=True, arrowhead=2, ax=-40, ay=30, font=dict(size=11, color="white", weight="bold"), bgcolor="rgba(0,128,0,0.8)", bordercolor="green", borderwidth=1, borderpad=4)

                    if today_m > 0:
                        fig1.add_trace(go.Scatter(x=extended_dates, y=ext_interp * today_m, mode='lines', name='현재Val', line=dict(color='red', width=1.5)))
                        fig1.add_annotation(x=df_price.index[-1], y=curr_p, text=f"현재: {today_m:.1f}x", showarrow=True, arrowhead=2, ax=-40, ay=-30, font=dict(size=11, color="white", weight="bold"), bgcolor="rgba(255,0,0,0.8)", bordercolor="red", borderwidth=1, borderpad=4)

                    fig1.add_trace(go.Scatter(x=extended_dates, y=ext_interp * float(target_mult), mode='lines', name='목표Val', line=dict(color='blue', width=1.5)))
                    if tp1 > 0:
                        fig1.add_annotation(x=fin_df[fin_df['Year'] == y1]['Plot_Date'].iloc[0], y=tp1, text=f"목표: {target_mult}x", showarrow=True, arrowhead=2, ax=-40, ay=-30, font=dict(size=11, color="white", weight="bold"), bgcolor="rgba(0,0,255,0.8)", bordercolor="blue", borderwidth=1, borderpad=4)

                    # 💡 Y축 범위도 선택된 기간 내의 최저/최고가에 맞춰 줌인되게 수정
                    df_filtered_price = df_price[df_price.index >= start_date_chart]
                    y_min = df_filtered_price['Close'].min() * 0.85 if not df_filtered_price.empty else df_price['Close'].min() * 0.85
                    y_max = max([df_filtered_price['Close'].max() if not df_filtered_price.empty else curr_p, tp1, tp2]) * 1.15
                    
                    fig1.update_yaxes(range=[y_min, y_max]); fig1.update_xaxes(range=x_range, tickmode='array', tickvals=fin_df['Plot_Date'], ticktext=[f"{str(y)[-2:]}년" for y in fin_df['Year']], showticklabels=True)
                    
                    fig1.update_layout(
                        height=400, margin=dict(l=0, r=0, t=60, b=10), title=dict(text=f"[{band_name} 밴드]", x=0.01, y=0.98, font=dict(size=14)),
                        showlegend=True, legend=dict(orientation="h", yanchor="bottom", y=1.05, xanchor="left", x=0, font=dict(size=10)),
                        hovermode="x unified", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)"
                    )
                    st.plotly_chart(fig1, use_container_width=True, config={'staticPlot': True})

                    # 하단 차트
                    st.write("")
                    safe_metric = pd.to_numeric(df_hist_daily['Metric'], errors='coerce').replace([0, np.nan], np.inf)
                    fig2 = go.Figure()
                    fig2.add_trace(go.Scatter(x=df_price.index, y=df_price['Close']/safe_metric, mode='lines', name='당일Val', line=dict(color='var(--text-color)', width=1.5)))
                    
                    x_start, x_end = df_price.index[0], extended_dates[-1]
                    for i, b in enumerate(bands):
                        if pd.notna(b):
                            fig2.add_trace(go.Scatter(x=[x_start, x_end], y=[float(b), float(b)], mode='lines', name=f'{b}x', line=dict(color=cols[i%4], width=1, dash='dash')))
                            fig2.add_annotation(x=extended_dates[-1] + timedelta(days=15), y=float(b), text=f"{b}x", showarrow=False, xanchor="left", yanchor="middle", font=dict(size=11, color=cols[i%4], weight="bold"))
                    
                    if avg_m_val > 0:
                        fig2.add_trace(go.Scatter(x=[x_start, x_end], y=[avg_m_val, avg_m_val], mode='lines', name=f'Avg {avg_m_val:.1f}x', line=dict(color='green', width=2)))
                        fig2.add_annotation(x=extended_dates[-1] + timedelta(days=15), y=avg_m_val, text=f"Avg: {avg_m_val:.1f}x", showarrow=False, xanchor="left", yanchor="middle", font=dict(size=11, color="green", weight="bold"))
                    
                    if today_m > 0: 
                        fig2.add_trace(go.Scatter(x=[x_start, x_end], y=[today_m, today_m], mode='lines', name='현재Val', line=dict(color='red', width=1.5)))
                        
                    fig2.add_trace(go.Scatter(x=[x_start, x_end], y=[target_mult, target_mult], mode='lines', name='목표Val', line=dict(color='blue', width=1.5)))
                    
                    fig2.update_yaxes(range=[0, max(bands[-1]*1.1 if bands else 30, target_mult*1.2)])
                    
                    bottom_x_labels = [f"{str(row['Year'])[-2:]}년<br>{row.get(col_p, 0):,.0f}억" for _, row in fin_df.iterrows()]
                    fig2.update_xaxes(range=x_range, tickmode='array', tickvals=fin_df['Plot_Date'], ticktext=bottom_x_labels, showticklabels=True)
                    
                    fig2.update_layout(
                        height=300, margin=dict(l=0, r=0, t=60, b=0), title=dict(text=f"[평균 {band_name} 밴드]", x=0.01, y=0.98, font=dict(size=14)),
                        showlegend=True, legend=dict(orientation="h", yanchor="bottom", y=1.05, xanchor="left", x=0, font=dict(size=10)),
                        hovermode="x unified", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)"
                    )
                    st.plotly_chart(fig2, use_container_width=True, config={'staticPlot': True})

                else:
                    st.error("❌ 주가 데이터를 불러오는 데 실패했습니다. 종목명을 확인하거나 잠시 후 다시 시도해주세요.")

    else:
        st.info("👆 상단에 종목명을 입력하고 갱신 버튼을 눌러주세요!")
