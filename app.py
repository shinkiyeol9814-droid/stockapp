import streamlit as st
import pandas as pd

# --- 💡 [필수] 페이지 기본 설정은 무조건 코드 최상단에 위치해야 합니다! ---
st.set_page_config(page_title="StkPro 통합 보드", page_icon="📊", layout="wide")

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
        
        /* 2. 💡 상단 여백 넉넉하게 확보 (메뉴 잘림 현상 해결!) */
        .block-container { 
            padding-top: 3.5rem !important; /* 👈 너무 바짝 당겼던 여백을 정상화했습니다 */
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
    </style>
""", unsafe_allow_html=True)

# CSS 세팅 후 나머지 모듈 불러오기
from new_high import render_new_high_menu
from valuation import render_valuation_menu, get_ticker_listing
from ui_report import render_report_summary  
from ui_earnings import render_earnings_menu
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
# 🚀 상단 가로형 메뉴 생성 (모바일 최적화)
# ==========================================
menu = option_menu(
    menu_title=None, 
    options=["가치평가", "신고가", "레포트", "실적"],
    icons=["graph-up-arrow", "rocket", "newspaper", "bar-chart-line"], 
    default_index=default_menu_idx,
    orientation="horizontal",
    styles={
        "container": {
            "padding": "0!important", 
            "background-color": "#ffffff", 
            "border-radius": "10px", 
            "border": "1px solid #eee",
            "margin-bottom": "15px" # 메뉴 아래 여백
        },
        "icon": {"color": "#FF4B4B", "font-size": "14px"}, 
        "nav-link": {
            "font-size": "12px",  
            "text-align": "center", 
            "margin": "0px", 
            "padding": "10px 2px", 
            "white-space": "nowrap", 
            "--hover-color": "#f0f2f6"
        },
        "nav-link-selected": {
            "background-color": "#FF4B4B", 
            "color": "white", 
            "font-weight": "bold", 
            "border-radius": "8px"
        },
    }
)

# 가치평가 화면이 아닐 때만 파라미터를 지워서 메뉴 꼬임을 방지합니다.
if menu != "가치평가":
    st.query_params.clear()
    
# ==========================================
# --- 메뉴 라우팅 ---
# ==========================================
if menu == "가치평가":
    render_valuation_menu()

elif menu == "신고가":
    render_new_high_menu()

elif menu == "레포트":
    render_report_summary()  

elif menu == "실적":
    render_earnings_menu()
