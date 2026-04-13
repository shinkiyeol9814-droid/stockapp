import os
import json
import asyncio
from datetime import datetime, timedelta
import pandas as pd
import FinanceDataReader as fdr
from telethon import TelegramClient
from telethon.sessions import StringSession
from google import genai

# 환경 변수 및 클라이언트 설정
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
client_ai = genai.Client(api_key=GEMINI_KEY)

# 💡 [기획 반영] 타겟 텔레그램 채널 리스트
TARGET_CHANNELS = [
#1. 소중한 추억 
    "https://t.me/DOC_POOL",
#2. 리포트 갤러리
    "https://t.me/report_figure_by_offset",
#3. [주식] 증권사 리포트
    "https://t.me/companyreport",
#4. 선진짱 주식공부방
    -1001378197756,
#5. 영리한타이거의 주식공부방
    "https://t.me/YoungTiger_stock",
#6. 언젠간 현인
    -1001710268401
]

async def get_reports_from_telegram(client, hours=12):
    all_messages = []
    limit_time = datetime.now() - timedelta(hours=hours)
    
    for channel in TARGET_CHANNELS:
        try:
            async for message in client.iter_messages(channel, limit=100):
                if message.date.replace(tzinfo=None) < limit_time:
                    break
                if message.text and ("리포트" in message.text or "기업분석" in message.text):
                    all_messages.append(message.text)
        except Exception as e:
            print(f"채널 {channel} 수집 에러: {e}")
    return "\n\n---\n\n".join(all_messages)

def analyze_reports_with_gemini(raw_text):
    prompt = f"""너는 증권사 레포트 핵심 데이터 추출기야. 아래 텍스트에서 레포트 정보를 찾아 JSON 배열로 응답해.
    1. 동일 종목이라도 증권사가 다르면 별도 객체로 생성해.
    2. 중복된 레포트(종목+증권사 동일)는 가장 내용이 알찬 하나만 남겨.
    3. '투자포인트'는 반드시 배열 형태로 2~3개 핵심 요약해.
    4. 목표주가가 없으면 "N/A"로 표기해.
    
    [응답 포맷]
    [
        {{
            "종목명": "종목이름",
            "증권사": "증권사명",
            "목표주가": "숫자만(예: 250000)",
            "평가방식": "구두 표기(예: 25년 PER 12배)",
            "투자포인트": ["포인트1", "포인트2"]
        }}
    ]
    
    [텍스트 데이터]
    {raw_text[:10000]} # 토큰 제한 방지
    """
    response = client_ai.models.generate_content(model='gemini-2.0-flash', contents=prompt)
    return json.loads(response.text.replace("```json", "").replace("```", ""))

async def main():
    # 1. 텔레그램 수집
    # (TelegramClient 설정 생략...)
    raw_text = await get_reports_from_telegram(client_tg)
    
    # 2. Gemini 분석
    analyzed_data = analyze_reports_with_gemini(raw_text)
    
    # 3. [기획 반영] 실시간 시총 및 Upside 계산
    df_listing = fdr.StockListing('KRX')
    results = []
    for item in analyzed_data:
        target_price = item.get("목표주가")
        # 시총 매칭 로직
        matched = df_listing[df_listing['Name'] == item['종목명']]
        if not matched.empty:
            curr_price = matched.iloc[0]['Close']
            curr_marcap = matched.iloc[0]['Marcap']
            item['현재시총'] = int(curr_marcap)
            if target_price != "N/A":
                upside = (int(target_price) / curr_price - 1) * 100
                item['Upside'] = round(upside, 1)
                item['목표시총'] = int(curr_marcap * (1 + upside/100))
            else:
                item['Upside'] = "N/A"
                item['목표시총'] = "N/A"
            results.append(item)

    # 4. JSON 저장
    # (파일 저장 로직 생략...)

if __name__ == "__main__":
    asyncio.run(main())
