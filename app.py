import streamlit as st
import pandas as pd
from new_high import render_new_high_menu
from valuation import render_valuation_menu, get_ticker_listing

# --- 페이지 기본 설정 ---
st.set_page_config(page_title="StkPro 통합 보드", page_icon="📊", layout="wide")

# UI 디테일 튜닝 CSS
st.markdown("""
    <style>
        .block-container { padding-top: 2.5rem !important; padding-bottom: 1rem !important; padding-left: 0.8rem !important; padding-right: 0.8rem !important; }
        .main-title { font-size: 1.4rem !important; font-weight: bold; margin-top: 1rem; margin-bottom: 1rem; }
    </style>
""", unsafe_allow_html=True)

# 세션 상태 초기화 (전역)
if 'last_ticker' not in st.session_state: st.session_state.last_ticker = ""
if 'target_mult' not in st.session_state: st.session_state.target_mult = 10
if 'search_corp_name' not in st.session_state: st.session_state.search_corp_name = ""

# --- 💡 URL 파라미터 확인 및 메뉴 자동 이동 로직 ---
query_stock_code = st.query_params.get("stock_code", "")
default_menu_idx = 0 # 0번 인덱스: 가치평가 시뮬레이터

if query_stock_code:
    listing = get_ticker_listing()
    matched = listing[listing['Code'] == str(query_stock_code).zfill(6)]
    if not matched.empty:
        st.session_state.search_corp_name = matched['Name'].values[0]
    
    # 꼬리표(파라미터)를 지워서 무한 검색에 빠지는 현상 방지
    st.query_params.clear()
    default_menu_idx = 0 

# --- 메뉴 구성 ---
st.sidebar.title("🧭 StkPro 메뉴")
menu = st.sidebar.radio("이동", ["📈 가치평가 시뮬레이터", "🚀 신고가 트래킹", "📰 관심종목 - 뉴스", "🛠️ 업데이트 이력"], index=default_menu_idx)

# 메뉴 라우팅
if menu == "📈 가치평가 시뮬레이터":
    render_valuation_menu()

elif menu == "🚀 신고가 트래킹":
    render_new_high_menu()

elif menu == "📰 관심종목 - 뉴스":
    st.markdown("<div class='main-title'>📰 관심종목 - 뉴스</div>", unsafe_allow_html=True)
    st.info("기능 준비 중입니다. 🚧")

elif menu == "🛠️ 업데이트 이력":
    st.markdown("<div class='main-title'>🛠️ 업데이트 이력</div>", unsafe_allow_html=True)
    df_history = pd.DataFrame({
        "버전": ["V3.0 (모듈화 및 연동)", "V2.0 (신고가 고도화)", "V1.3.11", "V1.3.10", "V1.3.9"], 
        "내용": [
            "app.py 메인 라우터 분리(valuation.py), 신고가 분석 결과에서 클릭 시 시뮬레이터 자동 검색 연동",
            "신고가 트래킹 분석 일자별 선택, 시가총액 필터 추가 및 UI 고도화",
            "재무 데이터 에디터 상단 배치 및 상/하단 차트 과거 평균(Avg) 라인 추가", 
            "평가방식 Select Box 내부 텍스트 잘림 현상 완벽 픽스", 
            "검색 폼 세로 3단 전환 및 라벨 칼각 정렬"
        ]
    })
    st.dataframe(df_history, hide_index=True, use_container_width=True)
