import os
import json
import asyncio
import time
from datetime import datetime, timedelta
import pandas as pd
import FinanceDataReader as fdr
import fitz  # PyMuPDF
from telethon import TelegramClient
from telethon.sessions import StringSession
from google import genai

# 환경 변수 설정
API_ID = int(os.environ.get("TELEGRAM_API_ID", 0))
API_HASH = os.environ.get("TELEGRAM_API_HASH", "")
SESSION_STR = os.environ.get("TELEGRAM_SESSION", "")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")

client_ai = genai.Client(api_key=GEMINI_KEY)

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

async def get_pdf_reports_from_telegram(client, hours=12):
    print(f"\n📥 텔레그램에서 최근 {hours}시간 이내의 PDF 레포트 수집 중...")
    extracted_texts = []
    limit_time = datetime.now() - timedelta(hours=hours)
    
    os.makedirs('temp_pdfs', exist_ok=True)
    
    for channel in TARGET_CHANNELS:
        try:
            # 💡 [수정] limit=10 -> 100으로 넉넉하게 변경하여 누락 방지!
            async for message in client.iter_messages(channel, limit=100):
                if message.date.replace(tzinfo=None) < limit_time:
                    break
                    
                if message.document and message.document.mime_type == 'application/pdf':
                    file_name = message.document.attributes[0].file_name
                    
                    pdf_path = await client.download_media(message.document, file=f"temp_pdfs/{file_name}")
                    
                    try:
                        doc = fitz.open(pdf_path)
                        first_page_text = doc[0].get_text()
                        
                        if len(first_page_text) > 200:
                            extracted_texts.append(f"--- [파일명: {file_name}] ---\n{first_page_text}\n")
                            # 💡 [요청사항 추가] 추출 성공한 PDF 파일명 로그 출력!
                            print(f"  📄 [성공] {file_name}")
                        
                        doc.close()
                    except Exception as pdf_e:
                        print(f"  ⚠️ PDF 읽기 에러 ({file_name}): {pdf_e}")
                    
                    if os.path.exists(pdf_path):
                        os.remove(pdf_path)
                        
        except Exception as e:
            print(f"⚠️ 채널 '{channel}' 수집 에러: {e}")
            
    return "\n".join(extracted_texts)

def analyze_reports_with_gemini(raw_text, max_retries=3):
    if not raw_text.strip():
        print("⚠️ [SKIP] 추출된 PDF 텍스트가 없습니다.")
        return []
        
    print(f"\n📊 [AI 분석 준비] 추출된 1페이지 텍스트 총합: {len(raw_text)}자")
        
    prompt = f"""너는 증권사 레포트 전문 분석가야. 
    아래 텍스트는 여러 증권사 레포트의 '1페이지'를 모아놓은 거야. 
    여기서 정보를 추출해서 반드시 아래 JSON 배열 포맷으로만 응답해.
    계산하지 말고 텍스트에 있는 팩트만 가져와.
    
    [응답 포맷]
    [
        {
            "종목명": "종목이름",
            "증권사": "증권사명",
            "레포트 제목": "레포트의 메인 타이틀(제목)", # 👈 이거 한 줄 추가!
            "목표주가": "숫자만(예: 250000)",
            "평가방식": "텍스트에 있는 밸류에이션 근거 (예: 25년 PER 12배)",
            "투자포인트": ["포인트1", "포인트2"]
        }
    ]
    [PDF 추출 데이터]
    {raw_text} 
    """
    # 💡 [수정] 위 {raw_text[:15000]} 에서 [:15000] 가위를 치워버렸습니다! 전체 텍스트 전송!
    
    for attempt in range(max_retries):
        try:
            current_model = 'gemini-2.5-flash'
            print(f"🚀 [AI 호출] {current_model} 요청 발송... (시도 {attempt + 1}/{max_retries})")
            start_time = time.time()
            
            response = client_ai.models.generate_content(model=current_model, contents=prompt)
            
            elapsed = time.time() - start_time
            print(f"✅ [AI 응답 성공] {elapsed:.1f}초 소요!")
            
            res_text = response.text.strip().replace("```json", "").replace("```", "")
            return json.loads(res_text)
            
        except Exception as e:
            print(f"❌ [AI 에러 발생] (시도 {attempt + 1}/{max_retries}) | 사유: {str(e)[:150]}")
            if attempt < max_retries - 1:
                wait_time = 30
                print(f"⏳ {wait_time}초 대기 후 재시도합니다...")
                time.sleep(wait_time)
            else:
                print("💀 [최종 실패] 분석을 종료합니다.")
                return []

async def main():
    print("=== 증권사 레포트 배치 시작 ===")
    
    client_tg = TelegramClient(StringSession(SESSION_STR), API_ID, API_HASH)
    await client_tg.start()
    
    raw_text = await get_pdf_reports_from_telegram(client_tg)
    await client_tg.disconnect() 
    
    if not raw_text:
        print("조건에 맞는 새로운 레포트가 없습니다.")
        return

    analyzed_data = analyze_reports_with_gemini(raw_text)
    
    if not analyzed_data:
        print("AI가 추출한 데이터가 없습니다.")
        return

    print("\n3. FDR 실시간 시총 및 Upside 매칭 중...")
    df_listing = fdr.StockListing('KRX')
    results = []
    
    for item in analyzed_data:
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
            item['현재시총'] = f"{int(curr_marcap // 100_000_000):,}억"
            
            if target_price != "N/A" and curr_price > 0 and target_price > 0:
                upside = (target_price / curr_price - 1) * 100
                item['Upside'] = round(upside, 1)
                
                target_marcap_val = int(curr_marcap * (1 + upside/100))
                item['목표시총'] = f"{int(target_marcap_val // 100_000_000):,}억"
                item['목표주가'] = f"{target_price:,}원"
            else:
                item['Upside'] = "N/A"
                item['목표시총'] = "N/A"
                if target_price != "N/A": item['목표주가'] = f"{target_price:,}원"
                
            results.append(item)

    print(f"4. 최종 데이터 {len(results)}건 저장 중...")
    
    save_dir = 'data/broker_report'
    os.makedirs(save_dir, exist_ok=True)
    
    now = datetime.utcnow() + timedelta(hours=9)
    
    if 8 <= now.hour < 20:
        market_type = "regular"
    else:
        market_type = "premarket"
        
    report = {
        "analysis_time": now.strftime("%Y-%m-%d %H:%M"),
        "market_type": market_type,
        "results": results
    }
    
    file_name = f"{save_dir}/{market_type}_{now.strftime('%Y%m%d_%H%M')}.json"
    with open(file_name, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=4, ensure_ascii=False)
        
    print(f"✅ 배치 완료! 저장된 파일: {file_name}")

if __name__ == "__main__":
    asyncio.run(main())
