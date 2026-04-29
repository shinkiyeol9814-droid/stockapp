import streamlit as st
import json
import os
import re
from streamlit_autorefresh import st_autorefresh

DATA_FILE = "data/earnings/earnings_data.json"
# 💡 [신규] 관심 종목만 따로 저장할 파일 경로
FAVORITES_FILE = "data/earnings/favorites.json"

# 관심 종목 불러오기/저장하기 헬퍼 함수
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
    
    # 관심 종목 세션 상태 관리
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
    
    # 필터 섹션
    available_quarters = sorted(list(set([
        row.get('해당분기') for row in results 
        if row.get('해당분기') and "미상" not in row.get('해당분기')
    ])), reverse=True)
    
    if not available_quarters:
        st.warning("표시할 분기 데이터가 없습니다.")
        return
    
    # 💡 [UX 개선] 필터 레이아웃 확장
    f_col1, f_col2, f_col3 = st.columns([1, 1, 1])
    with f_col1:
        selected_quarter = st.selectbox("📌 분기 필터", available_quarters, index=0)
    with f_col2:
        search_keyword = st.text_input("🔍 종목 검색", placeholder="종목명/코드")
    with f_col3:
        # 💡 [신규] 관심 종목만 보기 토글
        show_only_favs = st.toggle("⭐ 관심종목만", value=False)

    # 다중 필터링 로직
    filtered_results = []
    for row in results:
        code = row.get('코드', '')
        
        # 1. 분기 필터
        if row.get('해당분기') != selected_quarter:
            continue
        # 2. 검색어 필터
        if search_keyword:
            kw = search_keyword.replace(" ", "").lower()
            if kw not in row.get('종목명', '').lower() and kw not in code.lower():
                continue
        # 3. 관심종목 필터
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
        
        # 관심 종목 여부 확인
        is_fav = code in st.session_state.favorites
        
        # 💡 [수정] 버튼 공간을 0.1 -> 0.7 정도로 넉넉하게 늘려서 짤림을 방지합니다.
        c1, c2 = st.columns([0.7, 9.3]) 
        
        with c1:
            # 💡 버튼이 위쪽으로 너무 붙지 않게 투명한 빈 줄을 하나 넣어 수직 중앙 정렬 느낌을 줍니다.
            st.write("") 
            if st.button(f"{'⭐' if is_fav else '☆'}", key=f"fav_btn_{code}"):
                if is_fav:
                    st.session_state.favorites.remove(code)
                else:
                    st.session_state.favorites.add(code)
                save_favorites(st.session_state.favorites)
                st.rerun()

        with c2:
            raw_text = row.get('원문', '').replace('\n', '<br>')
            # (중략: 색상 및 텍스트 처리 로직은 기존과 동일)
            if "서프라이즈" in surf_status or "상회" in surf_status: status_color = "#FF0000" 
            elif "쇼크" in surf_status or "하회" in surf_status: status_color = "#1E90FF" 
            else: status_color = "#555555" 

            op_display = f"<b>{op}억</b>" if op != "-" else "<b>-</b>"
            if str(op).startswith('-'): op_display = f"<b style='color: #5A9BD4;'>{op}억</b>"
            
            gap_text = f"<span style='color: {status_color}; font-weight: bold;'>({gap}%)</span>" if gap else ""
            
            growth_html = ""
            if yoy or qoq:
                growth_html = f"<span style='font-size: 11px; color: #666;'>[YoY {yoy} | QoQ {qoq}]</span>"
            
            quarter_badge = f"<span style='font-size: 11px; padding: 2px 5px; background-color: #FFF3E0; color: #E65100; border-radius: 4px;'>{quarter}</span>" if quarter else ""

            # 카드 HTML 렌더링
            card_html = (
                f"<details style='border: 1px solid {'#FFD700' if is_fav else '#e0e0e0'}; border-radius: 8px; padding: 10px; margin-bottom: 10px; background-color: {'#FFFDF0' if is_fav else '#ffffff'};'>"
                f"<summary style='cursor: pointer; list-style: none;'>"
                f"  <div style='display: flex; justify-content: space-between; align-items: center;'>"
                f"    <div style='display: flex; align-items: center; gap: 8px;'>"
                f"      <span style='font-size: 15px; font-weight: bold;'>{corp_name}</span>"
                f"      <span style='font-size: 12px; color: #888;'>{code}</span>"
                f"      {quarter_badge}"
                f"      <span style='color: {status_color}; font-weight: bold; font-size: 13px;'>{surf_status}</span>"
                f"      <span style='font-size: 13px;'>영업익: {op_display} {gap_text} {growth_html}</span>"
                f"    </div>"
                f"    <div style='font-size: 11px; color: #aaa;'>{pub_time}</div>"
                f"  </div>"
                f"</summary>"
                f"<div style='margin-top: 10px; padding-top: 10px; border-top: 1px dashed #eee; font-size: 12px; color: #444;'>{raw_text}</div>"
                f"</details>"
            )
            st.markdown(card_html, unsafe_allow_html=True)
