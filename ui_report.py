import streamlit as st
import pandas as pd
import json
import os
import glob
from datetime import datetime

# 💡 파일명에 따라 UI 표시 이름을 다르게 만들어주는 함수
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
        # 시간 포맷 예쁘게 변환 (20260414_0730 -> 2026-04-14 07:30)
        dt = datetime.strptime(clean_name, "%Y%m%d_%H%M")
        return f"{prefix} 리포트 ({dt.strftime('%Y-%m-%d %H:%M')})"
    except:
        return base_name

def render_report_summary():
    st.markdown("<div class='main-title'>📊 증권사 레포트 AI 요약</div>", unsafe_allow_html=True)
    
    # 전용 폴더(broker_report)에서 모든 json 파일 불러오기
    report_files = sorted(glob.glob("data/broker_report/*.json"), reverse=True)
    
    if report_files:
        selected_file = st.selectbox("분석 데이터 선택", report_files, format_func=format_json_name)
        
        with open(selected_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            results = data.get('results', [])
            analysis_time = data.get('analysis_time', '알 수 없음')
            
            # 💡 [요청사항 반영] 데이터 통계 표기
            st.caption(f"📅 레포트 추출 및 분석 시점: {analysis_time} | 📊 총 {len(results)}개의 레포트가 추출되었습니다.")
            
            if results:
                df = pd.DataFrame(results)
                
                # 1. 정렬 및 비교용 숫자 컬럼 생성
                df['Upside_num'] = pd.to_numeric(df['Upside'], errors='coerce')
                
                # 💡 [요청사항 반영] 당일 가장 높은 Upside 종목 하이라이트
                if not df['Upside_num'].isna().all():
                    top_row = df.loc[df['Upside_num'].idxmax()]
                    if top_row['Upside_num'] > 0:
                        st.success(f"🚀 **오늘의 최고 기대 종목:** {top_row['종목명']} (기대수익률: **{top_row['Upside_num']:.1f}%** | {top_row['증권사']})")
                
                st.divider()

                # 💡 [요청사항 반영] Upside 높은 순으로 기본 정렬
                df = df.sort_values(by='Upside_num', ascending=False)

                # 2. Upside 포맷팅 (결측치는 N/A, 숫자는 %)
                df['Upside'] = df['Upside_num'].apply(lambda x: "N/A" if pd.isna(x) else f"{x:.1f}%")

                # 3. 투자포인트 리스트 변환
                df['투자포인트_표시'] = df['투자포인트'].apply(
                    lambda x: "\n".join([f"• {p}" for p in x]) if isinstance(x, list) else str(x)
                )

                # 4. Streamlit 타입 에러 방지 (표에 띄울 컬럼 전체를 문자열로 박제)
                display_df = df[['종목명', '증권사', '현재시총', '목표시총', 'Upside', '평가방식', '투자포인트_표시']].copy()
                display_df = display_df.astype(str)

                # 5. 최종 데이터 테이블 출력
                st.data_editor(
                    display_df,
                    column_config={
                        "투자포인트_표시": st.column_config.Column("투자포인트", width="large")
                    },
                    use_container_width=True,
                    hide_index=True
                )
            else:
                st.warning("해당 시간대에 분석된 종목 데이터가 없습니다.")
    else:
        st.info("실행된 배치 파일이 없습니다. batch_report.py를 먼저 실행해 주세요.")
