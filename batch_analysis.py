import os
import json
import asyncio
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import pandas as pd
import FinanceDataReader as fdr
import google.generativeai as genai
from telethon import TelegramClient
from telethon.sessions import StringSession

# ---------------------------------------------------------
# 환경 변수 세팅 (GitHub Secrets에서 주입됨)
# ---------------------------------------------------------
API_ID = int(os.environ.get("TELEGRAM_API_ID", 0))
API_HASH = os.environ.get("TELEGRAM_API_HASH", "")
SESSION_STR = os.environ.get("TELEGRAM_SESSION", "")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")

genai.configure(api_key=GEMINI_KEY)

# ---------------------------------------------------------
# 1. 시총 1000억 이상 주도주 및 신고가 판별
# ---------------------------------------------------------
def get_high_stocks():
    print("데이터 수집 및 1,000억 필터링 시작...")
    df = fdr.StockListing('KRX')
    
    # 1차 필터: 시총 1000억 이상, 동전주 제외, 거래량 10만 이상
    df = df[(df['Marcap'] >= 100_000_000_000) & (df['Close'] >= 1000) & (df['Volume'] >= 100000)].copy()
    
    # 당일 주도주 파악을 위해 등락률 상위 50개만 우선 추출 (배치 속도 최적화)
    candidates = df.sort_values('ChagesRatio', ascending=False).head(50)
    results = []
    
    # 과거 1년 차트 조회를 위한 날짜 설정
    start_date = (datetime.today() - timedelta(days=365)).strftime('%Y-%m-%d')
    
    print(f"주도주 {len(candidates)}개 종목 신고가 정밀 연산 중...")
    for row in candidates.itertuples():
        try:
            hist = fdr.DataReader(row.Code, start_date)
            if hist.empty or len(hist) < 20: continue
            
            # 기간별 고가 계산 (1주, 3개월, 6개월, 1년)
            high_1y = hist['High'].max()
            high_6m = hist['High'].tail(120).max() # 약 6개월 영업일
            high_3m = hist['High'].tail(60).max()  # 약 3개월 영업일
            high_1w = hist['High'].tail(5).max()   # 약 1주 영업일
            
            today_high = hist['High'].iloc[-1]
            today_close = int(hist['Close'].iloc[-1])
            
            # 오늘 고가가 특정 기간의 고가를 뚫었는지(또는 0.5% 내 근접) 확인
            period_flag = ""
            if today_high >= high_1y * 0.995: period_flag = "1년(52주) 신고가"
            elif today_high >= high_6m * 0.995: period_flag = "6개월 신고가"
            elif today_high >= high_3m * 0.995: period_flag = "3개월 신고가"
            elif today_high >= high_1w * 0.995: period_flag = "1주 신고가"
            
            if period_flag:
                results.append({
                    "종목명": row.Name,
                    "코드": row.Code,
                    "현재가": today_close,
                    "등락률": row.ChagesRatio,
                    "돌파기간": period_flag
                })
        except Exception as e:
            pass
            
    return results

# ---------------------------------------------------------
# 2. 텔레그램 전체 검색 (노이즈 필터링 포함)
# ---------------------------------------------------------
async def get_telegram_news(client, stock_name):
    messages_text = []
    today = datetime.now().date()
    
    try:
        # 첫 번째 인자 None = 사용자가 가입한 '모든' 대화방 통합 검색
        async for message in client.iter_messages(None, search=stock_name, limit=10):
            if message.date.date() == today and message.text:
                messages_text.append(message.text)
    except Exception as e:
        print(f"텔레그램 검색 에러 ({stock_name}): {e}")
        
    return " \n".join(messages_text)

# ---------------------------------------------------------
# 3. 네이버 특징주 뉴스 스크래핑
# ---------------------------------------------------------
def get_naver_news(stock_name):
    url = f"https://m.search.naver.com/search.naver?where=m_news&sm=mtb_jum&query={stock_name}+특징주"
    headers = {"User-Agent": "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/110.0.0.0 Mobile"}
    try:
        res = requests.get(url, headers=headers, timeout=5)
        soup = BeautifulSoup(res.text, 'html.parser')
        news_items = soup.select('.news_tit')[:3]
        news_list = [item.text.strip() for item in news_items]
        return " \n".join(news_list)
    except:
        return "뉴스 검색 실패"

# ---------------------------------------------------------
# 4. Gemini API 통합 모멘텀 요약
# ---------------------------------------------------------
def summarize_with_gemini(stock_name, tg_text, news_text):
    if not tg_text.strip() and not news_text.strip():
        return "시장 수급 유입 (구체적인 뉴스/찌라시 미발견)"
        
    model = genai.GenerativeModel('gemini-3.1-pro-preview')
    prompt = f"""
    너는 냉철한 주식 분석가야. 아래는 '{stock_name}' 종목의 오늘자 네이버 뉴스(팩트)와 텔레그램 찌라시(루머/수급) 데이터야.
    이 데이터를 교차 검증해서, 이 종목이 오늘 신고가를 뚫은 '핵심 모멘텀(진짜 이유)'을 불필요한 수식어 없이 딱 1줄(50자 이내)로 명확하게 요약해.
    
    [네이버 뉴스]:
    {news_text}
    
    [텔레그램 찌라시]:
    {tg_text}
    """
    try:
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        return f"AI 분석 에러: {e}"

# ---------------------------------------------------------
# 메인 파이프라인 실행
# ---------------------------------------------------------
async def main():
    print("=== 주도주 트래킹 배치 프로세스 시작 ===")
    stocks = get_high_stocks()
    
    if not stocks:
        print("조건을 만족하는 신고가 종목이 없습니다. 빈 리포트를 생성합니다.")
        stocks = []
    else:
        print(f"총 {len(stocks)}개의 신고가 종목 발견. 텔레그램/뉴스 교차 분석 시작...")
        
        # 텔레그램 클라이언트 연결
        client = TelegramClient(StringSession(SESSION_STR), API_ID, API_HASH)
        await client.start()
        
        for s in stocks:
            print(f" -> [{s['종목명']}] 데이터 수집 중...")
            tg_text = await get_telegram_news(client, s['종목명'])
            news_text = get_naver_news(s['종목명'])
            
            # AI 요약 생성
            reason = summarize_with_gemini(s['종목명'], tg_text, news_text)
            
            s['추정 사유'] = reason
            s['최신뉴스'] = news_text.split('\n')[0] if news_text else "관련 뉴스 없음"
            s['PER'] = "조회필요" # 배치 속도를 위해 생략
            
        await client.disconnect()

    # 결과 JSON 저장
    os.makedirs('data', exist_ok=True)
    now = datetime.now()
    report = {
        "analysis_time": now.strftime("%Y-%m-%d %H:%M"),
        "results": stocks
    }
    
    file_name = f"data/report_{now.strftime('%Y%m%d_%H%M')}.json"
    with open(file_name, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=4, ensure_ascii=False)
        
    print(f"=== 배치 완료. {file_name} 저장 성공 ===")

if __name__ == "__main__":
    asyncio.run(main())
