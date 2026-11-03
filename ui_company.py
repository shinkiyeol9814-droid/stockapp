"""
ui_company.py — 기업분석 탭.

사용자가 노션에서 관리하는 기업분석 DB를 앱 안에 그대로 띄운다(2026-10-30, 섹터별 탭 자리).

━━ 노션을 iframe에 넣을 수 있는 주소는 하나뿐이다 (2026-10-30 응답 헤더 실측) ━━
  app.notion.com/p/…, www.notion.so/…        X-Frame-Options: SAMEORIGIN → 못 넣음(로그인도 필요)
  {워크스페이스}.notion.site/{페이지}          같은 이유로 못 넣음(웹에 게시해도)
  {워크스페이스}.notion.site/ebd/{페이지ID}     frame-ancestors https: http: → 넣을 수 있음
마지막이 노션 "웹에 게시 → 임베드" 주소다. 그러려면 DB를 웹에 게시해야 하고, 게시하면
링크를 아는 누구나 볼 수 있다 — 그래서 주소는 공개 리포가 아니라 Streamlit Secrets
NOTION_EMBED_URL에 둔다. 게시 페이지 주소(…notion.site/제목-ID)를 넣어도 /ebd/ 주소로 바꿔 쓴다.
노션 "임베드" 메뉴가 복사해 주는 <iframe src="…"> 코드 통째로 넣어도, ID가 하이픈 섞인
UUID 꼴이어도 된다. 값이 있는데 못 쓰면 이유를 화면에 보인다(값 자체는 보이지 않는다).

Secrets가 없으면 새 탭으로 여는 버튼만 보인다(NOTION_DB_URL, 없으면 아래 기본 링크 —
워크스페이스 링크라 로그인한 본인만 열린다).
"""
import re

import streamlit as st
import streamlit.components.v1 as components

from ui_common import page_header

_DEFAULT_URL = ("https://app.notion.com/p/f421915b8386403298f400aa1c74c0fe"
                "?v=0b94653f9226464fb504d30d3bc3ddec&pvs=12")


def _secret(key: str) -> str:
    try:
        return (st.secrets.get(key) or "").strip()
    except Exception:       # secrets.toml이 아예 없는 로컬 실행
        return ""


_ID = r"(?<![0-9a-f])([0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12})(?![0-9a-f])"


def _embed_url(raw: str) -> str:
    """notion.site 게시·임베드 주소(또는 그 주소가 든 <iframe> 코드)를 /ebd/ 주소로.
    notion.site가 아닌 주소는 iframe에 넣을 수 없어 ""."""
    m = re.search(r"https://([\w-]+)\.notion\.site/([^\s\"'<>]*)", raw or "", re.I)
    if not m:
        return ""
    path, _, query = m.group(2).lower().partition("?")
    ids = re.findall(_ID, path)
    if not ids:
        return ""
    v = re.search(r"(?:^|[&;])v=" + _ID, query)      # DB는 보기(view)를 지정해야 그 표가 나온다
    out = f"https://{m.group(1)}.notion.site/ebd/{ids[-1].replace('-', '')}"
    return out + (f"?v={v.group(1).replace('-', '')}" if v else "")


def _why_not(raw: str) -> str:
    """시크릿 값이 있는데 임베드 주소로 못 바꾼 이유 — 값은 드러내지 않고 종류만."""
    if re.search(r"\.notion\.site/", raw, re.I):
        return "notion.site 주소이긴 한데 페이지 ID(32자리)를 찾지 못했습니다."
    host = re.search(r"https?://([^/\s\"'<>]+)", raw)
    if host and re.search(r"(^|\.)notion\.(so|com)$", host.group(1), re.I):
        return (f"{host.group(1)} 주소는 노션이 다른 사이트 안에 띄우는 것을 막아 둔 주소입니다. "
                "노션에서 <b>공유 → 게시 → 웹에 게시</b>를 켠 뒤 나오는 "
                "<code>…notion.site/…</code> 주소(또는 임베드 코드)를 넣어 주세요.")
    return "노션 웹 게시 주소(<code>https://….notion.site/…</code>) 형태가 아닙니다."


def render_company():
    page_header("기업분석", "company", refresh=False)
    raw = _secret("NOTION_EMBED_URL")
    embed = _embed_url(raw)
    open_url = _secret("NOTION_DB_URL") or _DEFAULT_URL

    if embed:
        # 노션 화면을 그대로 띄운다. 스크롤은 iframe 안에서 — 높이를 화면에 가깝게 잡는다.
        components.iframe(embed, height=900, scrolling=True)
        st.link_button("노션에서 열기 (편집)", open_url, icon=":material/open_in_new:")
        return

    if raw:
        msg = ("Secrets의 <code>NOTION_EMBED_URL</code> 값을 화면에 띄울 수 없습니다 — "
               + _why_not(raw) + " 그 전까지는 새 탭으로 엽니다.")
    else:
        msg = ("노션 DB를 이 화면에 바로 띄우려면 노션에서 <b>웹에 게시 → 임베드</b> 주소를 만들어 "
               "Secrets의 <code>NOTION_EMBED_URL</code>에 넣어 주세요. 그 전까지는 새 탭으로 엽니다.")
    st.markdown(f"<div style='font-size:12.5px;color:#666;margin:2px 0 6px;'>{msg}</div>",
                unsafe_allow_html=True)
    st.link_button("노션 기업분석 DB 열기", open_url, icon=":material/open_in_new:",
                   type="primary", width="stretch")
