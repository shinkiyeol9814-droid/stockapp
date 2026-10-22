"""
ui_common.py — 탭 공용 UI 조각: 페이지 제목 줄, 섹션 제목, 진행률 표시 병렬 조회.

━━ 왜 모았나 ━━
탭마다 제목을 제각각 그렸다(1.4rem div, ### 마크다운, #### …). 그래서 신고가 탭의
"### 통합 분석 결과"가 페이지 제목보다 더 크게 나왔다. 새로고침도 [8, 2] 컬럼에 넣어
두었는데, 휴대폰에선 컬럼이 세로로 쌓여 화면 폭 전체를 차지하는 막대가 됐다.
여기서 한 가지 모양으로 그리고, 스타일은 app.py 전역 CSS(.page-title / .sec-title /
st-key-hdr_* / st-key-inline_*)가 맡는다.
"""
import concurrent.futures
import html
import threading
import time

import streamlit as st
from streamlit.runtime.scriptrunner import add_script_run_ctx, get_script_run_ctx


def page_header(title: str, key: str, refresh: bool = True) -> bool:
    """
    페이지 제목 + 오른쪽 작은 정사각형 새로고침 버튼. 눌렸으면 True.

    휴대폰에서도 한 줄로 유지되도록 컨테이너 키(hdr_*)에 전역 CSS가 nowrap을 건다.
    """
    clicked = False
    with st.container(key=f"hdr_{key}"):
        c1, c2 = st.columns([1, 0.1], vertical_alignment="center")
        with c1:
            st.markdown(f"<div class='page-title'>{html.escape(title)}</div>", unsafe_allow_html=True)
        if refresh:
            with c2:
                clicked = st.button(":material/refresh:", key=f"refresh_{key}", help="새로고침")
    return clicked


def section_title(text: str) -> None:
    """페이지 안의 소제목 — 페이지 제목보다 한 단계 작게."""
    st.markdown(f"<div class='sec-title'>{html.escape(text)}</div>", unsafe_allow_html=True)


def run_with_progress(jobs: dict, label: str = "불러오는 중", max_workers: int = 12,
                      show_after: float = 0.3) -> dict:
    """
    {이름: 인자 없는 함수}를 병렬로 돌려 {이름: 결과}를 돌려준다(예외는 결과 자리에 그대로).
    오래 걸리면 0~100% 진행률 막대를 띄우고, 끝나면 지운다.

    💡 show_after초 안에 다 끝나면(=전부 캐시 적중) 막대를 아예 안 띄운다 — 3분마다 도는
    자동 새로고침 때마다 막대가 번쩍이면 오히려 거슬린다.

    st.cache_data 함수를 스레드에서 불러도 같은 캐시를 쓰도록 스크립트 실행 컨텍스트를 붙인다
    (안 붙이면 "missing ScriptRunContext" 경고가 쌓인다).
    """
    if not jobs:
        return {}
    ctx = get_script_run_ctx()

    def _init():
        if ctx is not None:
            add_script_run_ctx(threading.current_thread(), ctx)

    results, total = {}, len(jobs)
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(max_workers, total),
                                               initializer=_init) as ex:
        futs = {ex.submit(fn): name for name, fn in jobs.items()}
        pending = set(futs)
        started = time.time()
        bar = None
        while pending:
            done, pending = concurrent.futures.wait(pending, timeout=0.1,
                                                    return_when=concurrent.futures.FIRST_COMPLETED)
            for f in done:
                try:
                    results[futs[f]] = f.result()
                except Exception as e:      # 호출부가 결과 자리의 예외를 보고 판단한다
                    results[futs[f]] = e
            if bar is None and pending and time.time() - started >= show_after:
                bar = st.progress(0, text=f"{label} 0%")
            if bar is not None:
                n = total - len(pending)
                pct = int(n / total * 100)
                bar.progress(pct, text=f"{label} {pct}% ({n}/{total})")
        if bar is not None:
            bar.empty()
    return results
