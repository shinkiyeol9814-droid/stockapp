import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
import FinanceDataReader as fdr
import re

# 공통 헤더
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
}

@st.cache_data(ttl=86400)
def get_krx_listing():
    """종목명 -> 종목코드 매칭을 위한 KRX 리스트"""
    df = fdr.StockListing('KRX')
    return df[['Code', 'Name', 'Market']]

@st.cache_data(ttl=3600)
def scrape_52w_highs():
    """
    1순위: 인베스팅닷컴 (요청 URL)
    2순위: 네이버 금융 (인베스팅닷컴 봇 차단 시 대체)
    """
    # 1. 인베스팅닷컴 크롤링 시도
    inv_url = "https://kr.investing.com/equities/south-korea/52-week-high"
    try:
        res = requests.get(inv_url, headers=HEADERS, timeout=5)
        if res.status_code == 200:
            dfs = pd.read_html(res.text)
            if dfs:
                df = dfs[0]
                df = df.rename(columns={'이름': '종목명', '현재가': '현재가', '변동 %': '등락률'})
                return df[['종목명', '현재가', '등락률']].head(30) # 상위 30개만
    except:
        pass # 실패 시 네이버로 넘어감

    # 2. 네이버 금융 (대체제)
    nv_url = "https://finance.naver.com/sise/sise_high_up.naver"
    try:
        res = requests.get(nv_url, headers=HEADERS, timeout=5)
        res.encoding = 'euc-kr'
        dfs = pd.read_html(res.text)
        for df in dfs:
            if '종목명' in df.columns:
                df = df.dropna(subset=['종목명'])
                df = df[df['종목명'] != '종목명']
                return df[['종목명', '현재가', '등락률']].head(30)
    except Exception as e:
        return pd.DataFrame()
    return pd.DataFrame()

def get_company_details(ticker, corp_name):
    """네이버 금융에서 사업 요약, 뉴스, PER 등을 수집하여 사유 분석"""
    details = {
        "사업내용": "데이터 없음",
        "PER": "N/A",
        "최신뉴스": "관련 뉴스 없음",
        "신고가사유": "모멘텀/수급 유입 (분석 불가)"
    }
    
    # 1. 기업 개요 및 PER 수집
    main_url = f"https://finance.naver.com/item/main.naver?code={ticker}"
    try:
        res = requests.get(main_url, headers=HEADERS, timeout=3)
        soup = BeautifulSoup(res.text, 'html.parser')
        
        summary = soup.select_one('.summary_info')
        if summary: details["사업내용"] = summary.get_text(strip=True)[:60] + "..."
        
        per_em = soup.select_one('#_per')
        if per_em: details["PER"] = per_em.text
    except: pass

    # 2. 최신 뉴스 제목 3개 수집
    news_url = f"https://search.naver.com/search.naver?where=news&query={corp_name}+신고가"
    news_list = []
    try:
        res = requests.get(news_url, headers=HEADERS, timeout=3)
        soup = BeautifulSoup(res.text, 'html.parser')
        news_items = soup.select('.news_tit')[:3]
        news_list = [item.get('title') for item in news_items]
        if news_list: details["최신뉴스"] = news_list[0] # 가장 첫 번째 뉴스 노출
    except: pass

    # 3. 뉴스 텍스트 기반 사유 추론 (Rule-based)
    combined_news = " ".join(news_list)
    if any(k in combined_news for k in ['실적', '영업이익', '흑자', '어닝']):
        details["신고가사유"] = "🟢 호실적/어닝 서프라이즈 기대감"
    elif any(k in combined_news for k in ['수주', '계약', '공급', 'MOU']):
        details["신고가사유"] = "🔵 대규모 수주 및 공급 계약"
    elif any(k in combined_news for k in ['M&A', '인수', '합병', '지분']):
        details["신고가사유"] = "🟣 M&A 및 지분 투자 모멘텀"
    elif any(k in combined_news for k in ['특허', '임상', 'FDA']):
        details["신고가사유"] = "🟡 R&D, 임상/특허 호재"
    elif any(k in combined_news for k in ['테마', '관련주', '수혜주']):
        details["신고가사유"] = "🟠 특정 테마주 편입 및 단기 수급"
    elif news_list:
        details["신고가사유"] = "⚪ 개별 호재 및 기관/외인 수급"

    return details

def render_new_high_menu():
    st.markdown("<div style='font-size: 1.4rem; font-weight: bold; margin-bottom: 1rem;'>🚀 신고가 종목 종합 트래킹</div>", unsafe_allow_html=True)
    st.info("💡 인베스팅닷컴/네이버 52주 신고가 데이터를 기반으로 주도주의 모멘텀과 사유를 분석합니다.")

    # UI 필터부 (모양만 구성, 실제 데이터는 52주 신고가 기반)
    col1, col2 = st.columns(2)
    with col1:
        market_filter = st.selectbox("시장 선택", ["전체", "KOSPI", "KOSDAQ"])
    with col2:
        period_filter = st.selectbox("조회 기준 (참고용)", ["52주(1년) 신고가", "6개월 신고가 (준비중)", "3개월 신고가 (준비중)"])

    if st.button("실시간 신고가 분석 시작", use_container_width=True):
        with st.spinner("웹 크롤링 및 종목 데이터 분석 중... (약 10~20초 소요)"):
            
            raw_df = scrape_52w_highs()
            
            if raw_df.empty:
                st.error("데이터를 불러오지 못했습니다. 크롤링이 차단되었거나 네트워크 문제일 수 있습니다.")
                return

            krx_list = get_krx_listing()
            
            results = []
            # 응답 속도를 위해 상위 10개만 우선 정밀 분석 (원하면 늘릴 수 있음)
            analyze_target = raw_df.head(10) 
            
            progress_bar = st.progress(0)
            for idx, row in analyze_target.iterrows():
                name = str(row['종목명']).strip()
                
                # 종목코드 매칭
                ticker_match = krx_list[krx_list['Name'] == name]
                if not ticker_match.empty:
                    ticker = ticker_match['Code'].values[0]
                    market = ticker_match['Market'].values[0]
                    
                    # 시장 필터 적용
                    if market_filter != "전체" and market != market_filter:
                        continue
                        
                    details = get_company_details(ticker, name)
                    
                    results.append({
                        "종목명": name,
                        "코드": ticker,
                        "현재가": row['현재가'],
                        "등락률": row['등락률'],
                        "PER": details['PER'],
                        "추정 사유": details['신고가사유'],
                        "최신뉴스": details['최신뉴스'],
                        "사업 요약": details['사업내용']
                    })
                
                progress_bar.progress((idx + 1) / len(analyze_target))
            
            progress_bar.empty()

            if results:
                res_df = pd.DataFrame(results)
                
                st.markdown("### 📊 분석 결과 요약")
                # DataFrame 출력 시 컬럼명 정렬
                st.dataframe(
                    res_df[['종목명', '현재가', '등락률', 'PER', '추정 사유']], 
                    hide_index=True, 
                    use_container_width=True
                )
                
                st.markdown("### 🔍 종목별 상세 인사이트")
                for res in results:
                    with st.expander(f"[{res['종목명']}] {res['추정 사유']}"):
                        st.markdown(f"**현재가:** {res['현재가']} ({res['등락률']}) | **PER:** {res['PER']}배")
                        st.markdown(f"**📰 관련 뉴스:** {res['최신뉴스']}")
                        st.markdown(f"**🏭 사업 내용:** {res['사업 요약']}")
            else:
                st.warning("선택한 조건에 맞는 신고가 종목이 없거나 데이터 분석에 실패했습니다.")
