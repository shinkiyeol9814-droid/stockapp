import os
import json
import asyncio
import re
from datetime import datetime, timedelta
from telethon import TelegramClient
from telethon.sessions import StringSession

API_ID = int(os.environ.get("TELEGRAM_API_ID", 0))
API_HASH = os.environ.get("TELEGRAM_API_HASH", "")
SESSION_STR = os.environ.get("TELEGRAM_SESSION", "")
TARGET_CHANNEL = "https://t.me/darthacking" 
DATA_FILE = "data/earnings/earnings_data.json"

# 💡 [신규] 증감률 계산 헬퍼 함수
def calc_growth(cur_val, prev_val):
    try:
        cur = int(cur_val.replace(',', ''))
        prev = int(prev_val.replace(',', ''))
        if prev > 0 and cur > 0:
            val = ((cur / prev) - 1) * 100
            return f"+{val:.1f}%" if val > 0 else f"{val:.1f}%"
        elif prev < 0 and cur > 0: return "흑전"
        elif prev > 0 and cur < 0: return "적전"
        elif prev <= 0 and cur <= 0: return "적지"
        return "-"
    except:
        return "-"

def parse_earnings_text(text):
    if "기업명:" not in text or "영업익" not in text:
        return None
        
    data = {}
    try:
        time_match = re.search(r'(\d{4}\.\d{2}\.\d{2} \d{2}:\d{2}:\d{2})', text)
        data['발표시간'] = time_match.group(1) if time_match else datetime.now().strftime('%Y.%m.%d %H:%M:%S')
        
        corp_match = re.search(r'기업명:\s*([^\(]+).*?([A-Z0-9]{6})', text)
        if corp_match:
            data['종목명'] = corp_match.group(1).strip()
            data['코드'] = corp_match.group(2).strip()
        else:
            return None
            
        report_match = re.search(r'보고서명:\s*(.+)', text)
        data['보고서명'] = report_match.group(1).strip() if report_match else ""
        data['잠정여부'] = "잠정공시" if "잠정" in data['보고서명'] else "확정공시"
        
        quarter_match = re.search(r'\*\*최근 실적 추이\*\*\s*(\d{4}\.\d[Qq])', text)
        if quarter_match:
            data['해당분기'] = quarter_match.group(1).upper() 
        else:
            data['해당분기'] = "분기미상"
        
        rev_match = re.search(r'매출액\s*:\s*([-+]?[\d,]+)억\s*(?:\(예상치\s*:\s*([-+]?[\d,]+)[^\/]*\/\s*([+-]?\s*\d+)%\))?', text)
        if rev_match:
            data['매출액'] = rev_match.group(1)
            data['매출괴리율'] = rev_match.group(3).replace(' ', '') if rev_match.group(3) else ""
        else:
            data['매출액'] = "-"
            data['매출괴리율'] = ""
        
        op_match = re.search(r'영업익\s*:\s*([-+]?[\d,]+)억\s*(?:\(예상치\s*:\s*([-+]?[\d,]+)[^\/]*\/\s*([+-]?\s*\d+)%\))?', text)
        if op_match:
            data['영업익'] = op_match.group(1)
            data['예상영업익'] = op_match.group(2) if op_match.group(2) else ""
            raw_gap = op_match.group(3)
            data['괴리율'] = raw_gap.replace(' ', '') if raw_gap else ""
            
            if data['예상영업익'] and data['괴리율']:
                try:
                    diff = int(data['괴리율'])
                    if diff >= 10: data['서프_상태'] = "🔥 어닝서프라이즈"
                    elif diff > 0: data['서프_상태'] = "🔥 컨센상회"
                    elif diff <= -10: data['서프_상태'] = "❄️ 어닝쇼크"
                    elif diff < 0: data['서프_상태'] = "💧 컨센하회"
                    else: data['서프_상태'] = "✅ 컨센부합"
                except:
                    data['서프_상태'] = "💡 데이터오류"
            else:
                data['서프_상태'] = "💡 컨센없음"
        else:
            data['영업익'] = "-"
            data['서프_상태'] = "N/A"

        # 💡 [신규] 최근 실적 추이 블록을 읽어서 YoY / QoQ 추출
        data['YoY'] = ""
        data['QoQ'] = ""
        history_match = re.search(r'\*\*최근 실적 추이\*\*\s*(.+?)(?:공시링크|$)', text, re.DOTALL)
        if history_match:
            history_text = history_match.group(1)
            # 정규식: (분기) (매출)억/ (영업익)억 추출
            hist_lines = re.findall(r'(\d{4}\.\d[Qq])\s+([-+]?[\d,]+)억\s*/\s*([-+]?[\d,]+)억', history_text)
            
            # hist_lines[0]은 현재분기, [1]은 전분기, [4]는 전년동기
            if len(hist_lines) >= 2:
                data['QoQ'] = calc_growth(hist_lines[0][2], hist_lines[1][2])
            if len(hist_lines) >= 5:
                data['YoY'] = calc_growth(hist_lines[0][2], hist_lines[4][2])

        data['원문'] = text
        return data
    except Exception as e:
        print(f"파싱 에러 발생: {e}")
        return None

async def main():
    print("=== 실적 스크리닝 수집 시작 ===")
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    earnings_dict = {}
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            old_list = json.load(f)
            earnings_dict = {item['코드']: item for item in old_list}

    client = TelegramClient(StringSession(SESSION_STR), API_ID, API_HASH)
    await client.start()
    
    now_kst = datetime.utcnow() + timedelta(hours=9)
    target_time = datetime(now_kst.year, 1, 1) 
    
    new_count = 0
    current_run_seen = set() 
    
    async for message in client.iter_messages(TARGET_CHANNEL, limit=None):
        msg_time_kst = message.date.replace(tzinfo=None) + timedelta(hours=9)
        if msg_time_kst < target_time: break 
        
        if message.text:
            parsed_data = parse_earnings_text(message.text)
            if parsed_data:
                code = parsed_data['코드']
                if code not in current_run_seen:
                    current_run_seen.add(code)
                    earnings_dict[code] = parsed_data
                    new_count += 1
                    print(f"✅ 수집/갱신: {parsed_data['종목명']} ({parsed_data.get('해당분기')}) - {msg_time_kst.strftime('%m/%d')}")
                
    await client.disconnect()
    
    final_list = list(earnings_dict.values())
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(final_list, f, indent=4, ensure_ascii=False)
        
    print(f"=== 수집 종료! (최신 {new_count}건 갱신, 총 {len(final_list)}건 누적) ===")

if __name__ == "__main__":
    asyncio.run(main())
