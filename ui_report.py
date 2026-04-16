import streamlit as st
import pandas as pd
import json
import os
import glob
import re
from datetime import datetime

# 💡 파일명 변환 함수
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
    
    # 💡 Pre/Regular 상관없이 무조건 날짜/시간 추출해서 내림차순 정렬!
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
                
                df['Upside_num'] = pd.to_numeric(df['Upside'], errors='coerce')
                
                # 오늘의 최고 기대 종목 하이라이트
                if not df['Upside_num'].isna().all():
                    top_row = df.loc[df['Upside_num'].idxmax()]
                    if top_row['Upside_num'] > 0:
                        st.success(f"🚀 **오늘의 최고 기대 종목:** {top_row['종목명']} (기대수익률: **{top_row['Upside_num']:.1f}%** | {top_row['증권사']})")
                
                st.divider()

                # Upside 높은 순 정렬
                df = df.sort_values(by='Upside_num', ascending=False)
                df['Upside'] = df['Upside_num'].apply(lambda x: "N/A" if pd.isna(x) else f"{x:.1f}%")

                # 💡 [핵심] 기열님이 요청하신 바로 그 포맷! (제목, 가격, 포인트 통합)
                def format_invest_points(row):
                    title = row.get('레포트 제목', '제목 없음')
                    curr_price = row.get('현재가', 'N/A')
                    curr_mc = row.get('현재시총', 'N/A')
                    tgt_price = row.get('목표주가', 'N/A')
                    tgt_mc = row.get('목표시총', 'N/A')
                    
                    points = row.get('투자포인트', [])
                    points_str = "\n".join([f"- {p}" for p in points]) if isinstance(points, list) else str(points)
                    
                    # 요청하신 3줄 포맷 조립
                    formatted_str = f"📘 **{title}**\n"
                    formatted_str += f"📊 {curr_price} ({curr_mc}) ➡️ {tgt_price} ({tgt_mc})\n\n"
                    formatted_str += f"{points_str}"
                    return formatted_str

                df['투자포인트_표시'] = df.apply(format_invest_points, axis=1)

                # 현재시총, 목표시총, 원래 목표주가는 화면에서 숨기기
                display_df = df[['종목명', '증권사', 'Upside', '평가방식', '투자포인트_표시']].copy()
                display_df = display_df.astype(str)

                st.markdown("<br>", unsafe_allow_html=True)

                for _, row in df.iterrows():
                    # 1. 데이터 추출
                    title = row.get('레포트 제목', '제목 없음')
                    curr_price = row.get('현재가', 'N/A')
                    curr_mc = row.get('현재시총', 'N/A')
                    tgt_price = row.get('목표주가', 'N/A')
                    tgt_mc = row.get('목표시총', 'N/A')
                    
                    upside_val = row.get('Upside_num', 0)
                    upside_str = row.get('Upside', 'N/A')

                    # 2. Upside 높이에 따른 불꽃 이모지 (제목 강조용)
                    if pd.isna(upside_val):
                        fire = ""
                    elif upside_val >= 50:
                        fire = "🔥🔥🔥"
                    elif upside_val >= 30:
                        fire = "🔥🔥"
                    elif upside_val > 0:
                        fire = "🔥"
                    else:
                        fire = "❄️"

                    # 3. 기열님이 요청하신 완벽한 한 줄 요약 타이틀!
                    expander_title = f"{row['종목명']} ({row['증권사']}) | {title} | 🚀 Upside: {upside_str} {fire} | 📊 {curr_price} ({curr_mc}) ➡️ {tgt_price} ({tgt_mc})"
                    
                    # 4. 펼치기(Expander) UI 생성
                    with st.expander(expander_title):
                        # 내부 Upside 색상 처리 (높을수록 붉은색)
                        if upside_val >= 50:
                            color = "#FF0000" # 진한 빨강
                        elif upside_val >= 30:
                            color = "#FF4500" # 주황-빨강
                        elif upside_val > 0:
                            color = "#FFA500" # 주황
                        else:
                            color = "#808080" # 회색
                            
                        # 평가방식 및 컬러 풀 Upside 출력
                        st.markdown(f"<h3 style='color: {color}; margin-bottom: 5px;'>🚀 목표수익률: {upside_str}</h3>", unsafe_allow_html=True)
                        st.markdown(f"**💡 평가 방식:** {row.get('평가방식', 'N/A')}")
                        st.markdown("---")
                        
                        # 5. 핵심 투자 포인트 리스트업
                        st.markdown("**📝 핵심 투자 포인트**")
                        points = row.get('투자포인트', [])
                        if isinstance(points, list):
                            for p in points:
                                st.markdown(f"- {p}")
                        else:
                            st.markdown(f"- {points}")
            else:
                st.warning("해당 시간대에 분석된 종목 데이터가 없습니다.")
    else:
        st.info("실행된 배치 파일이 없습니다. batch_report.py를 먼저 실행해 주세요.")
