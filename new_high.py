import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
import FinanceDataReader as fdr
import re
import io
import time

# 💡 더욱 강력해진 스텔스 헤더 설정
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://finance.naver.com/", # 👈 네이버가 "자기네 사이트에서 온 클릭"이라고 믿게 만듬
    "Connection": "keep-alive"
}

@st.cache_data(ttl=86400)
def get_krx_listing():
    df = fdr.StockListing('KRX')
    return df[['Code', 'Name', 'Market']]

@st.cache_data(ttl=3600)
def scrape_52w_highs():
    """
    1순위: 네이버 금융 (파라미터 추가로 404 우회)
    2순위: 다음 금융 (JSON API 방식 - 차단에 매우 강함)
    """
    session = requests.Session()
    session.headers.update(HEADERS)

    # 1. 네이버 금융 (KOSPI/KOSDAQ 명시적 요청)
    # sosok=0(코스피), sosok=1(코스닥)
    nv_results = []
    for sosok in [0, 1]:
        url = f"https://finance.naver.com/sise/sise_high_up.naver?sosok={sosok}"
        try:
            res = session.get(url, timeout=10)
            res.encoding = 'euc-kr'
            if res.status_code == 200:
                dfs = pd.read_html(io.StringIO(res.text))
                for df in dfs:
                    if '종목명' in df.columns:
                        df = df.dropna(subset=['종목명'])
                        df = df[df['종목명'] != '종목명']
                        nv_results.append(df[['종목명', '현재가', '등락률']])
            time.sleep(0.5) # 👈 아주 짧은 지연으로 봇 감지 회피
        except:
            continue
            
    if nv_results:
        return pd.concat(nv_results).head(30)

    # 2. 백업: 다음 금융 (네이버가 끝까지 막을 경우 작동)
    # 다음 금융은 JSON 데이터를 직접 쏘아주기 때문에 훨씬 안정적입니다.
    daum_url = "https://finance.daum.net/api/trend/high_lows?category=high&type=52_week&pagination=true&perPage=30&page=1"
    daum_headers = HEADERS.copy()
    daum_headers["Referer"] = "https://finance.daum.net/domestic/high_low"
    daum_headers["X-Requested-With"] = "XMLHttpRequest" # 👈 API 호출임을 명시
    
    try:
        res = session.get(daum_url, headers=daum_headers, timeout=10)
        if res.status_code == 200:
            data = res.json()
            df = pd.DataFrame(data['data'])
            df = df.rename(columns={'name': '종목명', 'tradePrice': '현재가', 'changeRate': '등락률'})
            df['등락률'] = (df['등락률'] * 100).round(2).astype(str) + "%"
            return df[['종목명', '현재가', '등락률']]
    except:
        pass

    return pd.DataFrame()

def get_company_details(ticker, corp_name):
    details = {
        "사업내용": "데이터 없음",
        "PER": "N/A",
        "최신뉴스": "관련 뉴스 없음",
        "신고가사유": "모멘텀/수급 유입 (분석 불가)"
    }
    
    session = requests.Session()
    session.headers.update(HEADERS)

    # 1. 기업 개요 및 PER
    try:
        url = f"https://finance.naver.com/item/main.naver?code={ticker}"
        res = session.get(url, timeout=5)
        soup = BeautifulSoup(res.text, 'html.parser')
        summary = soup.select_one('.summary_info')
        if summary: details["사업내용"] = summary.get_text(strip=True)[:60] + "..."
        per_em = soup.select_one('#_per')
        if per_em: details["PER"] = per_em.text
    except: pass

    # 2. 최신 뉴스
    try:
        news_url = f"https://search.naver.com/search.naver?where=news&query={corp_name}+신고가"
        res = session.get(news_url, timeout=5)
        soup = BeautifulSoup(res.text, 'html.parser')
        news_items = soup.select('.news_tit')[:3]
        news_list = [item.get('title') for item in news_items]
        if news_list: details["최신뉴스"] = news_list[0]
        
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
    except: pass

    return details

def render_new_high_menu():
    st.markdown("<div style='font-size: 1.4rem; font-weight: bold; margin-bottom: 1rem;'>🚀 신고가 종목 종합 트래킹 (V1.3.12)</div>", unsafe_allow_html=True)
    st.info("💡 클라우드 환경의 IP 차단을 우회하기 위해 다중 크롤링 엔진 및 스텔스 헤더를 적용했습니다.")

    col1, col2 = st.columns(2)
    with col1:
        market_filter = st.selectbox("시장 선택", ["전체", "KOSPI", "KOSDAQ"])
    with col2:
        period_filter = st.selectbox("조회 기준 (참고용)", ["52주(1년) 신고가", "6개월 신고가 (준비중)", "3개월 신고가 (준비중)"])

    if st.button("실시간 신고가 분석 시작", use_container_width=True):
        with st.spinner("다중 소스에서 데이터를 수집 중입니다..."):
            raw_df = scrape_52w_highs()
            
            if raw_df.empty:
                st.error("❗ 모든 데이터 소스(네이버/다음)로부터 차단되었습니다. 잠시 후 다시 시도하거나 로컬 환경에서 테스트해 주세요.")
                return

            krx_list = get_krx_listing()
            results = []
            analyze_target = raw_df.head(10) 
            
            progress_bar = st.progress(0)
            for idx, row in analyze_target.iterrows():
                name = str(row['종목명']).strip()
                ticker_match = krx_list[krx_list['Name'] == name]
                if not ticker_match.empty:
                    ticker = ticker_match['Code'].values[0]
                    market = ticker_match['Market'].values[0]
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
                st.dataframe(res_df[['종목명', '현재가', '등락률', 'PER', '추정 사유']], hide_index=True, use_container_width=True)
                
                st.markdown("### 🔍 종목별 상세 인사이트")
                for res in results:
                    with st.expander(f"[{res['종목명']}] {res['추정 사유']}"):
                        st.markdown(f"**현재가:** {res['현재가']} ({res['등락률']}) | **PER:** {res['PER']}배")
                        st.markdown(f"**📰 관련 뉴스:** {res['최신뉴스']}")
                        st.markdown(f"**🏭 사업 내용:** {res['사업 요약']}")
