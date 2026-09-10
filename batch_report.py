import os
import io
import re
import json
import asyncio
import time
import requests
from datetime import datetime, timedelta
import pandas as pd
import fitz  # PyMuPDF
from telethon import TelegramClient
from telethon.sessions import StringSession
from google import genai
import FinanceDataReader as fdr

from krx_listing import fetch_krx_listing, fetch_krx_marcap

# 환경 변수 설정
API_ID = int(os.environ.get("TELEGRAM_API_ID", 0))
API_HASH = os.environ.get("TELEGRAM_API_HASH", "")
SESSION_STR = os.environ.get("TELEGRAM_SESSION_BATCH") or os.environ.get("TELEGRAM_SESSION", "")
# 💡 A계정 결제했음. Tier1이므로 통합하여 사용
GEMINI_KEY = os.environ.get("GEMINI_API_KEY_A", "") 

client_ai = genai.Client(api_key=GEMINI_KEY)

# 💡 텔레그램 채널 목록은 시크릿으로 받는다 (쉼표 구분).
#   REPORT_CHANNELS_TEXT — 버틀러 요약 텍스트 채널
#   REPORT_CHANNELS_PDF  — 증권사 PDF 채널 (비공개 채널 ID -100... 포함)
# 비공개 채널 ID/초대 링크는 사실상 접근 정보라 공개 리포지토리에 두면 안 된다.
def _parse_channels(env_name: str) -> list:
    """쉼표 구분 문자열을 채널 리스트로. 숫자면 int(채널 ID)로 변환."""
    raw = os.environ.get(env_name, "")
    out = []
    for tok in raw.split(","):
        tok = tok.strip()
        if not tok:
            continue
        # -100xxxxxxxxxx 형태의 채널 ID는 int여야 telethon이 받아준다
        out.append(int(tok) if re.fullmatch(r"-?\d+", tok) else tok)
    return out


TARGET_CHANNELS_TEXT = _parse_channels("REPORT_CHANNELS_TEXT")
TARGET_CHANNELS_PDF = _parse_channels("REPORT_CHANNELS_PDF")

# 💡 API 일일 사용량 관리 함수
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
            except Exception:
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

# 💡 데이터 수집 함수 (Fix된 시간 구간 적용)
async def get_all_reports_from_telegram(client, start_time, end_time):
    print(f"\n📥 텔레그램 레포트 수집 시작")
    print(f"  ⏱️ 수집 타겟 구간: {start_time.strftime('%Y-%m-%d %H:%M')} ~ {end_time.strftime('%Y-%m-%d %H:%M')}")
    
    docs_to_process = []
    doc_id_counter = 1
    seen_files = set()
    
    # [A] 버틀러 요약 텍스트
    for channel in TARGET_CHANNELS_TEXT:
        try:
            async for message in client.iter_messages(channel, limit=100):
                # 💡 텔레그램의 UTC 시간을 한국 시간(KST)으로 변환
                msg_time_kst = message.date.replace(tzinfo=None) + timedelta(hours=9)
                
                # 수집 시작 시간보다 과거 메시지면 반복문 중단
                if msg_time_kst < start_time: break 
                # 수집 종료 시간보다 미래 메시지면 스킵 (안전장치)
                if msg_time_kst > end_time: continue 
                
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
                msg_time_kst = message.date.replace(tzinfo=None) + timedelta(hours=9)
                
                if msg_time_kst < start_time: break 
                if msg_time_kst > end_time: continue
                    
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
                            
                            blacklist = [
                                '산업', '시황', 'weekly', '위클리', 'daily', '데일리', 
                                'morning', '모닝', 'macro', '매크로', '전략', 'strategy', 
                                'etf', '채권', 'spot', 
                                '섹터', '마켓', '클로징', '증시', '부동산', '포트폴리오', '시장_전망'
                            ]
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

# 💡 3. AI 분석 (503 재시도 로직 탑재)
def analyze_chunk_with_gemini(chunk_docs):
    if not chunk_docs: return []
    prompt_text = ""
    for d in chunk_docs:
        safe_text = d['text'][:2500] 
        prompt_text += f"\n\n[문서 ID: {d['id']}]\n{safe_text}"
        
    prompt = f"""너는 증권사 레포트 전문 분석가야.
    아래 텍스트에서 정보를 추출해서 반드시 JSON 배열 포맷으로만 응답해.
    기업 분석 레포트가 아니면 제외해.
    [응답 포맷]
    [
        {{
            "doc_id": "문서 ID",
            "종목명": "종목이름",
            "증권사": "증권사명",
            "레포트 제목": "제목",
            "발행일자": "YYYY-MM-DD",
            "현재주가": "레포트에 명시된 현재 주가 (숫자만, 원 단위. 없으면 null)",
            "목표주가": "목표주가 (숫자만, 원 단위)",
            "상승여력": "레포트에 명시된 상승여력 또는 Upside % (숫자만. 없으면 null)",
            "평가방식": "밸류에이션 방법론을 구체적으로 서술. 예) '2026년 EPS 4,200원에 PER 12배 적용', '12개월 선행 EV/EBITDA 8배', 'DCF 기반 WACC 9% 적용', 'SOTP: 핵심사업 PER 15배 + 자회사 지분가치 3,000억'. 적용 배수의 근거(역사적 평균, 피어그룹 평균, 할인 적용 등)도 포함. 없으면 null",
            "투자포인트": ["핵심 포인트1", "핵심 포인트2"]
        }}
    ]
    [분석할 문서들]
    {prompt_text}
    """
    
    current_usage = increment_api_usage()
    current_model = 'gemini-2.5-flash'
    max_retries = 3
    
    for attempt in range(max_retries):
        try:
            start_time = time.time()
            response = client_ai.models.generate_content(model=current_model, contents=prompt)
            elapsed = time.time() - start_time
            print(f"      ✅ AI 응답 성공 ({elapsed:.1f}초) 📊 [누적 요청: {current_usage}회]")
            res_text = response.text.strip().replace("```json", "").replace("```", "")
            return json.loads(res_text)
        except Exception as e:
            error_msg = str(e)
            if "503" in error_msg:
                if attempt < max_retries - 1:
                    print(f"      ⚠️ 서버 과부하(503). 3초 후 재시도합니다... (시도 {attempt+1}/{max_retries})")
                    time.sleep(3)
                    continue
                else:
                    print(f"      ❌ 503 에러 3회 연속 발생. 패자부활전으로 넘깁니다.")
                    return None
            elif "404" in error_msg:
                print(f"      🚨 [치명적 에러] 모델을 찾을 수 없습니다(404).")
                return "FATAL_404"
            elif "429" in error_msg:
                print(f"      🚨 [한도 초과] 429 에러 발생. 📊 [현재 누적 요청: {current_usage}회]")
                return "FATAL_429"
                
            print(f"      ⚠️ AI 처리 실패. 즉시 패자부활전으로 넘깁니다. (사유: {error_msg[:50]})")
            return None

# 💡 4-0. 벌크 조회에 주가가 없을 때 fdr 개별 조회 fallback (캐시로 중복 호출 방지)
_price_cache = {}

def get_price_fallback(code):
    """벌크 목록에 Close가 없을 때 fdr로 종목별 현재가를 개별 조회"""
    if code in _price_cache:
        return _price_cache[code]
    try:
        end = datetime.today().strftime('%Y-%m-%d')
        start = (datetime.today() - timedelta(days=10)).strftime('%Y-%m-%d')
        df = fdr.DataReader(code, start, end)
        if not df.empty and 'Close' in df.columns:
            price = float(df['Close'].iloc[-1])
            _price_cache[code] = price
            return price
    except Exception:
        pass
    _price_cache[code] = None
    return None


# 💡 4. JSON 파일 누적 저장 & 중복 제거 (종목명+제목)
def save_and_match_to_json(analyzed_data, df_listing, file_name, report_type_name, analysis_time):
    # [1] 기존 데이터 로드
    existing_results = []
    if os.path.exists(file_name):
        try:
            with open(file_name, "r", encoding="utf-8") as f:
                old_data = json.load(f)
                existing_results = old_data.get("results", [])
        except Exception as e:
            print(f"      ⚠️ 기존 파일 로드 에러 (무시하고 새로 생성): {e}")

    # [2] 💡 핵심 수정: 중복 판별용 딕셔너리 (증권사 제외, 종목명+제목만 사용)
    unique_results = {}
    for item in existing_results:
        raw_name = item.get('종목명') or ''
        clean_name = str(raw_name).split('(')[0].strip()
        
        raw_title = item.get('레포트 제목') or '제목없음'
        # 띄어쓰기가 달라서 중복 처리되는 것을 막기 위해 모든 공백 제거
        title_nospace = str(raw_title).replace(" ", "").strip()
        
        # 증권사를 키에서 완전히 배제합니다.
        dup_key = f"{clean_name}_{title_nospace}"
        unique_results[dup_key] = item

    # [3] 신규 데이터 저장 (KIND 매칭 여부와 무관하게 전체 저장)
    new_matched_count = 0
    for item in analyzed_data:
        raw_name = item.get('종목명') or ''
        clean_name = str(raw_name).split('(')[0].strip()

        raw_title = item.get('레포트 제목') or '제목없음'
        title_nospace = str(raw_title).replace(" ", "").strip()
        dup_key = f"{clean_name}_{title_nospace}"

        # --- 현재가 결정: ① 벌크 목록/fdr 개별 조회 → ② AI 추출값 순서 ---
        curr_price = None
        curr_marcap = 0

        matched = df_listing[df_listing['Name'] == clean_name]
        if not matched.empty:
            try:
                v = matched.iloc[0].get('Close')
                curr_price = float(v) if v is not None and not pd.isna(v) else None
            except (TypeError, ValueError):
                curr_price = None
            try:
                v = matched.iloc[0].get('Marcap')
                curr_marcap = float(v) if v is not None and not pd.isna(v) else 0
            except (TypeError, ValueError):
                curr_marcap = 0

            # 벌크 목록에 주가가 비어 있으면 fdr 개별 조회로 보완
            if curr_price is None:
                code = str(matched.iloc[0].get('Code', ''))
                if code:
                    curr_price = get_price_fallback(code)
                    if curr_price:
                        print(f"      📈 fdr fallback: {clean_name}({code}) = {curr_price:,.0f}원")

        # 벌크/개별 조회 모두 실패 시 AI가 레포트에서 추출한 현재주가 사용
        if curr_price is None:
            ai_price_raw = item.get("현재주가")
            if ai_price_raw:
                try:
                    digits = ''.join(filter(str.isdigit, str(ai_price_raw)))
                    curr_price = float(digits) if digits else None
                except (TypeError, ValueError):
                    curr_price = None

        # --- 목표주가 파싱 ---
        target_price_str = item.get("목표주가", "0")
        try:
            target_price = int(''.join(filter(str.isdigit, str(target_price_str))))
        except (TypeError, ValueError):
            target_price = 0

        # --- Upside 결정: ① AI 명시값 → ② 현재가·목표주가로 계산 ---
        ai_upside_raw = item.get("상승여력")
        if ai_upside_raw is not None:
            try:
                item['Upside'] = round(float(str(ai_upside_raw).replace('%', '').strip()), 1)
            except (TypeError, ValueError):
                pass
        elif curr_price and curr_price > 0 and target_price > 0:
            item['Upside'] = round((target_price / curr_price - 1) * 100, 1)

        # --- 표시용 필드 포맷 ---
        if curr_price:
            item['현재가'] = f"{int(curr_price):,}원"
            item['현재시총'] = f"{int(curr_marcap // 100_000_000):,}억" if curr_marcap else 'N/A'
        if target_price > 0:
            item['목표주가'] = f"{target_price:,}원"
            if curr_marcap and item.get('Upside') is not None:
                item['목표시총'] = f"{int(curr_marcap * (1 + item['Upside'] / 100) // 100_000_000):,}억"

        # AI에서 추출한 임시 필드 정리
        item.pop('현재주가', None)
        item.pop('상승여력', None)

        # --- 버틀러 우선순위 덮어쓰기 로직 ---
        is_butler = (item.get('source') == 'butler_works')
        if dup_key in unique_results:
            if is_butler and unique_results[dup_key].get('source_type') != 'butler_works':
                item['source_type'] = 'butler_works'
                unique_results[dup_key] = item
        else:
            item['source_type'] = 'butler_works' if is_butler else 'pdf'
            unique_results[dup_key] = item
            new_matched_count += 1
            
    # [4] 최종 결과 정리 및 저장
    final_list = []
    for val in unique_results.values():
        val.pop('doc_id', None)
        val.pop('source', None)
        final_list.append(val)
    
    report = {
        "analysis_time": analysis_time,
        "report_type": report_type_name,
        "results": final_list
    }
    
    with open(file_name, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=4, ensure_ascii=False)
        
    print(f"💾 [{report_type_name}] 누적 업데이트 완료! (기존 {len(existing_results)}건 + 신규 {new_matched_count}건 = 총 {len(final_list)}건)")

# 💡 5. 메인 루프 (시간 Fix 기준 적용)
async def main():
    if not TARGET_CHANNELS_TEXT and not TARGET_CHANNELS_PDF:
        raise RuntimeError(
            "REPORT_CHANNELS_TEXT / REPORT_CHANNELS_PDF 환경변수가 둘 다 비어 있습니다. "
            "GitHub Actions Secrets에 쉼표로 구분한 채널 목록을 등록하세요."
        )

    today_usage = get_today_api_usage()
    print("=== 증권사 레포트 배치 시작 (구간 픽스 & 누적 업데이트 모드) ===")
    print(f"📊 [현재 상태] 오늘 {today_usage}회의 API를 이미 사용했습니다.")

    now = datetime.utcnow() + timedelta(hours=9)
    hour = now.hour
    today_str = now.strftime("%Y%m%d")
    
    # 오늘의 07시와 20시 기준 객체 생성
    today_07 = now.replace(hour=7, minute=0, second=0, microsecond=0)
    today_20 = now.replace(hour=20, minute=0, second=0, microsecond=0)
    
    # 💡 [정밀 구간 픽스] 
    if 8 < hour <= 21: 
        # 낮/저녁에 도는 정규 레포트 (당일 07:00 ~ 당일 20:00)
        report_type_name = "Regular Report"
        file_name = f"data/broker_report/regular_report_{today_str}.json"
        fetch_start = today_07
        fetch_end = today_20
    else: 
        # 아침/밤에 도는 전일 레포트
        report_type_name = "Previous Day Report"
        file_name = f"data/broker_report/previous_day_report_{today_str}.json"
        
        if hour <= 8:
            # 아침에 도는 경우 (어제 20:00 ~ 오늘 07:00)
            yesterday_20 = today_20 - timedelta(days=1)
            fetch_start = yesterday_20
            fetch_end = today_07
        else:
            # 밤(22, 23시)에 도는 경우 (오늘 20:00 ~ 내일 07:00)
            tomorrow_07 = today_07 + timedelta(days=1)
            fetch_start = today_20
            fetch_end = tomorrow_07
            
    analysis_time = now.strftime("%Y-%m-%d %H:%M")
    os.makedirs('data/broker_report', exist_ok=True)
    
    client_tg = TelegramClient(StringSession(SESSION_STR), API_ID, API_HASH)
    await client_tg.start()
    
    docs_to_process = await get_all_reports_from_telegram(client_tg, fetch_start, fetch_end)
    await client_tg.disconnect()
    
    if not docs_to_process:
        print("조건에 맞는 새로운 레포트/텍스트가 없습니다.")
        return

    doc_source_map = {str(d['id']): d['source'] for d in docs_to_process}

    print(f"\n🔍 총 {len(docs_to_process)}개의 문서를 분석합니다.")
    print("📌 KRX 종목 데이터 수집 중...")

    # 💡 예전엔 종목명은 KIND, 주가는 pykrx로 따로 받아 병합했다.
    # 그런데 pykrx는 이제 KRX_ID/KRX_PW 환경변수(KRX 계정)를 요구해서
    # 자격증명 없이는 get_market_ohlcv_by_ticker가 항상 실패한다 —
    # 즉 이 배치는 계속 "주가 수집 실패"로 떨어져 종목마다 fdr 개별 조회
    # (get_price_fallback)를 하거나 AI가 레포트에서 읽은 값에 의존하고 있었다.
    #
    # fdr.StockListing('KRX') 한 번이면 Code/Name/Close/Marcap이 전부 나오므로
    # 그걸 1차로 쓰고, 실패 시 krx_listing의 폴백 체인으로 종목명만이라도
    # 확보한다(주가는 기존 get_price_fallback이 종목별로 메운다).
    #
    # 💡 fdr.StockListing을 직접 부르면 서드파티 marcap 캐시가 404일 때
    # (2026-09월 실제로 며칠 그랬다) 주가를 통째로 잃고 종목별 개별 조회라는
    # 느린 경로로 떨어진다. fetch_krx_marcap()은 그 캐시를 날짜를 되짚어
    # 직접 읽으므로 대부분의 404 구간을 그냥 넘긴다. 둘 다 실패하면
    # RuntimeError가 나고 아래 except가 기존 폴백으로 받는다.
    df_listing = None
    try:
        df_all = fetch_krx_marcap()
        cols = [c for c in ['Code', 'Name', 'Close', 'Marcap'] if c in df_all.columns]
        if 'Code' in cols and 'Name' in cols and not df_all.empty:
            df_listing = df_all[cols].copy()
            df_listing['Code'] = df_listing['Code'].astype(str).str.zfill(6)
            for c in ('Close', 'Marcap'):
                if c not in df_listing.columns:
                    df_listing[c] = None
            print(f"  ✅ fdr 종목/주가 수집 완료 ({len(df_listing)}개)")
    except Exception as e:
        print(f"  ⚠️ fdr.StockListing 실패: {type(e).__name__}: {e}")

    if df_listing is None:
        print("  📌 폴백: 종목명만 확보하고 주가는 종목별 개별 조회로 채웁니다.")
        df_listing = fetch_krx_listing()[['Code', 'Name']].copy()
        df_listing['Close'] = None
        df_listing['Marcap'] = None
        print(f"  ✅ 폴백 종목명 수집 완료 ({len(df_listing)}개)")

    chunk_size = 7
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
                failed_queue.extend(chunk)
                
            else:
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
                        
            print("      ⏳ 다음 문서를 위해 2초 대기합니다...") 
            time.sleep(2)
            
        # 매 패스(회전)가 끝날 때마다 성공한 데이터들을 누적 저장합니다.
        if all_analyzed_data:
            save_and_match_to_json(all_analyzed_data, df_listing, file_name, report_type_name, analysis_time)
            # 저장 후에는 다음 회전에서 중복 저장되지 않도록 비워줍니다.
            all_analyzed_data = [] 
            
        current_queue = failed_queue
        
        if current_queue and pass_num < MAX_PASSES:
            print(f"\n⏳ {pass_num}회전 종료. 누락된 {len(current_queue)}개 문서 재도전을 위해 10초 대기합니다...")
            time.sleep(10)
            
    if current_queue:
        print(f"\n💀 [최종 종료] 마지막 3차 패자부활전까지 시도했으나 {len(current_queue)}개 문서는 끝내 분석하지 못했습니다.")
    
    print(f"\n✅ 최종 배치 프로세스 완전 종료!")

if __name__ == "__main__":
    asyncio.run(main())
