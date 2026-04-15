import streamlit as st
import pandas as pd
import json
import os
import glob
from datetime import datetime

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

def render_report_summary():
    st.markdown("<div class='main-title'>📊 증권사 레포트 AI 요약</div>", unsafe_allow_html=True)
    
    report_files = sorted(glob.glob("data/broker_report/*.json"), reverse=True)
    
    if report_files:
        selected_file = st.selectbox("분석 데이터 선택", report_files, format_func=format_json_name)
        
        with open(selected_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            results = data.get('results', [])
            analysis_time = data.get('analysis_time', '알 수 없음')
            
            st.caption(f"📅 레포트 추출 및 분석 시점: {analysis_time} | 📊 총 {len(results)}개의 레포트가 추출되었습니다.")
            
            if results:
                df = pd.DataFrame(results)
                
                df['Upside_num'] = pd.to_numeric(df['Upside'], errors='coerce')
                
                if not df['Upside_num'].isna().all():
                    top_row = df.loc[df['Upside_num'].idxmax()]
                    if top_row['Upside_num'] > 0:
                        st.success(f"🚀 **오늘의 최고 기대 종목:** {top_row['종목명']} (기대수익률: **{top_row['Upside_num']:.1f}%** | {top_row['증권사']})")
                
                st.divider()

                df = df.sort_values(by='Upside_num', ascending=False)
                df['Upside'] = df['Upside_num'].apply(lambda x: "N/A" if pd.isna(x) else f"{x:.1f}%")

                # 💡 [핵심] 투자포인트 컬럼에 데이터를 예쁘게 압축 조립하는 로직
                def format_invest_points(row):
                    title = row.get('레포트 제목', '제목 없음')
                    curr_price = row.get('현재가', 'N/A')
                    curr_mc = row.get('현재시총', 'N/A')
                    tgt_price = row.get('목표주가', 'N/A')
                    tgt_mc = row.get('목표시총', 'N/A')
                    
                    points = row.get('투자포인트', [])
                    points_str = "\n".join([f"• {p}" for p in points]) if isinstance(points, list) else str(points)
                    
                    # 마크다운 포맷으로 조립
                    formatted_str = f"📑 **{title}**\n\n"
                    formatted_str += f"💰 {curr_price} ({curr_mc}) ➡️ **{tgt_price}** ({tgt_mc})\n\n"
                    formatted_str += f"{points_str}"
                    return formatted_str

                # 새로운 조립된 컬럼 적용
                df['투자포인트_표시'] = df.apply(format_invest_points, axis=1)

                # 💡 [요청사항 반영] 현재시총, 목표시총 컬럼 제거하고 꼭 필요한 것만 남김
                display_df = df[['종목명', '증권사', 'Upside', '평가방식', '투자포인트_표시']].copy()
                display_df = display_df.astype(str)

                st.data_editor(
                    display_df,
                    column_config={
                        "투자포인트_표시": st.column_config.TextColumn("레포트 요약 및 투자포인트", width="large")
                    },
                    use_container_width=True,
                    hide_index=True
                )
            else:
                st.warning("해당 시간대에 분석된 종목 데이터가 없습니다.")
    else:
        st.info("실행된 배치 파일이 없습니다. batch_report.py를 먼저 실행해 주세요.")
