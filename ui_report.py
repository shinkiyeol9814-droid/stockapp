import streamlit as st
import pandas as pd
import json
import os
import glob
import re
from datetime import datetime

# 💡 파일명 변환 함수 (UI 표시용)
def format_json_name(file_path):
    base_name = os.path.basename(file_path)
    if base_name.startswith("premarket_"):
        clean_name = base_name.replace("premarket_", "").replace(".json", "")
        prefix = "🌅 Pre-Market"
    elif base_name.startswith("regular_"):
        clean_name = base_name.replace("regular_", "").replace(".json", "")
        prefix = "🏢 Regular-Market"
    else:
        return base_name

    try:
        dt = datetime.strptime(clean_name, "%Y%m%d_%H%M")
        return f"{prefix} 리포트 ({dt.strftime('%Y-%m-%d %H:%M')})"
    except:
        return base_name

# 💡 날짜 추출용 정렬 함수 (항상 최신 날짜가 위로 오게)
def get_sort_key(file_path):
    match = re.search(r'(\d{8}_\d{4})', file_path)
    return match.group(1) if match else "00000000_0000"

def render_report_summary():
    st.markdown("<div class='main-title'>📊 증권사 레포트 AI 요약</div>", unsafe_allow_html=True)
    
    # 💡 Pre/Regular 상관없이 무조건 날짜/시간 추출해서 내림차순 정렬
    report_files = sorted(glob.glob("data/broker_report/*.json"), key=get_sort_key, reverse=True)
    
    if report_files:
        selected_file = st.selectbox("분석 데이터 선택", report_files, format_func=format_json_name)
        
        with open(selected_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            results = data.get('results', [])
            analysis_time = data.get('analysis_time', '알 수 없음')
            
            st.caption(f"📅 레포트 추출 및 분석 시점: {analysis_time} | 📊 총 {len(results)}개의 레포트가 분석되었습니다.")
            
            if results:
                df = pd.DataFrame(results)
                
                # 정렬을 위한 숫자 변환
                df['Upside_num'] = pd.to_numeric(df['Upside'], errors='coerce')
                
                # 오늘의 최고 기대 종목 하이라이트 배지
                if not df['Upside_num'].isna().all():
                    top_row = df.loc[df['Upside_num'].idxmax()]
                    if top_row['Upside_num'] > 0:
                        st.success(f"🚀 **오늘의 최고 기대 종목:** {top_row['종목명']} (기대수익률: **{top_row['Upside_num']:.1f}%** | {top_row['증권사']})")
                
                st.divider()

                # Upside 내림차순 정렬
                df = df.sort_values(by='Upside_num', ascending=False)
                df['Upside_str'] = df['Upside_num'].apply(lambda x: "N/A" if pd.isna(x) else f"{x:.1f}%")

                st.markdown("<br>", unsafe_allow_html=True)

                # 💡 데이터 에디터(표) 대신 HTML 커스텀 아코디언 카드 렌더링
                for _, row in df.iterrows():
                    title = row.get('레포트 제목', '제목 없음')
                    report_date = row.get('발행일자', 'N/A') # 👈 발행일자 가져오기
                    curr_price = row.get('현재가', 'N/A')
                    curr_mc = row.get('현재시총', 'N/A')
                    tgt_price = row.get('목표주가', 'N/A')
                    tgt_mc = row.get('목표시총', 'N/A')
                    
                    upside_val = row.get('Upside_num', 0)
                    upside_str = row.get('Upside_str', 'N/A')

                    # Upside 수치에 따른 색상 설정 (동일)
                    if pd.isna(upside_val): fire, up_color = "❄️", "#808080"
                    elif upside_val >= 50: fire, up_color = "🔥🔥🔥", "#FF0000"
                    elif upside_val >= 30: fire, up_color = "🔥🔥", "#FF4500"
                    elif upside_val > 0: fire, up_color = "🔥", "#FF8C00"
                    else: fire, up_color = "💧", "#1E90FF"

                    points = row.get('투자포인트', [])
                    points_html = "".join([f"<li style='margin-bottom: 4px;'>{p}</li>" for p in points]) if isinstance(points, list) else f"<li>{points}</li>"

                    # 💡 [최종 수정] 2줄 타이틀 + 발행일자 포함
                    card_html = (
                        f"<details style='border: 1px solid #e0e0e0; border-radius: 8px; padding: 12px; margin-bottom: 12px; background-color: #ffffff; box-shadow: 0 2px 4px rgba(0,0,0,0.05);'>"
                        f"<summary style='cursor: pointer; list-style: none; outline: none;'>"
                        f"<div style='margin-bottom: 6px;'>"
                        f"<span style='font-size: 16px; font-weight: bold; color: #222;'>{row['종목명']}</span> "
                        f"<span style='font-size: 13px; color: #666;'>({row['증권사']})</span>"
                        f"<span style='font-size: 14px; color: #ccc;'> &nbsp;|&nbsp; </span>"
                        f"<span style='font-size: 14px; color: #444;'>{title}</span>"
                        f"<span style='font-size: 14px; color: #ccc;'> &nbsp;|&nbsp; </span>"
                        f"<span style='font-size: 13px; color: #888;'>{report_date}</span>" # 👈 발행일자 추가됨
                        f"</div>"
                        f"<div style='font-size: 14px; color: #555;'>"
                        f"<span style='color: {up_color}; font-weight: bold;'>🚀 Upside: {upside_str} {fire}</span>"
                        f"<span style='color: #ccc;'> &nbsp;|&nbsp; </span>"
                        f"📊 {curr_price} ({curr_mc}) ➡️ <b>{tgt_price} ({tgt_mc})</b>"
                        f"</div>"
                        f"</summary>"
                        f"<div style='margin-top: 12px; padding-top: 12px; border-top: 1px dashed #eee; font-size: 14px; color: #333;'>"
                        f"<b style='color: #0056b3;'>💡 핵심 투자 포인트</b>"
                        f"<ul style='margin-top: 6px; padding-left: 20px;'>{points_html}</ul>"
                        f"<div style='margin-top: 10px; font-size: 12px; color: #888; background-color: #f9f9f9; padding: 8px; border-radius: 4px;'>"
                        f"<b>평가 방식:</b> {row.get('평가방식', 'N/A')}"
                        f"</div>"
                        f"</div>"
                        f"</details>"
                    )
                    
                    st.markdown(card_html, unsafe_allow_html=True)
            else:
                st.warning("해당 시간대에 분석된 종목 데이터가 없습니다.")
    else:
        st.info("실행된 배치 파일이 없습니다. batch_report.py를 먼저 실행해 주세요.")
