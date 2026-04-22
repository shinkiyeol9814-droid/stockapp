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
                    
                    # 💡 사유 1: 중복 파일
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
                            # 💡 사유 2: 텍스트 추출 실패
                            print(f"  ⏩ [제외] {file_name} (사유: 3페이지 내 유효 텍스트 없음/통이미지)")
                        else:
                            file_name_lower = file_name.lower()
                            text_lower = valid_text.lower()
                            
                            # 블랙리스트 체크
                            blacklist = ['산업', '시황', 'weekly', '위클리', 'daily', '데일리', 'morning', '모닝', 'macro', '매크로', '전략', 'strategy', 'etf', '채권', 'spot']
                            matched_black = [w for w in blacklist if w in file_name_lower]
                            
                            # 화이트리스트 체크
                            whitelist = ['목표주가', '목표가', '투자의견', 'target price', '매수', 'buy', 'not rated', 'n/r']
                            has_white = any(w in text_lower for w in whitelist)
                            
                            if matched_black:
                                # 💡 사유 3: 산업/매크로 키워드 감지
                                print(f"  ⏩ [제외] {file_name} (사유: 블랙리스트 키워드 '{matched_black[0]}' 포함)")
                            elif not has_white:
                                # 💡 사유 4: 기업 레포트 핵심 키워드 부재
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

# 💡 3. AI 분석 (내부 재시도 없이 쿨하게 패스)
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
    
    try:
        current_model = 'gemini-2.0-flash' # 💡 아까 찾은 혜자 모델로 세팅
        start_time = time.time()
        
        response = client_ai.models.generate_content(model=current_model, contents=prompt)
        
        elapsed = time.time() - start_time
        print(f"      ✅ AI 응답 성공 ({elapsed:.1f}초)")
        
        res_text = response.text.strip().replace("```json", "").replace("```", "")
        return json.loads(res_text)
        
    except Exception as e:
        error_msg = str(e)
        
        # 💡 [추가된 로직] 404 에러면 비상벨("FATAL_404")을 반환합니다!
        if "404" in error_msg:
            print(f"      🚨 [치명적 에러] 모델을 찾을 수 없습니다(404).")
            return "FATAL_404"
            
        print(f"      ⚠️ AI 처리 실패. 즉시 패자부활전으로 넘깁니다. (사유: {error_msg[:50]})")
        return None

# 💡 4. JSON 저장 & 중복 제거 (버틀러 우선)
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

    # 💡 [요구사항] 중복 제거: 버틀러 우선 원칙
    unique_results = {}
    for item in results:
        clean_name = item['종목명'].split('(')[0].strip()
        is_butler = (item.get('source') == 'butler_works')
        
        if clean_name in unique_results:
            # 기존에 들어간 게 PDF이고, 이번에 들어온 게 버틀러면 덮어쓰기!
            if is_butler and unique_results[clean_name].get('source') != 'butler_works':
                unique_results[clean_name] = item
        else:
            unique_results[clean_name] = item
            
    # 최종 저장할 때 쓸데없는 내부 변수(doc_id, source) 제거
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

# 💡 5. 메인 루프 (3연속 실패 시 강제 종료 & 3단 패자부활전)
async def main():
    print("=== 증권사 레포트 배치 시작 (버틀러 통합 & 3단 패자부활전) ===")
    
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

    chunk_size = 3
    MAX_PASSES = 4 
    
    current_queue = docs_to_process
    all_analyzed_data = []
    
    # 💡 [핵심 추가] 연속 실패 횟수를 기억하는 카운터
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
            
            if res == "FATAL_404":
                print("\n🛑 [배치 강제 종료] 404 모델 에러가 발생하여 더 이상 진행하지 않고 전체 배치를 취소합니다.")
                return  
                
            elif res is None:
                failed_queue.extend(chunk)
                
                # 💡 [핵심 추가] 실패 시 카운터 1 증가. 3번 연속 쌓이면 가차 없이 종료!
                consecutive_failures += 1
                if consecutive_failures >= 3:
                    print(f"\n🛑 [배치 셧다운] 3회 연속 API 호출에 실패했습니다! (API 한도 초과 또는 서버 장애 의심)")
                    print("시간 낭비를 막기 위해 남아있는 모든 작업을 즉시 취소하고 배치를 종료합니다.")
                    return 
            else:
                # 💡 [핵심 추가] 한 번이라도 성공하면 카운터를 다시 0으로 리셋!
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
            
            # 💡 [중요] 1분당 토큰 한도(429 에러) 방지를 위해 15초씩 넉넉히 대기
            time.sleep(15)
            
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
