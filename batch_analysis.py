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
            news_markdown.append(f"- [{title}]({link})")
            if i == 0:
                first_link = link
                
        ai_text = " \n".join(news_titles) if news_titles else "관련 뉴스 없음"
        ui_markdown = " \n".join(news_markdown) if news_markdown else "관련 뉴스 없음"
        
        return ai_text, ui_markdown, first_link
    except Exception as e:
        return f"뉴스 수집 에러: {e}", "관련 뉴스 없음", ""

# 💡 [핵심] 여러 종목을 한 번에 분석하는 일괄 처리 함수
def summarize_batch_with_gemini(batch_data, max_retries=3):
    prompt = """너는 냉철한 주식 분석가야. 아래 전달하는 '여러 종목'의 뉴스(팩트)와 텔레그램(루머) 데이터를 읽고, 각 종목이 신고가를 뚫은 핵심 모멘텀을 50자 이내로 1줄 요약해.
반드시 아래와 같은 순수 JSON 형식으로만 반환해. 마크다운 기호나 다른 설명은 절대 넣지마.

{
  "종목명1": "요약내용",
  "종목명2": "요약내용"
}

[분석할 데이터]
"""
    for data in batch_data:
        prompt += f"■ {data['name']}\n- 뉴스: {data['news']}\n- 찌라시: {data['tg']}\n\n"

    for attempt in range(max_retries):
        try:
            # 10번 호출로 줄었으므로 가장 똑똑한 2.5-flash 사용 가능!
            response = client_ai.models.generate_content(
                model='gemini-2.5-flash', 
                contents=prompt,
            )
            res_text = response.text.strip()
            
            # JSON 텍스트 파싱 방어 로직
            if res_text.startswith("```json"):
                res_text = res_text[7:-3].strip()
            elif res_text.startswith("```"):
                res_text = res_text[3:-3].strip()
                
            return json.loads(res_text)
            
        except Exception as e:
            print(f"   ⚠️ AI 일괄 분석 에러 재시도 중... ({e})")
            time.sleep(10)
            
    return {}

async def main():
    start_time = time.time()
    print("=== 주도주 트래킹 배치 시작 ===")
    stocks = get_high_stocks()
    
    if not stocks:
        print("조건을 만족하는 신고가 종목이 없습니다.")
        stocks = []
    else:
        client_tg = TelegramClient(StringSession(SESSION_STR), API_ID, API_HASH)
        await client_tg.start()
        
        # 1. API 호출 없이 일단 데이터만 싹 다 긁어모으기
        analysis_queue = []
        for s in stocks:
            print(f" -> [{s['종목명']}] 데이터 수집 중...")
            tg_text = await get_telegram_news(client_tg, s['종목명'])
            ai_news_text, ui_news_markdown, first_link = get_google_news(s['종목명'])
            
            s['최신뉴스'] = ai_news_text.split('\n')[0] if ai_news_text != "관련 뉴스 없음" else "관련 뉴스 없음"
            s['최신뉴스_링크'] = first_link 
            s['뉴스목록'] = ui_news_markdown
            s['PER'] = "조회필요"
            
            if not tg_text.strip() and ai_news_text == "관련 뉴스 없음":
                s['추정 사유'] = "시장 수급 유입 (구체적인 뉴스/찌라시 미발견)"
            else:
                s['추정 사유'] = "분석 대기"
                analysis_queue.append({'name': s['종목명'], 'tg': tg_text, 'news': ai_news_text, 'ref': s})
            
            await asyncio.sleep(1) # 크롤링은 속도 제한이 없으므로 빠르게 수집
            
        await client_tg.disconnect()

        # 2. 모아둔 데이터를 10개 단위로 쪼개서 AI에게 한 번에 질문하기 (API 한도 우회)
        chunk_size = 10
        for i in range(0, len(analysis_queue), chunk_size):
            chunk = analysis_queue[i:i+chunk_size]
            print(f"\n🚀 AI 일괄 분석 중 ({i+1}~{min(i+chunk_size, len(analysis_queue))}) / {len(analysis_queue)}개...")
            
            reasons_dict = summarize_batch_with_gemini(chunk)
            
            # 분석 결과를 원본 데이터에 매핑
            for item in chunk:
                item['ref']['추정 사유'] = reasons_dict.get(item['name'], "AI 분석 요약 실패 (수동 확인 필요)")
            
            time.sleep(5) # 청크 사이에 5초 휴식

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
        
    print(f"\n=== 완료. 소요시간: {execution_time_str} ===")

if __name__ == "__main__":
    asyncio.run(main())
