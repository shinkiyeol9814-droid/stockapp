"""
ui_company.py — 기업분석 탭.

사용자가 노션에서 관리하는 기업분석 DB로 가는 입구다(2026-10-30, 섹터별 탭 자리).
노션은 앱 안에 끼워 넣을 수 없다 — 워크스페이스 페이지(app.notion.com)는 로그인이 필요하고
다른 사이트의 iframe 표시를 막는다. 그래서 새 탭으로 여는 버튼을 둔다.

링크는 Streamlit Secrets의 NOTION_DB_URL이 있으면 그걸 쓴다. 이 리포는 공개라, DB를
'웹에 게시'로 열어 두는 경우엔 링크를 코드 대신 Secrets에 두는 편이 안전하다.
"""
import streamlit as st

from ui_common import page_header

_DEFAULT_URL = ("https://app.notion.com/p/f421915b8386403298f400aa1c74c0fe"
                "?v=0b94653f9226464fb504d30d3bc3ddec&pvs=12")


def _db_url() -> str:
    try:
        return st.secrets.get("NOTION_DB_URL") or _DEFAULT_URL
    except Exception:       # secrets.toml이 아예 없는 로컬 실행
        return _DEFAULT_URL


def render_company():
    page_header("기업분석", "company", refresh=False)
    st.markdown(
        "<div style='font-size:12.5px;color:#666;margin:2px 0 6px;'>"
        "노션에 정리하는 기업분석 DB입니다. 노션은 앱 화면 안에 넣을 수 없어 새 탭으로 열립니다."
        "</div>",
        unsafe_allow_html=True,
    )
    st.link_button("노션 기업분석 DB 열기", _db_url(), icon=":material/open_in_new:",
                   type="primary", width="stretch")
