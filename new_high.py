import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
import FinanceDataReader as fdr
from datetime import datetime, timedelta
import time

# 모바일 환경으로 위장하여 보안 차단 우회
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 13; SM-S918N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Mobile Safari/537.36",
    "Referer": "https://m.stock.naver.com/"
}

@st.cache_data(ttl=3600)
def get_krx_market_data(market_filter):
    """현재 주식시장 전 종목 데이터를 가져와 활발한 주도주 1차 필터링"""
    df = fdr.StockListing('KRX')
    
    # 1차 필터: 동전주 제외, 거래량 10만 주 이상인 유의미한 종목만
    df = df[(df['Close'] >= 1000) & (df['Volume'] >= 100000)].copy()
    
    if market_filter != "전체":
        # fdr Market 컬럼은 KOSPI, KOSDAQ 등으로 표기됨
        df = df[df['Market'].str.contains(market_filter, na=False)]
        
    # 2차 필터: 오늘 신고가를 쳤다면 상승률이 높을 확률이 높음. 당일 상승률 상위 100개만 추출 (연산 속도 최적화)
    candidates = df.sort_values('ChagesRatio', ascending=False).head(100)
    return candidates

def find_new_highs(period_months, market_filter):
    """과거 주가 차트를 직접 연산하여 정확한 기간별 신고가를 찾아냄"""
    candidates = get_krx_market_data(market_filter)
    results = []
    
    # 기간 설정 (3개월=90일, 6개월=180일, 1년=365일)
    days_map = {"3개월": 90, "6개월": 180, "1년": 365}
    target_days = days_map.get(period_months, 365)
    start_date = (datetime.today() - timedelta(days=target_days)).strftime('%Y-%m-%d')
    
    progress_text = "주도주 100개 종목의 차트 패턴을 정밀 분석 중입니다..."
    my_bar = st.progress(0, text=progress_text)
    
    total = len(candidates)
    for idx, row in enumerate(candidates.itertuples()):
        code = row.Code
        name = row.Name
        
        try:
            # 해당 종목의 지정 기간 동안의 차트 데이터 호출
            hist = fdr.DataReader(code, start_date)
            if hist.empty or len(hist) < 20: continue
            
            period_max_high = hist['High'].max()
            today_high = hist['High'].iloc[-1]
            today_close = hist['Close'].iloc[-1]
            
            # 오늘 고가가 기간 내 최고가와 같거나 0.5% 이내로 돌파/접근했다면 신고가로 판별
            if today_high >= period_max_high * 0.995:
                results.append({
                    "종목명": name,
                    "코드": code,
                    "현재가": today_close,
                    "등락률": row.ChagesRatio
                })
                
                # 속도 조절을 위해 최대 10개가 찾아지면 스탑
                if len(results) >= 10: break 
        except: pass
        
        my_bar.progress((idx + 1) / total, text=progress_text)
        
    my_bar.empty()
    return pd.DataFrame(results)

def get_company_details(ticker, corp_name):
    """HTML 스크래핑이 아닌 네이버 모바일 JSON API를 사용하여 차단 완벽 우회"""
    details = {
        "사업내용": "데이터 없음",
        "PER": "N/A",
        "최신뉴스": "관련 뉴스 없음",
        "신고가사유": "모멘텀/수급 유입 (분석 불가)"
    }
    
    # 1. 기업 개요 및 PER (네이버 모바일 JSON API 활용 - 봇 차단 없음)
    api_url = f"https://m.stock.naver.com/api/stock/{ticker}/integration"
    try:
        res = requests.get(api_url, headers=HEADERS, timeout=5)
        if res.status_code == 200:
            data = res.json()
            total_info = data.get('stockEndType', {}).get('totalInfo', {})
            summary_info = data.get('stockEndType', {}).get('summaryInfo', {})
            
            if 'per' in total_info: details["PER"] = total_info['per']
            if 'summary' in summary_info: details["사업내용"] = summary_info['summary'][:70] + "..."
    except Exception as e:
        print(f"API 에러: {e}")

    # 2. 최신 뉴스 (모바일 검색 페이지는 차단이 덜함)
    news_url = f"https://m.search.naver.com/search.naver?where=m_news&sm=mtb_jum&query={corp_name}+특징주"
    news_list = []
    try:
        res = requests.get(news_url, headers=HEADERS, timeout=5)
        soup = BeautifulSoup(res.text, 'html.parser')
        news_items = soup.select('.news_tit')[:3]
        news_list = [item.text.strip() for item in news_items]
        if news_list: details["최신뉴스"] = news_list[0]
        
        # 3. 룰 베이스 사유 추론
        combined_news = " ".join(news_list)
        if any(k in combined_news for k in ['실적', '영업이익', '흑자', '어닝']):
            details["신고가사유"] = "🟢 호실적/어닝 서프라이즈 기대감"
        elif any(k in combined_news for k in ['수주', '계약', '공급', 'MOU']):
            details["신고가사유"] = "🔵 대규모 수주 및 공급 계약"
        elif any(k in combined_news for k in ['M&A', '인수', '합병', '지분']):
            details["신고가사유"] = "🟣 M&A 및 지분 투자 모멘텀"
        elif any(k in combined_news for k in ['특허', '임상', 'FDA']):
            details["신고가사유"] = "🟡 R&D, 임상/특허 호재"
        elif any(k in combined_news for k in ['테마', '관련주', '수혜주', '특징주']):
            details["신고가사유"] = "🟠 특정 테마 편입 및 단기 수급"
        elif news_list:
            details["신고가사유"] = "⚪ 개별 호재 및 수급 유입"
    except: pass

    return details

def render_new_high_menu():
    st.markdown("<div style='font-size: 1.4rem; font-weight: bold; margin-bottom: 1rem;'>🚀 신고가 주도주 트래킹 (엔진 교체판)</div>", unsafe_allow_html=True)
    st.info("💡 클라우드 차단 문제를 해결하기 위해, 당일 급등 주도주 100개의 과거 차트 데이터를 직접 연산하여 진짜 신고가 종목을 발굴합니다.")

    col1, col2 = st.columns(2)
    with col1:
        market_filter = st.selectbox("시장 선택", ["전체", "KOSPI", "KOSDAQ"])
    with col2:
        period_filter = st.selectbox("돌파 기간 기준", ["3개월", "6개월", "1년"])

    if st.button(f"실시간 {period_filter} 신고가 분석 시작", use_container_width=True):
        
        # 1. 자체 알고리즘으로 신고가 종목 추출 (IP 차단 없음)
        highs_df = find_new_highs(period_filter, market_filter)
        
        if highs_df.empty:
            st.warning("선택한 조건에 맞는 의미 있는 신고가 주도주가 오늘은 발견되지 않았습니다.")
            return

        results = []
        with st.spinner("발굴된 신고가 종목의 재무 및 뉴스 모멘텀을 수집 중입니다..."):
            for _, row in highs_df.iterrows():
                details = get_company_details(row['코드'], row['종목명'])
                
                # 등락률 포맷팅
                change_str = f"{row['등락률']:.2f}%" if pd.notna(row['등락률']) else "N/A"
                
                results.append({
                    "종목명": row['종목명'],
                    "현재가": f"{int(row['현재가']):,}원",
                    "등락률": change_str,
                    "PER": details['PER'],
                    "추정 사유": details['신고가사유'],
                    "최신뉴스": details['최신뉴스'],
                    "사업 요약": details['사업내용']
                })

        if results:
            res_df = pd.DataFrame(results)
            st.markdown("### 📊 발굴된 주도주 요약")
            st.dataframe(res_df[['종목명', '현재가', '등락률', 'PER', '추정 사유']], hide_index=True, use_container_width=True)
            
            st.markdown("### 🔍 종목별 심층 인사이트")
            for res in results:
                with st.expander(f"[{res['종목명']}] {res['추정 사유']}"):
                    st.markdown(f"**현재가:** {res['현재가']} ({res['등락률']}) | **PER:** {res['PER']}배")
                    st.markdown(f"**📰 관련 뉴스:** {res['최신뉴스']}")
                    st.markdown(f"**🏭 사업 내용:** {res['사업 요약']}")
