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

API_ID = int(os.environ.get("TELEGRAM_API_ID", 0))
API_HASH = os.environ.get("TELEGRAM_API_HASH", "")
SESSION_STR = os.environ.get("TELEGRAM_SESSION", "")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")

client_ai = genai.Client(api_key=GEMINI_KEY)

def get_high_stocks():
    print("데이터 수집 및 1,000억 필터링 시작...")
    df = fdr.StockListing('KRX')
    
    df['Marcap'] = pd.to_numeric(df['Marcap'], errors='coerce').fillna(0)
    df['Close'] = pd.to_numeric(df['Close'], errors='coerce').fillna(0)
    df['Volume'] = pd.to_numeric(df['Volume'], errors='coerce').fillna(0)
    df['ChagesRatio'] = pd.to_numeric(df['ChagesRatio'], errors='coerce').fillna(0)
    
    df = df[(df['Marcap'] >= 100_000_000_000) & (df['Close'] >= 1000) & (df['Volume'] >= 100000)].copy()
    candidates = df.sort_values('ChagesRatio', ascending=False).head(50)
    results = []
    
    start_date = (datetime.today() - timedelta(days=365)).strftime('%Y-%m-%d')
    
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

async def get_telegram_news(client, stock_name):
    messages_text = []
    today = datetime.now().date()
    try:
        async for message in client.iter_messages(None, search=stock_name, limit=10):
            if message.date.date() == today and message.text:
                messages_text.append(message.text)
    except Exception as e:
        print(f"텔레그램 에러: {e}")
    return " \n".join(messages_text)

def get_google_news(stock_name):
    try:
        query = f'"{stock_name}" 특징주 OR 주가'
        encoded_query = urllib.parse.quote(query)
        url = f"https://news.google.com/rss/search?q={encoded_query}&hl=ko&gl=KR&ceid=KR:ko"
        
        res = requests.get(url, timeout=5)
        root = ET.fromstring(res.text)
        
        news_titles = []
        news_markdown = []
        first_link = "" 
        
        for i, item in enumerate(root.findall('.//item')[:5]):
            title = item.find('title').text
            link = item.find('link').text
            news_titles.append(title)
            # 마크다운 하이퍼링크 형식으로 조립
            news_markdown.append(f"- [{title}]({link})")
            if i == 0:
                first_link = link
                
        ai_text = " \n".join(news_titles) if news_titles else "관련 뉴스 없음"
        ui_markdown = " \n".join(news_markdown) if news_markdown else "관련 뉴스 없음"
        
        return ai_text, ui_markdown, first_link
    except Exception as e:
        return f"뉴스 수집 에러: {e}", "관련 뉴스 없음", ""

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
            # 💡 일 1,500회 무료인 Lite 모델로 고정!
            response = client_ai.models.generate_content(
                model='gemini-2.0-flash-lite-001', 
                contents=prompt,
            )
            return response.text.strip()
        except Exception as e:
            error_msg = str(e)
            if '503' in error_msg or '429' in error_msg:
                if attempt < max_retries - 1:
                    time.sleep(15) 
                    continue
            return f"AI 분석 에러: {e}"

async def main():
    start_time = time.time()
    print("=== 주도주 트래킹 배치 시작 ===")
    stocks = get_high_stocks()
    
    if stocks:
        client_tg = TelegramClient(StringSession(SESSION_STR), API_ID, API_HASH)
        await client_tg.start()
        
        for s in stocks:
            print(f" -> [{s['종목명']}] 분석 중...")
            tg_text = await get_telegram_news(client_tg, s['종목명'])
            ai_news_text, ui_news_markdown, first_link = get_google_news(s['종목명'])
            
            reason = summarize_with_gemini(s['종목명'], tg_text, ai_news_text)
            
            s['추정 사유'] = reason
            s['최신뉴스'] = ai_news_text.split('\n')[0] if ai_news_text != "관련 뉴스 없음" else "관련 뉴스 없음"
            s['최신뉴스_링크'] = first_link # UI 표에 링크 버튼을 달기 위한 데이터
            s['뉴스목록'] = ui_news_markdown
            s['PER'] = "조회필요"
            
            await asyncio.sleep(4) 
            
        await client_tg.disconnect()

    end_time = time.time()
    m, sec = divmod(end_time - start_time, 60)
    execution_time_str = f"{int(m)}분 {int(sec)}초"

    os.makedirs('data', exist_ok=True)
    now = datetime.utcnow() + timedelta(hours=9)
    
    report = {
        "analysis_time": now.strftime("%Y-%m-%d %H:%M"),
        "execution_time": execution_time_str,
        "results": stocks
    }
    
    file_name = f"data/report_{now.strftime('%Y%m%d_%H%M')}.json"
    with open(file_name, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=4, ensure_ascii=False)
        
    print(f"=== 완료. 소요시간: {execution_time_str} ===")

if __name__ == "__main__":
    asyncio.run(main())
