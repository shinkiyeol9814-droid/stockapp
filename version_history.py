"""
version_history.py — 앱 변경 이력.

화면 좌상단의 작은 버전 버튼을 누르면 팝업(모달)으로 열린다.

버전 규칙 (x.y.z, 통상적인 유의적 버전)
  x  전면 개편 — 메뉴 구성이나 화면 구조가 통째로 바뀐 릴리스
  y  기능 추가 — 새 탭/지표/차트처럼 없던 기능이 생겼을 때
  z  버그 수정 — 동작 교정, 성능/가독성 개선 등 소소한 변경
새 배포를 할 때는 VERSIONS 맨 앞에 (버전, 날짜, 한 줄 요약) 한 건만 추가한다.
"""
import streamlit as st

# (버전, 날짜, 한 줄 요약) — 최신순
VERSIONS = [
    ("4.7.2", "2026-09-20", "화면 상단 여백 축소 — 메뉴 위 빈 공간 58px 제거"),
    ("4.7.1", "2026-09-20", "증권사 목표가를 3개월 평균 카드로 · 버전 기록 팝업화 · 상단 빈 줄 제거"),
    ("4.7.0", "2026-09-20", "증권사 레포트 목표주가 취합, 모바일 키패드 억제 전 탭 확대"),
    ("4.6.1", "2026-09-18", "수출입 선택창에서 모바일 키패드가 목록을 가리던 문제 해결"),
    ("4.6.0", "2026-09-18", "수출입 탭에 개별 전력반도체 품목 추가"),
    ("4.5.2", "2026-09-16", "배치 커밋 때문에 앱이 수시로 재시작되던 문제 해결"),
    ("4.5.1", "2026-09-14", "차트 마커 가독성 개선, 레포트 평가방식 추출 정확도 향상"),
    ("4.5.0", "2026-09-14", "자동 접속으로 앱이 잠들지 않도록 유지"),
    ("4.4.1", "2026-09-12", "DART 배치 안정화 및 조회가 수 분씩 걸리던 지연 해소"),
    ("4.4.0", "2026-09-12", "PEG 평가방식과 재고자산 래깅 차트 추가"),
    ("4.3.0", "2026-09-11", "DART 수집을 GitHub Actions로 이전해 접속 차단 회피"),
    ("4.2.1", "2026-09-11", "섹터 탭 접속 실패 폴백, 사업부 선택 시 발생하던 오류 수정"),
    ("4.2.0", "2026-09-11", "가치평가 목표가 +2년 확장, 사업부별 가동률, 종목명 자동완성"),
    ("4.1.0", "2026-09-10", "가치평가에 DART 기반 재고자산·수주잔고 패널 추가"),
    ("4.0.0", "2026-09-09", "수출입 탭 분리, 섹터 탭 이슈 중심 개편 (전면 개편)"),
]

APP_VERSION = VERSIONS[0][0]

# 버전 버튼은 화면 주인공이 아니다 — 메뉴보다 작고 흐리게 둬서 눈에 안 띄게 한다.
# (가치평가 탭이 .stButton 색을 덮어쓰므로 키 기반 선택자로 되돌린다.)
_BADGE_CSS = """
<style>
.st-key-ver_btn { width: max-content; }
.st-key-ver_btn button {
    background: transparent !important;
    border: none !important;
    padding: 0 2px !important;
    min-height: 0 !important;
    height: 18px !important;
}
.st-key-ver_btn button p {
    font-size: 11px !important;
    color: #b0b0b0 !important;
    font-weight: 400 !important;
}
.st-key-ver_btn button:hover p { color: #666 !important; }
</style>
"""


@st.dialog("버전 기록", width="small")
def _version_dialog():
    st.caption("x 전면 개편 · y 기능 추가 · z 버그 수정")
    for ver, date, note in VERSIONS:
        st.markdown(
            f"<div style='margin-bottom:10px;line-height:1.45;'>"
            f"<b style='font-size:13px;'>v{ver}</b>"
            f"<span style='color:#aaa;font-size:11px;margin-left:6px;'>{date}</span><br>"
            f"<span style='font-size:13px;color:#444;'>{note}</span></div>",
            unsafe_allow_html=True)


def render_version_badge():
    """좌상단 숨김 버튼. 누를 때만 팝업으로 이력이 뜬다."""
    st.markdown(_BADGE_CSS, unsafe_allow_html=True)
    if st.button(f"v{APP_VERSION}", key="ver_btn", help="버전 기록 보기"):
        _version_dialog()
