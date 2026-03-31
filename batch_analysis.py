import os
import json
import asyncio
from datetime import datetime, timedelta
import pandas as pd
import FinanceDataReader as fdr
import google.generativeai as genai
from telethon import TelegramClient
from telethon.sessions import StringSession

# 환경 변수 로드
API_ID = int(os.environ.get("TELEGRAM_API_ID", 0))
API_HASH = os.environ.get("TELEGRAM_API_HASH")
SESSION_STR = os.environ.get("TELEGRAM_SESSION")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")

genai.configure(api_key=GEMINI_KEY)

# 타겟 텔레그램 채널 (본인이 구독 중인 정보방의 username 또는 ID 입력)
TARGET_CHANNELS = ['yeouido_info', 'stock_news_channel'] # 예시입니다. 실제 ID로 변경 필요.

def get_high_stocks():
    """시총 1000억 이상 종목 중 1주/3개월/6개월/1년 신고가 탐색 (단순화된 예시 로직)"""
    df = fdr.StockListing('KRX')
    df = df[(df['Marcap'] >= 100_000_000_000) & (df['Close'] >= 1000) & (df['Volume'] >= 100000)].copy()
    
    candidates = df.sort_values('ChagesRatio', ascending=False).head(50)
    results = []
    
    # 어제자 기준 분석을 위해 날짜 조정 (평일 기준)
    start_date = (datetime.today() - timedelta(days=365)).strftime('%Y-%m-%d')
    
    for row in candidates.itertuples():
        try:
            hist = fdr.DataReader(row.Code, start_date)
            if hist.empty or len(hist) < 20: continue
            
            period_max = hist['High'].max()
            today_high = hist['High'].iloc[-1]
            
            if today_high >= period_max * 0.995:
                results.append({
                    "종목명": row.Name, "코드": row.Code, "현재가": int(hist['Close'].iloc[-1]), "등락률": row.ChagesRatio
                })
                if len(results) >= 5: break # 리소스 관리를 위해 일단 5개만 추출
        except: pass
    return results

async def get_telegram_news(client, stock_name):
    """텔레그램 채널에서 해당 종목이 언급된 오늘자 메시지 수집"""
    messages_text = []
    today = datetime.now().date()
    
    try:
        for channel in TARGET_CHANNELS:
            # 종목명으로 검색
            async for message in client.iter_messages(channel, search=stock_name, limit=3):
                if message.date.date() == today and message.text:
                    messages_text.append(message.text)
    except Exception as e:
        print(f"텔레그램 수집 에러: {e}")
    return " \n".join(messages_text)

def summarize_with_gemini(stock_name, raw_text):
    """Gemini API를 이용한 핵심 사유 1줄 요약"""
    if not raw_text.strip():
        return "관련 모멘텀 정보(찌라시/뉴스)가 수집되지 않았습니다."
        
    model = genai.GenerativeModel('gemini-3.1-pro')
    prompt = f"""
    너는 냉철한 주식 분석가야. 아래는 '{stock_name}' 종목과 관련된 오늘자 텔레그램 찌라시 및 뉴스 텍스트야.
    이 종목이 오늘 신고가를 기록한 진짜 이유(핵심 모멘텀)를 불필요한 수식어 없이 딱 1줄로 명확하게 요약해.
    
    [데이터]:
    {raw_text}
    """
    try:
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        return f"분석 에러: {e}"

async def main():
    print("1. 신고가 종목 추출 중...")
    stocks = get_high_stocks()
    
    if not stocks:
        print("신고가 종목이 없습니다.")
        return

    print("2. 텔레그램 연결 및 Gemini 분석 시작...")
    client = TelegramClient(StringSession(SESSION_STR), API_ID, API_HASH)
    await client.start()
    
    final_results = []
    for s in stocks:
        print(f"- {s['종목명']} 모멘텀 수집 중...")
        tg_text = await get_telegram_news(client, s['종목명'])
        reason = summarize_with_gemini(s['종목명'], tg_text)
        
        s['추정 사유'] = reason
        s['최신뉴스'] = "텔레그램 데이터 기반 분석 완료" # 보조 데이터
        s['PER'] = "N/A" # 필요시 fdr 데이터로 대체
        final_results.append(s)
        
    await client.disconnect()

    # 3. JSON 저장
    os.makedirs('data', exist_ok=True)
    now = datetime.now()
    report = {
        "analysis_time": now.strftime("%Y-%m-%d %H:%M"),
        "results": final_results
    }
    file_name = f"data/report_{now.strftime('%Y%m%d_%H%M')}.json"
    
    with open(file_name, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=4, ensure_ascii=False)
    print(f"✅ 배치 완료. {file_name} 저장됨.")

if __name__ == "__main__":
    asyncio.run(main())
