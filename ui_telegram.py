import streamlit as st
import asyncio
import os
from datetime import timedelta
from telethon import TelegramClient
from telethon.sessions import StringSession

def _get_config():
    try:
        api_id = st.secrets.get("TELEGRAM_API_ID", "") or os.environ.get("TELEGRAM_API_ID", "")
        api_hash = st.secrets.get("TELEGRAM_API_HASH", "") or os.environ.get("TELEGRAM_API_HASH", "")
        session = st.secrets.get("TELEGRAM_SESSION", "") or os.environ.get("TELEGRAM_SESSION", "")
        return int(api_id) if api_id else 0, api_hash, session
    except Exception:
        return 0, "", ""

def _run(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()

async def _fetch_dialogs(api_id, api_hash, session_str):
    client = TelegramClient(StringSession(session_str), api_id, api_hash)
    await client.start()
    dialogs = []
    try:
        async for d in client.iter_dialogs():
            if not (d.is_channel or d.is_group):
                continue
            username = getattr(d.entity, 'username', None)
            dialogs.append({
                "id": d.id,
                "name": d.name or f"채널 {d.id}",
                "unread": d.unread_count,
                "entity_key": username if username else str(d.id),
            })
    finally:
        await client.disconnect()
    return dialogs

async def _fetch_messages(api_id, api_hash, session_str, entity_key, limit):
    client = TelegramClient(StringSession(session_str), api_id, api_hash)
    await client.start()
    msgs = []
    try:
        try:
            entity = int(entity_key)
        except ValueError:
            entity = entity_key
        async for m in client.iter_messages(entity, limit=limit):
            kst = m.date.replace(tzinfo=None) + timedelta(hours=9)
            item = {
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
    except Exception:
        pass
    finally:
        await client.disconnect()
    return msgs

@st.cache_data(ttl=300, show_spinner=False)
def load_dialogs():
    api_id, api_hash, session = _get_config()
    if not api_id or not session:
        return None
    try:
        return _run(_fetch_dialogs(api_id, api_hash, session))
    except Exception:
        return []

@st.cache_data(ttl=180, show_spinner=False)
def load_messages(entity_key, limit):
    api_id, api_hash, session = _get_config()
    if not api_id or not session:
        return []
    try:
        return _run(_fetch_messages(api_id, api_hash, session, entity_key, limit))
    except Exception:
        return []

def _render_msg(msg):
    time_str = msg["time_str"]
    text = msg["text"]
    doc_name = msg["doc_name"]

    if doc_name:
        icon = "📄" if doc_name.lower().endswith(".pdf") else "📎"
        caption = (f"<div style='font-size:12px;color:#666;margin-top:4px;'>"
                   f"{text[:120].replace(chr(10),' ')}</div>") if text else ""
        st.markdown(f"""
        <div style='border:1px solid #e0e0e0;background:#fff;border-radius:8px;
                    padding:10px 14px;margin-bottom:6px;'>
            <div style='font-size:11px;color:#999;margin-bottom:3px;'>🕐 {time_str}</div>
            <div style='font-size:13px;font-weight:600;color:#0088cc;'>{icon} {doc_name}</div>
            {caption}
        </div>
        """, unsafe_allow_html=True)

    elif text:
        full_html = text.replace('\n', '<br>')
        preview = text[:120].replace('\n', ' ')
        if len(text) > 120:
            st.markdown(f"""
            <details style='border-left:3px solid #0088cc;background:#f8f9fa;
                            border-radius:0 8px 8px 0;padding:10px 14px;margin-bottom:6px;'>
                <summary style='cursor:pointer;list-style:none;outline:none;'>
                    <span style='font-size:11px;color:#999;'>🕐 {time_str}&nbsp;&nbsp;</span>
                    <span style='font-size:13px;color:#333;'>{preview}…</span>
                </summary>
                <div style='margin-top:8px;font-size:13px;color:#222;line-height:1.7;'>{full_html}</div>
            </details>
            """, unsafe_allow_html=True)
        else:
            st.markdown(f"""
            <div style='border-left:3px solid #0088cc;background:#f8f9fa;
                        border-radius:0 8px 8px 0;padding:10px 14px;margin-bottom:6px;'>
                <div style='font-size:11px;color:#999;margin-bottom:3px;'>🕐 {time_str}</div>
                <div style='font-size:13px;color:#222;line-height:1.7;'>{full_html}</div>
            </div>
            """, unsafe_allow_html=True)

def render_telegram_viewer():
    api_id, _, session = _get_config()
    if not api_id or not session:
        st.error("Streamlit Secrets에 TELEGRAM_API_ID / TELEGRAM_API_HASH / TELEGRAM_SESSION을 추가해주세요.")
        return

    st.markdown("""
    <style>
    /* 채널 목록 버튼 — flat 리스트 아이템 스타일 */
    div[data-testid="column"]:first-child div[data-testid="stButton"] > button[kind="secondary"] {
        background: transparent !important;
        border: none !important;
        box-shadow: none !important;
        text-align: left !important;
        justify-content: flex-start !important;
        color: #262730 !important;
        padding: 6px 10px !important;
        font-size: 13px !important;
        border-radius: 6px !important;
        font-weight: normal !important;
    }
    div[data-testid="column"]:first-child div[data-testid="stButton"] > button[kind="secondary"]:hover {
        background: #f0f2f6 !important;
    }
    </style>
    """, unsafe_allow_html=True)

    if 'tg_selected' not in st.session_state:
        st.session_state.tg_selected = 0

    col_left, col_right = st.columns([2, 5], gap="medium")

    # ── 왼쪽: 채널 목록 ──────────────────────────────────
    with col_left:
        c_title, c_rfr = st.columns([3, 1])
        with c_title:
            st.markdown("<div style='font-weight:bold;font-size:15px;padding:4px 0;'>📱 채널</div>",
                        unsafe_allow_html=True)
        with c_rfr:
            if st.button("🔄", key="tg_ch_rfr", help="채널 목록 새로고침"):
                load_dialogs.clear()
                load_messages.clear()
                st.rerun()

        st.markdown("<hr style='margin:4px 0 6px;'>", unsafe_allow_html=True)

        with st.spinner("채널 목록 로딩..."):
            dialogs = load_dialogs()

        if dialogs is None:
            st.error("설정 오류")
            return
        if not dialogs:
            st.info("채널 없음")
            return

        tg_sel = min(st.session_state.tg_selected, len(dialogs) - 1)

        for i, d in enumerate(dialogs):
            label = f"🔵 {d['name']}" if d["unread"] > 0 else d["name"]
            if i == tg_sel:
                st.markdown(f"""
                <div style='background:#dbeafe;border-radius:6px;padding:6px 10px;
                            margin:1px 0;font-size:13px;font-weight:600;color:#1d4ed8;
                            border-left:3px solid #2563eb;'>
                    {label}
                </div>
                """, unsafe_allow_html=True)
            else:
                if st.button(label, key=f"tg_ch_{i}", use_container_width=True):
                    st.session_state.tg_selected = i
                    st.rerun()

    # ── 오른쪽: 메시지 ───────────────────────────────────
    with col_right:
        selected = dialogs[tg_sel]

        c1, c2, c3 = st.columns([5, 1.2, 0.7])
        with c1:
            st.markdown(f"<div style='font-size:16px;font-weight:bold;padding:2px 0;'>"
                        f"💬 {selected['name']}</div>", unsafe_allow_html=True)
        with c2:
            limit = st.selectbox("개수", [30, 50, 100],
                                 label_visibility="collapsed", key="tg_limit")
        with c3:
            if st.button("🔄", key="tg_msg_rfr", use_container_width=True):
                load_messages.clear()
                st.rerun()

        with st.spinner("메시지 로딩..."):
            messages = load_messages(selected["entity_key"], limit)

        st.caption(f"최근 {len(messages)}개 · 3분 자동 갱신")
        st.divider()

        if not messages:
            st.info("메시지가 없습니다.")
        else:
            for msg in messages:
                _render_msg(msg)
