import streamlit as st
import pandas as pd
import json
import os
import glob

def render_report_summary():
    st.markdown("<div class='main-title'>📊 증권사 레포트 AI 요약 (Pre-Market)</div>", unsafe_allow_html=True)
    
    report_files = sorted(glob.glob("data/report_summary_*.json"), reverse=True)
    
    if report_files:
        selected_file = st.selectbox("분석 결과 선택", report_files, format_func=lambda x: os.path.basename(x))
        
        with open(selected_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            st.caption(f"🕒 분석 시점: {data.get('analysis_time', '알 수 없음')}")
            
            if data.get('results'):
                df = pd.DataFrame(data['results'])
                
                # 1. 정렬용 숫자 컬럼 생성 및 정렬
                df['Upside_num'] = pd.to_numeric(df['Upside'], errors='coerce')
                df = df.sort_values(['종목명', 'Upside_num'], ascending=[True, False])

                # 2. Upside 포맷팅
                df['Upside'] = df['Upside_num'].apply(lambda x: "N/A" if pd.isna(x) else f"{x:.1f}%")

                # 3. 투자포인트 리스트 변환
                df['투자포인트_표시'] = df['투자포인트'].apply(
                    lambda x: "\n".join([f"• {p}" for p in x]) if isinstance(x, list) else str(x)
                )

                # 💡 [핵심 철벽 방어] 화면에 그릴 컬럼만 따로 빼서 '전부 문자열'로 강제 변환!
                # 옛날 JSON에 숫자가 들어있든 결측치가 있든 여기서 무조건 텍스트가 됩니다.
                display_df = df[['종목명', '증권사', '현재시총', '목표시총', 'Upside', '평가방식', '투자포인트_표시']].copy()
                display_df = display_df.astype(str)

                # 4. 출력 (TextColumn 충돌을 막기 위해 width 지정이 필요한 포인트만 세팅)
                st.data_editor(
                    display_df,
                    column_config={
                        "투자포인트_표시": st.column_config.Column("투자포인트", width="large")
                    },
                    use_container_width=True,
                    hide_index=True
                )
            else:
                st.warning("분석된 종목 데이터가 없습니다.")
    else:
        st.info("실행된 배치 파일이 없습니다. batch_report.py를 먼저 실행해 주세요.")

    st.divider()
    
    # --- 데이터 수동 입력 UI ---
    st.subheader("✍️ 리포트 수동 추가")
    st.info("💡 텔레그램 배치가 놓친 데이터나 직접 분석하신 내용을 기록하세요.")
    
    with st.form("manual_input_form", clear_on_submit=True):
        col1, col2 = st.columns(2)
        with col1:
            m_name = st.text_input("종목명")
            m_broker = st.text_input("증권사")
            m_target = st.number_input("목표주가", min_value=0, step=100)
        with col2:
            m_marcap = st.text_input("목표 시총 (ex: 5,400억)")
            m_method = st.text_input("평가방식 (ex: 25년 PER 12배)")
            m_upside = st.number_input("Upside (%)", step=0.1)
        
        m_points = st.text_area("투자 포인트 (엔터로 구분)")
        
        if st.form_submit_button("데이터 수동 저장"):
            st.success(f"✅ [{m_name}] 데이터가 대시보드에 임시 기록되었습니다.")
