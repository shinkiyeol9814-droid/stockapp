import streamlit as st
import json
import os
import re
from pathlib import Path
from github import Github  # 💡 [필수] 깃허브 연동 라이브러리 추가
from streamlit_autorefresh import st_autorefresh

BASE_DIR = Path(__file__).parent
DATA_FILE = BASE_DIR / "data" / "earnings" / "earnings_data.json"
FAVORITES_FILE = "data/earnings/favorites.json"  # 깃허브 경로는 그대로 유지
LOCAL_FAVORITES_FILE = BASE_DIR / "data" / "earnings" / "favorites.json"

# 💡 [핵심 추가] 깃허브 레포지토리 연결 헬퍼 함수
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
    /* 하단 카드 체크박스 위치 고정 (절대 건드리지 않음) */
    div[data-testid="stCheckbox"] {
        position: relative;
        z-index: 99;         
        left: 14px;
