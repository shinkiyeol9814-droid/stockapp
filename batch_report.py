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
# 💡 B계정(새 API 키) 사용
GEMINI_KEY = os.environ.get("GEMINI_API_KEY_A", "") 

client_ai = genai.Client(api_key=GEMINI_KEY)

# 텔레그램 채널 설정
TARGET_CHANNELS_TEXT = ["https://t.me/butler_works"]
TARGET_CHANNELS_PDF = [
    "https://t.me/DOC_POOL",
    "https://t.me/report_figure_by_offset",
    "https://t.me/companyreport",
    -1001378197756,
    "https://t.me/YoungTiger_stock",
    -1001710268401                  
]

# 💡 [신규] API 일일 사용량 관리 함수
USAGE_LOG_FILE = "data/api_usage_log.json"

def get_today_api_usage():
    today_str = datetime.now().strftime("%Y-%m-%d")
    os.makedirs(os.path.dirname(USAGE_LOG_FILE), exist_ok=True)
    
    if os.path.exists(USAGE_LOG_FILE):
        with open(USAGE_LOG_FILE, "r") as f:
            try:
                data = json.load(f)
                if data.get("date") == today_str:
                    return data.get("count", 0)
            except:
                pass
    return 0

def increment_api_usage():
    today_str = datetime.now().strftime("%Y-%m-%d")
    current_count = get_today_api_usage() + 1
    
    data = {
        "date": today_str,
        "count": current_count
    }
    
    with open(USAGE_LOG_FILE, "w") as f:
        json.dump(data, f)
        
    return current_count

# 💡 데이터 수집 함수 (제외 사유 로그 상세화)
async def get_all_reports_from_telegram(client, hours=12):
    print(f"\n📥 텔레그램에서 최근 {hours}시간 이내의 레포트 수집 시작...")
    docs_to_process = []
    doc_id_counter = 1
    seen_files = set()
    limit_time = datetime.now() - timedelta(hours=hours)
    
    # [A] 버틀러 요약 텍스트
    for channel in TARGET_CHANNELS_TEXT:
        try:
            async for message in client.iter_messages(channel, limit=100):
                if message.date.replace(tzinfo=None) < limit_time: break
                if message.text and len(message.text) > 50:
                    docs_to_process.append({
                        "id": str(doc_id_counter),
                        "source": "butler_works",
                        "text": f"--- [버틀러 요약 텍스트] ---\n{message.text}"
                    })
                    print(f"  📝 [성공] 버틀러 요약본 (ID: {doc_id_counter})")
                    doc_id_counter += 1
        except Exception as e:
            print(f"  ⚠️ 텍스트 채널 에러: {e}")

    # [B] PDF 파일 분석
    os.makedirs('temp_pdfs', exist_ok=True)
    for channel in TARGET_CHANNELS_PDF:
        try:
            async for message in client.iter_messages(channel, limit=100):
                if message.date.replace(tzinfo=None) < limit_time: break
                    
                if message.document and message.document.mime_type == 'application/pdf':
                    file_name = message.document.attributes[0].file_name
                    
                    if file_name in seen_files:
                        print(f"  ⏩ [제외] {file_name} (사유: 중복 수집됨)")
                        continue
                    seen_files.add(file_name)
                    
                    pdf_path = await client.download_media(message.document, file=f"temp_pdfs/{file_name}")
                    
                    try:
                        doc = fitz.open(pdf_path)
                        valid_text = ""
                        for page_num in range(min(3, doc.page_count)):
                            page_text = doc[page_num].get_text()
                            if len(page_text) > 200:
                                valid_text = page_text
                                break 
                        doc.close()
                        
                        if not valid_text:
                            print(f"  ⏩ [제외] {file_name} (사유: 3페이지 내 유효 텍스트 없음/통이미지)")
                        else:
                            file_name_lower = file_name.lower()
                            text_lower = valid_text.lower()
                            
                            blacklist = ['산업', '시황', 'weekly', '위클리', 'daily', '데일리', 'morning', '모닝', 'macro', '매크로', '전략', 'strategy', 'etf', '채권', 'spot']
                            matched_black = [w for w in blacklist if w in file_name_lower]
                            
                            whitelist = ['목표주가', '목표가', '투자의견', 'target price', '매수', 'buy', 'not rated', 'n/r']
                            has_white = any(w in text_lower for w in whitelist)
                            
                            if matched_black:
                                print(f"  ⏩ [제외] {file_name} (사유: 블랙리스트 키워드 '{matched_black[0]}' 포함)")
                            elif not has_white:
                                print(f"  ⏩ [제외] {file_name} (사유: 투자의견/목표가 등 핵심 키워드 없음)")
                            else:
                                docs_to_process.append({
                                    "id": str(doc_id_counter),
                                    "source": file_name,
                                    "text": f"--- [파일명: {file_name}] ---\n{valid_text}"
                                })
                                print(f"  📄 [성공] {file_name} (ID: {doc_id_counter})")
                                doc_id_counter += 1
                                
                    except Exception as pdf_e:
                        print(f"  ⚠️ [실패] {file_name} (사유: PDF 파싱 에러 - {pdf_e})")
                    
                    if os.path.exists(pdf_path): os.remove(pdf_path)
        except Exception as e:
            print(f"  ⚠️ PDF 채널 에러: {e}")
            
    return docs_to_process

# 💡 3. AI 분석 
def analyze_chunk_with_gemini(chunk_docs):
    if not chunk_docs: return []
    
    prompt_text = ""
    for d in chunk_docs:
        safe_text = d['text'][:2500] 
        prompt_text += f"\n\n[문서 ID: {d['id']}]\n{safe_text}"
        
    prompt = f"""너는 증권사 레포트 전문 분석가야. 
    아래 텍스트는 여러 증권사 레포트의 요약본(버틀러)이거나 PDF 발췌본이야. 
    각 문서별로 정보를 추출해서 반드시 아래 JSON 배열 포맷으로만 응답해.
    기업 분석 레포트가 아니라고 판단되면 결과에서 제외해.
    
    [주의사항: 버틀러 요약 텍스트 처리법]
    - 첫 줄 괄호 앞은 '종목명', '작성자'는 '증권사', 두 번째 줄은 '레포트 제목', '- '로 시작하는 문장들은 '투자포인트'로 정리해.
    - '평가방식'이 명시되어 있지 않으면 "N/A"로 기입해.

    [응답 포맷]
    [
        {{
            "doc_id": "원문에 부여된 문서 ID (반드시 기입할 것)",
            "종목명": "종목이름",
            "증권사": "증권사명",
            "레포트 제목": "레포트의 메인 타이틀(제목)",
            "발행일자": "YYYY-MM-DD 형식",
            "목표주가": "숫자만(예: 250000)",
            "평가방식": "텍스트에 있는 밸류에이션 근거",
            "투자포인트": ["포인트1", "포인트2"]
        }}
    ]
    
    [분석할 문서들]
    {prompt_text} 
    """
    
    # 💡 [핵심 수정] API 요청을 시도하기 직전에 "무조건" 카운트를 1 올립니다!
    # 이렇게 해야 실패하든 성공하든 구글의 실제 카운터와 완벽하게 동기화됩니다.
    current_usage = increment_api_usage()
    
    try:
        current_model = 'gemini-2.5-flash'
        start_time = time.time()
        
        response = client_ai.models.generate_content(model=current_model, contents=prompt)
        
        elapsed = time.time() - start_time
        print(f"      ✅ AI 응답 성공 ({elapsed:.1f}초) 📊 [오늘 누적 요청: {current_usage}회]")
        
        res_text = response.text.strip().replace("```json", "").replace("```", "")
        return json.loads(res_text)
        
    except Exception as e:
        error_msg = str(e)
        
        if "404" in error_msg:
            print(f"      🚨 [치명적 에러] 모델을 찾을 수 없습니다(404).")
            return "FATAL_404"
            
        if "429" in error_msg:
            # 카운트를 위에서 이미 올렸으므로 현재 변수를 그대로 출력하면 됩니다.
            print(f"      🚨 [한도 초과] 429 에러 발생. 📊 [현재 누적 요청: {current_usage}회]")
            return "FATAL_429"
            
        print(f"      ⚠️ AI 처리 실패. 즉시 패자부활전으로 넘깁니다. 📊 [누적 요청: {current_usage}회] (사유: {error_msg[:50]})")
        return None

# 💡 4. JSON 저장 & 중복 제거
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

    unique_results = {}
    for item in results:
        clean_name = item['종목명'].split('(')[0].strip()
        is_butler = (item.get('source') == 'butler_works')
        
        if clean_name in unique_results:
            if is_butler and unique_results[clean_name].get('source') != 'butler_works':
                unique_results[clean_name] = item
        else:
            unique_results[clean_name] = item
            
    final_results = []
    for val in unique_results.values():
        val.pop('doc_id', None)
        val.pop('source', None)
        final_results.append(val)
    
    report = {
        "analysis_time": analysis_time,
        "market_type": market_type,
        "results": final_results
    }
    
    with open(file_name, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=4, ensure_ascii=False)
        
    print(f"\n💾 [{pass_num}회전 저장] 누적 데이터 {len(final_results)}건이 웹 대시보드에 업데이트되었습니다!")

# 💡 5. 메인 루프 
async def main():
    today_usage = get_today_api_usage()
    print("=== 증권사 레포트 배치 시작 (버틀러 통합 & 3단 패자부활전) ===")
    print(f"📊 [현재 상태] 오늘 {today_usage}회의 API를 이미 사용했습니다.")
    
    now = datetime.utcnow() + timedelta(hours=9)
    market_type = "regular" if 8 <= now.hour < 20 else "premarket"
    analysis_time = now.strftime("%Y-%m-%d %H:%M")
    
    save_dir = 'data/broker_report'
    os.makedirs(save_dir, exist_ok=True)
    file_name = f"{save_dir}/{market_type}_{now.strftime('%Y%m%d_%H%M')}.json"
    
    client_tg = TelegramClient(StringSession(SESSION_STR), API_ID, API_HASH)
    await client_tg.start()
    
    docs_to_process = await get_all_reports_from_telegram(client_tg)
    await client_tg.disconnect() 
    
    if not docs_to_process:
        print("조건에 맞는 새로운 레포트/텍스트가 없습니다.")
        return

    doc_source_map = {str(d['id']): d['source'] for d in docs_to_process}

    print(f"\n🔍 총 {len(docs_to_process)}개의 문서를 분석합니다.")
    df_listing = fdr.StockListing('KRX')

    chunk_size = 5 # 💡 청크 사이즈 다시 5개로 원복!
    MAX_PASSES = 4 
    
    current_queue = docs_to_process
    all_analyzed_data = []
    consecutive_failures = 0 

    for pass_num in range(1, MAX_PASSES + 1):
        if not current_queue:
            print(f"\n🎉 [완벽 성공] 누락된 문서 없이 모든 분석이 완료되었습니다!")
            break
            
        phase_name = "본게임" if pass_num == 1 else f"패자부활전 {pass_num-1}차"
        print(f"\n=============================================")
        print(f"🚀 [{pass_num}회전: {phase_name}] 총 {len(current_queue)}개 문서 진행 중...")
        print(f"=============================================")
        
        failed_queue = []
        
        for i in range(0, len(current_queue), chunk_size):
            chunk = current_queue[i : i + chunk_size]
            
            print(f"\n▶️ 진행 중... ({i+1}~{min(i+chunk_size, len(current_queue))}) / {len(current_queue)}")
            res = analyze_chunk_with_gemini(chunk)
            
            # 💡 [로직 완전 정리] 404, 429, 503(None) 구별
            if res == "FATAL_404":
                print("\n🛑 [배치 강제 종료] 404 모델 에러가 발생하여 전체 배치를 취소합니다.")
                return  
                
            elif res == "FATAL_429":
                failed_queue.extend(chunk)
                consecutive_failures += 1
                if consecutive_failures >= 3:
                    print(f"\n🛑 [배치 셧다운] 429(한도 초과) 3회 연속 발생! 배치를 즉시 종료합니다.")
                    return 
                    
            elif res is None:
                # 503 등 일시적 서버 에러는 카운트를 올리지 않고 조용히 패자부활전으로 넘김
                failed_queue.extend(chunk)
                
            else:
                # 성공하면 연속 429 실패 카운터를 리셋!
                consecutive_failures = 0
                
                returned_ids = [str(r.get('doc_id', '')) for r in res]
                
                for r in res:
                    if '종목명' in r and r['종목명']:
                        r['source'] = doc_source_map.get(str(r.get('doc_id', '')), 'pdf')
                        all_analyzed_data.append(r)
                
                for d in chunk:
                    doc_id = str(d['id'])
                    if doc_id in returned_ids:
                        print(f"      ➡️ 성공: [ID {doc_id}]")
                    else:
                        print(f"      ⚠️ 누락: [ID {doc_id}] -> 패자부활전 대기열 추가")
                        failed_queue.append(d)
            
            # # 💡 15초 대기 (if-elif-else 구문이 끝난 뒤 무조건 실행)
            # print("      ⏳ 다음 문서를 위해 15초 대기합니다...") 
            # time.sleep(15)
            
        if all_analyzed_data:
            save_and_match_to_json(all_analyzed_data, df_listing, file_name, market_type, analysis_time, pass_num)
            
        current_queue = failed_queue
        
        if current_queue and pass_num < MAX_PASSES:
            print(f"\n⏳ {pass_num}회전 종료. 누락된 {len(current_queue)}개 문서 재도전을 위해 10초 대기합니다...")
            time.sleep(10)
            
    if current_queue:
        print(f"\n💀 [최종 종료] 마지막 3차 패자부활전까지 시도했으나 {len(current_queue)}개 문서는 끝내 분석하지 못했습니다.")
    
    print(f"\n✅ 최종 배치 프로세스 완전 종료!")

if __name__ == "__main__":
    asyncio.run(main())
