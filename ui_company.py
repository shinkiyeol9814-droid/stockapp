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


def _embed_url(url: str) -> str:
    """notion.site 게시 주소를 임베드(/ebd/) 주소로. 그 밖의 주소는 임베드할 수 없어 ""."""
    m = re.match(r"https://([\w-]+)\.notion\.site/(?:ebd/)?(?:[^?#]*-)?([0-9a-f]{32})", url)
    if not m:
        return ""
    v = re.search(r"[?&]v=([0-9a-f]{32})", url)      # DB는 보기(view)를 지정해야 그 표가 나온다
    return f"https://{m.group(1)}.notion.site/ebd/{m.group(2)}" + (f"?v={v.group(1)}" if v else "")


def render_company():
    page_header("기업분석", "company", refresh=False)
    embed = _embed_url(_secret("NOTION_EMBED_URL"))
    open_url = _secret("NOTION_DB_URL") or _DEFAULT_URL

    if embed:
        # 노션 화면을 그대로 띄운다. 스크롤은 iframe 안에서 — 높이를 화면에 가깝게 잡는다.
        components.iframe(embed, height=900, scrolling=True)
        st.link_button("노션에서 열기 (편집)", open_url, icon=":material/open_in_new:")
        return

    st.markdown(
        "<div style='font-size:12.5px;color:#666;margin:2px 0 6px;'>"
        "노션 DB를 이 화면에 바로 띄우려면 노션에서 <b>웹에 게시 → 임베드</b> 주소를 만들어 "
        "Secrets의 <code>NOTION_EMBED_URL</code>에 넣어 주세요. 그 전까지는 새 탭으로 엽니다."
        "</div>",
        unsafe_allow_html=True,
    )
    st.link_button("노션 기업분석 DB 열기", open_url, icon=":material/open_in_new:",
                   type="primary", width="stretch")
