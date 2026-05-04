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

    # 💡 [완전 정화] 문제의 토글 CSS 싹 다 날렸습니다! (카드 체크박스만 안전하게 유지)
    st.markdown("""
    <style>
    /* 하단 카드 체크박스 위치 고정 (절대 건드리지 않음) */
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
            
    # earnings 데이터 읽기도 절대경로로
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
    
    f_col1, f_col2, f_col3 = st.columns([2, 3, 2])
    with f_col1:
        selected_quarter = st.selectbox("📌 분기 필터", available_quarters, index=0)
    with f_col2:
        search_keyword = st.text_input("🔍 종목 검색", placeholder="종목명/코드")
    with f_col3:
        # 💡 [핵심 해결책] 기열님 아이디어 적용! 종목 검색처럼 위에 라벨, 아래에 토글 배치
        st.markdown("<div style='font-size: 14px; color: #31333F; margin-bottom: 8px;'>⭐ 관심종목만</div>", unsafe_allow_html=True)
        # 토글 자체의 텍스트는 완전히 숨김(collapsed) 처리하여 절대 세로로 찢어지지 않음
        show_only_favs = st.toggle("hidden_fav_toggle", value=False, label_visibility="collapsed")

    filtered_results = []
    for row in results:
        code = row.get('코드', '')
        
        if row.get('해당분기') != selected_quarter:
            continue
        if search_keyword:
            kw = search_keyword.replace(" ", "").lower()
            if kw not in row.get('종목명', '').lower() and kw not in code.lower():
                continue
        if show_only_favs and code not in st.session_state.favorites:
            continue
            
        filtered_results.append(row)

    st.markdown("<div style='margin-top: 15px;'></div>", unsafe_allow_html=True)
    st.caption(f"📊 **{selected_quarter}** 기준 총 **{len(filtered_results)}**개의 실적 공시가 있습니다.")
    st.divider()
    st.markdown("<div style='height: 24px;'></div>", unsafe_allow_html=True)
    
    for row in filtered_results:
        corp_name = row.get('종목명', '')
        code = row.get('코드', '')
        pub_time = row.get('발표시간', '')
        is_provisional = row.get('잠정여부', '')
        op = row.get('영업익', '-')
        gap = row.get('괴리율', '')
        surf_status = row.get('서프_상태', '')
        yoy = row.get('YoY', '')
        qoq = row.get('QoQ', '')
        
        is_fav = code in st.session_state.favorites
        
        # 💡 복잡한 세션 주입 없이 value=is_fav 로 깔끔하게 통제
        new_fav = st.checkbox("", value=is_fav, key=f"fav_btn_{code}", label_visibility="collapsed")
        
        if new_fav != is_fav:
            if new_fav:
                st.session_state.favorites.add(code)
            else:
                st.session_state.favorites.discard(code)  # 기열님의 굿 아이디어 유지!
            
            with st.spinner("저장 중..."): # 기열님의 디테일 유지!
                save_favorites(st.session_state.favorites)
            st.rerun()

        raw_text = row.get('원문', '').replace('\n', '<br>')
        if "서프라이즈" in surf_status or "상회" in surf_status: status_color = "#FF0000" 
        elif "쇼크" in surf_status or "하회" in surf_status: status_color = "#1E90FF" 
        else: status_color = "#555555" 

        op_display = f"<b>{op}억</b>" if op != "-" else "<b>-</b>"
        if str(op).startswith('-'): op_display = f"<b style='color: #1E90FF;'>{op}억</b>"
        
        gap_text = f"<span style='color: {status_color}; font-weight: bold;'>({gap}%)</span>" if gap else ""
        
        def get_growth_color(val):
            if "+" in val or "흑전" in val: return "#FF0000" 
            if "-" in val or "적전" in val or "적자" in val: return "#1E90FF" 
            return "#555555" 

        growth_html = ""
        if yoy or qoq:
            yoy_colored = f"<span style='color: {get_growth_color(yoy)}; font-weight: 600;'>{yoy}</span>" if yoy else "-"
            qoq_colored = f"<span style='color: {get_growth_color(qoq)}; font-weight: 600;'>{qoq}</span>" if qoq else "-"
            growth_html = f"<span style='font-size: 12px; color: #888; margin-left: 10px;'>YoY {yoy_colored} &nbsp;|&nbsp; QoQ {qoq_colored}</span>"

        short_time = pub_time[5:16] if len(pub_time) >= 16 else pub_time

        card_html = (
            f"<details style='margin-top: -36px; margin-bottom: 12px; border: 1px solid {'#FFD700' if is_fav else '#e0e0e0'}; border-radius: 8px; background-color: {'#FFFDF0' if is_fav else '#ffffff'}; position: relative; z-index: 1;'>"
            f"<summary style='cursor: pointer; list-style: none; outline: none; padding: 12px 12px 12px 42px; box-sizing: border-box;'>"
            f"  <div style='display: flex; flex-direction: column; gap: 6px; width: 100%;'>" 
            
            f"    <div style='display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; width: 100%;'>"
            f"      <span style='font-size: 16px; font-weight: bold; color: #222;'>{corp_name}</span>"
            f"      <span style='color: {status_color}; font-weight: 900; font-size: 12.5px;'>{surf_status}</span>"
            f"      <span style='font-size: 11px; color: #aaa;'>{short_time}</span>" 
            f"    </div>"
            
            f"    <div style='font-size: 14px; color: #333; text-align: left; width: 100%; white-space: normal; line-height: 1.4;'>"
            f"      <span style='color: #888; font-size: 12px;'>OP:</span> {op_display} {gap_text}"
            f"      {growth_html}"
            f"    </div>"
            
            f"  </div>"
            f"</summary>"
            f"<div style='padding: 0px 12px 12px 42px; border-top: 1px dashed #eee; font-size: 13px; color: #444; line-height: 1.6;'>"
            f"{raw_text}"
            f"</div>"
            f"</details>"
        )
        st.markdown(card_html, unsafe_allow_html=True)
