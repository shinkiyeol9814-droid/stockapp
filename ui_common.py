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


# ── 종목명 → 네이버 금융 링크 ────────────────────────────────────────────────
# st.dataframe/data_editor의 셀에는 HTML을 못 넣고, LinkColumn이 글자를 정하는
# 수단은 **URL에 거는 정규식(display_text)** 하나뿐이다. 그래서 종목명을 URL
# 프래그먼트(#이름)로 실어 보내고 거기서 되뽑는다. 프래그먼트는 서버로 전송되지
# 않으므로 네이버 요청에는 아무 영향이 없다(ui_surge가 먼저 쓰던 방법).
NAVER_ITEM = "https://finance.naver.com/item/main.naver?code={code}"
NAME_FROM_URL = r"#(.+)$"      # LinkColumn(display_text=...)에 그대로 넘긴다


def naver_link(code, name=None) -> str:
    """'…?code=005930#삼성전자'. 코드가 없으면 빈 문자열(칸이 비고 링크도 없다)."""
    c = str(code or "").strip()
    if not c.isdigit() or len(c) != 6:
        return ""
    # 이름이 비면 정규식이 안 걸려 URL이 통째로 보이므로 코드로 메운다.
    return NAVER_ITEM.format(code=c) + "#" + (str(name).strip() or c)


def naver_name_column(label: str = "종목명", **kw):
    """종목명 자리에 쓰는 LinkColumn. 호출부가 column_config에 그대로 넣는다."""
    return st.column_config.LinkColumn(label, display_text=NAME_FROM_URL, **kw)


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


def section_title_select(title: str, options, key: str, index: int = 0,
                         width: int = 104):
    """
    소제목 + 바로 옆 작은 선택 상자. 고른 값을 돌려준다.

    기간 선택을 라디오로 깔면 보기(3~5개)가 한 줄을 통째로 먹고 'OO 기간'이라는
    라벨 줄까지 붙어, 화면 하나에 섹션이 서넛 있으면 선택지가 내용보다 많아 보인다.
    제목 옆 선택 상자는 지금 값만 한 칸으로 보여준다.

    휴대폰에서도 한 줄로 유지되도록 컨테이너 키(inline_*)에 전역 CSS가 nowrap을 건다.
    """
    opts = list(options)
    with st.container(key=f"inline_sec_{key}"):
        c1, c2 = st.columns([1, 0.1], vertical_alignment="center")
        with c1:
            st.markdown(f"<div class='sec-title'>{html.escape(title)}</div>",
                        unsafe_allow_html=True)
        with c2:
            sel = st.selectbox(title, opts, index=index, key=f"secsel_{key}",
                               label_visibility="collapsed", width=width)
    return sel


def run_with_progress(jobs: dict, label: str = "불러오는 중", max_workers: int = 12,
                      show_after: float = 1.0) -> dict:
    """
    {이름: 인자 없는 함수}를 병렬로 돌려 {이름: 결과}를 돌려준다(예외는 결과 자리에 그대로).
    오래 걸리면 0~100% 진행률 막대를 띄우고, 끝나면 지운다.

    💡 show_after초 안에 다 끝나면(=전부 캐시 적중) 막대를 아예 안 띄운다 — 3분마다 도는
    자동 새로고침 때마다 막대가 번쩍이면 오히려 거슬린다. 0.3초였는데 짧은 조회에도 막대가
    떠 "느리다"는 인상을 줘서 1초로 늘렸다(2026-10-26, 탭 전환 오버레이를 없앤 날).

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
