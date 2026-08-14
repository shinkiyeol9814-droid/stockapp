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
        /* 1. 촌스러운 회색 로딩 박스 아예 안 보이게 투명 처리! (깜빡임 완벽 해결) */
        div[data-testid="stSkeleton"] {
            display: none !important;
            opacity: 0 !important;
        }

        /* 2. 상단 여백 넉넉하게 확보 (메뉴 잘림 현상 해결!) */
        .block-container {
            padding-top: 3.5rem !important;
            padding-bottom: 1rem !important;
            padding-left: 0.8rem !important;
            padding-right: 0.8rem !important;
        }

        /* 3. 메인 타이틀 여백 조정 */
        .main-title {
            font-size: 1.4rem !important;
            font-weight: bold;
            margin-top: 0.5rem !important;
            margin-bottom: 1rem !important;
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
    </style>
""", unsafe_allow_html=True)

# CSS 세팅 후 나머지 모듈 불러오기
from new_high import render_new_high_menu
from valuation import render_valuation_menu, get_ticker_listing
from ui_report import render_report_summary
from ui_earnings import render_earnings_menu
from ui_telegram import render_telegram_viewer
from ui_watchlist import render_watchlist
from ui_macro import render_macro
from ui_sector import render_sector_menu
from streamlit_option_menu import option_menu

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
    # 윗줄, 뒤 4개가 아랫줄이 된다. 윗줄 = 가치평가/워치리스트/레포트/텔레그램,
    # 아랫줄 = 섹터별/신고가/실적/매크로.
    options=["가치평가", "워치리스트", "레포트", "텔레그램", "섹터별", "신고가", "실적", "매크로"],
    icons=["graph-up-arrow", "list-check", "newspaper", "chat-dots", "pie-chart", "rocket", "bar-chart-line", "globe"],
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

# ==========================================
# 💡 상단 메뉴 고정 그리드 강제 (기기 폭에 따라 줄바꿈 위치가 달라지는 문제 방지)
# option_menu는 iframe 컴포넌트라 CSS가 직접 안 닿으므로, window.parent로 넘어가
# iframe 내부 문서에 <style>을 주입해 4개씩 고정 wrap 되도록 강제합니다.
# (7개 항목 → 위 4개 + 아래 3개, 두 줄 모두 칸 폭 25%로 동일)
# ==========================================
components.html("""
<script>
(function() {
    // 모든 단계를 try/catch로 감싸 어느 한 줄에서 예외가 나도 스크립트 전체가
    // 죽지 않고(=재시도 루프가 살아남고) 다음 tick에 다시 시도하도록 합니다.
    function tryInject() {
        try {
            var mainDoc = (window.parent || window).document;
            var frame = mainDoc.querySelector('iframe[title="streamlit_option_menu.option_menu"]');
            if (!frame) return false;
            var fdoc = frame.contentDocument;
            if (!fdoc || !fdoc.head) return false;
            if (fdoc.getElementById('__menu_grid_fix__')) return true;
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
            return true;
        } catch (e) {
            return false;
        }
    }
    // option_menu 컴포넌트 프론트엔드 로딩이 느릴 수 있어(특히 모바일 콜드스타트)
    // 재시도 횟수를 제한하지 않고 성공할 때까지 인터벌로 계속 시도합니다.
    // 이 iframe 자체가 다음 rerun 때 교체되면서 인터벌도 함께 정리되므로 누수 걱정은 없습니다.
    try {
        var iv = setInterval(function() {
            if (tryInject()) clearInterval(iv);
        }, 300);
    } catch (e) {}
})();
</script>
""", height=0)

# 가치평가 화면이 아닐 때만 파라미터를 지워서 메뉴 꼬임을 방지합니다.
if menu != "가치평가":
    st.query_params.clear()
    
# ==========================================
# 💡 탭 전환 시 이전 탭의 렌더링 잔상이 남지 않도록 처리합니다.
# st.empty()/컨테이너로 "즉시 비우기"를 시도해봤지만 효과가 없었습니다 —
# Streamlit이 vertical block의 남은 자식을 정리하는 시점 자체가 "그 블록을
# 담당하는 with문이 끝날 때(=느린 탭이면 그 함수가 다 실행된 뒤)"라서,
# 파이썬 쪽에서 미리 비워봐야 프레임워크가 실제로 반영해주지 않습니다
# (Streamlit 자체의 알려진 동작/버그 — 커뮤니티에도 같은 사례가 있음).
# 그래서 파이썬이 아니라 프론트엔드 쪽에서 해결합니다: 메뉴를 클릭하는
# "즉시" 전체 화면 로딩 오버레이를 띄워 이전 탭 잔상을 가려버리고,
# 새 탭 렌더링이 끝나면(=러닝 표시가 사라지면) 오버레이를 걷어냅니다.
# ==========================================
components.html("""
<script>
(function() {
    function mainDoc() { return (window.parent || window).document; }

    function ensureOverlay() {
        var doc = mainDoc();
        var ov = doc.getElementById('__tab_loading_overlay__');
        if (ov) return ov;
        if (!doc.getElementById('__tab_spin_style__')) {
            var style = doc.createElement('style');
            style.id = '__tab_spin_style__';
            style.textContent = '@keyframes __tab_spin__ { to { transform: rotate(360deg); } }';
            doc.head.appendChild(style);
        }
        ov = doc.createElement('div');
        ov.id = '__tab_loading_overlay__';
        ov.style.cssText = 'display:none;position:fixed;inset:0;z-index:99999;'
            + 'background:rgba(255,255,255,0.94);flex-direction:column;'
            + 'align-items:center;justify-content:center;';
        ov.innerHTML =
            '<div style="border:4px solid #f0f0f0;border-top:4px solid #FF4B4B;'
            + 'border-radius:50%;width:40px;height:40px;'
            + 'animation:__tab_spin__ 0.8s linear infinite;"></div>'
            + '<div style="margin-top:12px;color:#666;font-size:14px;">불러오는 중...</div>';
        doc.body.appendChild(ov);
        return ov;
    }

    function showOverlay() { ensureOverlay().style.display = 'flex'; }
    function hideOverlay() {
        var ov = mainDoc().getElementById('__tab_loading_overlay__');
        if (ov) ov.style.display = 'none';
    }

    function waitForRunFinish() {
        // 💡 "Stop" 텍스트로 실행중 여부를 판단했었는데, Streamlit 버전이 올라가며
        // 그 표시 방식이 바뀌어 더 이상 감지가 안 됐다 — 그 결과 항상 세이프티넷
        // 시간(그때는 20초)까지 꽉 채워 대기하는 바람에, 원래 빠르던 탭 전환까지
        // 전부 느려 보이는 역효과가 났다. Streamlit 내부 UI 문구/구조에 기대지
        // 않도록, 대신 "DOM이 더 이상 안 바뀌는 시점"을 실행 완료 신호로 쓴다.
        var doc = mainDoc();
        var lastMutation = Date.now();
        var startedAt = Date.now();
        var mo = new MutationObserver(function() { lastMutation = Date.now(); });
        try {
            mo.observe(doc.body, {childList: true, subtree: true, attributes: true, characterData: true});
        } catch (e) {}

        var iv = setInterval(function() {
            var now = Date.now();
            var quietFor = now - lastMutation;
            var elapsed = now - startedAt;
            // 최소 300ms는 무조건 떠 있어(너무 짧으면 깜빡임), 600ms 동안 DOM 변화가
            // 없으면 렌더 완료로 간주. 감지가 완전히 실패해도 화면이 영영 안 가려져
            // 있진 않도록 60초를 최후의 세이프티넷으로 둔다(콜드 캐시로 워치리스트가
            // 60~100초 걸리는 경우가 있어 너무 짧게 잡으면 로딩 중에 먼저 걷혀버림).
            if ((elapsed > 300 && quietFor > 600) || elapsed > 60000) {
                clearInterval(iv);
                mo.disconnect();
                hideOverlay();
            }
        }, 150);
    }

    function attachListeners() {
        try {
            var menuFrame = mainDoc().querySelector('iframe[title="streamlit_option_menu.option_menu"]');
            if (!menuFrame) return false;
            var fdoc = menuFrame.contentDocument;
            if (!fdoc) return false;
            var links = fdoc.querySelectorAll('a.nav-link');
            if (!links.length) return false;
            links.forEach(function(a) {
                if (a.dataset.tabOverlayBound) return;
                a.dataset.tabOverlayBound = '1';
                a.addEventListener('click', function() {
                    showOverlay();
                    waitForRunFinish();
                });
            });
            return true;
        } catch (e) { return false; }
    }

    // 메뉴 iframe이 rerun마다 새로 생기므로 계속 재확인하며 리스너를 다시 붙인다.
    setInterval(attachListeners, 400);
})();
</script>
""", height=0)

# ==========================================
# --- 메뉴 라우팅 ---
# ==========================================
if menu == "가치평가":
    render_valuation_menu()

elif menu == "워치리스트":
    render_watchlist()

elif menu == "레포트":
    render_report_summary()

elif menu == "텔레그램":
    render_telegram_viewer()

elif menu == "섹터별":
    render_sector_menu()

elif menu == "신고가":
    render_new_high_menu()

elif menu == "실적":
    render_earnings_menu()

elif menu == "매크로":
    render_macro()
