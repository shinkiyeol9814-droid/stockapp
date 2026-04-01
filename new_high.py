import streamlit as st
import pandas as pd
import json
import os
import requests
import base64

GITHUB_REPO = "shinkiyeol9814-droid/stockapp"
GITHUB_BRANCH = "dev" 

def get_latest_report():
    data_path = "data/"
    if not os.path.exists(data_path): return None, None
    files = [f for f in os.listdir(data_path) if f.startswith("report_") and f.endswith(".json")]
    if not files: return None, None
    latest_file = sorted(files, reverse=True)[0]
    with open(os.path.join(data_path, latest_file), "r", encoding="utf-8") as f:
        return json.load(f), latest_file

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
    if put_res.status_code in [200, 201]:
        return True, "성공"
    else:
        return False, put_res.text 

def render_new_high_menu():
    st.markdown("<div style='font-size: 1.4rem; font-weight: bold;'>🚀 주도주 모멘텀 & 코멘트 관리 (V1.7.0)</div>", unsafe_allow_html=True)
    
    report_data, file_name = get_latest_report()
    
    if not report_data:
        st.warning("분석된 데이터가 없습니다. 장 마감 후 자동 배치가 실행될 때까지 기다려주세요.")
        return

    # 💡 [해결됨] 소요시간 표시
    analysis_time = report_data.get('analysis_time', 'N/A')
    exec_time = report_data.get('execution_time', '알 수 없음')
    st.success(f"📅 **최종 분석 시점:** {analysis_time} &nbsp;&nbsp;|&nbsp;&nbsp; ⏱️ **소요시간:** {exec_time}")
    
    if not report_data.get('results'):
        st.write("조건을 만족하는 주도주가 없습니다.")
        return

    all_df = pd.DataFrame(report_data['results'])
    
    st.markdown("### 🔍 필터 설정")
    periods = ["전체", "1년(52주) 신고가", "6개월 신고가", "3개월 신고가", "1주 신고가"]
    selected_period = st.selectbox("보고 싶은 신고가 기간을 선택하세요", periods, index=0)

    if selected_period == "전체":
        filtered_df = all_df.copy()
    else:
        filtered_df = all_df[all_df['돌파기간'] == selected_period].copy()

    disp_df = filtered_df.copy()
    disp_df['현재가'] = disp_df['현재가'].apply(lambda x: f"{int(x):,}원" if str(x).isdigit() else x)
    disp_df['등락률'] = disp_df['등락률'].apply(lambda x: f"{float(x):.2f}%" if not isinstance(x, str) else x)
    
# 💡 [추가] 과거 데이터 로드 시 '최신뉴스_링크' 컬럼이 없어서 에러나는 것 방지
    disp_df['최신뉴스_링크'] = disp_df.get('최신뉴스_링크', "")
    
    st.markdown(f"### 📝 통합 분석 결과 ({len(filtered_df)}건)")
    st.caption("💡 '추정 사유' 셀을 더블클릭하여 수정 후, 반드시 **`Enter` 키**를 누르고 하단의 저장 버튼을 눌러주세요.")
    
    # 💡 [수정] '최신뉴스_링크' 컬럼 추가 및 LinkColumn 세팅
    edited_df = st.data_editor(
        disp_df[['종목명', '현재가', '등락률', '돌파기간', '추정 사유', '최신뉴스', '최신뉴스_링크', '코드']],
        column_config={
            "추정 사유": st.column_config.TextColumn("추정 사유 (수정 가능)", width="large"),
            "최신뉴스": st.column_config.TextColumn("최신뉴스 헤드라인", width="medium"),
            "최신뉴스_링크": st.column_config.LinkColumn(
                "원문 링크", 
                display_text="기사 보기 🔗", 
                width="small"
            ),
            "코드": None 
        },
        disabled=['종목명', '현재가', '등락률', '돌파기간', '최신뉴스', '최신뉴스_링크'], 
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
            success, error_msg = save_to_github(f"data/{file_name}", json_content, f"Update comments via Web UI")
            
            if success:
                st.success("✅ 변경사항이 안전하게 저장되었습니다!")
            else:
                st.error(f"❌ 저장 실패: {error_msg}")

    st.markdown("---")
    st.markdown("### 🔍 주요 뉴스 모니터링 (최대 5개)")
    for _, row in edited_df.iterrows():
        original_row = next((item for item in report_data['results'] if item['코드'] == row['코드']), {})
        with st.expander(f"[{row['종목명']}] 뉴스 살펴보기"):
            # 💡 [해결됨] 하이퍼링크가 걸린 마크다운 리스트 출력
            news_md = original_row.get('뉴스목록', '관련 뉴스 없음')
            # 만약 구형 데이터라 '뉴스목록'이 없다면 기존 텍스트 렌더링
            if news_md == '관련 뉴스 없음' and original_row.get('최신뉴스'):
                news_md = original_row.get('최신뉴스')
                
            st.markdown(news_md)
