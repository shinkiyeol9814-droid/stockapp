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

# 💡 모바일 가시성 및 테마 적응형 CSS
st.markdown("""
    <style>
        .main-title { font-size: 1.1rem !important; font-weight: bold; margin-top: -2.5rem; margin-bottom: 0.5rem; }
        .sub-header { font-size: 1rem !important; font-weight: bold; margin-top: 10px; margin-bottom: 5px; }
        
        /* 정보 표시 카드형 UI (세로형 가시성 확보) */
        .info-box { background-color: rgba(128, 128, 128, 0.05); padding: 12px; border-radius: 10px; margin-bottom: 15px; border: 1px solid rgba(128, 128, 128, 0.2); }
        .info-row { display: flex; flex-direction: column; gap: 2px; border-bottom: 1px solid rgba(128, 128, 128, 0.1); padding-bottom: 6px; margin-bottom: 6px; }
        .info-row:last-child { border-bottom: none; }
        .metric-label { font-size: 12px; color: gray; }
        .metric-main { font-size: 16px; font-weight: bold; }
        
        /* 💡 갱신 버튼 스타일 (종목명 옆 배치용) */
        .search-btn-container { display: flex; align-items: flex-end; padding-bottom: 2px; height: 100%; }
        .search-btn-container button { width: 100% !important; background-color: #ff4b4b !important; color: white !important; font-weight: bold !important; border-radius: 8px !important; }

        /* 테이블 폰트 및 적응형 설정 */
        [data-testid="stDataFrame"] { font-size: 11px !important; }
        
        /* 가로 여백 제거 */
        .block-container { padding-left: 0.5rem !important; padding-right: 0.5rem !important; }
    </style>
""", unsafe_allow_html=True)

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
# 💡 사이드바 메뉴 (요청사항 1: 메뉴 복구)
# ==========================================
st.sidebar.title("🧭 메뉴")
menu = st.sidebar.radio("이동", ["📈 밸류에이션 (PER/POR밴드)", "📰 관심종목 - 뉴스", "📝 증권사 레포트", "🛠️ 업데이트 이력"], label_visibility="collapsed")

if menu == "📈 밸류에이션 (PER/POR밴드)":
    st.markdown("<h1 class='main-title'>📈 가치평가 시뮬레이터</h1>", unsafe_allow_html=True)
    
    # 💡 요청사항 2: 종목명 옆에 갱신 버튼 배치
    c1, btn_col, c2, c3 = st.columns([1.5, 0.6, 1.2, 1])
    with c1: corp_name = st.text_input("🔍 종목명 (예: 삼성전자)", value="").strip()
    with btn_col: 
        st.markdown("<div class='search-btn-container'>", unsafe_allow_html=True)
        search_clicked = st.button("갱신", use_container_width=True)
        st.markdown("</div>", unsafe_allow_html=True)
    with c2: val_type = st.selectbox("기준", ["PER(순이익)", "POR(영업익)"])
    with c3: target_mult = st.number_input("목표배수", value=10.0, step=0.5, format="%.1f")

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
                    
                    # 목표가 산출 (TypeError 방지 처리)
                    y1, y2 = datetime.today().year, datetime.today().year + 1
                    col_p = '영업이익' if "POR" in val_type else '당기순이익'
                    
                    def get_t(y):
                        v = fin_df[fin_df['Year'] == y][col_p].values
                        if len(v) > 0 and pd.notna(v[0]) and v[0] > 0:
                            # float() 타입 에러 방지를 위해 명시적 변환
                            try:
                                tp = float((v[0] * 100_000_000 / stocks_count) * target_mult)
                                return tp, float(((tp/curr_p)-1)*100), float((tp*stocks_count)/100_000_000)
                            except: return 0, 0, 0
                        return 0, 0, 0
                    
                    tp1, up1, tm1 = get_t(y1); tp2, up2, tm2 = get_t(y2)

                    st.markdown(f"""
                        <div class='info-box'>
                            <div class='info-row'>
                                <span class='metric-label'>현재가</span>
                                <span class='metric-main'>{curr_p:,.0f}원 <small style='color:gray'>({curr_marcap:,.0f}억)</small> <span style='color:{"#ff4b4b" if updown>=0 else "#0068c9"}; font-size:14px;'>{updown:+.2f}%</span></span>
                            </div>
                            <div class='info-row'>
                                <span class='metric-label'>{str(y1)[-2:]}년 목표가</span>
                                <span class='metric-main'>{tp1:,.0f}원 <small style='color:gray'>({tm1:,.0f}억)</small> <span style='color:{"#ff4b4b" if up1>0 else "#0068c9"}; font-size:14px;'>상승여력: {up1:+.1f}%</span></span>
                            </div>
                            <div class='info-row'>
                                <span class='metric-label'>{str(y2)[-2:]}년 목표가</span>
                                <span class='metric-main'>{tp2:,.0f}원 <small style='color:gray'>({tm2:,.0f}억)</small> <span style='color:{"#ff4b4b" if up2>0 else "#0068c9"}; font-size:14px;'>상승여력: {up2:+.1f}%</span></span>
                            </div>
                        </div>
                    """, unsafe_allow_html=True)

                    # --- 차트 데이터 준비 ---
                    historical_metric_dict = {row['Plot_Date'].year: float(row['당기순이익' if "PER" in val_type else '영업이익']) * 100_000_000 / stocks_count for idx, row in fin_df[fin_df['Year'] <= 2024].iterrows() if pd.notna(row['당기순이익' if "PER" in val_type else '영업이익'])}
                    df_hist_daily = df_price.copy(); df_hist_daily['Metric'] = df_hist_daily.index.year.map(historical_metric_dict).ffill().bfill()
                    
                    bands = []
                    # NoneType 에러의 핵심 원인 해결: Metric이 없거나 음수일 때 필터링
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
                    
                    # 차트 생성 함수 (TypeError 방어 코드 추가)
                    def create_valuation_chart(static_mode=False):
                        # 실시간 인터폴레이션 시 None/NaN 값을 0.001로 치환하여 에러 원천 차단
                        raw_metrics = pd.to_numeric(fin_df['당기순이익' if "PER" in val_type else '영업이익'], errors='coerce').values
                        cur_metrics = np.nan_to_num(raw_metrics, nan=0.001) * 100_000_000 / stocks_count
                        cur_metrics = np.where(cur_metrics <= 0, 0.001, cur_metrics) # 음수도 방어
                        
                        ext_interp = np.interp(extended_dates.map(datetime.timestamp).values, band_dates_ts, cur_metrics)
                        today_m = float(curr_p / ext_interp[len(df_price)-1]) if ext_interp[len(df_price)-1] > 0.1 else 0

                        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.05, row_heights=[0.7, 0.3])
                        
                        fig.add_trace(go.Scatter(x=df_price.index, y=df_price['Close'], mode='lines', name='주가', line=dict(color='#666666', width=3)), row=1, col=1)
                        
                        cols = ['#1f77b4', '#ff7f0e', '#2ca02c', '#9467bd']
                        for i, b in enumerate(bands):
                            if pd.notna(b):
                                fig.add_trace(go.Scatter(x=extended_dates, y=ext_interp * float(b), mode='lines', name=f'{b}x', line=dict(color=cols[i%4], width=1, dash='dot')), row=1, col=1)

                        if pd.notna(today_m) and today_m > 0:
                            fig.add_trace(go.Scatter(x=extended_dates, y=ext_interp * today_m, mode='lines', name='현재Val', line=dict(color='red', width=1.2)), row=1, col=1)
                        fig.add_trace(go.Scatter(x=extended_dates, y=ext_interp * float(target_mult), mode='lines', name='목표Val', line=dict(color='blue', width=1.2)), row=1, col=1)

                        # 하단 차트 (ZeroDivision 방지)
                        safe_metric = pd.to_numeric(df_hist_daily['Metric'], errors='coerce').replace([0, np.nan], np.inf)
                        fig.add_trace(go.Scatter(x=df_price.index, y=df_price['Close']/safe_metric, mode='lines', name='당일Val', line=dict(color='purple', width=1.5)), row=2, col=1)
                        
                        x_range = [pd.to_datetime("2021-01-01"), fin_df['Plot_Date'].max() + timedelta(days=90)]
                        fig.update_xaxes(range=x_range, showticklabels=True, row=1, col=1)
                        fig.update_xaxes(range=x_range, tickmode='array', tickvals=fin_df['Plot_Date'], ticktext=[f"{str(y)[-2:]}년" for y in fin_df['Year']], row=2, col=1)
                        fig.update_yaxes(showticklabels=True, row=1, col=1)
                        
                        fig.update_layout(
                            height=550, margin=dict(l=5, r=5, t=60, b=10),
                            title=dict(text=f"[{'POR' if 'POR' in val_type else 'PER'} 밴드]", x=0.01, y=0.98, font=dict(size=16)),
                            legend=dict(orientation="h", yanchor="top", y=0.94, xanchor="left", x=0, font=dict(size=10)),
                            hovermode="x unified", template="none"
                        )
                        return fig

                    main_fig = create_valuation_chart(static_mode=False)
                    st.plotly_chart(main_fig, use_container_width=True, config={'displayModeBar': True, 'scrollZoom': False})

                    st.markdown("### 연도별 재무 상세 <span style='color:red; font-size:0.85rem;'>(※ 값 입력/수정하여 밸류 측정가능)</span>", unsafe_allow_html=True)
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

                    st.write("---")
                    st.markdown("<div style='font-size:12px; color:gray; text-align:center;'>⬇️ 아래는 고정 이미지형 그래프입니다 (비교용)</div>", unsafe_allow_html=True)
                    img_fig = create_valuation_chart(static_mode=True)
                    st.plotly_chart(img_fig, use_container_width=True, config={'staticPlot': True})

    else:
        st.info("👆 상단에 종목명을 입력하고 갱신 버튼을 눌러주세요!")

# ==========================================
# 💡 페이지 2: 관심종목 - 뉴스 (복구)
# ==========================================
elif menu == "📰 관심종목 - 뉴스":
    st.title("📰 관심종목 - 실시간 뉴스")
    st.write("사용자의 관심종목과 관련된 핵심 뉴스를 스크래핑하여 보여주는 공간입니다.")
    st.info("🛠️ 현재 서비스 준비 중입니다. 다음 업데이트를 기대해 주세요!")

# ==========================================
# 💡 페이지 3: 증권사 레포트 (복구)
# ==========================================
elif menu == "📝 증권사 레포트":
    st.title("📝 최신 증권사 레포트 요약")
    st.write("주요 증권사에서 발간된 리서치 자료 및 목표가 컨센서스를 요약 제공합니다.")
    st.info("🛠️ 현재 서비스 준비 중입니다. 다음 업데이트를 기대해 주세요!")

# ==========================================
# 💡 기타 메뉴: 업데이트 이력
# ==========================================
elif menu == "🛠️ 업데이트 이력":
    st.title("🛠️ 업데이트 이력")
    df_history = pd.DataFrame({
        "버전": ["V1.2.1 (핫픽스)", "V1.2.0", "V1.1.6", "V1.1.4", "V1.0.4"],
        "업데이트 내용": [
            "TypeError(NoneType) 버그 수정, 관심종목/레포트 메뉴 복구, [갱신] 버튼 종목명 옆으로 이동 및 디자인 변경",
            "메이저 UI 개편: 검색 버튼 추가, 재무표 하단 이동 및 헤더 강조, 주가 선 테마 적응형 색상 적용, 이미지형 차트 추가, 가시성 카드 UI 도입",
            "코드 노출 HTML 버그 수정 및 표 펼침 적용",
            "FnGuide 크롤링 로직 안정화 (21~27년 데이터 완벽 복구)",
            "암호키(encparam) 추출 기반 초고속 API 도입"
        ]
    })
    st.dataframe(df_history, hide_index=True, use_container_width=True)
