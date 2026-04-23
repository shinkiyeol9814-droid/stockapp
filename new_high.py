import streamlit as st
import pandas as pd
import json
import os
import requests
import base64

GITHUB_REPO = "shinkiyeol9814-droid/stockapp"
GITHUB_BRANCH = "main" 

# new_high.py 수정
def get_all_reports():
    data_path = "data/new_high/"
    if not os.path.exists(data_path): return []
    
    # 💡 "report_" 또는 "newhigh_"로 시작하는 파일 모두 가져오기
    files = [f for f in os.listdir(data_path) 
             if (f.startswith("report_") or f.startswith("newhigh_")) and f.endswith(".json")]
    
    return sorted(files, reverse=True)

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
    st.markdown("<div style='font-size: 1.4rem; font-weight: bold;'>🚀 신고가 트래킹 </div>", unsafe_allow_html=True)
    
    report_files = get_all_reports()
    if not report_files:
        st.warning("분석된 데이터가 없습니다. 장 마감 후 자동 배치가 실행될 때까지 기다려주세요.")
        return

    # 💡 아래 format_filename 함수도 수정 (둘 다 잘라낼 수 있게)
    def format_filename(f):
        try:
            # report_ 또는 newhigh_ 문구 삭제
            date_part = f.replace("report_", "").replace("newhigh_", "").replace(".json", "")
            return f"{date_part[:4]}년 {date_part[4:6]}월 {date_part[6:8]}일 {date_part[9:11]}:{date_part[11:13]} 분석본"
        except: return f
        
    selected_file = st.selectbox("📅 분석 일자 선택", report_files, format_func=format_filename)
    
    # ✅ 수정 후
    with open(f"data/new_high/{selected_file}", "r", encoding="utf-8") as f:
        report_data = json.load(f)

    analysis_time = report_data.get('analysis_time', 'N/A')
    exec_time = report_data.get('execution_time', '알 수 없음')
    st.success(f"✅ **기준 시점:** {analysis_time} &nbsp;&nbsp;|&nbsp;&nbsp; ⏱️ **소요시간:** {exec_time}")
    
    if not report_data.get('results'):
        st.write("조건을 만족하는 주도주가 없습니다.")
        return

    all_df = pd.DataFrame(report_data['results'])
    
    if '시가총액' not in all_df.columns:
        all_df['시가총액'] = 0

    st.markdown("### 🔍 필터 설정")
    col1, col2 = st.columns(2)
    with col1:
        periods = ["전체", "1년(52주) 신고가", "6개월 신고가", "3개월 신고가"]
        selected_period = st.selectbox("📌 신고가 기간", periods, index=0)
    with col2:
        marcap_filters = ["500억 ~ 5,000억 미만 (중소형)", "5,000억 이상 (대형)", "전체"]
        selected_marcap = st.selectbox("💰 시가총액", marcap_filters, index=0)

    if selected_period != "전체":
        filtered_df = all_df[all_df['돌파기간'] == selected_period].copy()
    else:
        filtered_df = all_df.copy()

    if selected_marcap == "500억 ~ 5,000억 미만 (중소형)":
        filtered_df = filtered_df[(filtered_df['시가총액'] >= 50000000000) & (filtered_df['시가총액'] < 500000000000)]
    elif selected_marcap == "5,000억 이상 (대형)":
        filtered_df = filtered_df[filtered_df['시가총액'] >= 500000000000]

    disp_df = filtered_df.copy()
    
    # ---------------------------------------------------------
    # ✨ 데이터 포맷팅 및 순수 URL 맵핑
    # ---------------------------------------------------------
    disp_df['등락률'] = disp_df['등락률'].apply(lambda x: f"{float(x):.2f}%" if not isinstance(x, str) else x)
    disp_df['시가총액'] = disp_df['시가총액'].apply(lambda x: f"{int(x) // 100000000:,}억" if pd.notnull(x) and x > 0 else "N/A")
    
    disp_df['가치평가_이동'] = "/?stock_code=" + disp_df['코드']
    disp_df['최신뉴스_링크'] = disp_df.get('최신뉴스_링크', "")
    
    st.markdown(f"### 📝 통합 분석 결과 ({len(filtered_df)}건)")
    st.caption("💡 '추정 사유' 셀을 더블클릭하여 수정 후, 반드시 **`Enter` 키**를 누르고 하단의 저장 버튼을 눌러주세요.")
    
    # ---------------------------------------------------------
    # ✨ Data Editor 렌더링 (돋보기 아이콘 적용 및 컬럼 순서 재배치)
    # ---------------------------------------------------------
    edited_df = st.data_editor(
        # 💡 [핵심] 요청하신 순서대로 컬럼 배열 완벽 변경! (버튼들은 맨 뒤로)
        disp_df[['종목명', '등락률', '시가총액', '추정 사유', '돌파기간', '최신뉴스', '가치평가_이동', '최신뉴스_링크', '코드']],
        column_config={
            "종목명": st.column_config.TextColumn("종목명", width="small"),
            "추정 사유": st.column_config.TextColumn("추정 사유 (수정 가능)", width="large"),
            "최신뉴스": st.column_config.TextColumn("최신뉴스 헤드라인", width="medium"),
            "시가총액": st.column_config.TextColumn("시가총액"),
            "가치평가_이동": st.column_config.LinkColumn(
                "분석", 
                display_text="🔍", 
                width="small"
            ),
            "최신뉴스_링크": st.column_config.LinkColumn(
                "원문", 
                display_text="🔍", 
                width="small"
            ),
            "코드": None  
        },
        disabled=['종목명', '등락률', '시가총액', '돌파기간', '최신뉴스', '가치평가_이동', '최신뉴스_링크'], 
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
        # ✅ 수정 후
        with st.spinner("GitHub 서버에 덮어쓰는 중..."):
            success, error_msg = save_to_github(f"data/new_high/{selected_file}", json_content, f"Update comments via Web UI")
            if success:
                st.success("✅ 변경사항이 안전하게 저장되었습니다!")
            else:
                st.error(f"❌ 저장 실패: {error_msg}")

    st.markdown("---")
    st.markdown("### 🔍 주요 뉴스 모니터링 (최대 3개)")
    for _, row in edited_df.iterrows():
        original_row = next((item for item in report_data['results'] if item['코드'] == row['코드']), {})
        corp_name_clean = original_row.get('종목명', '알 수 없음')
        
        with st.expander(f"[{corp_name_clean}] 주요 뉴스 살펴보기"):
            news_md = original_row.get('뉴스목록', '관련 뉴스 없음')
            st.markdown(news_md)
