import streamlit as st
import json
import os
from streamlit_autorefresh import st_autorefresh

DATA_FILE = "data/earnings/earnings_data.json"

def render_earnings_menu():
    # 💡 3분마다 자동 갱신
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

    # 💡 시간이 최신일수록 상단에 오도록 내림차순 정렬
    results = sorted(results, key=lambda x: x['발표시간'], reverse=True)
    
    st.caption(f"📊 총 **{len(results)}**개의 1분기 실적 공시가 스크리닝 되었습니다.")
    st.divider()
    
    # 티켓 형태의 HTML 아코디언 카드 렌더링
    for row in results:
        corp_name = row.get('종목명', '')
        code = row.get('코드', '')
        pub_time = row.get('발표시간', '')
        is_provisional = row.get('잠정여부', '')
        
        rev = row.get('매출액', '-')
        op = row.get('영업익', '-')
        exp_op = row.get('예상영업익', '')
        gap = row.get('괴리율', '')
        surf_status = row.get('서프_상태', '')
        
        raw_text = row.get('원문', '').replace('\n', '<br>')
        
        # 상태에 따른 색상 분기
        if "서프라이즈" in surf_status or "상회" in surf_status: 
            status_color = "#FF0000" # 빨간색
        elif "쇼크" in surf_status or "하회" in surf_status: 
            status_color = "#1E90FF" # 파란색
        else: 
            status_color = "#555555" # 회색 (부합/없음)

        # 괴리율 표기 텍스트
        gap_text = f"<span style='color: {status_color}; font-weight: bold;'>({gap}%)</span>" if gap else ""
        
        card_html = (
            f"<details style='border: 1px solid #e0e0e0; border-radius: 8px; padding: 12px; margin-bottom: 12px; background-color: #ffffff; box-shadow: 0 2px 4px rgba(0,0,0,0.05);'>"
            f"<summary style='cursor: pointer; list-style: none; outline: none;'>"
            f"<div style='margin-bottom: 6px; display: flex; justify-content: space-between; align-items: center;'>"
            f"  <div>"
            f"    <span style='font-size: 16px; font-weight: bold; color: #222;'>{corp_name}</span> "
            f"    <span style='font-size: 13px; color: #888;'>[{code}]</span>"
            f"    <span style='font-size: 12px; margin-left: 8px; padding: 2px 6px; border-radius: 4px; background-color: #eee; color: #555;'>{is_provisional}</span>"
            f"  </div>"
            f"  <div style='font-size: 12px; color: #aaa;'>{pub_time}</div>"
            f"</div>"
            f"<div style='font-size: 15px; color: #333; margin-top: 8px;'>"
            f"  <span style='color: {status_color}; font-weight: 900; margin-right: 12px;'>{surf_status}</span>"
            f"  <span>💰 영업익: <b>{op}억</b> {gap_text}</span>"
            f"  <span style='color: #ccc;'> &nbsp;|&nbsp; </span>"
            f"  <span>📈 매출: <b>{rev}억</b></span>"
            f"</div>"
            f"</summary>"
            f"<div style='margin-top: 12px; padding-top: 12px; border-top: 1px dashed #eee; font-size: 13px; color: #444; line-height: 1.5;'>"
            f"{raw_text}"
            f"</div>"
            f"</details>"
        )
        st.markdown(card_html, unsafe_allow_html=True)
