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
    "https://t.me/DOC_POOL",
    "https://t.me/report_figure_by_offset",
    "https://t.me/companyreport",
    -1001378197756,
    "https://t.me/YoungTiger_stock",
    -1001710268401                  
]

# 💡 [수정] 텍스트 하나로 뭉치지 않고, 각 레포트를 리스트 형태로 반환합니다!
# 💡 1. PDF 다운로드 함수 (파일명 중복 방지 추가)
async def get_pdf_reports_from_telegram_list(client, hours=12):
    print(f"\n📥 텔레그램에서 최근 {hours}시간 이내의 PDF 레포트 수집 중...")
    extracted_texts_list = []
    seen_files = set() # 👈 중복 체크용 바구니 추가!
    limit_time = datetime.now() - timedelta(hours=hours)
    
    os.makedirs('temp_pdfs', exist_ok=True)
    
    for channel in TARGET_CHANNELS:
        try:
            async for message in client.iter_messages(channel, limit=100):
                if message.date.replace(tzinfo=None) < limit_time:
                    break
                    
                if message.document and message.document.mime_type == 'application/pdf':
                    file_name = message.document.attributes[0].file_name
                    
                    # 💡 파일명이 이미 수집한 거면 패스!
                    if file_name in seen_files:
                        continue
                    seen_files.add(file_name)
                    
                    pdf_path = await client.download_media(message.document, file=f"temp_pdfs/{file_name}")
                    
                    try:
                        doc = fitz.open(pdf_path)
                        first_page_text = doc[0].get_text()
                        
                        if len(first_page_text) > 200:
                            extracted_texts_list.append(f"--- [파일명: {file_name}] ---\n{first_page_text}\n")
                            print(f"  📄 [성공] {file_name}")
                        doc.close()
                    except Exception as pdf_e:
                        print(f"  ⚠️ PDF 읽기 에러 ({file_name}): {pdf_e}")
                    
                    if os.path.exists(pdf_path):
                        os.remove(pdf_path)
                        
        except Exception as e:
            print(f"⚠️ 채널 '{channel}' 수집 에러: {e}")
            
    return extracted_texts_list

def analyze_reports_with_gemini(raw_text, max_retries=3):
    if not raw_text.strip():
        return []
        
    prompt = f"""너는 증권사 레포트 전문 분석가야. 
    아래 텍스트는 여러 증권사 레포트의 '1페이지'를 모아놓은 거야. 
    여기서 정보를 추출해서 반드시 아래 JSON 배열 포맷으로만 응답해.
    계산하지 말고 텍스트에 있는 팩트만 가져와.
    
    [응답 포맷]
    [
        {{
            "종목명": "종목이름",
            "증권사": "증권사명",
            "레포트 제목": "레포트의 메인 타이틀(제목)",
            "발행일자": "YYYY-MM-DD 형식", # 👈 이 줄을 추가하세요!
            "목표주가": "숫자만(예: 250000)",
            "평가방식": "텍스트에 있는 밸류에이션 근거",
            "투자포인트": ["포인트1", "포인트2"]
        }}
    ]
    
    [PDF 추출 데이터]
    {raw_text} 
    """
    
    for attempt in range(max_retries):
        try:
            current_model = 'gemini-2.5-flash'
            start_time = time.time()
            
            response = client_ai.models.generate_content(model=current_model, contents=prompt)
            
            elapsed = time.time() - start_time
            print(f"      ✅ AI 응답 성공 ({elapsed:.1f}초)")
            
            res_text = response.text.strip().replace("```json", "").replace("```", "")
            return json.loads(res_text)
            
        except Exception as e:
            wait_time = 30 if "429" in str(e) else 10
            print(f"      ⚠️ AI 에러 (시도 {attempt + 1}/{max_retries}) | {wait_time}초 대기... (사유: {str(e)[:50]})")
            if attempt < max_retries - 1:
                time.sleep(wait_time)
            else:
                print("      💀 [해당 구간 최종 실패] 다음 구간으로 넘어갑니다.")
                return []

async def main():
    print("=== 증권사 레포트 배치 시작 ===")
    
    client_tg = TelegramClient(StringSession(SESSION_STR), API_ID, API_HASH)
    await client_tg.start()
    
    # 💡 1. 텍스트를 하나로 뭉치지 않고 리스트로 받습니다.
    report_text_list = await get_pdf_reports_from_telegram_list(client_tg)
    await client_tg.disconnect() 
    
    if not report_text_list:
        print("조건에 맞는 새로운 레포트가 없습니다.")
        return

    print(f"\n📊 총 {len(report_text_list)}개의 레포트를 분석합니다. (API 한도 보호를 위해 10개씩 나누어 처리)")

    # 💡 2. 10개씩 쪼개서(Chunking) AI에게 질문합니다!
    chunk_size = 10
    all_analyzed_data = []
    
    for i in range(0, len(report_text_list), chunk_size):
        chunk = report_text_list[i : i + chunk_size]
        chunk_raw_text = "\n".join(chunk)
        
        print(f"\n🚀 AI 분석 중... ({i+1}~{min(i+chunk_size, len(report_text_list))}번째 레포트)")
        analyzed_part = analyze_reports_with_gemini(chunk_raw_text)
        
        if analyzed_part:
            print(f"      ➡️ {len(analyzed_part)}건 추출 완료")
            all_analyzed_data.extend(analyzed_part)
        
        # 💡 [수정] API 1분당 토큰 한도(Quota) 보호를 위해 충분한 휴식 부여
        print("      ⏳ API 한도 보호를 위해 20초 대기합니다...")
        time.sleep(20)

    if not all_analyzed_data:
        print("AI가 추출한 데이터가 최종적으로 0건입니다.")
        return

    print(f"\n3. FDR 실시간 시총 및 Upside 매칭 중... (총 {len(all_analyzed_data)}건)")
    df_listing = fdr.StockListing('KRX')
    results = []
    
    for item in all_analyzed_data:
        # 1. AI가 추출한 종목명에서 괄호와 그 안의 숫자(코드)를 싹 지우고 공백 제거
        raw_name = item.get('종목명', '')
        clean_name = raw_name.split('(')[0].strip() 
        
        target_price_str = item.get("목표주가", "N/A")
        target_price = 0
        if target_price_str != "N/A":
            try:
                target_price = int(''.join(filter(str.isdigit, str(target_price_str))))
            except:
                target_price = "N/A"

        matched = df_listing[df_listing['Name'] == clean_name]
        
        if not matched.empty:
            curr_price = matched.iloc[0]['Close']
            curr_marcap = matched.iloc[0]['Marcap']
            
            # 💡 [여기 수정됨!] 현재가를 FDR에서 가져와 콤마 찍어서 저장
            item['현재가'] = f"{int(curr_price):,}원"
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
        else:
            print(f"   ⚠️ 매칭 실패: AI가 뽑은 이름 [{raw_name}] -> 필터 통과 이름 [{clean_name}]")

    # 💡 [수정] 종목명 기준으로 중복 제거 (리스트 에러 방지 및 가장 최신 1개만 유지)
    unique_results = {}
    for item in results:
        unique_results[item['종목명']] = item
    results = list(unique_results.values())

    print(f"\n4. 최종 데이터 {len(results)}건 저장 중...")
    
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
