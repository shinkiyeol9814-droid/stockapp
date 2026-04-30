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
        quarter = row.get('해당분기', '') 
        op = row.get('영업익', '-')
        gap = row.get('괴리율', '')
        surf_status = row.get('서프_상태', '')
        yoy = row.get('YoY', '')
        qoq = row.get('QoQ', '')
        
        is_fav = code in st.session_state.favorites
        
        # 💡 [수정] 텍스트 싹 날리고 아주 깔끔하게 체크박스와 별표만 남깁니다.
        new_fav = st.checkbox("⭐", value=is_fav, key=f"fav_btn_{code}")
        
        if new_fav != is_fav:
            if new_fav:
                st.session_state.favorites.add(code)
            else:
                st.session_state.favorites.remove(code)
            save_favorites(st.session_state.favorites)
            st.rerun()

        raw_text = row.get('원문', '').replace('\n', '<br>')
        if "서프라이즈" in surf_status or "상회" in surf_status: status_color = "#FF0000" 
        elif "쇼크" in surf_status or "하회" in surf_status: status_color = "#1E90FF" 
        else: status_color = "#555555" 

        op_display = f"<b>{op}억</b>" if op != "-" else "<b>-</b>"
        if str(op).startswith('-'): op_display = f"<b style='color: #1E90FF;'>{op}억</b>"
        
        gap_text = f"<span style='color: {status_color}; font-weight: bold;'>({gap}%)</span>" if gap else ""
        
        # ==========================================
        # 💡 [핵심 추가] YoY, QoQ 텍스트 분석 후 색상 입히기
        def get_growth_color(val):
            if "+" in val or "흑전" in val: return "#FF0000" # 상승/흑전은 빨간색
            if "-" in val or "적전" in val or "적지" in val: return "#1E90FF" # 하락/적자는 파란색
            return "#555555" # 그 외(0% 등)는 기본 회색

        growth_html = ""
        if yoy or qoq:
            yoy_colored = f"<span style='color: {get_growth_color(yoy)}; font-weight: 600;'>{yoy}</span>" if yoy else "-"
            qoq_colored = f"<span style='color: {get_growth_color(qoq)}; font-weight: 600;'>{qoq}</span>" if qoq else "-"
            growth_html = f"<span style='font-size: 13px; color: #888; margin-left: 4px;'>[YoY {yoy_colored} | QoQ {qoq_colored}]</span>"
        # ==========================================
        
        quarter_badge = f"<span style='font-size: 11px; padding: 2px 5px; background-color: #FFF3E0; color: #E65100; border-radius: 4px;'>{quarter}</span>" if quarter else ""

        # 💡 [핵심] 2줄 레이아웃으로 분리 (Flex Column 적용)
        card_html = (
            f"<details style='border: 1px solid {'#FFD700' if is_fav else '#e0e0e0'}; border-radius: 8px; padding: 12px; margin-bottom: 16px; background-color: {'#FFFDF0' if is_fav else '#ffffff'};'>"
            f"<summary style='cursor: pointer; list-style: none; outline: none;'>"
            f"  <div style='display: flex; flex-direction: column; gap: 8px; width: 100%;'>" # 전체를 위아래 2줄로 쪼개는 컨테이너
            
            # --- 윗줄 (종목명, 배지, 서프상태 + 오른쪽 시간) ---
            f"    <div style='display: flex; justify-content: space-between; align-items: flex-start; width: 100%;'>"
            f"      <div style='display: flex; align-items: center; gap: 6px; flex-wrap: wrap;'>"
            f"        <span style='font-size: 15px; font-weight: bold; color: #222;'>{corp_name}</span>"
            f"        <span style='font-size: 12px; color: #888;'>{code}</span>"
            f"        {quarter_badge}"
            f"        <span style='color: {status_color}; font-weight: 900; font-size: 13px;'>{surf_status}</span>"
            f"      </div>"
            f"      <div style='font-size: 11px; color: #aaa; white-space: nowrap; flex-shrink: 0; margin-left: 10px; margin-top: 2px;'>{pub_time}</div>" # 💡 시간 우측 고정 (짤림 방지)
            f"    </div>"
            
            # --- 아랫줄 (영업익 + 증감률 데이터) ---
            f"    <div style='font-size: 14px; color: #333; display: flex; align-items: center; gap: 6px; flex-wrap: wrap;'>"
            f"      영업익: {op_display} {gap_text} {growth_html}"
            f"    </div>"
            
            f"  </div>"
            f"</summary>"
            f"<div style='margin-top: 12px; padding-top: 12px; border-top: 1px dashed #eee; font-size: 13px; color: #444; line-height: 1.6;'>"
            f"{raw_text}"
            f"</div>"
            f"</details>"
        )
        st.markdown(card_html, unsafe_allow_html=True)
