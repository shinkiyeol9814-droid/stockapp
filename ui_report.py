import streamlit as st
import pandas as pd
import json
import os
import glob

# 💡 [핵심] 파일명이 아닌 JSON 내부 데이터를 읽어 예쁜 옵션 리스트를 만드는 함수
def get_report_options():
    file_list = glob.glob('data/broker_report/*.json')
    options = {}

    for file_path in file_list:
        try:
            # 파일 속을 열어서 메타데이터 확인
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            r_type = data.get('report_type', '')
            a_time = data.get('analysis_time', '')
            
            if not r_type or not a_time:
                continue 

            # 한글 패치 및 예쁜 이름 조립
            kor_type = "☀️ 정규 레포트" if "Regular" in r_type else "🌙 전일 레포트" if "Previous" in r_type else "기타 레포트"
            display_name = f"{kor_type} ({a_time} 업데이트)"
            
            # 정렬을 위해 시간 데이터도 함께 임시 저장
            options[display_name] = {
                "path": file_path,
                "time": a_time
            }
        except Exception:
            continue

    # analysis_time을 기준으로 내림차순(최신순) 정렬
    sorted_options = dict(sorted(options.items(), key=lambda item: item[1]['time'], reverse=True))
    
    # UI 셀렉트박스에서 쓰기 편하게 { "예쁜이름": "파일경로" } 형태로 반환
    final_options = {k: v['path'] for k, v in sorted_options.items()}
    return final_options


def render_report_summary():
    st.markdown("<div class='main-title'>📊 증권사 레포트 AI 요약</div>", unsafe_allow_html=True)
    
    # 💡 새로 만든 함수로 옵션 딕셔너리 가져오기
    report_options = get_report_options()
    
    if report_options:
        # 💡 예쁜 이름(딕셔너리의 키)들을 셀렉트박스에 나열
        selected_name = st.selectbox("분석 데이터 선택", list(report_options.keys()))
        
        # 💡 유저가 선택한 이름의 진짜 파일 경로를 꺼내옴
        selected_file = report_options[selected_name]
        
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

                # HTML 커스텀 아코디언 카드 렌더링
                for _, row in df.iterrows():
                    title = row.get('레포트 제목', '제목 없음')
                    report_date = row.get('발행일자', 'N/A') 
                    curr_price = row.get('현재가', 'N/A')
                    curr_mc = row.get('현재시총', 'N/A')
                    tgt_price = row.get('목표주가', 'N/A')
                    tgt_mc = row.get('목표시총', 'N/A')
                    
                    upside_val = row.get('Upside_num', 0)
                    upside_str = row.get('Upside_str', 'N/A')

                    # Upside 수치에 따른 색상 설정
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
                        f"<span style='font-size: 16px; font-weight: bold; color: #222;'>{row['종목명']}</span> "
                        f"<span style='font-size: 13px; color: #666;'>({row['증권사']})</span>"
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
