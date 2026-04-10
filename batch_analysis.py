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

# 환경 변수 설정
API_ID = int(os.environ.get("TELEGRAM_API_ID", 0))
API_HASH = os.environ.get("TELEGRAM_API_HASH", "")
SESSION_STR = os.environ.get("TELEGRAM_SESSION", "")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")

client_ai = genai.Client(api_key=GEMINI_KEY)

def get_high_stocks():
    print("데이터 수집 및 필터링 시작...")
    df = fdr.StockListing('KRX')
    
    # 데이터 숫자형 변환
    df['Marcap'] = pd.to_numeric(df['Marcap'], errors='coerce').fillna(0)
    df['Close'] = pd.to_numeric(df['Close'], errors='coerce').fillna(0)
    df['Volume'] = pd.to_numeric(df['Volume'], errors='coerce').fillna(0)
    df['ChagesRatio'] = pd.to_numeric(df['ChagesRatio'], errors='coerce').fillna(0)
    
    # 💡 1. 요청사항 반영: 거래대금/거래량 조건 모두 제외 (시총 500억, 주가 1000원 이상만 유지)
    df = df[(df['Marcap'] >= 50_000_000_000) & (df['Close'] >= 1000)].copy()
    
    # 당일 상승 마감(양봉) 종목만 선정
    df = df[df['ChagesRatio'] > 0.0] 
    candidates = df.sort_values('ChagesRatio', ascending=False)
    results = []
    
    start_date = (datetime.today() - timedelta(days=365)).strftime('%Y-%m-%d')
    
    print(f"필터 통과 {len(candidates)}개 종목 신고가 정밀 연산 중...")
    for row in candidates.itertuples():
        try:
            hist = fdr.DataReader(row.Code, start_date)
            if hist.empty or len(hist) < 20: continue
            
            # 오늘을 제외한 '과거' 데이터만 분리하여 매물대 계산 (윗꼬리 왜곡 방지)
            past_hist = hist.iloc[:-1]
            if past_hist.empty: continue
            
            # 과거 기간별 최고 '종가' (매물대 저항선)
            past_max_1y = past_hist['Close'].max()
            past_max_6m = past_hist['Close'].tail(120).max()
            past_max_3m = past_hist['Close'].tail(60).max()
            
            today_close = int(hist['Close'].iloc[-1])
            
            period_flag = ""
            # 오늘 종가가 과거 고점의 98% 이상이면 안착으로 간주
            if today_close >= past_max_1y * 0.98: period_flag = "1년(52주) 신고가"
            elif today_close >= past_max_6m * 0.98: period_flag = "6개월 신고가"
            elif today_close >= past_max_3m * 0.98: period_flag = "3개월 신고가"
            
            if period_flag:
                results.append({
                    "종목명": row.Name,
                    "코드": row.Code,
                    "현재가": today_close,
                    "시가총액": int(row.Marcap),
                    "등락률": row.ChagesRatio,
                    "돌파기간": period_flag
                })
        except Exception:
            pass
            
    return results

async def get_telegram_news(client, stock_name):
    messages_text = []
    today = datetime.now().date()
    try:
        # 💡 2. 요청사항 반영: 찌라시 수집 개수 5개로 변경
        async for message in client.iter_messages(None, search=stock_name, limit=5):
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
        
        # 구글 뉴스는 핵심 2개만 유지하여 토큰 절약
        for i, item in enumerate(root.findall('.//item')[:2]):
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

def summarize_batch_with_gemini(batch_data, max_retries=2):
    if not batch_data: return {}

    prompt = f"""너는 냉철한 주식 분석가야. 아래 전달하는 {len(batch_data)}개 종목의 뉴스(팩트)와 텔레그램(루머) 데이터를 읽고, 각 종목이 신고가를 뚫은 핵심 모멘텀을 50자 이내로 1줄 요약해.
반드시 아래와 같이 [종목명|요약내용] 규칙의 텍스트로만 대답하고, 전달된 {len(batch_data)}개 종목을 단 하나도 빠짐없이 전부 출력해.
주의: 앞에 '1.', '-', '*' 같은 기호나 번호를 절대 붙이지 말고 오직 '종목명|요약내용' 형태로만 출력해.

[출력 예시]
삼성전자|반도체 업황 회복 및 HBM 수혜 기대
카카오|비용 절감 및 실적 개선

[분석할 데이터]
"""
    for data in batch_data:
        # 텍스트 슬라이싱으로 토큰 한도 초과 방어
        safe_news = data['news'][:300] + "..." if len(data['news']) > 300 else data['news']
        safe_tg = data['tg'][:300] + "..." if len(data['tg']) > 300 else data['tg']
        prompt += f"■ {data['name']}\n- 뉴스: {safe_news}\n- 찌라시: {safe_tg}\n\n"

    for attempt in range(max_retries):
        try:
            response = client_ai.models.generate_content(
                model='gemini-2.0-flash', 
                contents=prompt,
            )
            res_text = response.text.strip()
            reasons_dict = {}
            for line in res_text.split('\n'):
                if '|' in line:
                    parts = line.split('|', 1)
                    # 무적의 정규식 파싱: AI가 붙인 기호 제거
                    raw_name = parts[0].strip()
                    stock_name = re.sub(r'^[\d\.\-\*\s]+', '', raw_name).replace("[", "").replace("]", "")
                    summary = parts[1].strip().replace("[", "").replace("]", "")
                    reasons_dict[stock_name] = summary
            return reasons_dict
        except Exception as e:
            wait_time = 65 if "429" in str(e) else 10
            print(f"   ⚠️ AI 분석 에러 (시도 {attempt+1}/{max_retries}) | {wait_time}초 대기...")
            time.sleep(wait_time) 
    return {}

async def main():
    start_time = time.time()
    print("=== 주도주 트래킹 배치 시작 ===")
    stocks = get_high_stocks()
    
    if not stocks:
        print("조건을 만족하는 신고가 종목이 없습니다.")
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

        # 💡 3. 요청사항 반영: 청크 사이즈 5개로 유지 (정확도 극대화)
        chunk_size = 5 
        retry_queue = []
        
        # 1차 분석
        for i in range(0, len(analysis_queue), chunk_size):
            chunk = analysis_queue[i:i+chunk_size]
            print(f"\n🚀 1차 AI 일괄 분석 중 ({i+1}~{min(i+chunk_size, len(analysis_queue))}) / {len(analysis_queue)}개...")
            result_dict = summarize_batch_with_gemini(chunk)
            
            for item in chunk:
                stock_name = item['name']
                if stock_name in result_dict:
                    item['ref']['추정 사유'] = result_dict[stock_name]
                else:
                    print(f"   🚨 AI 요약 누락: [{stock_name}] -> 재시도 대기열 추가")
                    retry_queue.append(item)
            
            # API 제한 방어 휴식
            time.sleep(10)

        # 누락분 재시도
        if retry_queue:
            print(f"\n♻️ 누락된 {len(retry_queue)}개 종목에 대해 2차 재시도 분석을 시작합니다...")
            for i in range(0, len(retry_queue), chunk_size):
                chunk = retry_queue[i:i+chunk_size]
                print(f"   -> 재시도 분석 중 ({i+1}~{min(i+chunk_size, len(retry_queue))}) / {len(retry_queue)}개...")
                
                result_dict = summarize_batch_with_gemini(chunk)
                
                for item in chunk:
                    if item['name'] in result_dict:
                        item['ref']['추정 사유'] = result_dict[item['name']]
                        print(f"   ✅ 복구 완료: [{item['name']}]")
                    else:
                        print(f"   ❌ 최종 누락: [{item['name']}] -> 수동 확인 필요")
                        item['ref']['추정 사유'] = "추출 누락 (수동 확인 필요)"
                
                time.sleep(10)

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
        
    print(f"\n=== 모든 분석 완료. 소요시간: {execution_time_str} ===")

if __name__ == "__main__":
    asyncio.run(main())
