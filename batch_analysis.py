import os
import json
import asyncio
import time
import requests
import urllib.parse
import xml.etree.ElementTree as ET
import re 
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
    print("데이터 수집 및 필터링 시작...")
    df = fdr.StockListing('KRX')
    
    df['Marcap'] = pd.to_numeric(df['Marcap'], errors='coerce').fillna(0)
    df['Close'] = pd.to_numeric(df['Close'], errors='coerce').fillna(0)
    df['Volume'] = pd.to_numeric(df['Volume'], errors='coerce').fillna(0)
    df['ChagesRatio'] = pd.to_numeric(df['ChagesRatio'], errors='coerce').fillna(0)
    
    df = df[(df['Marcap'] >= 100_000_000_000) & (df['Close'] >= 1000) & (df['Volume'] >= 100000)].copy()
    
    df = df[df['ChagesRatio'] >= 2.0] 
    candidates = df.sort_values('ChagesRatio', ascending=False).head(200)
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
        
        for i, item in enumerate(root.findall('.//item')[:3]):
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

# 💡 AI의 수다와 대괄호 기호를 완벽하게 방어하는 클렌징 로직 적용
def summarize_batch_with_gemini(batch_data, max_retries=3):
    prompt = """너는 냉철한 주식 분석가야. 아래 전달하는 '여러 종목'의 뉴스(팩트)와 텔레그램(루머) 데이터를 읽고, 각 종목이 신고가를 뚫은 핵심 모멘텀을 50자 이내로 1줄 요약해.
반드시 아래와 같이 [종목명|요약내용] 규칙의 텍스트로만 대답해. 부가 설명이나 기호는 절대 넣지마.

[출력 예시]
삼성전자|반도체 업황 회복 및 HBM 수혜 기대
카카오|비용 절감 및 실적 개선

[분석할 데이터]
"""
    for data in batch_data:
        prompt += f"■ {data['name']}\n- 뉴스: {data['news']}\n- 찌라시: {data['tg']}\n\n"

    error_message = "알 수 없는 에러"
    
    for attempt in range(max_retries):
        try:
            # 💡 [최종 확정] 가장 성능이 좋은 2.5-flash 모델! (하루 1번 돌리면 20회 한도 절대 안 넘음)
            response = client_ai.models.generate_content(
                model='gemini-2.5-flash', 
                contents=prompt,
            )
            res_text = response.text.strip()
            
            reasons_dict = {}
            for line in res_text.split('\n'):
                if '|' in line:
                    parts = line.split('|', 1)
                    stock_name = parts[0].strip().replace("-", "").replace("*", "").replace("[", "").replace("]", "")
                    summary = parts[1].strip().replace("[", "").replace("]", "")
                    reasons_dict[stock_name] = summary
                    
            if not reasons_dict:
                raise ValueError("파이프(|)로 구분된 결과를 찾을 수 없습니다.")
                
            return True, reasons_dict
            
        except Exception as e:
            error_message = str(e)
            print(f"   ⚠️ AI 분석 에러 (시도 {attempt+1}/{max_retries}) | 20초 대기 후 재시도... 사유: {error_message}")
            time.sleep(20) # 💡 에러 시 20초 대기
            
    return False, f"AI 분석 에러: {error_message}"

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
            
            await asyncio.sleep(1) 
            
        await client_tg.disconnect()

        # 💡 [핵심] 글자 수(Token) 초과 에러를 막기 위해 5개씩 쪼개기
        # 💡 [main 함수 하단 수정] 7개씩 묶어서 질문하고, 20초씩 여유롭게 쉬기
        chunk_size = 7 
        for i in range(0, len(analysis_queue), chunk_size):
            chunk = analysis_queue[i:i+chunk_size]
            print(f"\n🚀 AI 일괄 분석 중 ({i+1}~{min(i+chunk_size, len(analysis_queue))}) / {len(analysis_queue)}개...")
            
            success, result_data = summarize_batch_with_gemini(chunk)
            
            for item in chunk:
                if success:
                    item['ref']['추정 사유'] = result_data.get(item['name'], "추출 누락 (수동 확인 필요)")
                else:
                    item['ref']['추정 사유'] = result_data 
            
            # 💡 분당 요청 수(RPM) 제한에 안 걸리도록 묶음마다 20초 푹 쉬기
            time.sleep(20)

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
