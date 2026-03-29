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

# 💡 모바일 가시성 극대화 CSS
st.markdown("""
    <style>
        .main-title { font-size: 1.2rem !important; font-weight: bold; margin-top: -2rem; margin-bottom: 0.5rem; }
        .sub-header { font-size: 1.1rem !important; font-weight: bold; color: #31333F; margin-top: 10px; margin-bottom: 10px; }
        
        /* 정보 표시 카드형 UI */
        .info-box { background-color: #f8f9fa; padding: 10px; border-radius: 8px; margin-bottom: 15px; font-size: 13px; line-height: 1.6; }
        .info-row { display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #eee; padding-bottom: 4px; margin-bottom: 4px; }
        .info-row:last-child { border-bottom: none; padding-bottom: 0; margin-bottom: 0; }
        
        /* 검색창 높이 조절 */
        .stTextInput > div > div > input { font-size: 13px !important; padding: 5px 8px !important; }
        
        /* 테이블 크기 및 드래그 방지 */
        [data-testid="stDataFrame"] { font-size: 11px !important; user-select: none !important; }
        [data-testid="stDataFrame"] th { padding: 4px !important; }
        [data-testid="stDataFrame"] td { padding: 4px !important; }
        
        /* Streamlit 기본 여백 제거 */
        .block-container { padding-top: 2rem !important; padding-bottom: 1rem !important; }
    </style>
""", unsafe_allow_html=True)

# --- 데이터 캐싱 함수들 (다이렉트 API) ---
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
    
    urls_to_scrape = [
        f"https://navercomp.wisereport.co.kr/v2/company/ajax/cF1001.aspx?cmp_cd={ticker}&fin_typ=0&freq_typ=Y",
        f"https://navercomp.wisereport.co.kr/v2/company/ajax/cF3002.aspx?cmp_cd={ticker}&fin_typ=0&freq_typ=Y"
    ]
    headers = {"User-Agent": "Mozilla/5.0", "Referer": f"https://finance.naver.com/item/coinfo.naver?code={ticker}"}

    for url in urls_to_scrape:
        try:
            res = requests.get(url, headers=headers, timeout=5)
            df_parsed = parse_and_filter_html(res.text)
            if df_parsed is not None:
                for c in df_parsed.columns:
                    match_yr = re.search(r'(\d{4})', str(c[-1] if isinstance(c, tuple) else str(c)))
                    if match_yr:
                        y = int(match_yr.group(1))
                        if y in target_years:
                            def get_val(patterns):
                                for p in patterns:
                                    m = [k for k in df_parsed.index if re.search(p, k)]
                                    if m:
                                        val = df_parsed.loc[m[0], c]
                                        try: return float(re.sub(r'[^\d\.-]', '', str(val))) if pd.notna(val) and str(val).strip() not in ['', '-', 'N/A'] else np.nan
                                        except: pass
                                return np.nan
                            rev = get_val([r'^(매출액|영업수익)'])
                            op = get_val([r'^영업이익\(발표기준\)', r'^영업이익$'])
                            ni = get_val([r'^당기순이익'])
                            if pd.isna(master_dict[y]['매출액']) and pd.notna(rev): master_dict[y]['매출액'] = rev
                            if pd.isna(master_dict[y]['영업이익']) and pd.notna(op): master_dict[y]['영업이익'] = op
                            if pd.isna(master_dict[y]['당기순이익']) and pd.notna(ni): master_dict[y]['당기순이익'] = ni
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


# --- 메인 UI ---
st.markdown("<h1 class='main-title'>📈 가치평가 시뮬레이터</h1>", unsafe_allow_html=True)

# 💡 최근 검색 로직 및 UI 완전 삭제 (요청사항 1번)
c1, c2, c3 = st.columns([1.5, 1.2, 1])
with c1: corp_name = st.text_input("🔍 종목명 (예: 삼성전자)", value="").strip()
with c2: val_type = st.selectbox("기준", ["PER(순이익)", "POR(영업익)"])
with c3: target_mult = st.number_input("목표배수", value=10.0, step=0.5, format="%.1f")

is_por = "POR" in val_type
band_name = "POR" if is_por else "PER"
profit_col = '영업이익' if is_por else '당기순이익'

st.markdown("<div style='margin-bottom: 5px;'></div>", unsafe_allow_html=True)

if corp_name:
    with st.spinner("데이터 분석 중..."):
        listing = get_ticker_listing()
        ticker_row = listing[listing['Name'].str.upper() == corp_name.upper()]
        
        if ticker_row.empty:
            st.error("❌ 종목을 찾을 수 없습니다.")
        else:
            ticker = ticker_row['Code'].values[0]
            official_name = ticker_row['Name'].values[0]
            stocks_count = ticker_row['Stocks'].values[0]
            
            if stocks_count <= 0:
                try:
                    res = requests.get(f"https://m.stock.naver.com/api/stock/{ticker}/integration", headers={'User-Agent': 'Mozilla/5.0'}).json()
                    stocks_count = int(res['stockEndType']['totalInfo']['stockCount'])
                except: stocks_count = 1

            fin_df = get_hybrid_financials(ticker)
            
            # 💡 차트가 21년부터만 나오도록 시작점 고정 (요청사항 8번)
            df_price = get_stock_price_data(ticker, "2021-01-01", datetime.today())
            
            if not df_price.empty:
                curr_p = df_price.iloc[-1]['Close']
                prev_p = df_price.iloc[-2]['Close'] if len(df_price) > 1 else curr_p
                curr_marcap = (curr_p * stocks_count) / 100_000_000
                curr_updown_pct = ((curr_p / prev_p) - 1) * 100
                curr_color = "#ff4b4b" if curr_updown_pct >= 0 else "#0068c9"
                
                # 💡 종목 분석 결과 글자 크기 축소 (요청사항 2번)
                st.markdown(f"<div class='sub-header'>📊 {official_name} ({ticker})</div>", unsafe_allow_html=True)
                
                year_1, year_2 = datetime.today().year, datetime.today().year + 1
                
                def calc_target(y):
                    val = fin_df[fin_df['Year'] == y][profit_col].values
                    if len(val) > 0 and pd.notna(val[0]) and val[0] > 0:
                        tp = (val[0] * 100_000_000 / stocks_count) * target_mult
                        return tp, ((tp / curr_p) - 1) * 100, (tp * stocks_count) / 100_000_000
                    return 0, 0, 0

                tp1, up1, tm1 = calc_target(year_1)
                tp2, up2, tm2 = calc_target(year_2)
                
                # 💡 가로 한 줄 표기 방식 적용 (요청사항 3번)
                html_info = f"""
                <div class='info-box'>
                    <div class='info-row'>
                        <span><b style='color:#333'>현재가:</b> {curr_p:,.0f}원 <span style='color:#888;font-size:11px'>({curr_marcap:,.0f}억)</span></span>
                        <span style='color:{curr_color}; font-weight:bold;'>{curr_updown_pct:+.2f}%</span>
                    </div>
                """
                
                if tp1 > 0:
                    c1 = "#ff4b4b" if up1 >= 0 else "#0068c9"
                    html_info += f"""
                    <div class='info-row'>
                        <span><b style='color:#333'>{str(year_1)[-2:]}년 목표:</b> {tp1:,.0f}원 <span style='color:#888;font-size:11px'>({tm1:,.0f}억)</span></span>
                        <span style='color:{c1}; font-weight:bold;'>상승여력: {up1:+.1f}%</span>
                    </div>
                    """
                else:
                    html_info += f"<div class='info-row'><span style='color:gray;'>{str(year_1)[-2:]}년 목표: 실적 데이터 없음</span></div>"
                    
                if tp2 > 0:
                    c2 = "#ff4b4b" if up2 >= 0 else "#0068c9"
                    html_info += f"""
                    <div class='info-row'>
                        <span><b style='color:#333'>{str(year_2)[-2:]}년 목표:</b> {tp2:,.0f}원 <span style='color:#888;font-size:11px'>({tm2:,.0f}억)</span></span>
                        <span style='color:{c2}; font-weight:bold;'>상승여력: {up2:+.1f}%</span>
                    </div>
                    """
                else:
                    html_info += f"<div class='info-row'><span style='color:gray;'>{str(year_2)[-2:]}년 목표: 실적 데이터 없음</span></div>"
                    
                html_info += "</div>"
                st.markdown(html_info, unsafe_allow_html=True)

                # 재무 테이블 
                with st.expander("📋 연도별 재무 상세 (입력/수정 가능)", expanded=False):
                    edited_df = st.data_editor(
                        fin_df[['Label', '매출액', '영업이익', '당기순이익']],
                        column_config={
                            "Label": st.column_config.Column("연도", disabled=True),
                            "매출액": st.column_config.NumberColumn("매출(억)", format="%,d", step=1),
                            "영업이익": st.column_config.NumberColumn("영업익(억)", format="%,d", step=1),
                            "당기순이익": st.column_config.NumberColumn("순이익(억)", format="%,d", step=1),
                        },
                        hide_index=True, use_container_width=True
                    )

                fin_df['영업이익'] = pd.to_numeric(edited_df['영업이익'], errors='coerce')
                fin_df['당기순이익'] = pd.to_numeric(edited_df['당기순이익'], errors='coerce')

                if is_por: fin_df['Metric_Per_Share'] = (fin_df['영업이익'] * 100_000_000) / stocks_count
                else: fin_df['Metric_Per_Share'] = (fin_df['당기순이익'] * 100_000_000) / stocks_count
                
                fin_df['Plot_Metric'] = fin_df['Metric_Per_Share'].apply(lambda x: 0.001 if pd.isna(x) or x <= 0 else x)

                historical_metric_dict = {row['Plot_Date'].year: row['Metric_Per_Share'] for idx, row in fin_df[fin_df['Year'] <= 2024].iterrows()}
                df_hist_daily = df_price.copy()
                df_hist_daily['Year'] = df_hist_daily.index.year
                df_hist_daily['Metric'] = df_hist_daily['Year'].map(historical_metric_dict).ffill().bfill()
                valid_hist = df_hist_daily[df_hist_daily['Metric'] > 0].copy()
                
                bands = []
                if not valid_hist.empty:
                    valid_hist['Multiple'] = valid_hist['Close'] / valid_hist['Metric']
                    min_mult, max_mult = valid_hist['Multiple'].min(), valid_hist['Multiple'].max()
                    step = (max_mult - min_mult) / 3
                    bands = sorted(list(set([round(min_mult + (step * i), 1) for i in range(4) if (min_mult + (step * i)) > 0])))

                future_dates = pd.date_range(start=df_price.index[-1], end=pd.to_datetime('2028-02-28'), freq='D')
                extended_dates = df_price.index.append(future_dates[1:])
                band_dates_ts = fin_df['Plot_Date'].map(datetime.timestamp).values
                band_metrics = fin_df['Plot_Metric'].values
                
                extended_metrics_interp = np.interp(extended_dates.map(datetime.timestamp).values, band_dates_ts, band_metrics)
                daily_metrics_interp = np.interp(df_price.index.map(datetime.timestamp).values, band_dates_ts, band_metrics)
                
                df_price['Est_Metric'] = daily_metrics_interp
                df_price['Current_Valuation'] = np.where(df_price['Est_Metric'] <= 0.002, np.nan, df_price['Close'] / df_price['Est_Metric'])
                
                today_est_metric = df_price['Est_Metric'].iloc[-1]
                today_mult = curr_p / today_est_metric if pd.notna(today_est_metric) and today_est_metric > 0.002 else np.nan

                # 💡 상/하단 X축 라벨 형식 분리 생성 (요청사항 5번)
                top_x_labels = [f"{str(d.year)[-2:]}년" for d in fin_df['Plot_Date']]
                bottom_x_labels = []
                for idx, row in fin_df.iterrows():
                    val = row.get(profit_col, pd.NA)
                    fmt = f"{val:,.0f}억" if pd.notna(val) else "-"
                    if pd.notna(val) and val <= 0: fmt = f"{val:,.0f}억(적자)"
                    bottom_x_labels.append(f"{str(row['Year'])[-2:]}년<br>{fmt}")

                # --- 차트 그리기 ---
                fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.1, row_heights=[0.7, 0.3])
                
                fig.add_trace(go.Scatter(x=df_price.index, y=df_price['Close'], mode='lines', name='현재 주가', line=dict(color='#888888', width=2)), row=1, col=1)
                
                colors = ['#1f77b4', '#ff7f0e', '#2ca02c', '#9467bd']
                for i, b in enumerate(bands):
                    fig.add_trace(go.Scatter(x=extended_dates, y=extended_metrics_interp * b, mode='lines', name=f'{b:.1f}x', line=dict(color=colors[i%len(colors)], width=1, dash='dot')), row=1, col=1)

                # 💡 실시간/목표 선 굵기 축소 (요청사항 10번)
                if pd.notna(today_mult):
                    fig.add_trace(go.Scatter(x=extended_dates, y=extended_metrics_interp * today_mult, mode='lines', name=f'현재({today_mult:.1f}x)', line=dict(color='red', width=1.2)), row=1, col=1)
                fig.add_trace(go.Scatter(x=extended_dates, y=extended_metrics_interp * target_mult, mode='lines', name=f'목표({target_mult}x)', line=dict(color='blue', width=1.2)), row=1, col=1)

                # 상단 차트 팝업
                if pd.notna(today_mult):
                    fig.add_annotation(x=df_price.index[-1], y=curr_p, text=f"현재: {today_mult:.1f}x", showarrow=True, arrowhead=2, ax=-50, ay=-35, font=dict(size=12, color="red", weight="bold"), bgcolor="rgba(255,255,255,0.8)", bordercolor="red", borderwidth=1, row=1, col=1)
                if tp1 > 0:
                    fig.add_annotation(x=fin_df[fin_df['Year'] == year_1]['Plot_Date'].iloc[0], y=tp1, text=f"목표: {target_mult:.1f}x", showarrow=True, arrowhead=2, ax=-50, ay=-35, font=dict(size=12, color="blue", weight="bold"), bgcolor="rgba(255,255,255,0.8)", bordercolor="blue", borderwidth=1, row=1, col=1)

                # 하단 차트
                valid_price = df_price[df_price['Est_Metric'] > 0.001]
                fig.add_trace(go.Scatter(x=valid_price.index, y=valid_price['Current_Valuation'], mode='lines', name=f'당일 {band_name}', line=dict(color='purple', width=1.5)), row=2, col=1)
                if pd.notna(today_mult): fig.add_hline(y=today_mult, line_dash="dash", line_color="red", line_width=1, row=2, col=1)
                fig.add_hline(y=target_mult, line_dash="solid", line_color="blue", line_width=1, row=2, col=1)

                # 💡 차트 여백 제로화 & Y축 텍스트 삭제 & X축 21년 강제 고정 (요청사항 4, 6, 7, 8번)
                # Y축 타이틀 삭제, 마진 최소화
                max_y = max([df_price['Close'].max(), curr_p, tp1, tp2]) * 1.2
                fig.update_yaxes(range=[0, max_y], title_text="", row=1, col=1)
                fig.update_yaxes(range=[0, max(bands[-1]*1.1 if bands else 30, target_mult*1.2)], title_text="", row=2, col=1)

                # X축 21년도부터 고정 (상단 차트에도 연도 표시)
                x_range = [pd.to_datetime("2021-01-01"), fin_df['Plot_Date'].max() + timedelta(days=90)]
                fig.update_xaxes(tickmode='array', tickvals=fin_df['Plot_Date'], ticktext=top_x_labels, showticklabels=True, range=x_range, row=1, col=1)
                fig.update_xaxes(tickmode='array', tickvals=fin_df['Plot_Date'], ticktext=bottom_x_labels, showticklabels=True, range=x_range, row=2, col=1)

                fig.update_layout(
                    height=550, 
                    hovermode="x unified",
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0, font=dict(size=11)),
                    margin=dict(l=0, r=0, t=30, b=0) # 극단적 타이트 마진
                )

                # 💡 터치 잠금 유지 & 우측 상단 확대 아이콘 지원 (요청사항 9번)
                st.plotly_chart(fig, use_container_width=True, config={'staticPlot': True, 'displayModeBar': False})
                st.caption("🔍 **Tip:** 이미지가 작다면 우측 상단의 **[⛶ 전체화면]** 아이콘을 눌러 크게 보세요.")

else:
    st.info("👆 상단 검색창에 분석을 원하시는 종목명을 입력해주세요! (예: 삼성전자, 에코프로비엠)")

# --- 기타 메뉴 (히스토리) ---
if menu == "🛠️ 업데이트 이력":
    st.title("🛠️ 업데이트 이력")
    df_history = pd.DataFrame({
        "버전": ["V1.1.2 (가시성 극대화)", "V1.1.1", "V1.1.0", "V1.0.4", "V1.0.0"],
        "업데이트 내용": [
            "최근검색 삭제, [현재-시총-업사이드] 가로 1줄 압축, 차트 여백 0화 및 Y축 범례 삭제, 목표선 두께 축소, 21년 시작점 고정",
            "차트 X축 뭉개짐 픽스 (시작점 21년 1월 강제 고정 및 범례 재배치)",
            "모바일 최적화 (Static 차트, 목표 시총 추가, 크기 압축)",
            "FnGuide 암호키 추출 로직으로 21~27년 데이터 누락 픽스",
            "가상 브라우저 폐기 & 초고속 API 정식 런칭"
        ]
    })
    st.markdown("""<style>[data-testid="stDataFrame"] td { text-align: left !important; } [data-testid="stDataFrame"] th { text-align: center !important; }</style>""", unsafe_allow_html=True)
    st.dataframe(df_history, hide_index=True)
