"""
batch_surge_alert.py — 장중 급등주 감지 & 텔레그램 알림.

네이버 모바일증권의 "상승률 상위" 목록(코스피/코스닥)을 5분 간격으로 폴링해,
등락률이 기준치(SURGE_THRESHOLD) 이상으로 올라선 종목을 그 즉시 텔레그램으로
알린다. "직전 폴링 대비 변화량"을 계산하는 방식은 상위 N개 밖에 있던 종목이
갑자기 뛰어든 경우 비교 기준(직전 값)이 아예 없어서 놓치는 구조적 사각지대가
있다 — 대신 "임계치를 넘겼는데 오늘 아직 알림을 안 보낸 종목"을 잡는 방식으로,
5분 폴링 주기 자체가 "감지 지연"을 자연스럽게 대신하게 한다.

필터/보강:
  - ETF 제외, 거래대금 하한으로 호가만 튄 허수 신호 배제
  - 오늘자 실적 서프라이즈 명단과 종목명 교차 확인 (있으면 근거로 첨부)
  - 업종 평균 등락률과 비교해 테마성/단일종목 여부 코멘트
  - 이번 주기에 새로 잡힌 종목 전체를 메시지 1건으로 묶어 전송
"""
import os
import re
import json
import asyncio
import requests
from datetime import datetime, timedelta, timezone
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.messages import ImportChatInviteRequest
from telethon.errors import UserAlreadyParticipantError

from ui_sector import get_sector_performance

API_ID = int(os.environ.get("TELEGRAM_API_ID", 0))
API_HASH = os.environ.get("TELEGRAM_API_HASH", "")
SESSION_STR = os.environ.get("TELEGRAM_SESSION_BATCH") or os.environ.get("TELEGRAM_SESSION", "")

TARGET_CHAT_LINK = "https://t.me/+YuE4e3XDKbpjNWY1"
TARGET_INVITE_HASH = "YuE4e3XDKbpjNWY1"

DATA_DIR = "data/surge_alert"
ALERTED_FILE = f"{DATA_DIR}/alerted_today.json"
EARNINGS_FILE = "data/earnings/earnings_data.json"

SURGE_THRESHOLD = 7.0         # 등락률(%) 이 기준 이상이면 알림
MIN_TRADING_VALUE = 500_000_000  # 거래대금(원) 이 이 밑이면 호가만 튄 허수로 보고 제외 (5억원)
THEME_THRESHOLD = 2.0         # 업종 평균 등락률이 이 이상이면 "테마성"으로 판단
TOP_N = 100                   # 네이버 API의 pageSize 상한이 100이라 마켓별 최대 100개까지 스캔
KST = timezone(timedelta(hours=9))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}


def fetch_top_gainers(market: str):
    """market: 'KOSPI' 또는 'KOSDAQ'. {코드: {...}} 딕셔너리와 marketStatus 반환."""
    try:
        url = f"https://m.stock.naver.com/api/stocks/up/{market}?page=1&pageSize={TOP_N}"
        res = requests.get(url, headers=HEADERS, timeout=10)
        data = res.json()
        out = {}
        for s in data.get("stocks", []):
            code = s.get("itemCode")
            ratio_raw = s.get("fluctuationsRatio")
            if not code or ratio_raw is None:
                continue
            try:
                ratio = float(ratio_raw)
                value_raw = float(s.get("accumulatedTradingValueRaw") or 0)
            except (TypeError, ValueError):
                continue
            out[code] = {
                "name": s.get("stockName"),
                "ratio": ratio,
                "price": s.get("closePriceRaw"),
                "is_etf": s.get("stockEndType") == "etf",
                "trading_value": value_raw,
            }
        return out, data.get("marketStatus")
    except Exception as e:
        print(f"⚠️ {market} 조회 실패: {e}")
        return {}, None


def get_stock_sector(code: str):
    """종목코드로 소속 업종코드/업종명을 알아낸다. 실패하면 (None, None)."""
    try:
        url = f"https://finance.naver.com/item/main.naver?code={code}"
        res = requests.get(url, headers=HEADERS, timeout=7)
        # 💡 sise_group.naver 계열은 EUC-KR인데 이 페이지(item/main.naver)는
        # UTF-8이다 — 다른 곳과 똑같이 euc-kr로 강제하면 업종명이 깨진다.
        res.encoding = "utf-8"
        m = re.search(r'sise_group_detail\.naver\?type=upjong&no=(\d+)">([^<]+)<', res.text)
        if m:
            return m.group(1), m.group(2)
    except Exception:
        pass
    return None, None


def get_today_earnings_map():
    """오늘자 실적 발표 종목명 → 서프라이즈 정보 딕셔너리."""
    today_str = datetime.now(KST).strftime("%Y.%m.%d")
    out = {}
    try:
        with open(EARNINGS_FILE, "r", encoding="utf-8") as f:
            rows = json.load(f)
        for row in rows:
            if not str(row.get("발표시간", "")).startswith(today_str):
                continue
            name_key = str(row.get("종목명", "")).replace(" ", "")
            if name_key:
                out[name_key] = row
    except Exception as e:
        print(f"⚠️ 실적 데이터 로드 실패: {e}")
    return out


def build_theme_comment(code: str, sector_df):
    """업종 평균 등락률과 비교해 테마성/단일종목 코멘트를 만든다."""
    sector_no, sector_name = get_stock_sector(code)
    if not sector_no or sector_df is None or sector_df.empty:
        return "🏭 업종 확인 불가"
    row = sector_df[sector_df["업종코드"] == sector_no]
    if row.empty:
        return "🏭 업종 확인 불가"
    sector_ratio = float(row.iloc[0]["등락률_num"])
    if sector_ratio >= THEME_THRESHOLD:
        return f"🔥 테마성 (「{sector_name}」 업종 전체 평균 {sector_ratio:+.2f}% 동반 상승)"
    return f"🎯 단일 종목 특이 움직임 (「{sector_name}」 업종 평균은 {sector_ratio:+.2f}%로 잠잠함)"


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


TELEGRAM_MSG_LIMIT = 3500  # 텔레그램 4096자 제한에 여유를 두고 청크 분할 기준


def chunk_message(header: str, blocks: list, limit: int = TELEGRAM_MSG_LIMIT):
    """급등 종목이 많아 한 메시지(4096자 제한)를 넘기면 여러 메시지로 나눈다."""
    chunks = []
    current = header
    for block in blocks:
        candidate = current + "\n\n" + block
        if len(candidate) > limit and current != header:
            chunks.append(current)
            current = header + " (이어서)\n\n" + block
        else:
            current = candidate
    chunks.append(current)
    return chunks


async def send_telegram_messages(texts):
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

        for text in texts:
            await client.send_message(entity, text, parse_mode="markdown", link_preview=False)
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
    candidates = []
    for market in ["KOSPI", "KOSDAQ"]:
        gainers, status = fetch_top_gainers(market)
        if status == "OPEN":
            any_open = True
        for code, info in gainers.items():
            if code in alerted["codes"] or info["is_etf"]:
                continue
            if info["ratio"] < SURGE_THRESHOLD:
                continue
            if info["trading_value"] < MIN_TRADING_VALUE:
                continue  # 거래대금이 너무 적음 — 호가만 튄 허수 신호로 판단해 제외
            candidates.append((code, info))
            alerted["codes"].append(code)

    if not any_open:
        print("🛑 장 운영 시간이 아니라 스킵합니다 (marketStatus != OPEN).")
        return

    if candidates:
        print(f"🚨 신규 급등 감지: {len(candidates)}건 — 근거 보강 후 메시지 1건으로 전송")
        earnings_map = get_today_earnings_map()
        sector_df = get_sector_performance()

        blocks = []
        for code, info in candidates:
            trading_eok = info["trading_value"] / 100_000_000  # 억원 단위
            block = (
                f"🚀 *{info['name']}* ({code})\n"
                f"등락률 {info['ratio']:+.2f}% | 현재가 {info['price']}원 | 거래대금 {trading_eok:.1f}억"
            )

            name_key = str(info["name"] or "").replace(" ", "")
            earn = earnings_map.get(name_key)
            if earn:
                block += f"\n📰 오늘 실적: {earn.get('서프_상태','')} (영업익 {earn.get('영업익','-')}억, 괴리율 {earn.get('괴리율','') or 'N/A'})"

            block += f"\n{build_theme_comment(code, sector_df)}"

            news_q = requests.utils.quote(info["name"] or "")
            block += f"\n[관련 뉴스 보기](https://news.google.com/search?q={news_q}&hl=ko&gl=KR)"
            blocks.append(block)

        header = f"📢 *급등주 감지* ({now_kst.strftime('%H:%M')} 기준, {len(candidates)}건)"
        messages = chunk_message(header, blocks)
        print(f"   메시지 {len(messages)}건으로 분할 전송" if len(messages) > 1 else "")
        asyncio.run(send_telegram_messages(messages))
    else:
        print("신규 급등 없음.")

    save_json(ALERTED_FILE, alerted)
    print("=== 완료 ===")


if __name__ == "__main__":
    main()
