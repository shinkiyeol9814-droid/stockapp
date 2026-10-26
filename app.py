import streamlit as st
import pandas as pd
import streamlit.components.v1 as components
from streamlit_autorefresh import st_autorefresh

# --- 💡 [필수] 페이지 기본 설정은 무조건 코드 최상단에 위치해야 합니다! ---
st.set_page_config(page_title="StkPro 통합 보드", page_icon="📊", layout="wide")

# 전역 autorefresh: 앱 전체에 1개만 등록해 메뉴 전환 시 중복 타이머를 방지합니다.
st.session_state.auto_refresh_count = st_autorefresh(
    interval=3 * 60 * 1000, key="global_auto_refresh"
)

# ==========================================
# 💡 [핵심 마법] 스켈레톤 제거 & 상단 잘림 방지 CSS
# ==========================================
st.markdown("""
    <style>
        /* 0. 브라우저의 '강제 다크모드'(크롬 Auto Dark Theme, 삼성인터넷 다크모드)
           옵트아웃. 이건 Streamlit 테마와 다른 층위다 — config.toml의
           base="light"는 Streamlit이 칠하는 색만 정하고, 강제 다크모드는 그
           결과물을 브라우저가 한 번 더 뒤집는다. 그래서 하드코딩한 #ffe6e6
           버튼까지 적갈색으로 바뀌어 보였다. only light를 선언하면 브라우저가
           "이 페이지는 라이트 전용"으로 보고 반전을 걸지 않는다. */
        :root {
            color-scheme: only light !important;
        }
        /* 1. 촌스러운 회색 로딩 박스 아예 안 보이게 투명 처리! (깜빡임 완벽 해결) */
        div[data-testid="stSkeleton"] {
            display: none !important;
            opacity: 0 !important;
        }

        /* 2. 상단 여백. Streamlit 툴바는 전체 폭 y 0~60을 덮는 오버레이라
           (z-index 999990) 그 아래로 들어간 메뉴는 윗부분이 안 눌린다.
           3.75rem(60px)이 메뉴가 툴바를 벗어나는 최소값이다.
           예전 3.5rem은 아래 autorefresh 빈 칸까지 겹쳐 과하게 밀려 있었다. */
        .block-container {
            padding-top: 3.75rem !important;
            padding-bottom: 1rem !important;
            padding-left: 0.8rem !important;
            padding-right: 0.8rem !important;
        }

        /* 2-1. 요소 사이 간격·구분선 여백 줄이기 (2026-10-23 "불필요한 여백 제거").
           Streamlit 세로 블록의 기본 간격은 1rem이라 요소가 많은 탭은 빈 줄이 겹겹이 생겼다.
           구분선(st.divider / ---)도 위아래 여백이 커서 한 줄짜리 빈칸처럼 보였다. */
        [data-testid="stVerticalBlock"] {
            gap: 0.6rem !important;
        }
        [data-testid="stMarkdownContainer"] hr, hr {
            margin: 0.35rem 0 !important;
        }

        /* 3. 제목 크기 통일 (ui_common.page_header / section_title).
           예전엔 탭마다 1.4rem div · ### · ####가 섞여, 신고가의 "### 통합 분석 결과"가
           페이지 제목보다 컸다. 페이지 제목 1.15rem, 소제목 1.0rem으로 맞춘다. */
        .page-title, .main-title {
            font-size: 1.15rem !important;
            font-weight: 700;
            line-height: 1.3;
            margin: 0 !important;
        }
        .sec-title {
            font-size: 1.0rem;
            font-weight: 700;
            margin: 14px 0 4px;
        }

        /* 3-1. 제목 줄(st-key-hdr_*)과 작은 옵션 줄(st-key-inline_*)은 휴대폰에서도 한 줄.
           Streamlit은 좁은 화면에서 컬럼을 세로로 쌓아(폭 100%) 새로고침 버튼이 화면 폭
           전체 막대가 됐다. 이 두 종류의 줄만 쌓지 않게 한다. */
        [class*="st-key-hdr_"] [data-testid="stHorizontalBlock"],
        [class*="st-key-inline_"] [data-testid="stHorizontalBlock"] {
            flex-wrap: nowrap !important;
            align-items: center !important;
            gap: 0.5rem !important;
        }
        [class*="st-key-hdr_"] [data-testid="stColumn"]:first-child {
            flex: 1 1 auto !important; width: auto !important; min-width: 0 !important;
        }
        [class*="st-key-hdr_"] [data-testid="stColumn"]:last-child {
            flex: 0 0 2.3rem !important; width: 2.3rem !important; min-width: 2.3rem !important;
        }
        [class*="st-key-hdr_"] [data-testid="stButton"] button {
            width: 2.3rem !important; height: 2.3rem !important; min-height: 2.3rem !important;
            padding: 0 !important;
        }
        [class*="st-key-inline_"] [data-testid="stColumn"] {
            flex: 0 0 auto !important; width: auto !important; min-width: 0 !important;
        }

        /* 3-2. 거래급증 일자·시각 줄 — 시각 버튼("종합 (113)")을 작게 촘촘히 (ui_surge). */
        .st-key-surge_bar [data-testid="stButtonGroup"] button {
            padding: 0.15rem 0.5rem !important;
            min-height: 2rem !important;
        }
        .st-key-surge_bar [data-testid="stButtonGroup"] button p {
            font-size: 0.78rem !important;
        }

        /* 4. 버튼 클릭 잔상·트랜지션 제거 */
        div[data-testid="stButton"] > button,
        div[data-testid="stFormSubmitButton"] > button {
            transition: none !important;
            -webkit-transition: none !important;
        }

        /* 5. 탭 전환 시 컨텐츠 영역 깜빡임 방지 */
        .main .block-container > div {
            animation: none !important;
        }

        /* 6. 높이 0짜리 요소가 만드는 빈 줄 제거.
           Streamlit의 세로 블록은 flex(gap:16px)라, 화면에 안 보이는
           components.html(height=0)과 <style> 전용 markdown도 간격을 한 칸씩
           차지한다. 가치평가 탭은 그런 요소가 많아 제목이 80px나 밀려 있었다. */
        [data-testid="stElementContainer"][height="0px"] {
            display: none !important;
        }
        [data-testid="stElementContainer"]:has(> .stMarkdown [data-testid="stMarkdownContainer"] > style:only-child) {
            display: none !important;
        }

        /* 7. 전역 autorefresh 타이머는 아무것도 그리지 않으면서 26px + gap 16px를
           먹어 메뉴 위에 빈 줄을 만들었다. display:none은 숨긴 iframe의 타이머가
           throttle될 수 있어 쓰지 않고, 흐름에서만 빼낸다(렌더링은 계속된다). */
        .st-key-global_auto_refresh {
            position: absolute !important;
            height: 0 !important;
            overflow: hidden !important;
            pointer-events: none !important;
        }
    </style>
""", unsafe_allow_html=True)

# CSS 세팅 후 나머지 모듈 불러오기
from new_high import render_new_high_menu
from valuation import render_valuation_menu, get_ticker_listing
from ui_report import render_report_summary
from ui_earnings import render_earnings_menu
from ui_surge import render_surge
from ui_macro import render_macro
from ui_trade import render_trade
from ui_sector import render_sector_menu
from streamlit_option_menu import option_menu
from version_history import render_version_badge

# 세션 상태 초기화 (전역)
if 'last_ticker' not in st.session_state: st.session_state.last_ticker = ""
if 'target_mult' not in st.session_state: st.session_state.target_mult = 10
if 'search_corp_name' not in st.session_state: st.session_state.search_corp_name = ""

# --- URL 파라미터 확인 및 메뉴 자동 이동 로직 ---
query_stock_code = st.query_params.get("stock_code", "")
default_menu_idx = 0 

if query_stock_code:
    listing = get_ticker_listing()
    matched = listing[listing['Code'] == str(query_stock_code).zfill(6)]
    if not matched.empty:
        st.session_state.search_corp_name = matched['Name'].values[0]
    default_menu_idx = 0 

# ==========================================
# 🚀 상단 가로형 메뉴 생성 (모바일 최적화 & 아이콘 색상 동적 전환)
# ==========================================
menu = option_menu(
    menu_title=None,
    # 💡 2줄로 감싸지는 메뉴라(4개씩) 순서가 곧 화면 배치: 리스트의 앞 4개가
    # 윗줄, 뒤 4개가 아랫줄이 된다. 윗줄 = 가치평가/거래급증/레포트/수출입,
    # 아랫줄 = 섹터별/신고가/실적/매크로.
    options=["가치평가", "거래급증", "레포트", "수출입", "섹터별", "신고가", "실적", "매크로"],
    icons=["graph-up-arrow", "lightning-charge", "newspaper", "box-seam", "pie-chart", "rocket", "bar-chart-line", "globe"],
    default_index=default_menu_idx,
    orientation="horizontal",
    styles={
        "container": {
            "padding": "0!important", 
            "background-color": "#ffffff", 
            "border-radius": "10px", 
            "border": "1px solid #eee",
            "margin-bottom": "15px"
        },
        # 💡 [핵심 1] 아이콘 색상을 고정(#FF4B4B)하지 않고, 동적 변수인 var(--icon-color)로 받습니다!
        "icon": {
            "color": "var(--icon-color)", 
            "font-size": "14px"
        }, 
        "nav-link": {
            # 💡 [핵심 2] 평소(선택 안 됨) 상태일 때는 변수값을 '빨간색'으로 줍니다.
            "--icon-color": "#FF4B4B",
            "font-size": "12px",
            "text-align": "center",
            "margin": "0px",
            "padding": "10px 2px",
            "white-space": "nowrap",
            "--hover-color": "#f0f2f6",
            "border": "1px solid #ddd",
            "box-sizing": "border-box"
        },
        "nav-link-selected": {
            # 💡 [핵심 3] 메뉴가 선택되면 변수값을 '하얀색'으로 덮어씌웁니다! (위장술 타파)
            "--icon-color": "white", 
            "background-color": "#FF4B4B", 
            "color": "white", 
            "font-weight": "bold", 
            "border-radius": "8px"
        },
    }
)

# 버전 기록 — 메뉴 바로 아래 좌측에 작게. 누르면 팝업으로 펼쳐진다.
# st.columns로 감싸면 모바일에서 세로로 쌓이며 전체 폭을 먹으므로 그냥 둔다.
render_version_badge()

# ==========================================
# 💡 상단 메뉴 고정 그리드 강제 (기기 폭에 따라 줄바꿈 위치가 달라지는 문제 방지)
# option_menu는 iframe 컴포넌트라 CSS가 직접 안 닿으므로, window.parent로 넘어가
# iframe 내부 문서에 <style>을 주입해 4개씩 고정 wrap 되도록 강제합니다.
# (8개 항목 → 위 4개 + 아래 4개, 칸 폭 25%)
#
# ⚠️ 배포 직후 메뉴가 "윗줄 4개만" 또는 "8개가 한 줄"로 보이던 문제(2026-10-26 원인 확인):
#  · option_menu는 iframe 높이를 마운트·업데이트 순간에만 잰다(setFrameHeight, ResizeObserver 없음).
#    우리 <style>이 그보다 늦게 들어가면 메뉴는 두 줄이 되는데 iframe은 한 줄 높이로 남아
#    아랫줄이 잘린다 → "버튼 4개".
#  · 예전 스크립트는 한 번 주입에 성공하면 감시를 멈췄다. 재배포 뒤 재연결 등으로 메뉴 iframe
#    문서만 새로 만들어지면 아무도 다시 주입하지 않아 → "8개가 한 줄".
# 그래서 감시를 멈추지 않고(가볍다: 0.4초마다 확인 몇 개), 스타일이 없으면 다시 넣고,
# iframe 높이가 실제 내용과 다르면 직접 맞춘다.
# ==========================================
components.html("""
<script>
(function() {
    function ensure() {
        try {
            var mainDoc = (window.parent || window).document;
            var frame = mainDoc.querySelector('iframe[title="streamlit_option_menu.option_menu"]');
            if (!frame) return;
            var fdoc = frame.contentDocument;
            if (!fdoc || !fdoc.head || !fdoc.body) return;
            if (!fdoc.getElementById('__menu_grid_fix__')) {
                var style = fdoc.createElement('style');
                style.id = '__menu_grid_fix__';
                style.textContent = `
                    ul.nav.nav-justified { flex-wrap: wrap !important; }
                    ul.nav.nav-justified > li.nav-item {
                        flex: 0 0 25% !important;
                        max-width: 25% !important;
                        box-sizing: border-box !important;
                    }
                `;
                fdoc.head.appendChild(style);
            }
            // 메뉴가 아직 그려지지 않았으면(로딩 중) 높이를 건드리지 않는다
            if (!fdoc.querySelector('ul.nav li')) return;
            var need = Math.ceil(fdoc.body.scrollHeight);
            var cur = Math.round(frame.getBoundingClientRect().height);
            if (need > 0 && Math.abs(need - cur) > 1) {
                frame.style.height = need + 'px';
                frame.setAttribute('height', need);
            }
        } catch (e) {}
    }
    ensure();
    setInterval(ensure, 400);
})();
</script>
""", height=0)

# 강제 다크모드 옵트아웃을 바깥 문서(앱을 감싼 프레임)에도 건다.
# 위 CSS는 앱 iframe 안에만 닿는데, 브라우저는 최상위 문서 기준으로 반전 여부를
# 판단하므로 거기에도 선언이 있어야 한다.
components.html("""
<script>
(function() {
    try {
        // 컴포넌트 iframe에서 window.parent는 '앱 문서'까지만이다. 브라우저가
        // 반전 여부를 판단하는 건 최상위 문서이므로 window.top까지 거슬러
        // 올라가며 전부 선언한다(교차 출처면 접근에서 예외 → 건너뜀).
        var docs = [], w = window;
        for (var i = 0; i < 5 && w; i++) {
            try { if (w.document) docs.push(w.document); } catch (e) { break; }
            if (w === w.parent) break;
            w = w.parent;
        }
        docs.forEach(function(d) {
            d.documentElement.style.colorScheme = 'only light';
            if (!d.getElementById('__cs_only_light__')) {
                var m = d.createElement('meta');
                m.id = '__cs_only_light__';
                m.name = 'color-scheme';
                m.content = 'only light';
                (d.head || d.documentElement).appendChild(m);
            }
        });
    } catch (e) {}
})();
</script>
""", height=0)

# 가치평가 화면이 아닐 때만 파라미터를 지워서 메뉴 꼬임을 방지합니다.
if menu != "가치평가":
    st.query_params.clear()
    
# ==========================================
# 💡 탭 전환 전체 화면 로딩 오버레이는 2026-10-26 없앴다(사용자 요청).
# 이전 탭 잔상을 가리려고 흰 막(94%)을 덮었는데, 매크로처럼 0.7초를 넘는 탭은
# 매번 막이 떠서 "느리다"는 인상만 남겼다. 이제 화면을 가리지 않고 바로 그린다 —
# 다시 그리는 동안은 Streamlit이 이전 내용을 흐리게 보여주고, 정말 오래 걸리는
# 조회만 페이지 안 진행률(ui_common.run_with_progress, 1초 넘을 때만)이 뜬다.
# ==========================================

# ==========================================
# --- 메뉴 라우팅 ---
# ==========================================
if menu == "가치평가":
    render_valuation_menu()

elif menu == "거래급증":
    render_surge()

elif menu == "레포트":
    render_report_summary()


elif menu == "섹터별":
    render_sector_menu()

elif menu == "신고가":
    render_new_high_menu()

elif menu == "실적":
    render_earnings_menu()

elif menu == "매크로":
    render_macro()

elif menu == "수출입":
    render_trade()
