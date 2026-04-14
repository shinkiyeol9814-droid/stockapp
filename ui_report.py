import streamlit as st
import pandas as pd
import json
import os
import glob

def render_report_summary():
    st.markdown("<div class='main-title'>📊 증권사 레포트 AI 요약 (Pre-Market)</div>", unsafe_allow_html=True)
    
    # --- 1. 저장된 JSON 파일 불러오기 ---
    report_files = sorted(glob.glob("data/report_summary_*.json"), reverse=True)
    
    if report_files:
        selected_file = st.selectbox("분석 결과 선택", report_files, format_func=lambda x: os.path.basename(x))
        
        with open(selected_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            st.caption(f"🕒 분석 시점: {data.get('analysis_time', '알 수 없음')}")
            
            if data.get('results'):
                df = pd.DataFrame(data['results'])
                
                # 💡 [핵심 수정] Streamlit 타입 에러 완벽 차단 로직
                
                # 1. 정렬을 위해 진짜 숫자형(float) 컬럼을 임시로 만듭니다. (N/A는 NaN 처리됨)
                df['Upside_num'] = pd.to_numeric(df['Upside'], errors='coerce')
                
                # 2. 숫자를 기준으로 정렬 (종목명 묶기 -> Upside 높은 순)
                df = df.sort_values(['종목명', 'Upside_num'], ascending=[True, False])

                # 3. 화면에 보여줄 Upside 컬럼을 "15.2%" 형태의 '문자열'로 완전히 고정!
                df['Upside'] = df['Upside_num'].apply(lambda x: "N/A" if pd.isna(x) else f"{x:.1f}%")

                # 4. 투자포인트 리스트를 보기 좋은 문자열로 변환
                df['투자포인트_표시'] = df['투자포인트'].apply(
                    lambda x: "\n".join([f"• {p}" for p in x]) if isinstance(x, list) else str(x)
                )

                # 5. 컬럼 설정 및 출력 (NumberColumn 삭제하여 에러 원천 차단)
                st.data_editor(
                    df[['종목명', '증권사', '현재시총', '목표시총', 'Upside', '평가방식', '투자포인트_표시']],
                    column_config={
                        # Upside는 문자열이 되었으므로 설정 제외! (알아서 예쁘게 나옴)
                        "투자포인트_표시": st.column_config.TextColumn("투자포인트", width="large"),
                        "현재시총": st.column_config.TextColumn("현재시총"),
                        "목표시총": st.column_config.TextColumn("목표시총")
                    },
                    use_container_width=True,
                    hide_index=True
                )
            else:
                st.warning("분석된 종목 데이터가 없습니다.")
    else:
        st.info("실행된 배치 파일이 없습니다. batch_report.py를 먼저 실행해 주세요.")

    st.divider()
    
    # --- 2. 데이터 수동 입력 UI ---
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
