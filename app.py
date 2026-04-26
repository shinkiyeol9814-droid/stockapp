import streamlit as st
import pandas as pd
from new_high import render_new_high_menu
from valuation import render_valuation_menu, get_ticker_listing
from ui_report import render_report_summary  # 💡 ui_report 모듈 Import!
from ui_earnings import render_earnings_menu

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

# --- URL 파라미터 확인 및 메뉴 자동 이동 로직 ---
query_stock_code = st.query_params.get("stock_code", "")
default_menu_idx = 0 

if query_stock_code:
    listing = get_ticker_listing()
    matched = listing[listing['Code'] == str(query_stock_code).zfill(6)]
    if not matched.empty:
        st.session_state.search_corp_name = matched['Name'].values[0]
        
    # 💡 [삭제] 여기서 냅다 지워버리면 새로고침 시 기억을 잃습니다!
    # st.query_params.clear() 
    default_menu_idx = 0 

# --- 메뉴 구성 ---
st.sidebar.title("🧭 StkPro 메뉴")
# 사이드바 메뉴 리스트에 4번째 메뉴 추가
menu = st.sidebar.radio(
    "이동", 
    ["📈 가치평가 시뮬레이터", "🚀 신고가 트래킹", "📰 레포트 요약", "📊 실적 스크리닝", "🛠️ 업데이트 이력"], 
    index=default_menu_idx
)

# 💡 [핵심 추가] 시뮬레이터 화면이 아닐 때만 파라미터를 지워서 메뉴 꼬임을 방지합니다.
if menu != "📈 가치평가 시뮬레이터":
    st.query_params.clear()
    
# --- 메뉴 라우팅 ---
if menu == "📈 가치평가 시뮬레이터":
    render_valuation_menu()

elif menu == "🚀 신고가 트래킹":
    render_new_high_menu()

elif menu == "📰 레포트 요약":
    render_report_summary()  # 💡 ui_report.py의 함수 호출!

elif menu == "📊 실적 스크리닝":
    render_earnings_menu()
    
elif menu == "🛠️ 업데이트 이력":
    st.markdown("<div class='main-title'>🛠️ 업데이트 이력</div>", unsafe_allow_html=True)
    
    # 💡 3.3.0 ~ 3.4.0 최신 릴리즈 노트 반영!
    df_history = pd.DataFrame({
        "버전": [
            "v3.4.0 (데이터 3분할 아키텍처 & GitHub 연동)", 
            "v3.3.0 (증권사 레포트 AI 요약 뷰어)", 
            "v3.2.0 (시뮬레이터 커스텀 모드 및 배치 최적화)", 
            "v3.1.0 (시뮬레이터 & 신고가 고도화)", 
            "v3.0 (모듈화 및 연동)"
        ], 
        "내용": [
            "(1) 데이터 격리: Valuation, New High, Report 전용 스토리지(폴더) 분리 및 UI 경로 동기화 / (2) Valuation: GitHub API 연동을 통한 '내 추정치' 클라우드 다이렉트 저장 완벽 지원 (KeyError 및 Type 충돌 철벽 방어)",
            "(1) Report: 텔레그램 PDF/텍스트 기반 증권사 레포트 AI 핵심 요약(Gemini 2.5 Flash) 대시보드 / (2) Pre-Market(야간) 및 Regular(정규장) 데이터 자동 분류 / (3) 데이터 수동 입력 UI 추가",
            "(1) Valuation: '내 추정치' 수동 입력 폼 추가 / (2) New High: 평일 자동화 스케줄러 연동 및 타겟 필터 조건(500억/양봉) 완화",
            "(1) Valuation: 차트 라벨 겹침 방지 알고리즘 및 목표 배수 자동 동기화 / (2) New High: 시총 필터 추가 및 AI 배치 최적화",
            "app.py 메인 라우터 분리(valuation.py) 및 신고가 분석 화면에서 시뮬레이터 자동 검색(URL 파라미터) 연동"
        ]
    })
    
    # 화면을 꽉 채우고 행 높이를 조절하여 긴 텍스트도 잘리지 않게 출력
    st.data_editor(
        df_history, 
        hide_index=True, 
        use_container_width=True,
        disabled=True,
        column_config={
            "버전": st.column_config.TextColumn("버전 (Release Date)", width="medium"),
            "내용": st.column_config.TextColumn("주요 업데이트 내용", width="large")
        }
    )
