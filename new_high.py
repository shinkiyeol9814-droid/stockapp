import streamlit as st
import pandas as pd
import json
import os
import requests
import base64

GITHUB_REPO = "shinkiyeol9814-droid/stockapp"
GITHUB_BRANCH = "dev" 

# 💡 [수정] 전체 리포트 파일 목록을 가져오는 함수
def get_all_reports():
    data_path = "data/"
    if not os.path.exists(data_path): return []
    files = [f for f in os.listdir(data_path) if f.startswith("report_") and f.endswith(".json")]
    return sorted(files, reverse=True) # 최신순 정렬

def save_to_github(file_path, content, message):
    github_token = st.secrets.get("GH_PAT")
    if not github_token:
        return False, "Streamlit Secrets에 GH_PAT (GitHub 토큰)가 없습니다."

    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{file_path}"
    headers = {
        "Authorization": f"token {github_token}", 
        "Accept": "application/vnd.github.v3+json"
    }
    
    res = requests.get(url, headers=headers, params={"ref": GITHUB_BRANCH})
    sha = res.json().get('sha') if res.status_code == 200 else None
    
    payload = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode("utf-8"),
        "branch": GITHUB_BRANCH
    }
    if sha: payload["sha"] = sha
    
    put_res = requests.put(url, headers=headers, json=payload)
    if put_res.status_code in [200, 201]: return True, "성공"
    else: return False, put_res.text 

def render_new_high_menu():
    st.markdown("<div style='font-size: 1.4rem; font-weight: bold;'>🚀 주도주 모멘텀 & 코멘트 관리 (V2.0)</div>", unsafe_allow_html=True)
    
    report_files = get_all_reports()
    if not report_files:
        st.warning("분석된 데이터가 없습니다. 장 마감 후 자동 배치가 실행될 때까지 기다려주세요.")
        return

    # 💡 [추가] 분석 일자 선택 UI
    def format_filename(f):
        # report_20260402_1530.json -> 2026년 04월 02일 15:30
        try:
            date_part = f.replace("report_", "").replace(".json", "")
            return f"{date_part[:4]}년 {date_part[4:6]}월 {date_part[6:8]}일 {date_part[9:11]}:{date_part[11:13]} 분석본"
        except: return f
        
    selected_file = st.selectbox("📅 분석 일자 선택", report_files, format_func=format_filename)
    
    # 선택한 파일 읽기
    with open(f"data/{selected_file}", "r", encoding="utf-8") as f:
        report_data = json.load(f)

    analysis_time = report_data.get('analysis_time', 'N/A')
    exec_time = report_data.get('execution_time', '알 수 없음')
    st.success(f"✅ **기준 시점:** {analysis_time} &nbsp;&nbsp;|&nbsp;&nbsp; ⏱️ **소요시간:** {exec_time}")
    
    if not report_data.get('results'):
        st.write("조건을 만족하는 주도주가 없습니다.")
        return

    all_df = pd.DataFrame(report_data['results'])
    
    # 💡 과거 JSON 파일 호환성 (시가총액 컬럼이 없으면 0으로 세팅)
    if '시가총액' not in all_df.columns:
        all_df['시가총액'] = 0

    st.markdown("### 🔍 필터 설정")
    col1, col2 = st.columns(2)
    with col1:
        # 💡 [수정] 1주 신고가 제거
        periods = ["전체", "1년(52주) 신고가", "6개월 신고가", "3개월 신고가"]
        selected_period = st.selectbox("📌 신고가 기간", periods, index=0)
    with col2:
        # 💡 [추가] 시가총액 필터 (기본값 중소형)
        marcap_filters = ["500억 ~ 5,000억 미만 (중소형)", "5,000억 이상 (대형)", "전체"]
        selected_marcap = st.selectbox("💰 시가총액", marcap_filters, index=0)

    # 1차 필터링: 돌파기간
    if selected_period != "전체":
        filtered_df = all_df[all_df['돌파기간'] == selected_period].copy()
    else:
        filtered_df = all_df.copy()

    # 2차 필터링: 시가총액
    if selected_marcap == "500억 ~ 5,000억 미만 (중소형)":
        filtered_df = filtered_df[(filtered_df['시가총액'] >= 50000000000) & (filtered_df['시가총액'] < 500000000000)]
    elif selected_marcap == "5,000억 이상 (대형)":
        filtered_df = filtered_df[filtered_df['시가총액'] >= 500000000000]

    disp_df = filtered_df.copy()
    
    # ---------------------------------------------------------
    # ✨ 데이터 포맷팅 및 링크 처리
    # ---------------------------------------------------------
    # 현재가는 화면에서 제외하므로 포맷팅 삭제
    disp_df['등락률'] = disp_df['등락률'].apply(lambda x: f"{float(x):.2f}%" if not isinstance(x, str) else x)
    disp_df['시가총액'] = disp_df['시가총액'].apply(lambda x: f"{int(x) // 100000000:,}억" if pd.notnull(x) and x > 0 else "N/A")
    
    # (1) 종목명 마크다운 링크 처리 (가치평가 시뮬레이터 연동)
    disp_df['종목명'] = disp_df.apply(lambda x: f"[{x['종목명']}](/?stock_code={str(x['코드']).zfill(6)})", axis=1)

    # (2) 기사 원문 마크다운 링크 처리 ('기사'라는 텍스트에 하이퍼링크 적용)
    disp_df['최신뉴스_링크'] = disp_df.get('최신뉴스_링크', "")
    disp_df['기사'] = disp_df['최신뉴스_링크'].apply(lambda x: f"[기사]({x})" if pd.notnull(x) and str(x).strip() != "" else "")
    
    st.markdown(f"### 📝 통합 분석 결과 ({len(filtered_df)}건)")
    st.caption("💡 '추정 사유' 셀을 더블클릭하여 수정 후, 반드시 **`Enter` 키**를 누르고 하단의 저장 버튼을 눌러주세요. / 종목명을 클릭하면 시뮬레이터로 이동합니다.")
    
    # ---------------------------------------------------------
    # ✨ Data Editor 렌더링 (불필요한 컬럼 제거 및 재배치)
    # ---------------------------------------------------------
    edited_df = st.data_editor(
        disp_df[['종목명', '등락률', '시가총액', '돌파기간', '추정 사유', '최신뉴스', '기사', '코드']],
        column_config={
            "종목명": st.column_config.TextColumn("종목명 (분석이동)"), # 마크다운 자동 렌더링
            "추정 사유": st.column_config.TextColumn("추정 사유 (수정 가능)", width="large"),
            "최신뉴스": st.column_config.TextColumn("최신뉴스 헤드라인", width="medium"),
            "시가총액": st.column_config.TextColumn("시가총액"),
            "기사": st.column_config.TextColumn("원문보기"), # 마크다운 자동 렌더링
            "코드": None  # 데이터 매핑용으로 존재하되 화면엔 숨김
        },
        disabled=['종목명', '등락률', '시가총액', '돌파기간', '최신뉴스', '기사'], 
        hide_index=True, 
        use_container_width=True, 
        key="high_price_editor"
    )

    if st.button("저장", type="primary"):
        edited_records = edited_df.to_dict(orient='records')
        comment_map = {row['코드']: row['추정 사유'] for row in edited_records}
        
        for item in report_data['results']:
            if item['코드'] in comment_map:
                item['추정 사유'] = comment_map[item['코드']]
                
        json_content = json.dumps(report_data, indent=4, ensure_ascii=False)
        
        with st.spinner("GitHub 서버에 덮어쓰는 중..."):
            success, error_msg = save_to_github(f"data/{selected_file}", json_content, f"Update comments via Web UI")
            if success:
                st.success("✅ 변경사항이 안전하게 저장되었습니다!")
            else:
                st.error(f"❌ 저장 실패: {error_msg}")

    st.markdown("---")
    st.markdown("### 🔍 주요 뉴스 모니터링 (최대 3개)")
    for _, row in edited_df.iterrows():
        original_row = next((item for item in report_data['results'] if item['코드'] == row['코드']), {})
        
        # 💡 [버그 방지] edited_df의 '종목명'은 마크다운 링크 문법이 포함되어 있어 
        # 그대로 쓰면 제목이 깨지므로 original_row에서 순수 텍스트 종목명을 가져옵니다.
        corp_name_clean = original_row.get('종목명', '알 수 없음')
        
        with st.expander(f"[{corp_name_clean}] 주요 뉴스 살펴보기"):
            news_md = original_row.get('뉴스목록', '관련 뉴스 없음')
            st.markdown(news_md)
