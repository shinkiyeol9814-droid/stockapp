import streamlit as st
import json
import os
import re
from pathlib import Path
from github import Github
from streamlit_autorefresh import st_autorefresh

BASE_DIR = Path(__file__).parent
DATA_FILE = BASE_DIR / "data" / "earnings" / "earnings_data.json"
FAVORITES_FILE = "data/earnings/favorites.json"
LOCAL_FAVORITES_FILE = BASE_DIR / "data" / "earnings" / "favorites.json"

def get_github_repo():
    token = st.secrets.get("GITHUB_TOKEN", "")
    repo_name = st.secrets.get("GITHUB_REPO", "")
    if token and repo_name:
        g = Github(token)
        return g.get_repo(repo_name)
    return None

def load_favorites():
    repo = get_github_repo()
    if repo:
        try:
            file_content = repo.get_contents(str(FAVORITES_FILE))
            return set(json.loads(file_content.decoded_content.decode('utf-8')))
        except:
            return set()
    else:
        if LOCAL_FAVORITES_FILE.exists():
            with open(LOCAL_FAVORITES_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        return set()

def save_favorites(favorites_set):
    repo = get_github_repo()
    content_str = json.dumps(list(favorites_set), ensure_ascii=False)
    if repo:
        try:
            file = repo.get_contents(str(FAVORITES_FILE))
            repo.update_file(str(FAVORITES_FILE), "Update favorites", content_str, file.sha)
        except Exception:
            repo.create_file(str(FAVORITES_FILE), "Create favorites", content_str)
    else:
        LOCAL_FAVORITES_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOCAL_FAVORITES_FILE, "w", encoding="utf-8") as f:
            json.dump(list(favorites_set), f, ensure_ascii=False)

def render_earnings_menu():
    st_autorefresh(interval=3 * 60 * 1000, key="earnings_auto_refresh")
    
    if 'favorites' not in st.session_state:
        st.session_state.favorites = load_favorites()

    st.markdown("""
    <style>
    div[data-testid="stCheckbox"] {
        position: relative;
        z-index: 99;         
        left: 14px;          
        top: 14px;           
        width: 30px;         
    }
    </style>
    """, unsafe_allow_html=True)

    col1, col2 = st.columns([8, 2])
    with col1:
        st.markdown("<div class='main-title'>📈 실적 스크리닝 (AWAKE)</div>", unsafe_allow_html=True)
    with col2:
        if st.button("🔄 새로고침", use_container_width=True):
            st.rerun()
            
    if not DATA_FILE.exists():
        st.info("📂 수집된 실적 데이터가 없습니다.")
        return
    
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        results = json.load(f)
        
    if not results:
        st.warning("분석된 실적 데이터가 없습니다.")
        return

    results = sorted(results, key=lambda x: x['발표시간'], reverse=True)
    
    available_quarters = sorted(list(set([
        row.get('해당분기') for row in results 
        if row.get('해당분기') and "미상" not in row.get('해당분기')
    ])), reverse=True)
    
    if not available_quarters:
        st.warning("표시할 분기 데이터가 없습니다.")
        return
    
    if 'ea_quarter' not in st.session_state:
        st.session_state.ea_quarter = available_quarters[0]
    if 'ea_keyword' not in st.session_state:
        st.session_state.ea_keyword = ""
    if 'ea_favs' not in st.session_state:
        st.session_state.ea_favs = False

    with st.form("earnings_search_form", border=False):
        f_col1, f_col2, f_col3, f_col4 = st.columns([2, 3, 1.2, 1.8])
        with f_col1:
            idx = available_quarters.index(st.session_state.ea_quarter) if st.session_state.ea_quarter in available_quarters else 0
            ui_quarter = st.selectbox("📌 분기 필터", available_quarters, index=idx)
        with f_col2:
            ui_keyword = st.text_input("🔍 종목 검색", value=st.session_state.ea_keyword, placeholder="종목명/코드")
        with f_col3:
            st.markdown("<div style='margin-top: 28px;'></div>", unsafe_allow_html=True)
            search_btn = st.form_submit_button("검색", type="primary", use_container_width=True)
        with f_col4:
            st.markdown("<div style='font-size: 14px; color: #31333F; margin-bottom: 8px;'>⭐ 관심종목만</div>", unsafe_allow_html=True)
            ui_show_favs = st.toggle("hidden_fav_toggle", value=st.session_state.ea_favs, label_visibility="collapsed")

    if search_btn:
        st.session_state.ea_quarter = ui
