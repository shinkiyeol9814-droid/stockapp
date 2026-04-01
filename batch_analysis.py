import os
import json
import asyncio
import time
import requests
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
import pandas as pd
import FinanceDataReader as fdr
from telethon import TelegramClient
from telethon.sessions import StringSession
from google import genai

# ---------------------------------------------------------
# 환경 변수 세팅 (GitHub Secrets에서 주입됨)
# ---------------------------------------------------------
API_ID = int(os.environ.get("TELEGRAM_API_ID", 0))
API_HASH = os.environ.get("TELEGRAM_API_HASH", "")
SESSION_STR = os.environ.get("TELEGRAM_SESSION", "")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")

client_ai = genai.Client(api_key=GEMINI_KEY)

# ---------------------------------------------------------
# 1. 시총 1000억 이상 주도주 및 신고가 판별
# ---------------------------------------------------------
def get_high_stocks():
    print("데이터 수집 및 1,000억 필터링 시작...")
    df = fdr.StockListing('KRX')
    
    # 💡 [추가된 핵심 로직] 텍스트(str)로 잘못 들어온 데이터를 숫자형(float/int)으로 강제 변환
    # 변환할 수 없는 이상한 문자열이 있으면 에러를 내지 않고 NaN(빈 값)으로 처리한 뒤 0으로 채움
    df['Marcap'] = pd.to_numeric(df['Marcap'], errors='coerce').fillna(0)
    df['Close'] = pd.to_numeric(df['Close'], errors='coerce').fillna(0)
    df['Volume'] = pd.to_numeric(df['Volume'], errors='coerce').fillna(0)
    df['ChagesRatio'] = pd.to_numeric(df['ChagesRatio'], errors='coerce').fillna(0)
    
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
            
            high_1y = hist['High'].max()
            high_6m = hist['High'].tail(120).max()
            high_3m = hist['High'].tail(60).max()
            high_1w = hist['High'].tail(5).max()
            
            today_high = hist['High'].iloc[-1]
            today_close = int(hist['Close'].iloc[-1])
            
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
# 2. 텔레그램 전체 검색
# ---------------------------------------------------------
async def get_telegram_news(client, stock_name):
    messages_text = []
    today = datetime.now().date()
    
    try:
        async for message in client.iter_messages(None, search=stock_name, limit=10):
            if message.date.date() == today and message.text:
                messages_text.append(message.text)
    except Exception as e:
        print(f"텔레그램 검색 에러 ({stock_name}): {e}")
        
    return " \n".join(messages_text)

# ---------------------------------------------------------
# 3. 구글 뉴스 RSS 수집 (네이버 봇 차단 우회)
# ---------------------------------------------------------
def get_google_news(stock_name):
    try:
        # 정확도 향상을 위해 검색어 조합 (종목명 + 특징주 OR 주가)
        query = f'"{stock_name}" 특징주 OR 주가'
        encoded_query = urllib.parse.quote(query)
        url = f"https://news.google.com/rss/search?q={encoded_query}&hl=ko&gl=KR&ceid=KR:ko"
        
        res = requests.get(url, timeout=5)
        root = ET.fromstring(res.text)
        
        news_list = []
        for item in root.findall('.//item')[:3]:  # 상위 3개 뉴스 추출
            title = item.find('title').text
            news_list.append(title)
            
        return " \n".join(news_list) if news_list else "관련 뉴스 없음"
    except Exception as e:
        return f"뉴스 수집 에러: {e}"

# ---------------------------------------------------------
# 4. Gemini API 통합 모멘텀 요약 (재시도 로직 강화)
# ---------------------------------------------------------
def summarize_with_gemini(stock_name, tg_text, news_text, max_retries=3):
    if not tg_text.strip() and (not news_text.strip() or "관련 뉴스 없음" in news_text):
        return "시장 수급 유입 (구체적인 뉴스/찌라시 미발견)"
        
    prompt = f"""
    너는 냉철한 주식 분석가야. 아래는 '{stock_name}' 종목의 오늘자 뉴스 헤드라인(팩트)과 텔레그램 찌라시(루머/수급) 데이터야.
    이 데이터를 교차 검증해서, 이 종목이 오늘 신고가를 뚫은 '핵심 모멘텀(진짜 이유)'을 불필요한 수식어 없이 딱 1줄(50자 이내)로 명확하게 요약해.
    
    [뉴스 데이터]:
    {news_text}
    
    [텔레그램 찌라시]:
    {tg_text}
    """
    
    for attempt in range(max_retries):
        try:
            # 무료 티어에서 비교적 안정적인 1.5-flash 모델 적용
            response = client_ai.models.generate_content(
                model='gemini-2.5-flash', 
                contents=prompt,
            )
            return response.text.strip()
            
        except Exception as e:
            error_msg = str(e)
            if '503' in error_msg or '429' in error_msg:
                if attempt < max_retries - 1:
                    print(f"   ⚠️ [API 대기중] 과부하/한도초과. 60초 후 재시도합니다... (시도 {attempt+1}/{max_retries})")
                    time.sleep(60) # 구글 API 요구사항에 맞춰 대기 시간을 60초로 대폭 연장
                    continue
            return f"AI 분석 에러: {e}"

# ---------------------------------------------------------
# 메인 파이프라인 실행
# ---------------------------------------------------------
async def main():
    start_time = time.time() # 💡 [추가] 배치 시작 시간 기록
    print("=== 주도주 트래킹 배치 프로세스 시작 ===")
    stocks = get_high_stocks()
    
    if not stocks:
        print("조건을 만족하는 신고가 종목이 없습니다.")
        stocks = []
    else:
        print(f"총 {len(stocks)}개의 신고가 종목 발견. 텔레그램/뉴스 교차 분석 시작...")
        
        client_tg = TelegramClient(StringSession(SESSION_STR), API_ID, API_HASH)
        await client_tg.start()
        
        for s in stocks:
            print(f" -> [{s['종목명']}] 데이터 수집 및 AI 분석 중...")
            tg_text = await get_telegram_news(client_tg, s['종목명'])
            news_text = get_google_news(s['종목명'])
            
            reason = summarize_with_gemini(s['종목명'], tg_text, news_text)
            
            s['추정 사유'] = reason
            # 뉴스 전체 텍스트 중 첫 번째 줄만 잘라서 표에 저장
            s['최신뉴스'] = news_text.split('\n')[0] if news_text else "관련 뉴스 없음"
            s['PER'] = "조회필요"
            
            # API 제한 방지를 위해 종목당 10초 대기 (안정성 최우선)
            await asyncio.sleep(10)
            
        await client_tg.disconnect()
        
    # 💡 [추가] 소요 시간 계산 (분, 초)
    end_time = time.time()
    elapsed_sec = end_time - start_time
    m, s = divmod(elapsed_sec, 60)
    execution_time_str = f"{int(m)}분 {int(s)}초"
    
    # 결과 JSON 저장
    os.makedirs('data', exist_ok=True)
    # now = datetime.now() -> UTC 기반인 경우 아래처럼 KST 변환 적용
    now = datetime.utcnow() + timedelta(hours=9)
    
    report = {
        "analysis_time": now.strftime("%Y-%m-%d %H:%M"),
        "execution_time": execution_time_str, # 💡 [추가] JSON에 소요 시간 데이터 포함
        "results": stocks
    }
    
    file_name = f"data/report_{now.strftime('%Y%m%d_%H%M')}.json"
    with open(file_name, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=4, ensure_ascii=False)
        
    print(f"=== 배치 완료. 소요시간: {execution_time_str} | {file_name} 저장 성공 ===")

if __name__ == "__main__":
    asyncio.run(main())
