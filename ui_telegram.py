import streamlit as st
import asyncio
from datetime import datetime, timedelta
from telethon import TelegramClient
from telethon.sessions import StringSession

CHANNELS = {
    "📝 버틀러 요약": "https://t.me/butler_works",
    "📄 DOC_POOL": "https://t.me/DOC_POOL",
    "📄 레포트 피규어": "https://t.me/report_figure_by_offset",
    "📄 기업리포트": "https://t.me/companyreport",
    "📄 영타이거": "https://t.me/YoungTiger_stock",
}

def _get_config():
    try:
        return (
            int(st.secrets.get("TELEGRAM_API_ID", 0)),
            st.secrets.get("TELEGRAM_API_HASH", ""),
            st.secrets.get("TELEGRAM_SESSION", ""),
        )
    except Exception:
        return 0, "", ""

async def _fetch(api_id, api_hash, session_str, channel, limit):
    client = TelegramClient(StringSession(session_str), api_id, api_hash)
    await client.start()
    msgs = []
    try:
        async for m in client.iter_messages(channel, limit=limit):
            kst = m.date.replace(tzinfo=None) + timedelta(hours=9)
            item = {
                "id": m.id,
                "time_str": kst.strftime("%m/%d %H:%M"),
                "text": m.text or "",
                "doc_name": "",
            }
            if m.document:
                try:
                    item["doc_name"] = m.document.attributes[0].file_name
                except Exception:
                    item["doc_name"] = f"file_{m.id}"
            if item["text"] or item["doc_name"]:
                msgs.append(item)
    finally:
        await client.disconnect()
    return msgs

@st.cache_data(ttl=180, show_spinner=False)
def load_messages(channel_key, limit):
    api_id, api_hash, session = _get_config()
    if not api_id or not session:
        return None
    channel = CHANNELS[channel_key]
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_fetch(api_id, api_hash, session, channel, limit))
    except Exception as e:
        st.error(f"텔레그램 연결 실패: {e}")
        return []
    finally:
        loop.close()

def render_telegram_viewer():
    col1, col2, col3, col4 = st.columns([4, 2.5, 1.5, 1])
    with col1:
        st.markdown("<div class='main-title'>💬 텔레그램 뷰어</div>", unsafe_allow_html=True)
    with col2:
        channel_key = st.selectbox("채널", list(CHANNELS.keys()), label_visibility="collapsed")
    with col3:
        limit = st.selectbox("개수", [30, 50, 100], label_visibility="collapsed")
    with col4:
        if st.button("🔄 새로고침", use_container_width=True):
            load_messages.clear()
            st.rerun()

    with st.spinner("메시지 불러오는 중..."):
        messages = load_messages(channel_key, limit)

    if messages is None:
        st.error("텔레그램 설정(API_ID/SESSION)이 없습니다. Streamlit Secrets를 확인해주세요.")
        return
    if not messages:
        st.info("메시지가 없습니다.")
        return

    st.caption(f"최근 {len(messages)}개 | 3분마다 자동 갱신")
    st.divider()

    is_butler = "버틀러" in channel_key

    for msg in messages:
        time_str = msg["time_str"]
        text = msg["text"]
        doc_name = msg["doc_name"]

        if is_butler and text:
            # 버틀러 요약 텍스트 — 길면 접을 수 있게
            preview = text[:120].replace('\n', ' ')
            full_html = text.replace('\n', '<br>')
            is_long = len(text) > 120

            if is_long:
                st.markdown(f"""
                <details style='border-left:3px solid #0088cc; background:#f8f9fa;
                                border-radius:0 6px 6px 0; padding:10px 14px; margin-bottom:10px;'>
                    <summary style='cursor:pointer; list-style:none; outline:none;'>
                        <span style='font-size:11px; color:#888;'>🕐 {time_str} &nbsp;</span>
                        <span style='font-size:13px; color:#333;'>{preview}…</span>
                    </summary>
                    <div style='margin-top:10px; font-size:13px; color:#222; line-height:1.7;'>{full_html}</div>
                </details>
                """, unsafe_allow_html=True)
            else:
                st.markdown(f"""
                <div style='border-left:3px solid #0088cc; background:#f8f9fa;
                            border-radius:0 6px 6px 0; padding:10px 14px; margin-bottom:10px;'>
                    <div style='font-size:11px; color:#888; margin-bottom:4px;'>🕐 {time_str}</div>
                    <div style='font-size:13px; color:#222; line-height:1.7;'>{full_html}</div>
                </div>
                """, unsafe_allow_html=True)

        elif doc_name:
            # 문서(PDF 등) 메시지
            icon = "📄" if doc_name.lower().endswith(".pdf") else "📎"
            caption_html = ""
            if text:
                short = text[:100].replace('\n', ' ')
                caption_html = f"<div style='font-size:12px; color:#666; margin-top:4px;'>{short}</div>"

            st.markdown(f"""
            <div style='border:1px solid #e0e0e0; background:#fff;
                        border-radius:6px; padding:10px 14px; margin-bottom:8px;'>
                <div style='font-size:11px; color:#888; margin-bottom:4px;'>🕐 {time_str}</div>
                <div style='font-size:13px; font-weight:600; color:#0088cc;'>{icon} {doc_name}</div>
                {caption_html}
            </div>
            """, unsafe_allow_html=True)

        elif text:
            # 일반 텍스트 메시지
            short_html = text[:200].replace('\n', '<br>')
            st.markdown(f"""
            <div style='border:1px solid #e0e0e0; background:#fff;
                        border-radius:6px; padding:10px 14px; margin-bottom:8px;'>
                <div style='font-size:11px; color:#888; margin-bottom:4px;'>🕐 {time_str}</div>
                <div style='font-size:13px; color:#333; line-height:1.6;'>{short_html}</div>
            </div>
            """, unsafe_allow_html=True)
