import streamlit as st
import json
import os
import re
from streamlit_autorefresh import st_autorefresh

DATA_FILE = "data/earnings/earnings_data.json"
FAVORITES_FILE = "data/earnings/favorites.json"

def load_favorites():
    if os.path.exists(FAVORITES_FILE):
        with open(FAVORITES_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()

def save_favorites(favorites_set):
    os.makedirs(os.path.dirname(FAVORITES_FILE), exist_ok=True)
    with open(FAVORITES_FILE, "w", encoding="utf-8") as f:
        json.dump(list(favorites_set), f, ensure_ascii=False)

def render_earnings_menu():
    st_autorefresh(interval=3 * 60 * 1000, key="earnings_auto_refresh")
    
    if 'favorites' not in st.session_state:
        st.session_state.favorites = load_favorites()

    # 💡 [핵심 마법] 체크박스를 왼쪽으로 보내고, 크기를 줄여 종목명 옆에 쏙 들어갈 수 있게 설정
    st.markdown("""
    <style>
    div[data-testid="stCheckbox"] {
        display: flex;
        justify-content: flex-start;     /* 좌측 정렬 */
        margin-bottom: -43px !important; /* 아래 카드를 위로 끌어올림 */
        padding-top: 14px !important;    /* 텍스트와 높이 줄맞춤 */
        padding-left: 14px !important;   /* 좌측 여백 */
        position: relative;
        z-index: 99;                     /* 클릭 가능하도록 띄움 */
        width: fit-content;              /* 차지하는 너비를 최소화 */
    }
    </style>
    """, unsafe_allow_html=True)

    col1, col2 = st.columns([8, 2])
    with col1:
        st.markdown("<div class='main-title'>📈 실적 스크리닝 (AWAKE)</div>", unsafe_allow_html=True)
    with col2:
        if st.button("🔄 새로고침", use_container_width=True):
            st.rerun()
            
    if not os.path.exists(DATA_FILE):
        st.info("📂 수집된 실적 데이터가 없습니다. 배치를 먼저 실행해주세요.")
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
    
    f_col1, f_col2, f_col3 = st.columns([1, 1, 1])
    with f_col1:
        selected_quarter = st.selectbox("📌 분기 필터", available_quarters, index=0)
    with f_col2:
        search_keyword = st.text_input("🔍 종목 검색", placeholder="종목명/코드")
    with f_col3:
        show_only_favs = st.toggle("⭐ 관심종목만", value=False)

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

    st.caption(f"📊 **{selected_quarter}** 기준 총 **{len(filtered_results)}**개의 실적 공시가 있습니다.")
    st.divider()
    
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
        
        # 💡 [1] 체크박스 렌더링 (CSS에 의해 왼쪽 끝으로 이동)
        new_fav = st.checkbox("⭐", value=is_fav, key=f"fav_btn_{code}")
        
        if new_fav != is_fav:
            if new_fav:
                st.session_state.favorites.add(code)
            else:
                st.session_state.favorites.remove(code)
            save_favorites(st.session_state.favorites)
            st.rerun()

        # 💡 [2] 데이터 및 색상 처리
        raw_text = row.get('원문', '').replace('\n', '<br>')
        if "서프라이즈" in surf_status or "상회" in surf_status: status_color = "#FF0000" 
        elif "쇼크" in surf_status or "하회" in surf_status: status_color = "#1E90FF" 
        else: status_color = "#555555" 

        op_display = f"<b>{op}억</b>" if op != "-" else "<b>-</b>"
        if str(op).startswith('-'): op_display = f"<b style='color: #1E90FF;'>{op}억</b>"
        
        gap_text = f"<span style='color: {status_color}; font-weight: bold;'>({gap}%)</span>" if gap else ""
        
        def get_growth_color(val):
            if "+" in val or "흑전" in val: return "#FF0000" 
            if "-" in val or "적전" in val or "적지" in val: return "#1E90FF" 
            return "#555555" 

        growth_html = ""
        if yoy or qoq:
            yoy_colored = f"<span style='color: {get_growth_color(yoy)}; font-weight: 600;'>{yoy}</span>" if yoy else "-"
            qoq_colored = f"<span style='color: {get_growth_color(qoq)}; font-weight: 600;'>{qoq}</span>" if qoq else "-"
            growth_html = f"<span style='font-size: 12px; color: #888; margin-left: 10px;'>YoY {yoy_colored} &nbsp;|&nbsp; QoQ {qoq_colored}</span>"

        short_time = pub_time[5:16] if len(pub_time) >= 16 else pub_time

        # 💡 [3] 카드 HTML 렌더링 
        card_html = (
            f"<details style='border: 1px solid {'#FFD700' if is_fav else '#e0e0e0'}; border-radius: 8px; padding: 12px; margin-bottom: 16px; background-color: {'#FFFDF0' if is_fav else '#ffffff'};'>"
            # 💡 [핵심] padding-left: 45px 를 추가하여, 카드 텍스트 시작점을 우측으로 밀어냅니다. 
            # 그 비워진 왼쪽 공간에 위에서 그린 체크박스가 쏙! 들어가게 됩니다.
            f"<summary style='cursor: pointer; list-style: none; outline: none; padding-left: 45px;'>"
            f"  <div style='display: flex; flex-direction: column; gap: 6px; width: 100%;'>" 
            
            # --- 윗줄 (종목명, 서프상태, 시간) ---
            f"    <div style='display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; width: 100%;'>"
            f"      <span style='font-size: 16px; font-weight: bold; color: #222;'>{corp_name}</span>"
            f"      <span style='color: {status_color}; font-weight: 900; font-size: 12.5px;'>{surf_status}</span>"
            f"      <span style='font-size: 11px; color: #aaa;'>{short_time}</span>" 
            f"    </div>"
            
            # --- 아랫줄 (완벽한 왼쪽 정렬) ---
            f"    <div style='font-size: 14px; color: #333; text-align: left; width: 100%; white-space: normal; line-height: 1.4;'>"
            f"      <span style='color: #888; font-size: 12px;'>OP:</span> {op_display} {gap_text}"
            f"      {growth_html}"
            f"    </div>"
            
            f"  </div>"
            f"</summary>"
            f"<div style='margin-top: 12px; padding-top: 12px; border-top: 1px dashed #eee; font-size: 13px; color: #444; line-height: 1.6;'>"
            f"{raw_text}"
            f"</div>"
            f"</details>"
        )
        st.markdown(card_html, unsafe_allow_html=True)
