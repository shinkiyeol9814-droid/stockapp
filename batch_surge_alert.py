"""
batch_surge_alert.py — 장중 급등주 감지 & 텔레그램 알림.

네이버 모바일증권의 "상승률 상위" 목록(코스피/코스닥)을 5분 간격으로 폴링해,
등락률이 기준치(SURGE_THRESHOLD) 이상으로 올라선 종목을 그 즉시 텔레그램으로
알린다. "직전 폴링 대비 변화량"을 계산하는 방식은 상위 N개 밖에 있던 종목이
갑자기 뛰어든 경우 비교 기준(직전 값)이 아예 없어서 놓치는 구조적 사각지대가
있다 — 대신 "임계치를 넘겼는데 오늘 아직 알림을 안 보낸 종목"을 잡는 방식으로,
5분 폴링 주기 자체가 "감지 지연"을 자연스럽게 대신하게 한다.
"""
import os
import json
import asyncio
import requests
from datetime import datetime, timedelta, timezone
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.messages import ImportChatInviteRequest
from telethon.errors import UserAlreadyParticipantError

API_ID = int(os.environ.get("TELEGRAM_API_ID", 0))
API_HASH = os.environ.get("TELEGRAM_API_HASH", "")
SESSION_STR = os.environ.get("TELEGRAM_SESSION_BATCH") or os.environ.get("TELEGRAM_SESSION", "")

TARGET_CHAT_LINK = "https://t.me/+YuE4e3XDKbpjNWY1"
TARGET_INVITE_HASH = "YuE4e3XDKbpjNWY1"

DATA_DIR = "data/surge_alert"
ALERTED_FILE = f"{DATA_DIR}/alerted_today.json"

SURGE_THRESHOLD = 5.0   # 등락률(%) 이 기준 이상이면 알림
TOP_N = 100             # 네이버 API의 pageSize 상한이 100이라 마켓별 최대 100개까지 스캔
KST = timezone(timedelta(hours=9))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}


def fetch_top_gainers(market: str):
    """market: 'KOSPI' 또는 'KOSDAQ'. {코드: {name, ratio, price}} 딕셔너리와 marketStatus 반환."""
    try:
        url = f"https://m.stock.naver.com/api/stocks/up/{market}?page=1&pageSize={TOP_N}"
        res = requests.get(url, headers=HEADERS, timeout=10)
        data = res.json()
        out = {}
        for s in data.get("stocks", []):
            code = s.get("itemCode")
            name = s.get("stockName")
            ratio_raw = s.get("fluctuationsRatio")
            price_raw = s.get("closePriceRaw")
            if not code or ratio_raw is None:
                continue
            try:
                ratio = float(ratio_raw)
            except (TypeError, ValueError):
                continue
            out[code] = {"name": name, "ratio": ratio, "price": price_raw}
        return out, data.get("marketStatus")
    except Exception as e:
        print(f"⚠️ {market} 조회 실패: {e}")
        return {}, None


def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default
    return default


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


async def send_telegram_alerts(messages):
    client = TelegramClient(StringSession(SESSION_STR), API_ID, API_HASH, connection_retries=5, timeout=20)
    await client.start()
    try:
        entity = None
        try:
            updates = await client(ImportChatInviteRequest(TARGET_INVITE_HASH))
            entity = updates.chats[0] if updates.chats else None
        except UserAlreadyParticipantError:
            entity = await client.get_entity(TARGET_CHAT_LINK)

        if entity is None:
            entity = await client.get_entity(TARGET_CHAT_LINK)

        for msg in messages:
            await client.send_message(entity, msg, parse_mode="markdown", link_preview=False)
    finally:
        await client.disconnect()


def main():
    now_kst = datetime.now(KST)
    today_str = now_kst.strftime("%Y-%m-%d")
    print(f"=== 급등주 감지 시작 ({now_kst.strftime('%Y-%m-%d %H:%M:%S')} KST) ===")

    alerted = load_json(ALERTED_FILE, {"date": today_str, "codes": []})
    if alerted.get("date") != today_str:
        alerted = {"date": today_str, "codes": []}

    any_open = False
    alerts = []
    for market in ["KOSPI", "KOSDAQ"]:
        gainers, status = fetch_top_gainers(market)
        if status == "OPEN":
            any_open = True
        for code, info in gainers.items():
            if code in alerted["codes"]:
                continue
            if info["ratio"] >= SURGE_THRESHOLD:
                news_q = requests.utils.quote(info["name"])
                alerts.append(
                    f"🚀 *{info['name']}* ({code})\n"
                    f"현재 등락률 {info['ratio']:+.2f}% | 현재가 {info['price']}원\n"
                    f"[관련 뉴스 보기](https://news.google.com/search?q={news_q}&hl=ko&gl=KR)"
                )
                alerted["codes"].append(code)

    if not any_open:
        print("🛑 장 운영 시간이 아니라 스킵합니다 (marketStatus != OPEN).")
        return

    if alerts:
        print(f"🚨 신규 급등 감지: {len(alerts)}건 — 텔레그램 전송")
        asyncio.run(send_telegram_alerts(alerts))
    else:
        print("신규 급등 없음.")

    save_json(ALERTED_FILE, alerted)
    print("=== 완료 ===")


if __name__ == "__main__":
    main()
