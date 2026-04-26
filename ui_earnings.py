import streamlit as st
import json
import os
import re # 💡 링크 변환을 위해 정규식 라이브러리 추가
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

    results = sorted(results, key=lambda x: x['발표시간'], reverse=True)
    
    st.caption(f"📊 총 **{len(results)}**개의 1분기 실적 공시가 스크리닝 되었습니다.")
    st.divider()
    
    for row in results:
        corp_name = row.get('종목명', '')
        code = row.get('코드', '')
        pub_time = row.get('발표시간', '')
        is_provisional = row.get('잠정여부', '')
        
        op = row.get('영업익', '-')
        gap = row.get('괴리율', '')
        surf_status = row.get('서프_상태', '')
        
        # 💡 [핵심 1] 텍스트 줄바꿈 처리 및 실제 클릭 가능한 하이퍼링크(<a> 태그)로 변환
        raw_text = row.get('원문', '').replace('\n', '<br>')
        raw_text = re.sub(
            r'(https?://[^\s<]+)', 
            r'<a href="\1" target="_blank" style="color: #0066cc; text-decoration: underline;">\1</a>', 
            raw_text
        )
        
        # 상태에 따른 색상 분기
        if "서프라이즈" in surf_status or "상회" in surf_status: 
            status_color = "#FF0000" 
        elif "쇼크" in surf_status or "하회" in surf_status: 
            status_color = "#1E90FF" 
        else: 
            status_color = "#555555" 

        # 마이너스(-) 영업익 차분한 파란색 처리
        if str(op).startswith('-'):
            op_display = f"<b style='color: #5A9BD4;'>{op}억</b>"
        else:
            op_display = f"<b>{op}억</b>"

        # 💡 괴리율 텍스트 조립 (+ 기호 명시적으로 추가)
        if gap:
            try:
                gap_num = int(gap)
                gap_str = f"+{gap_num}%" if gap_num > 0 else f"{gap_num}%"
                gap_text = f"<span style='color: {status_color}; font-weight: bold;'>({gap_str})</span>"
            except:
                gap_text = f"({gap}%)"
        else:
            gap_text = ""
        
        # 💡 [핵심 2] display: flex 를 사용하여 모든 요소를 한 줄(일자)로 나열! (매출 삭제됨)
        card_html = (
            f"<details style='border: 1px solid #e0e0e0; border-radius: 8px; padding: 12px; margin-bottom: 12px; background-color: #ffffff; box-shadow: 0 2px 4px rgba(0,0,0,0.05);'>"
            f"<summary style='cursor: pointer; list-style: none; outline: none;'>"
            f"<div style='display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px;'>"
            f"  <div style='display: flex; align-items: center; flex-wrap: wrap; gap: 10px;'>"
            f"    <span style='font-size: 16px; font-weight: bold; color: #222;'>{corp_name}</span>"
            f"    <span style='font-size: 13px; color: #888;'>[{code}]</span>"
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
