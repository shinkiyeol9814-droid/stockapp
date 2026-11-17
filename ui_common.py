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
import re
import threading
import time

import streamlit as st
from streamlit.runtime.scriptrunner import add_script_run_ctx, get_script_run_ctx


# ── 종목명 → 네이버 금융 링크 ────────────────────────────────────────────────
# st.dataframe/data_editor의 셀에는 HTML을 못 넣고, LinkColumn이 글자를 정하는
# 수단은 **URL에 거는 정규식(display_text)** 하나뿐이다. 그래서 종목명을 URL
# 프래그먼트(#이름)로 실어 보내고 거기서 되뽑는다. 프래그먼트는 서버로 전송되지
# 않으므로 네이버 요청에는 아무 영향이 없다(ui_surge가 먼저 쓰던 방법).
# ⚠️ finance.naver.com/item/main.naver?code=… 를 쓰지 말 것(2026-11-17 휴대폰 제보).
# 휴대폰에서는 m.stock.naver.com/item/index.nhn?code=… 로 갔다가 거기서 다시 모바일 **첫 화면**으로
# 301되어 종목이 사라진다(안드로이드·아이폰 둘 다 실측). 데스크톱은 이미 아래 주소로 302되고 있어
# 처음부터 새 주소를 쓴다 — 휴대폰은 m.stock…/total, 데스크톱은 stock…/price로 각자 맞게 간다.
NAVER_ITEM = "https://stock.naver.com/domestic/stock/{code}"
NAME_FROM_URL = r"#(.+)$"      # LinkColumn(display_text=...)에 그대로 넘긴다

# 종목명 열의 기본 폭(픽셀). "small"(75px)은 한글 일곱 자(약 79px)를 못 담았고,
# "medium"(200px)은 휴대폰에서 빈칸이 너무 컸다. 아래 pad_tap_target이 이 폭에
# 맞춰 공백을 채우므로, 열 폭을 바꾸면 그 값도 같이 넘겨야 한다.
NAME_COL_PX = 110


# ── ⚠️ 표 안의 링크는 '글자가 그려진 자리'만 눌린다 ─────────────────────────
# st.dataframe은 캔버스(glide-data-grid)라 셀이 <a>가 아니다. 어디를 눌렀는지는
# 표시 글자를 measureText로 재서 만든 상자에 사방 4px을 더해 판정한다
# (Streamlit 1.55 번들 실측: 상자 높이 = 글자 ascent + 8px).
#
# 2026-11-17 실측(400 12.25px "Source Sans", 행 높이 35px):
#     KT          21 x 16px
#     SK텔레콤     55 x 19px
#     나이스정보통신 87 x 19px
# 안드로이드 권장 최소 터치 영역은 48 x 48px이다 — 휴대폰에서 "눌러도 안 넘어간다"는
# 제보가 나온 이유가 이것이다. 데스크톱은 마우스를 올리면 밑줄이 떠서 조준이 되지만
# 휴대폰에는 hover가 없다.
#
# **폭은 늘릴 수 있다.** 판정 상자의 너비가 표시 글자의 measureText 폭이므로,
# 이름 뒤에 공백을 채우면 그만큼 넓어진다. 공백은 보이지 않고, 그리는 쪽은
# 셀 폭/4 글자에서 잘라내므로(말줄임표도 안 붙는다) 화면은 그대로다.
# **높이(글자 ascent + 8px = 19px)는 못 늘린다** — 행 높이 35px 중 가운데 19px만
# 살아 있다. 그래서 이 수선은 "확실히 눌린다"가 아니라 "훨씬 잘 눌린다"이다.
# 완전히 없애려면 표를 캔버스가 아닌 HTML로 그려야 한다(실적 탭처럼).
_SPACE_PX = 2.45        # 실측. 아래 글자 폭도 전부 같은 글꼴에서 잰 값이다.


def _text_px(s: str) -> float:
    """표시 글자의 대략적인 폭(px). 실측보다 **작게** 잡는다 — 모자라게 잡으면
    공백이 넉넉해질 뿐이고, 넘친 몫은 셀 밖이라 잘려서 해가 없다."""
    w = 0.0
    for ch in s:
        if ord(ch) > 0x2000:        # 한글·한자 등 전각 (실측 11.27)
            w += 11.0
        elif ch.isupper():          # 실측 7.09
            w += 7.0
        else:                       # 소문자 6.17 · 숫자 6.09
            w += 6.0
    return w


def pad_tap_target(text: str, width_px: int = NAME_COL_PX) -> str:
    """셀 폭만큼 보이지 않는 공백을 덧대 탭 영역을 넓힌다(위 머리말 참고)."""
    t = str(text)
    if not t:
        return t
    need = width_px - 8 - _text_px(t)      # 8 = 셀 좌우 여백(실측 7) + 여유
    return t + " " * max(0, min(80, int(need / _SPACE_PX) + 1))


def naver_link(code, name=None, width_px: int = NAME_COL_PX) -> str:
    """'…/stock/005930#삼성전자'. 코드가 없으면 빈 문자열(칸이 비고 링크도 없다).

    ⚠️ 코드가 숫자만은 아니다 — KRX가 신규 상장·우선주에 영문이 섞인 6자리를 준다
    (매드업 0039P0, 삼성에피스홀딩스 0126Z0, CJ4우(전환) 00104K). 숫자만 받던 시절엔
    그 종목들의 이름 칸이 통째로 비었다(2026-11-17 제보, 저장된 급증·투신 파일에 42종목).
    네이버는 이 코드로도 종목 페이지가 정상으로 열린다(휴대폰·데스크톱 실측)."""
    c = str(code or "").strip().upper()
    if not re.fullmatch(r"[0-9][0-9A-Z]{5}", c):      # 첫 자리는 늘 숫자 — 실적의 잘린 코드(A04391)는 거른다
        return ""
    # 이름이 비면 정규식이 안 걸려 URL이 통째로 보이므로 코드로 메운다.
    # 뒤의 공백은 탭 영역을 넓히려는 것 — 프래그먼트라 네이버 요청에는 안 간다.
    return NAVER_ITEM.format(code=c) + "#" + pad_tap_target(str(name or "").strip() or c, width_px)


def naver_name_column(label: str = "종목명", width=NAME_COL_PX, **kw):
    """종목명 자리에 쓰는 LinkColumn. 호출부가 column_config에 그대로 넣는다."""
    return st.column_config.LinkColumn(label, display_text=NAME_FROM_URL,
                                       width=width, **kw)


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
