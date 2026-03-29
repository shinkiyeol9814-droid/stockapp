import streamlit as st
import FinanceDataReader as fdr
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import io
import re
import requests
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# --- 페이지 기본 설정 ---
st.set_page_config(page_title="StkPro 가치평가", page_icon="📈", layout="wide")

# 💡 강제 라이트모드 절대 금지 (테마 적응형) 및 수직 정렬 유지 CSS
st.markdown("""
    <style>
        /* 타이틀 여백 정상화 */
        .block-container { padding-top: 2.5rem !important; padding-bottom: 1rem !important; padding-left: 0.8rem !important; padding-right: 0.8rem !important; }
        .main-title { font-size: 1.4rem !important; font-weight: bold; margin-top: 1rem; margin-bottom: 1rem; }
        .sub-header { font-size: 1.1rem !important; font-weight: bold; margin-top: 10px; margin-bottom: 10px; }
        
        /* 카드형 UI (테마에 맞춰 투명도로 색상 조절) */
        .info-box { background-color: rgba(128, 128, 128, 0.05) !important; padding: 12px 15px; border-radius: 10px; margin-bottom: 15px; border: 1px solid rgba(128, 128, 128, 0.2) !important; box-shadow: 0 2px 4px rgba(0,0,0,0.05); }
        
        /* 💡 글자 들뜸 방지: 수직 중앙 정렬(align-items: center) 유지 */
        .info-row { 
            display: flex; 
            flex-direction: row; 
            justify-content: flex-start; 
            align-items: center !important; 
            border-bottom: 1px solid rgba(128, 128, 128, 0.1) !important; 
            padding-bottom: 8px; 
            margin-bottom: 8px; 
            white-space: nowrap; 
            overflow-x: auto; 
            height: 24px; 
        }
        .info-row:last-child { border-bottom: none; padding-bottom: 0; margin-bottom: 0; }
        
        /* 다크모드 대응: 색상을 강제(#000)하지 않고 투명도(opacity)로 조절 */
        .col-title { width: 90px; font-weight: bold; font-size: 13px; text-align: left; flex-shrink: 0; line-height: 1.5; margin-top: 2px; opacity: 0.85; }
        .col-divider { margin: 0 8px; flex-shrink: 0; line-height: 1.5; opacity: 0.3; }
        .col-price { width: 80px; font-weight: bold; font-size: 15px; text-align: right; flex-shrink: 0; line-height: 1.5; }
        .col-marcap { width: 75px; font-size: 12px; text-align: right; flex-shrink: 0; line-height: 1.5; opacity: 0.6; }
        .col-rate { width: 100px; font-weight: bold; font-size: 14px; text-align: right; flex-shrink: 0; line-height: 1.5; }
        
        /* 등락률 색상은 테마 무관하게 강제 유지 */
        .rate-up { color: #ff4b4b !important; }
        .rate-down { color: #0068c9 !important; }
        .rate-none { opacity: 0.5; }

        /* 검색폼 한 줄 정렬 */
        .search-container { display: flex; align-items: center; margin-bottom: 10px; width: 100%; }
        .search-label { font-size: 14px; font-weight: bold; margin-right: 10px; white-space: nowrap; }
        .search-input-wrap { flex-grow: 1; margin-right: 8px; }
        .search-btn-wrap { width: 60px; flex-shrink: 0; }
        
        /* Streamlit 폼 컨트롤 여백 제거 */
        .stTextInput, .stSelectbox, .stNumberInput { margin-bottom: -15px !important; }
        .stTextInput > div > div > input, .stSelectbox > div > div > div, .stNumberInput > div > div > input { 
            height: 36px !important; min-height: 36px !important; font-size: 13px !important; padding: 0 8px !important; 
        }
        
        div.stButton > button { 
            height: 36px !important; min-height: 36px !important; padding: 0 !important; width: 100% !important;
            background-color: #ff4b4b !important; color: white !important; font-weight: bold !important; border-radius: 6px !important; border: none !important; 
        }
        
        /* 테이블 폰트 */
        [data-testid="stDataFrame"] { font-size: 12px !important; }
    </style>
""", unsafe_allow_html=True)

if 'history' not in st.session_state:
    st.session_state.history = []

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
        res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
        df = pd.read_html(io.StringIO(res.text), header=0)[0]
        df = df.rename(columns={'회사명': 'Name', '종목코드': 'Code'})
        df['Code'] = df['Code'].astype(str).str.zfill(6)
        df['Market'], df['Stocks'], df['Marcap'] = 'KOSPI', 0, 0
        return df
    except: return pd.DataFrame(columns=['Code', 'Name', 'Stocks', 'Marcap'])

@st.cache_data 
def get_stock_price_data(ticker, start_date, end_date):
    return fdr.DataReader(ticker, start_date, end_date)

def parse_and_filter_html(html):
    dfs = pd.read_html(io.StringIO(html))
    target_df = None
    for df in dfs:
        if 'IFRS' in " ".join([str(c) for c in df.columns]): target_df = df.copy(); break
    if target_df is None: return None
    target_df.index = target_df.iloc[:, 0].astype(str).str.strip().str.replace(' ', '')
    date_cols = [c for c in target_df.columns if re.search(r'\d{4}', str(c))]
    target_df = target_df[date_cols]
    return target_df

@st.cache_data
def get_hybrid_financials(ticker):
    target_years = [2021, 2022, 2023, 2024, 2025, 2026, 2027]
    master_dict = {y: {'매출액': np.nan, '영업이익': np.nan, '당기순이익': np.nan} for y in target_years}
    try:
        main_url = f"https://navercomp.wisereport.co.kr/v2/company/c1010001.aspx?cmp_cd={ticker}"
        main_res = requests.get(main_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        encparam = ""
        match = re.search(r"encparam\s*:\s*'([^']+)'", main_res.text)
        if match: encparam = match.group(1)
        urls = [
            f"https://navercomp.wisereport.co.kr/v2/company/ajax/cF1001.aspx?cmp_cd={ticker}&fin_typ=0&freq_typ=Y&encparam={encparam}",
            f"https://navercomp.wisereport.co.kr/v2/company/ajax/cF3002.aspx?cmp_cd={ticker}&fin_typ=0&freq_typ=Y&encparam={encparam}"
        ]
        for url in urls:
            res = requests.get(url, headers={"User-Agent": "Mozilla/5.0", "Referer": main_url}, timeout=10)
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

# ==========================================
# 💡 사이드바 메뉴
# ==========================================
st.sidebar.title("🧭 메뉴")
menu = st.sidebar.radio("이동", ["📈 가치평가 시뮬레이터", "📰 관심종목 - 뉴스", "📝 증권사 레포트", "🛠️ 업데이트 이력"])

if menu == "📈 가치평가 시뮬레이터":
    
    st.markdown("<div class='main-title'>📈 가치평가 시뮬레이터</div>", unsafe_allow_html=True)
    
    # 검색폼 한 줄 정렬
    col_input, col_btn = st.columns([1, 0.2])
    with col_input:
        st.markdown("<div class='search-container'><div class='search-label'>종목명</div><div class='search-input-wrap'>", unsafe_allow_html=True)
        corp_name = st.text_input("종목명", value="", placeholder="예: 삼성전자", label_visibility="collapsed").strip()
        st.markdown("</div></div>", unsafe_allow_html=True)
    with col_btn:
        st.markdown("<div style='margin-top:2px;'></div>", unsafe_allow_html=True)
        search_clicked = st.button("갱신", use_container_width=True)

    # 평가방식 / 목표배수 나란히
    st.write("")
    col_type, col_mult = st.columns(2)
    with col_type:
        st.markdown("<div class='search-container'><div class='search-label'>평가방식</div><div class='search-input-wrap'>", unsafe_allow_html=True)
        val_type = st.selectbox("평가방식", ["PER(순이익)", "POR(영업익)"], label_visibility="collapsed")
        st.markdown("</div></div>", unsafe_allow_html=True)
    with col_mult:
        st.markdown("<div class='search-container'><div class='search-label'>목표배수</div><div class='search-input-wrap'>", unsafe_allow_html=True)
        target_mult = st.number_input("목표배수", value=10.0, step=0.5, format="%.1f", label_visibility="collapsed")
        st.markdown("</div></div>", unsafe_allow_html=True)

    st.write("")

    if corp_name:
        with st.spinner("데이터 분석 중..."):
            listing = get_ticker_listing()
            ticker_row = listing[listing['Name'].str.upper() == corp_name.upper()]
            
            if ticker_row.empty:
                st.error("❌ 종목을 찾을 수 없습니다.")
            else:
                ticker = ticker_row['Code'].values[0]
                stocks_count = ticker_row['Stocks'].values[0]
                if stocks_count <= 0:
                    try:
                        res = requests.get(f"https://m.stock.naver.com/api/stock/{ticker}/integration", timeout=5).json()
                        stocks_count = int(res['stockEndType']['totalInfo']['stockCount'])
                    except: stocks_count = 1

                fin_df = get_hybrid_financials(ticker)
                df_price = get_stock_price_data(ticker, "2021-01-01", datetime.today())
                
                if not df_price.empty:
                    curr_p = df_price.iloc[-1]['Close']
                    prev_p = df_price.iloc[-2]['Close'] if len(df_price) > 1 else curr_p
                    curr_marcap = (curr_p * stocks_count) / 100_000_000
                    updown = ((curr_p / prev_p) - 1) * 100
                    
                    st.markdown(f"<div class='sub-header'>📊 {corp_name} ({ticker})</div>", unsafe_allow_html=True)
                    
                    y1, y2 = datetime.today().year, datetime.today().year + 1
                    col_p = '영업이익' if "POR" in val_type else '당기순이익'
                    
                    def get_t(y):
                        v = fin_df[fin_df['Year'] == y][col_p].values
                        if len(v) > 0 and pd.notna(v[0]) and v[0] > 0:
                            try:
                                tp = float((v[0] * 100_000_000 / stocks_count) * target_mult)
                                return tp, float(((tp/curr_p)-1)*100), float((tp*stocks_count)/100_000_000)
                            except: return 0, 0, 0
                        return 0, 0, 0
                    
                    tp1, up1, tm1 = get_t(y1); tp2, up2, tm2 = get_t(y2)

                    html_divider = "<span class='col-divider'>|</span>"
                    
                    c_updown = "rate-up" if updown >= 0 else "rate-down"
                    c_up1 = "rate-none" if tp1 == 0 else ("rate-up" if up1 >= 0 else "rate-down")
                    c_up2 = "rate-none" if tp2 == 0 else ("rate-up" if up2 >= 0 else "rate-down")
                    
                    t_up1 = "데이터 없음" if tp1 == 0 else (f"Up: +{up1:.1f}%" if up1 >= 0 else f"Down: {up1:.1f}%")
                    t_up2 = "데이터 없음" if tp2 == 0 else (f"Up: +{up2:.1f}%" if up2 >= 0 else f"Down: {up2:.1f}%")

                    st.markdown(f"""
                    <div class='info-box'>
                        <div class='info-row'>
                            <span class='col-title'>현재가</span>
                            {html_divider}
                            <span class='col-price'>{curr_p:,.0f}원</span>
                            {html_divider}
                            <span class='col-marcap'>({curr_marcap:,.0f}억)</span>
                            {html_divider}
                            <span class='col-rate {c_updown}'>{updown:+.2f}%</span>
                        </div>
                        <div class='info-row'>
                            <span class='col-title'>목표가({str(y1)[-2:]}년)</span>
                            {html_divider}
                            <span class='col-price'>{tp1:,.0f}원</span>
                            {html_divider}
                            <span class='col-marcap'>({tm1:,.0f}억)</span>
                            {html_divider}
                            <span class='col-rate {c_up1}'>{t_up1}</span>
                        </div>
                        <div class='info-row'>
                            <span class='col-title'>목표가({str(y2)[-2:]}년)</span>
                            {html_divider}
                            <span class='col-price'>{tp2:,.0f}원</span>
                            {html_divider}
                            <span class='col-marcap'>({tm2:,.0f}억)</span>
                            {html_divider}
                            <span class='col-rate {c_up2}'>{t_up2}</span>
                        </div>
                    </div>
                    """, unsafe_allow_html=True)

                    # --- 차트 데이터 준비 ---
                    historical_metric_dict = {row['Plot_Date'].year: float(row['당기순이익' if "PER" in val_type else '영업이익']) * 100_000_000 / stocks_count for idx, row in fin_df[fin_df['Year'] <= 2024].iterrows() if pd.notna(row['당기순이익' if "PER" in val_type else '영업이익'])}
                    df_hist_daily = df_price.copy()
                    df_hist_daily['Year'] = df_hist_daily.index.year
                    df_hist_daily['Metric'] = df_hist_daily['Year'].map(historical_metric_dict).ffill().bfill()
                    
                    bands = []
                    valid_hist = df_hist_daily[pd.to_numeric(df_hist_daily['Metric'], errors='coerce') > 0].copy()
                    if not valid_hist.empty:
                        valid_hist['Mult'] = valid_hist['Close'] / valid_hist['Metric']
                        mn, mx = valid_hist['Mult'].min(), valid_hist['Mult'].max()
                        stp = (mx-mn)/3
                        if not np.isnan(mn) and not np.isnan(stp):
                            bands = sorted(list(set([round(mn + (stp * i), 1) for i in range(4) if mn+(stp*i) > 0])))

                    future_dates = pd.date_range(start=df_price.index[-1], end=pd.to_datetime('2028-02-28'), freq='D')
                    extended_dates = df_price.index.append(future_dates[1:])
                    band_dates_ts = fin_df['Plot_Date'].map(datetime.timestamp).values
                    
                    def create_valuation_chart(static_mode=False):
                        raw_metrics = pd.to_numeric(fin_df['당기순이익' if "PER" in val_type else '영업이익'], errors='coerce').values
                        cur_metrics = np.nan_to_num(raw_metrics, nan=0.001) * 100_000_000 / stocks_count
                        cur_metrics = np.where(cur_metrics <= 0, 0.001, cur_metrics)
                        
                        ext_interp = np.interp(extended_dates.map(datetime.timestamp).values, band_dates_ts, cur_metrics)
                        today_m = float(curr_p / ext_interp[len(df_price)-1]) if ext_interp[len(df_price)-1] > 0.1 else 0

                        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.05, row_heights=[0.7, 0.3])
                        
                        # 테마 반응형: 선 색상을 진한 회색(#888888)으로 설정하여 다크/라이트 모두 잘 보이게 함
                        fig.add_trace(go.Scatter(x=df_price.index, y=df_price['Close'], mode='lines', name='주가', line=dict(color='#888888', width=3)), row=1, col=1)
                        
                        cols = ['#1f77b4', '#ff7f0e', '#2ca02c', '#9467bd']
                        for i, b in enumerate(bands):
                            if pd.notna(b):
                                fig.add_trace(go.Scatter(x=extended_dates, y=ext_interp * float(b), mode='lines', name=f'{b}x', line=dict(color=cols[i%4], width=1, dash='dot')), row=1, col=1)

                        if pd.notna(today_m) and today_m > 0:
                            fig.add_trace(go.Scatter(x=extended_dates, y=ext_interp * today_m, mode='lines', name='현재Val', line=dict(color='red', width=1.5)), row=1, col=1)
                        fig.add_trace(go.Scatter(x=extended_dates, y=ext_interp * float(target_mult), mode='lines', name='목표Val', line=dict(color='blue', width=1.5)), row=1, col=1)

                        safe_metric = pd.to_numeric(df_hist_daily['Metric'], errors='coerce').replace([0, np.nan], np.inf)
                        fig.add_trace(go.Scatter(x=df_price.index, y=df_price['Close']/safe_metric, mode='lines', name='당일Val', line=dict(color='purple', width=1.5)), row=2, col=1)
                        
                        x_range = [pd.to_datetime("2021-01-01"), fin_df['Plot_Date'].max() + timedelta(days=90)]
                        fig.update_xaxes(range=x_range, showticklabels=True, row=1, col=1)
                        fig.update_xaxes(range=x_range, tickmode='array', tickvals=fin_df['Plot_Date'], ticktext=[f"{str(y)[-2:]}년" for y in fin_df['Year']], row=2, col=1)
                        fig.update_yaxes(showticklabels=True, row=1, col=1)
                        
                        # 💡 테마(다크/라이트) 완벽 반응: 폰트 색상을 빼고, 배경을 투명하게(rgba(0,0,0,0)) 처리
                        fig.update_layout(
                            height=500, margin=dict(l=0, r=0, t=50, b=0),
                            title=dict(text=f"[{'POR' if 'POR' in val_type else 'PER'} 밴드]", x=0.01, y=0.98, font=dict(size=14)),
                            legend=dict(orientation="h", yanchor="top", y=0.99, xanchor="left", x=0, font=dict(size=10)),
                            hovermode="x unified",
                            paper_bgcolor="rgba(0,0,0,0)",
                            plot_bgcolor="rgba(0,0,0,0)"
                        )
                        fig.update_xaxes(showgrid=True, gridwidth=1, gridcolor='rgba(128,128,128,0.2)')
                        fig.update_yaxes(showgrid=True, gridwidth=1, gridcolor='rgba(128,128,128,0.2)')
                        return fig

                    # 상단 인터랙티브 차트
                    main_fig = create_valuation_chart(static_mode=False)
                    st.plotly_chart(main_fig, use_container_width=True, config={'displayModeBar': True, 'scrollZoom': False})

                    st.markdown("<div class='sub-header' style='margin-top:20px;'>연도별 재무 상세 <span style='color:red; font-size:12px;'>(※ 값 수정 시 밸류 즉시 재측정)</span></div>", unsafe_allow_html=True)
                    edited_df = st.data_editor(
                        fin_df[['Label', '매출액', '영업이익', '당기순이익']],
                        column_config={
                            "Label": st.column_config.Column("연도", disabled=True),
                            "매출액": st.column_config.NumberColumn("매출(억)", format="%,d"),
                            "영업이익": st.column_config.NumberColumn("영업익(억)", format="%,d"),
                            "당기순이익": st.column_config.NumberColumn("순이익(억)", format="%,d"),
                        },
                        hide_index=True, use_container_width=True, key="financial_editor"
                    )
                    
                    fin_df['매출액'] = edited_df['매출액'].values
                    fin_df['영업이익'] = edited_df['영업이익'].values
                    fin_df['당기순이익'] = edited_df['당기순이익'].values

                    # 하단 이미지형 차트
                    st.write("---")
                    st.markdown("<div style='font-size:12px; color:gray; text-align:center;'>⬇️ 이미지형 고정 차트 (비교용)</div>", unsafe_allow_html=True)
                    img_fig = create_valuation_chart(static_mode=True)
                    st.plotly_chart(img_fig, use_container_width=True, config={'staticPlot': True})

    else:
        st.info("👆 상단에 종목명을 입력하고 갱신 버튼을 눌러주세요!")

# ==========================================
# 💡 유지 메뉴
# ==========================================
elif menu == "📰 관심종목 - 뉴스":
    st.markdown("<div class='main-title'>📰 관심종목 - 실시간 뉴스</div>", unsafe_allow_html=True)
    st.info("🛠️ 현재 서비스 준비 중입니다. 다음 업데이트를 기대해 주세요!")

elif menu == "📝 증권사 레포트":
    st.markdown("<div class='main-title'>📝 최신 증권사 레포트 요약</div>", unsafe_allow_html=True)
    st.info("🛠️ 현재 서비스 준비 중입니다. 다음 업데이트를 기대해 주세요!")

elif menu == "🛠️ 업데이트 이력":
    st.markdown("<div class='main-title'>🛠️ 업데이트 이력</div>", unsafe_allow_html=True)
    df_history = pd.DataFrame({
        "버전": ["V1.2.10 (다크모드 완벽 롤백)", "V1.2.9", "V1.2.8"],
        "업데이트 내용": [
            "강제 라이트 모드(화이트 배경) 완전 삭제, 다크/라이트 테마 자동 적응 및 텍스트 수직 중앙 정렬(들뜸 방지) 완벽 통합",
            "분석 결과 텍스트 수직 중앙 정렬(align-items) 및 들뜸 방지",
            "강제 라이트모드 추가(오류 원인) 및 제목 마진 수정"
        ]
    })
    st.dataframe(df_history, hide_index=True, use_container_width=True)
