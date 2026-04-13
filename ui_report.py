import streamlit as st
import pandas as pd

def render_report_summary():
    st.header("📊 증권사 레포트 AI 요약 (Pre-Market)")
    
    # 파일 선택 및 로드 (생략...)
    df = pd.DataFrame(data['results'])
    
    # 종목명/증권사 정렬 (종목명으로 묶기)
    df = df.sort_values(['종목명', 'Upside'], ascending=[True, False])

    # 투자포인트 리스트를 문자열로 변환
    df['투자포인트_표시'] = df['투자포인트'].apply(lambda x: "\n".join([f"• {p}" for p in x]))

    # 컬럼 설정 및 출력
    st.data_editor(
        df[['종목명', '증권사', '현재시총', '목표시총', 'Upside', '평가방식', '투자포인트_표시']],
        column_config={
            "Upside": st.column_config.NumberColumn("Upside", format="%.1f%%"),
            "투자포인트_표시": st.column_config.TextColumn("투자포인트", width="large"),
            "현재시총": st.column_config.TextColumn("현재시총"),
            "목표시총": st.column_config.TextColumn("목표시총")
        },
        use_container_width=True,
        hide_index=True
    )
