import streamlit as st
import json
import os
import re
from streamlit_autorefresh import st_autorefresh

DATA_FILE = "data/earnings/earnings_data.json"

def render_earnings_menu():
    st_autorefresh(interval=3 * 60 * 1000, key="earnings_auto_refresh")
    
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

    # 최신순 정렬
    results = sorted(results, key=lambda x: x['발표시간'], reverse=True)
    
    # ==========================================
    # 💡 [핵심] 분기 필터 UI 및 로직 추가
    # ==========================================
    # 1. 수집된 데이터에서 존재하는 모든 분기를 중복 없이 뽑아내어 정렬 (예: ['2026.1Q', '2025.4Q'])
    available_quarters = sorted(list(set([row.get('해당분기', '미상') for row in results if row.get('해당분기', '미상') != "미상"])), reverse=True)
    filter_options = ["전체"] + available_quarters

    # 2. 화면 상단에 필터 셀렉트박스 배치
    selected_quarter = st.selectbox("📌 분기 필터", filter_options, index=0)

    # 3. 유저가 선택한 분기에 맞춰 데이터 필터링
    if selected_quarter != "전체":
        filtered_results = [row for row in results if row.get('해당분기') == selected_quarter]
    else:
        filtered_results = results
    # ==========================================

    # 필터링된 결과 개수 출력
    st.caption(f"📊 선택된 분기 기준 총 **{len(filtered_results)}**개의 실적 공시가 있습니다.")
    st.divider()
    
    # results 대신 filtered_results 로 반복문 실행
    for row in filtered_results:
        corp_name = row.get('종목명', '')
        code = row.get('코드', '')
        pub_time = row.get('발표시간', '')
        is_provisional = row.get('잠정여부', '')
        quarter = row.get('해당분기', '미상') 
        
        op = row.get('영업익', '-')
        gap = row.get('괴리율', '')
        surf_status = row.get('서프_상태', '')
        
        raw_text = row.get('원문', '').replace('\n', '<br>')
        raw_text = re.sub(
            r'(https?://[^\s<]+)', 
            r'<a href="\1" target="_blank" style="color: #0066cc; text-decoration: underline;">\1</a>', 
            raw_text
        )
        
        if "서프라이즈" in surf_status or "상회" in surf_status: status_color = "#FF0000" 
        elif "쇼크" in surf_status or "하회" in surf_status: status_color = "#1E90FF" 
        else: status_color = "#555555" 

        if op == "-": op_display = "<b>-</b>"
        elif str(op).startswith('-'): op_display = f"<b style='color: #5A9BD4;'>{op}억</b>"
        else: op_display = f"<b>{op}억</b>"

        if gap:
            try:
                gap_num = int(gap)
                gap_str = f"+{gap_num}%" if gap_num > 0 else f"{gap_num}%"
                gap_text = f"<span style='color: {status_color}; font-weight: bold;'>({gap_str})</span>"
            except:
                gap_text = f"({gap}%)"
        else:
            gap_text = ""
        
        card_html = (
            f"<details style='border: 1px solid #e0e0e0; border-radius: 8px; padding: 12px; margin-bottom: 12px; background-color: #ffffff; box-shadow: 0 2px 4px rgba(0,0,0,0.05);'>"
            f"<summary style='cursor: pointer; list-style: none; outline: none;'>"
            f"<div style='display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px;'>"
            f"  <div style='display: flex; align-items: center; flex-wrap: wrap; gap: 10px;'>"
            f"    <span style='font-size: 16px; font-weight: bold; color: #222;'>{corp_name}</span>"
            f"    <span style='font-size: 13px; color: #888;'>[{code}]</span>"
            f"    <span style='font-size: 12px; padding: 2px 6px; border-radius: 4px; background-color: #FFF3E0; color: #E65100; font-weight: bold; border: 1px solid #FFE0B2;'>{quarter}</span>"
            f"    <span style='font-size: 12px; padding: 2px 6px; border-radius: 4px; background-color: #eee; color: #555;'>{is_provisional}</span>"
            f"    <span style='color: #ddd;'>|</span>"
            f"    <span style='color: {status_color}; font-weight: 900; font-size: 14px;'>{surf_status}</span>"
            f"    <span style='font-size: 14px;'>💰 영업익: {op_display} {gap_text}</span>"
            f"  </div>"
            f"  <div style='font-size: 12px; color: #aaa; min-width: 120px; text-align: right;'>{pub_time}</div>"
            f"</div>"
            f"</summary>"
            f"<div style='margin-top: 12px; padding-top: 12px; border-top: 1px dashed #eee; font-size: 13px; color: #444; line-height: 1.6;'>"
            f"{raw_text}"
            f"</div>"
            f"</details>"
        )
        st.markdown(card_html, unsafe_allow_html=True)
