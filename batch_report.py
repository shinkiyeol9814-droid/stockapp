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

async def get_pdf_reports_from_telegram_list(client, hours=12):
    print(f"\n📥 텔레그램에서 최근 {hours}시간 이내의 PDF 레포트 수집 중...")
    extracted_texts_list = []
    seen_files = set()
    limit_time = datetime.now() - timedelta(hours=hours)
    
    os.makedirs('temp_pdfs', exist_ok=True)
    
    for channel in TARGET_CHANNELS:
        try:
            async for message in client.iter_messages(channel, limit=100):
                if message.date.replace(tzinfo=None) < limit_time:
                    break
                    
                if message.document and message.document.mime_type == 'application/pdf':
                    file_name = message.document.attributes[0].file_name
                    
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
            "발행일자": "YYYY-MM-DD 형식",
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
            error_msg = str(e)
            wait_time = 30 if ("429" in error_msg or "503" in error_msg) else 10
            print(f"      ⚠️ AI 에러 (시도 {attempt + 1}/{max_retries}) | {wait_time}초 대기... (사유: {error_msg[:30]})")
            
            if attempt < max_retries - 1:
                time.sleep(wait_time)
            else:
                return None

# 💡 [핵심] 매칭과 저장을 1바퀴 돌 때마다 호출할 수 있도록 분리한 함수
def save_and_match_to_json(analyzed_data, df_listing, file_name, market_type, analysis_time, pass_num):
    results = []
    
    for item in analyzed_data:
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

    # 종목명 기준 중복 제거
    unique_results = {}
    for item in results:
        unique_results[item['종목명']] = item
    results = list(unique_results.values())
    
    report = {
        "analysis_time": analysis_time,
        "market_type": market_type,
        "results": results
    }
    
    with open(file_name, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=4, ensure_ascii=False)
        
    print(f"\n💾 [{pass_num}차 저장 완료] 누적 데이터 {len(results)}건이 웹 대시보드에 즉시 업데이트되었습니다!")

async def main():
    print("=== 증권사 레포트 배치 시작 ===")
    
    # 초기에 파일명과 시간을 고정해두어, 루프를 돌며 동일한 파일에 덮어쓰기(업데이트) 합니다.
    now = datetime.utcnow() + timedelta(hours=9)
    market_type = "regular" if 8 <= now.hour < 20 else "premarket"
    analysis_time = now.strftime("%Y-%m-%d %H:%M")
    
    save_dir = 'data/broker_report'
    os.makedirs(save_dir, exist_ok=True)
    file_name = f"{save_dir}/{market_type}_{now.strftime('%Y%m%d_%H%M')}.json"
    
    client_tg = TelegramClient(StringSession(SESSION_STR), API_ID, API_HASH)
    await client_tg.start()
    
    report_text_list = await get_pdf_reports_from_telegram_list(client_tg)
    await client_tg.disconnect() 
    
    if not report_text_list:
        print("조건에 맞는 새로운 레포트가 없습니다.")
        return

    # FDR 종목 정보는 시작할 때 딱 한 번만 불러옵니다 (속도 최적화)
    print("\n🔍 한국거래소 종목 리스트 불러오는 중...")
    df_listing = fdr.StockListing('KRX')

    chunk_size = 10
    MAX_PASSES = 5 # 💡 최대 5바퀴 (1차 본게임 + 4차 패자부활전)
    current_chunks = [report_text_list[i : i + chunk_size] for i in range(0, len(report_text_list), chunk_size)]
    all_analyzed_data = []

    for pass_num in range(1, MAX_PASSES + 1):
        if not current_chunks:
            print(f"\n🎉 [완벽 성공] 실패한 구간 없이 모든 레포트 분석이 완료되었습니다! (총 {pass_num-1}회전)")
            break
            
        print(f"\n=============================================")
        print(f"🚀 [{pass_num}차 분석 시작] 총 {len(current_chunks)}개 구간 분석 중...")
        print(f"=============================================")
        
        failed_chunks = []
        
        for idx, chunk in enumerate(current_chunks):
            chunk_raw_text = "\n".join(chunk)
            
            print(f"\n▶️ 진행 중... (구간 {idx+1}/{len(current_chunks)})")
            analyzed_part = analyze_reports_with_gemini(chunk_raw_text)
            
            if analyzed_part is not None:
                print(f"      ➡️ {len(analyzed_part)}건 추출 성공!")
                all_analyzed_data.extend(analyzed_part)
            else:
                print(f"      ❌ [실패] 해당 구간은 다음 패자부활전으로 넘깁니다.")
                failed_chunks.append(chunk)
            
            # API 한도 보호
            time.sleep(20)
            
        # 💡 [핵심] 1바퀴 돌 때마다 모인 데이터로 JSON을 즉시 덮어씌웁니다!
        if all_analyzed_data:
            save_and_match_to_json(all_analyzed_data, df_listing, file_name, market_type, analysis_time, pass_num)
            
        # 다음 바퀴를 위해 실패한 청크들을 장전
        current_chunks = failed_chunks
        
        if current_chunks and pass_num < MAX_PASSES:
            print(f"\n⏳ {pass_num}차 분석 종료. {len(current_chunks)}개 실패 구간 재도전을 위해 30초 대기합니다...")
            time.sleep(30)
            
    if current_chunks:
        print(f"\n💀 [최종 종료] 최대 {MAX_PASSES}회 시도했으나 {len(current_chunks)}개 구간은 끝내 구출하지 못했습니다.")
    
    print(f"\n✅ 최종 배치 프로세스 완전 종료!")

if __name__ == "__main__":
    asyncio.run(main())
