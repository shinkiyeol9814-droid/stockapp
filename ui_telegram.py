import re
import html
import streamlit as st
import asyncio
import os
from datetime import timedelta
from telethon import TelegramClient, utils
from telethon.sessions import StringSession
from telethon.tl.functions.messages import GetDialogFiltersRequest
from telethon.tl.types import DialogFilter

def _get_config():
    try:
        api_id = st.secrets.get("TELEGRAM_API_ID", "") or os.environ.get("TELEGRAM_API_ID", "")
        api_hash = st.secrets.get("TELEGRAM_API_HASH", "") or os.environ.get("TELEGRAM_API_HASH", "")
        session = st.secrets.get("TELEGRAM_SESSION", "") or os.environ.get("TELEGRAM_SESSION", "")
        # 텔레그램 앱에서 만든 폴더 이름 — 이 폴더에 들어있는 채널만 가져온다.
        # 시크릿으로 오버라이드 가능, 기본값은 "매일".
        folder = st.secrets.get("TELEGRAM_FOLDER", "") or os.environ.get("TELEGRAM_FOLDER", "") or "매일"
        return int(api_id) if api_id else 0, api_hash, session, folder
    except Exception:
        return 0, "", "", "매일"

def _run(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()

async def _get_folder_peer_ids(client, folder_name):
    """텔레그램 앱에 설정된 폴더(예: '매일')에 포함된 대화의 peer id 집합을 반환.
    폴더를 못 찾으면 None (호출부에서 '전체 보기'로 폴백 처리)."""
    try:
        result = await client(GetDialogFiltersRequest())
        filters = getattr(result, 'filters', result)
        for f in filters:
            if not isinstance(f, DialogFilter):
                continue  # DialogFilterDefault(전체) / DialogFilterChatlist(공유폴더)는 제외
            title = getattr(f.title, 'text', f.title)  # 최신 API는 title이 TextWithEntities
            if title == folder_name:
                return {utils.get_peer_id(p) for p in f.include_peers}
    except Exception:
        pass
    return None

async def _fetch_dialogs(api_id, api_hash, session_str, folder_name=None):
    client = TelegramClient(StringSession(session_str), api_id, api_hash)
    await client.start()
    dialogs = []
    folder_found = True
    try:
        allowed_ids = None
        if folder_name:
            allowed_ids = await _get_folder_peer_ids(client, folder_name)
            folder_found = allowed_ids is not None
        async for d in client.iter_dialogs():
            if not (d.is_channel or d.is_group):
                continue
            if allowed_ids is not None and d.id not in allowed_ids:
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
    return dialogs, folder_found

async def _fetch_messages(api_id, api_hash, session_str, entity_key, limit, query=""):
    client = TelegramClient(StringSession(session_str), api_id, api_hash)
    await client.start()
    msgs = []
    try:
        try:
            entity = int(entity_key)
        except ValueError:
            entity = entity_key
        kwargs = {"limit": limit}
        if query:
            kwargs["search"] = query
        async for m in client.iter_messages(entity, **kwargs):
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
    api_id, api_hash, session, folder = _get_config()
    if not api_id or not session:
        return None, True
    try:
        return _run(_fetch_dialogs(api_id, api_hash, session, folder))
    except Exception:
        return [], True

@st.cache_data(ttl=180, show_spinner=False)
def load_messages(entity_key, limit):
    api_id, api_hash, session, _ = _get_config()
    if not api_id or not session:
        return []
    try:
        return _run(_fetch_messages(api_id, api_hash, session, entity_key, limit))
    except Exception:
        return []

@st.cache_data(ttl=60, show_spinner=False)
def search_messages(entity_key, query, limit):
    api_id, api_hash, session, _ = _get_config()
    if not api_id or not session:
        return []
    try:
        return _run(_fetch_messages(api_id, api_hash, session, entity_key, limit, query=query))
    except Exception:
        return []

# 채널 이름 해시로 아바타 색/이모지를 고정 배정 (같은 채널 = 항상 같은 색)
_AVATAR_PALETTE = [
    ("#e17076", "🔴"), ("#7bc862", "🟢"), ("#e5ca77", "🟡"), ("#65aadd", "🔵"),
    ("#a695e7", "🟣"), ("#ee7aae", "🌸"), ("#6ec9cb", "🔷"), ("#faa774", "🟠"),
]

def _avatar_idx(name):
    return sum(ord(c) for c in (name or "?")) % len(_AVATAR_PALETTE)

def _avatar_dot(name):
    """채널 목록용 — 텍스트 라벨 안에 넣을 수 있는 색상 이모지 (st.radio는 HTML을 못 그림)."""
    return _AVATAR_PALETTE[_avatar_idx(name)][1]

def _avatar_html(name, size=36):
    """채팅 헤더/말풍선용 — 진짜 원형 아바타(이니셜)."""
    initial = html.escape((name or "?").strip()[:1].upper())
    color = _AVATAR_PALETTE[_avatar_idx(name)][0]
    return (
        f"<div style='width:{size}px;height:{size}px;min-width:{size}px;border-radius:50%;"
        f"background:{color};display:flex;align-items:center;justify-content:center;"
        f"color:#fff;font-size:{int(size*0.42)}px;font-weight:700;'>{initial}</div>"
    )

_URL_RE = re.compile(r'(https?://[^\s<>"\']+[^\s<>"\'.,!?)\]])')

def _linkify(text):
    """HTML 이스케이프 후 URL만 링크로 변환 (텔레그램 메시지는 신뢰할 수 없는 외부 입력)."""
    escaped = html.escape(text)
    return _URL_RE.sub(
        r'<a href="\1" target="_blank" rel="noopener noreferrer" '
        r'style="color:#0088cc;text-decoration:underline;word-break:break-all;">\1</a>',
        escaped
    )

# 텔레그램 말풍선 느낌 — 왼쪽 아래만 덜 둥글게 해서 '꼬리' 느낌, 시간은 우측 하단
_BUBBLE_OPEN = (
    "background:#fff;border-radius:14px 14px 14px 4px;padding:9px 12px 7px 12px;"
    "margin-bottom:8px;max-width:82%;box-shadow:0 1px 2px rgba(0,0,0,0.07);"
)
_TIME_HTML = "<div style='text-align:right;font-size:11px;color:#9aa0a6;margin-top:3px;'>{t}</div>"

def _render_msg(msg):
    time_str = msg["time_str"]
    text = msg["text"]
    doc_name = html.escape(msg["doc_name"]) if msg["doc_name"] else ""

    if doc_name:
        icon = "📄" if doc_name.lower().endswith(".pdf") else "📎"
        caption_raw = text[:120].replace(chr(10), ' ') if text else ""
        caption = (f"<div style='font-size:13px;color:#555;margin-top:4px;'>"
                   f"{_linkify(caption_raw)}</div>") if caption_raw else ""
        st.markdown(f"""
        <div style='{_BUBBLE_OPEN}'>
            <div style='display:flex;align-items:center;gap:8px;'>
                <div style='font-size:22px;'>{icon}</div>
                <div style='font-size:14px;font-weight:600;color:#0088cc;word-break:break-all;'>{doc_name}</div>
            </div>
            {caption}
            {_TIME_HTML.format(t=time_str)}
        </div>
        """, unsafe_allow_html=True)

    elif text:
        full_html = _linkify(text).replace('\n', '<br>')
        preview = html.escape(text[:120].replace('\n', ' '))
        if len(text) > 120:
            st.markdown(f"""
            <details style='{_BUBBLE_OPEN}'>
                <summary style='cursor:pointer;list-style:none;outline:none;font-size:14px;color:#333;line-height:1.5;'>
                    {preview}…
                    {_TIME_HTML.format(t=time_str)}
                </summary>
                <div style='margin-top:6px;font-size:14px;color:#1a1a1a;line-height:1.6;'>{full_html}</div>
            </details>
            """, unsafe_allow_html=True)
        else:
            st.markdown(f"""
            <div style='{_BUBBLE_OPEN}'>
                <div style='font-size:14px;color:#1a1a1a;line-height:1.5;'>{full_html}</div>
                {_TIME_HTML.format(t=time_str)}
            </div>
            """, unsafe_allow_html=True)

def render_telegram_viewer():
    api_id, _, session, folder_name = _get_config()
    if not api_id or not session:
        st.error("Streamlit Secrets에 TELEGRAM_API_ID / TELEGRAM_API_HASH / TELEGRAM_SESSION을 추가해주세요.")
        return

    st.markdown("""
    <style>
    /* ── Telegram-style 채널 리스트 (radio 기반) ── */
    div[data-testid="stRadio"] > div { gap: 2px !important; }

    /* 라디오 원형 완전히 숨기기 */
    div[data-testid="stRadio"] label > div:first-child { display: none !important; }

    /* 라벨: 전체 너비 리스트 아이템 */
    div[data-testid="stRadio"] label {
        width: 100% !important;
        padding: 9px 10px !important;
        border-radius: 9px !important;
        cursor: pointer !important;
        font-size: 13.5px !important;
        color: #262730 !important;
        margin: 0 !important;
        display: flex !important;
        align-items: center !important;
    }
    div[data-testid="stRadio"] label:hover { background: #eef2f7 !important; }

    /* 텍스트: 왼쪽 정렬 + 말줄임 */
    div[data-testid="stRadio"] label > div:last-child,
    div[data-testid="stRadio"] label > div:last-child p {
        text-align: left !important;
        overflow: hidden !important;
        text-overflow: ellipsis !important;
        white-space: nowrap !important;
        margin: 0 !important;
        width: 100% !important;
    }

    /* 선택된 항목: 텔레그램 브랜드 블루 + 흰 글자 */
    div[data-testid="stRadio"] label:has(input[type="radio"]:checked) {
        background: #3390ec !important;
        font-weight: 600 !important;
    }
    div[data-testid="stRadio"] label:has(input[type="radio"]:checked) p,
    div[data-testid="stRadio"] label:has(input[type="radio"]:checked) > div:last-child {
        color: white !important;
    }
    </style>
    """, unsafe_allow_html=True)

    col_left, col_right = st.columns([1.4, 5], gap="medium")

    # ── 왼쪽: 채널 목록 ──────────────────────────────────
    with col_left:
        c_title, c_rfr = st.columns([3, 1])
        with c_title:
            st.markdown(f"<div style='font-weight:bold;font-size:15px;padding:4px 0;'>📱 {html.escape(folder_name)}</div>",
                        unsafe_allow_html=True)
        with c_rfr:
            if st.button("🔄", key="tg_ch_rfr", help="채널 목록 새로고침"):
                load_dialogs.clear()
                load_messages.clear()
                st.rerun()

        st.markdown("<hr style='margin:4px 0 6px;'>", unsafe_allow_html=True)

        with st.spinner("채널 목록 로딩..."):
            dialogs, folder_found = load_dialogs()

        if dialogs is None:
            st.error("설정 오류")
            return
        if not folder_found:
            st.warning(f"'{folder_name}' 폴더를 찾지 못해 전체 채널을 보여줍니다. "
                       f"텔레그램 앱에서 폴더 이름을 확인해주세요.")
        if not dialogs:
            st.info("채널 없음")
            return

        # 채널마다 고정 배정된 색 이모지로 아바타 느낌 (st.radio 라벨은 순수 텍스트만 지원)
        labels = [
            f"{_avatar_dot(d['name'])} {d['name']}" + (f"  ({d['unread']})" if d["unread"] > 0 else "")
            for d in dialogs
        ]
        selected_idx = st.radio(
            "채널",
            range(len(dialogs)),
            format_func=lambda i: labels[i],
            label_visibility="collapsed",
            key="tg_selected",
        )

    # ── 오른쪽: 메시지 ───────────────────────────────────
    with col_right:
        selected = dialogs[selected_idx]

        c_av, c1, c2, c3 = st.columns([0.5, 4.5, 1.2, 0.7])
        with c_av:
            st.markdown(_avatar_html(selected['name'], 34), unsafe_allow_html=True)
        with c1:
            st.markdown(f"<div style='font-size:16px;font-weight:bold;padding:5px 0 2px 2px;'>"
                        f"{html.escape(selected['name'])}</div>", unsafe_allow_html=True)
        with c2:
            limit = st.selectbox("개수", [30, 50, 100],
                                 label_visibility="collapsed", key="tg_limit")
        with c3:
            if st.button("🔄", key="tg_msg_rfr", use_container_width=True):
                load_messages.clear()
                search_messages.clear()
                st.rerun()

        query = st.text_input(
            "검색", placeholder="🔍 채널 내 단어 검색...",
            label_visibility="collapsed", key="tg_search",
        )

        is_search = bool(query and query.strip())

        with st.spinner("메시지 로딩..." if not is_search else f"'{query}' 검색 중..."):
            if is_search:
                messages = search_messages(selected["entity_key"], query.strip(), limit)
            else:
                messages = load_messages(selected["entity_key"], limit)

        if is_search:
            st.caption(f"🔍 **'{query}'** 검색 결과 {len(messages)}개")
        else:
            st.caption(f"최근 {len(messages)}개 · 3분 자동 갱신")
        st.divider()

        if not messages:
            st.info("메시지가 없습니다." if not is_search else f"'{query}' 검색 결과가 없습니다.")
        else:
            for msg in messages:
                _render_msg(msg)
