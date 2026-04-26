import os
import json
import asyncio
import re
from datetime import datetime, timedelta
from telethon import TelegramClient
from telethon.sessions import StringSession

# 환경 변수 설정 (기존과 동일)
API_ID = int(os.environ.get("TELEGRAM_API_ID", 0))
API_HASH = os.environ.get("TELEGRAM_API_HASH", "")
SESSION_STR = os.environ.get("TELEGRAM_SESSION", "")

# 💡 AWAKE 채널 ID (문자열 링크 또는 숫자 ID 입력)
TARGET_CHANNEL = "https://t.me/darthacking" # 또는 실제 방 링크/ID로 변경하세요

DATA_FILE = "data/earnings/earnings_data.json"

def parse_earnings_text(text):
    # '기업명:' 과 '영업익' 이 포함되지 않은 일반 텍스트는 필터링
    if "기업명:" not in text or "영업익" not in text:
        return None
        
    data = {}
    try:
        # 1. 발표 시간 추출
        time_match = re.search(r'(\d{4}\.\d{2}\.\d{2} \d{2}:\d{2}:\d{2})', text)
        data['발표시간'] = time_match.group(1) if time_match else datetime.now().strftime('%Y.%m.%d %H:%M:%S')
        
        # 2. 기업명 & 종목코드
        corp_match = re.search(r'기업명:\s*([^\(]+).*?([A-Z0-9]{6})', text)
        if corp_match:
            data['종목명'] = corp_match.group(1).strip()
            data['코드'] = corp_match.group(2).strip()
        else:
            return None # 종목 코드가 없으면 패스
            
        # 3. 보고서명 & 잠정공시 여부
        report_match = re.search(r'보고서명:\s*(.+)', text)
        data['보고서명'] = report_match.group(1).strip() if report_match else ""
        data['잠정여부'] = "잠정공시" if "잠정" in data['보고서명'] else "확정공시"
        
        # 4. 매출액
        rev_match = re.search(r'매출액\s*:\s*([\d,]+)억', text)
        data['매출액'] = rev_match.group(1) if rev_match else "-"
        
        # 5. 영업이익 & 예상치 & 괴리율 추출 (핵심 로직)
        # 예: 영업익 : 1,523억(예상치 : 1,694억/ -10%)
        op_match = re.search(r'영업익\s*:\s*([\d,]+)억\s*(?:\(예상치\s*:\s*([\d,]+)억[^\/]*\/\s*([+-]?\d+)%\))?', text)
        
        if op_match:
            data['영업익'] = op_match.group(1)
            data['예상영업익'] = op_match.group(2) if op_match.group(2) else ""
            data['괴리율'] = op_match.group(3) if op_match.group(3) else ""
            
            # 서프라이즈 / 쇼크 판별
            if data['예상영업익'] and data['괴리율']:
                diff = int(data['괴리율'])
                if diff >= 10: data['서프_상태'] = "🔥 어닝서프라이즈"
                elif diff > 0: data['서프_상태'] = "🔥 컨센상회"
                elif diff <= -10: data['서프_상태'] = "❄️ 어닝쇼크"
                elif diff < 0: data['서프_상태'] = "💧 컨센하회"
                else: data['서프_상태'] = "✅ 컨센부합"
            else:
                data['서프_상태'] = "💡 컨센없음 (흑전/적전 등)"
        else:
            data['영업익'] = "-"
            data['서프_상태'] = "N/A"

        data['원문'] = text
        return data
    except Exception as e:
        print(f"파싱 에러 발생: {e}")
        return None

async def main():
    print("=== 실적 스크리닝 수집 시작 (Gemini 안 씀!) ===")
    
    # 기존 데이터 로드 (중복 제거 및 덮어쓰기를 위해 딕셔너리로 관리)
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    earnings_dict = {}
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            old_list = json.load(f)
            # 종목코드를 키(Key)로 사용
            earnings_dict = {item['코드']: item for item in old_list}

    client = TelegramClient(StringSession(SESSION_STR), API_ID, API_HASH)
    await client.start()
    
    # 💡 최근 24시간 메시지 50개만 긁어옵니다.
    target_time = datetime.utcnow() + timedelta(hours=9) - timedelta(days=1)
    new_count = 0
    
    async for message in client.iter_messages(TARGET_CHANNEL, limit=50):
        msg_time_kst = message.date.replace(tzinfo=None) + timedelta(hours=9)
        if msg_time_kst < target_time: break 
        
        if message.text:
            parsed_data = parse_earnings_text(message.text)
            if parsed_data:
                code = parsed_data['코드']
                
                # 💡 중복 제거 로직: 동일 종목이 들어오면 최신(또는 잠정->확정)으로 덮어씁니다.
                # (텔레그램은 최신 메시지부터 긁어오므로, 이미 dict에 넣었다면 그게 가장 최신임)
                if code not in earnings_dict:
                    earnings_dict[code] = parsed_data
                    new_count += 1
                    print(f"✅ 수집: {parsed_data['종목명']} ({parsed_data['서프_상태']})")
                
    await client.disconnect()
    
    # 저장할 때는 리스트로 변환
    final_list = list(earnings_dict.values())
    
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(final_list, f, indent=4, ensure_ascii=False)
        
    print(f"=== 수집 종료! (신규 {new_count}건 추가, 총 {len(final_list)}건 누적) ===")

if __name__ == "__main__":
    asyncio.run(main())
