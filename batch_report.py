import os
import json
import asyncio
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
            async for message in client.iter_messages(channel, limit=50):
                if message.date.replace(tzinfo=None) < limit_time:
                    break
                # 리포트나 목표주가 관련 키워드가 있는 메시지만 수집
                if message.text and any(k in message.text for k in ["리포트", "기업분석", "목표주가", "TP"]):
                    all_messages.append(message.text)
        except Exception as e:
            print(f"⚠️ 채널 '{channel}' 수집 에러: {e}")
            
    return "\n\n---\n\n".join(all_messages)

def analyze_reports_with_gemini(raw_text):
    if not raw_text.strip():
        return []
        
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
            "평가방식": "구두 표기(예: 25년 PER 12배 적용)",
            "투자포인트": ["포인트1", "포인트2"]
        }}
    ]
    
    [텍스트 데이터]
    {raw_text[:20000]}
    """
    try:
        response = client_ai.models.generate_content(model='gemini-2.5-flash', contents=prompt)
        res_text = response.text.strip().replace("```json", "").replace("```", "")
        return json.loads(res_text)
    except Exception as e:
        print(f"⚠️ AI 분석 에러: {e}")
        return []

async def main():
    print("=== 장전/장후 증권사 레포트 배치 시작 ===")
    
    # 1. 텔레그램 연결 (아까 빠졌던 부분!)
    client_tg = TelegramClient(StringSession(SESSION_STR), API_ID, API_HASH)
    await client_tg.start()
    
    print("1. 텔레그램 6개 채널 수집 중...")
    raw_text = await get_reports_from_telegram(client_tg)
    
    await client_tg.disconnect() # 안전하게 연결 종료
    
    if not raw_text:
        print("조건에 맞는 새로운 레포트 메시지가 없습니다.")
        return

    print("2. Gemini AI 데이터 정제 중...")
    analyzed_data = analyze_reports_with_gemini(raw_text)
    
    if not analyzed_data:
        print("AI가 추출한 데이터가 없습니다.")
        return

    print("3. FDR 실시간 시총 및 Upside 매칭 중...")
    df_listing = fdr.StockListing('KRX')
    results = []
    
    for item in analyzed_data:
        # 목표주가에 쉼표(,)나 '원' 글자가 섞여있어도 숫자만 빼내기
        target_price_str = item.get("목표주가", "N/A")
        target_price = 0
        if target_price_str != "N/A":
            try:
                target_price = int(''.join(filter(str.isdigit, str(target_price_str))))
            except:
                target_price = "N/A"

        matched = df_listing[df_listing['Name'] == item['종목명']]
        if not matched.empty:
            curr_price = matched.iloc[0]['Close']
            curr_marcap = matched.iloc[0]['Marcap']
            item['현재시총'] = int(curr_marcap)
            
            if target_price != "N/A" and curr_price > 0 and target_price > 0:
                upside = (target_price / curr_price - 1) * 100
                item['Upside'] = round(upside, 1)
                item['목표시총'] = int(curr_marcap * (1 + upside/100))
                item['목표주가'] = f"{target_price:,}원" # 보기 좋게 다시 포맷팅
            else:
                item['Upside'] = "N/A"
                item['목표시총'] = "N/A"
                if target_price != "N/A": item['목표주가'] = f"{target_price:,}원"
                
            results.append(item)

    # 4. JSON 파일로 이쁘게 굽기
    print(f"4. 최종 데이터 {len(results)}건 저장 중...")
    os.makedirs('data', exist_ok=True)
    now = datetime.utcnow() + timedelta(hours=9)
    report = {
        "analysis_time": now.strftime("%Y-%m-%d %H:%M"),
        "results": results
    }
    
    file_name = f"data/report_summary_{now.strftime('%Y%m%d_%H%M')}.json"
    with open(file_name, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=4, ensure_ascii=False)
        
    print(f"✅ 배치 완료! 저장된 파일: {file_name}")

if __name__ == "__main__":
    asyncio.run(main())
