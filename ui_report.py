import streamlit as st
import pandas as pd
import json
import os
import glob
import re
from streamlit_autorefresh import st_autorefresh

# 💡 하이브리드 포맷 리더: 신규/구형 파일 모두 호환하여 예쁜 옵션 리스트 생성
def get_report_options():
    file_list = glob.glob('data/broker_report/*.json')
    options = {}

    for file_path in file_list:
        base_name = os.path.basename(file_path)
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # 💡 [루트 1: 신규 포맷] JSON 내부에 메타데이터가 예쁘게 들어있는 경우
            if isinstance(data, dict) and 'analysis_time' in data:
                r_type = data.get('report_type', '')
                a_time = data.get('analysis_time', '')
                kor_type = "☀️ 정규 레포트" if "Regular" in r_type else "🌙 전일 레포트" if "Previous" in r_type else "기타"
                display_name = f"{kor_type} ({a_time} 업데이트)"
                sort_time = a_time # 정렬 기준
            
            # 💡 [루트 2: 구형 포맷] JSON은 데이터만 있고, 파일명에 시간이 적힌 과거 데이터
            else:
                match = re.search(r'(\d{8}_\d{4})', base_name)
                if match:
                    time_str = match.group(1) # 예: 20260417_0942
                    formatted_time = f"{time_str[:4]}-{time_str[4:6]}-{time_str[6:8]} {time_str[9:11]}:{time_str[11:13]}"
                    
                    kor_type = "☀️ 정규 레포트" if "regular" in base_name.lower() else "🌙 전일 레포트" if "previous" in base_name.lower() else "기타"
                    display_name = f"{kor_type} ({formatted_time} 과거데이터)"
                    sort_time = formatted_time
                else:
                    continue # 정체불명의 파일은 스킵

            options[display_name] = {
                "path": file_path,
                "time": sort_time
            }
        except Exception:
            continue

    # 시간을 기준으로 최신순 내림차순 정렬
    sorted_options = dict(sorted(options.items(), key=lambda item: item[1]['time'], reverse=True))
    return {k: v['path'] for k, v in sorted_options.items()}


def render_report_summary():
    # 💡 [자동 갱신] 3분(180,000ms)마다 화면을 조용히 새로고침합니다!
    st_autorefresh(interval=3 * 60 * 1000, key="report_auto_refresh")
    
    # 💡 [수동 갱신] 사용자가 원할 때 즉시 갱신할 수 있는 버튼 추가
    col1, col2 = st.columns([8, 2])
    with col1:
        st.markdown("<div class='main-title'>📊 증권사 레포트 AI 요약</div>", unsafe_allow_html=True)
    with col2:
        if st.button("🔄 새로고침", use_container_width=True):
            st.rerun()
            
    report_options = get_report_options()
    
    if report_options:
        selected_name = st.selectbox("분석 데이터 선택", list(report_options.keys()))
        selected_file = report_options[selected_name]
        
        with open(selected_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            
            # 💡 구형/신규 데이터 추출 호환 처리
            if isinstance(data, dict) and 'results' in data:
                results = data.get('results', [])
                analysis_time = data.get('analysis_time', '알 수 없음')
            else:
                results = data if isinstance(data, list) else []
                analysis_time = "과거 데이터 (파일명 참조)"
            
            st.caption(f"📅 레포트 추출 및 분석 시점: {analysis_time} | 📊 총 {len(results)}개의 레포트가 분석되었습니다.")
            
            if results:
                df = pd.DataFrame(results)
                
                # 안전한 변환 (과거 데이터 중 Upside 필드가 없을 수도 있음)
                df['Upside_num'] = pd.to_numeric(df.get('Upside', 0), errors='coerce')
                
                # 오늘의 최고 기대 종목 하이라이트 배지
                if not df['Upside_num'].isna().all():
                    top_row = df.loc[df['Upside_num'].idxmax()]
                    if top_row['Upside_num'] > 0:
                        st.success(f"🚀 **오늘의 최고 기대 종목:** {top_row.get('종목명', 'N/A')} (기대수익률: **{top_row['Upside_num']:.1f}%** | {top_row.get('증권사', 'N/A')})")
                
                st.divider()

                df = df.sort_values(by='Upside_num', ascending=False)
                df['Upside_str'] = df['Upside_num'].apply(lambda x: "N/A" if pd.isna(x) else f"{x:.1f}%")

                st.markdown("<br>", unsafe_allow_html=True)

                for _, row in df.iterrows():
                    title = row.get('레포트 제목', '제목 없음')
                    report_date = row.get('발행일자', 'N/A') 
                    curr_price = row.get('현재가', 'N/A')
                    curr_mc = row.get('현재시총', 'N/A')
                    tgt_price = row.get('목표주가', 'N/A')
                    tgt_mc = row.get('목표시총', 'N/A')
                    
                    upside_val = row.get('Upside_num', 0)
                    upside_str = row.get('Upside_str', 'N/A')

                    if pd.isna(upside_val): fire, up_color = "❄️", "#808080"
                    elif upside_val >= 50: fire, up_color = "🔥🔥🔥", "#FF0000"
                    elif upside_val >= 30: fire, up_color = "🔥🔥", "#FF4500"
                    elif upside_val > 0: fire, up_color = "🔥", "#FF8C00"
                    else: fire, up_color = "💧", "#1E90FF"

                    points = row.get('투자포인트', [])
                    points_html = "".join([f"<li style='margin-bottom: 4px;'>{p}</li>" for p in points]) if isinstance(points, list) else f"<li>{points}</li>"

                    card_html = (
                        f"<details style='border: 1px solid #e0e0e0; border-radius: 8px; padding: 12px; margin-bottom: 12px; background-color: #ffffff; box-shadow: 0 2px 4px rgba(0,0,0,0.05);'>"
                        f"<summary style='cursor: pointer; list-style: none; outline: none;'>"
                        f"<div style='margin-bottom: 6px;'>"
                        f"<span style='font-size: 16px; font-weight: bold; color: #222;'>{row.get('종목명', 'N/A')}</span> "
                        f"<span style='font-size: 13px; color: #666;'>({row.get('증권사', 'N/A')})</span>"
                        f"<span style='font-size: 14px; color: #ccc;'> &nbsp;|&nbsp; </span>"
                        f"<span style='font-size: 14px; color: #444;'>{title}</span>"
                        f"<span style='font-size: 14px; color: #ccc;'> &nbsp;|&nbsp; </span>"
                        f"<span style='font-size: 13px; color: #888;'>{report_date}</span>" 
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
