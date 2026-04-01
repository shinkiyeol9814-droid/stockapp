import streamlit as st
import pandas as pd
import json
import os
import requests
import base64

# ---------------------------------------------------------
# GitHub 저장소 설정
# ---------------------------------------------------------
GITHUB_REPO = "shinkiyeol9814-droid/stockapp"
GITHUB_BRANCH = "dev" 

def get_latest_report():
    """data/ 폴더에서 가장 최신 JSON 리포트 파일을 로드"""
    data_path = "data/"
    if not os.path.exists(data_path): return None, None
    files = [f for f in os.listdir(data_path) if f.startswith("report_") and f.endswith(".json")]
    if not files: return None, None
    latest_file = sorted(files, reverse=True)[0]
    with open(os.path.join(data_path, latest_file), "r", encoding="utf-8") as f:
        return json.load(f), latest_file

def save_to_github(file_path, content, message):
    """수정된 코멘트를 GitHub 저장소에 직접 커밋 및 에러 반환"""
    github_token = st.secrets.get("GH_PAT")
    if not github_token:
        return False, "Streamlit Secrets에 GH_PAT (GitHub 토큰)가 없습니다."

    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{file_path}"
    headers = {
        "Authorization": f"token {github_token}", 
        "Accept": "application/vnd.github.v3+json"
    }
    
    # 기존 파일 SHA 조회
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
        return False, put_res.text # 에러 상세 내용 리턴

def render_new_high_menu():
    st.markdown("<div style='font-size: 1.4rem; font-weight: bold;'>🚀 주도주 모멘텀 & 코멘트 관리 (V1.6.0)</div>", unsafe_allow_html=True)
    
    report_data, file_name = get_latest_report()
    
    if not report_data:
        st.warning("분석된 데이터가 없습니다. 장 마감 후 자동 배치가 실행될 때까지 기다려주세요.")
        return

    st.success(f"📅 **최종 분석 시점:** {report_data.get('analysis_time', 'N/A')}")
    
    if not report_data.get('results'):
        st.write("조건을 만족하는 주도주가 없습니다.")
        return

    # 원본 데이터 생성
    all_df = pd.DataFrame(report_data['results'])
    
    # --- [UI 개선: Select Box 형태의 필터링] ---
    st.markdown("### 🔍 필터 설정")
    periods = ["전체", "1년(52주) 신고가", "6개월 신고가", "3개월 신고가", "1주 신고가"]
    
    # multiselect 대신 단일 선택이 가능한 selectbox 사용
    selected_period = st.selectbox("보고 싶은 신고가 기간을 선택하세요", periods, index=0)

    # 필터링 로직
    if selected_period == "전체":
        filtered_df = all_df.copy()
    else:
        filtered_df = all_df[all_df['돌파기간'] == selected_period].copy()
    # ----------------------------------------

    # 화면 표시용 데이터 가공
    disp_df = filtered_df.copy()
    disp_df['현재가'] = disp_df['현재가'].apply(lambda x: f"{int(x):,}원" if str(x).isdigit() else x)
    disp_df['등락률'] = disp_df['등락률'].apply(lambda x: f"{float(x):.2f}%" if not isinstance(x, str) else x)
    
    st.markdown(f"### 📝 통합 분석 결과 ({len(filtered_df)}건)")
    st.caption("💡 '추정 사유' 셀을 더블클릭하여 수정 후, 반드시 **`Enter` 키**를 누르고 하단의 저장 버튼을 눌러주세요.")
    
    # --- [UI 개선: 뉴스와 코멘트 통합 뷰] ---
    # 최신뉴스 컬럼을 에디터에 함께 배치하여 한눈에 비교 가능하게 수정
    edited_df = st.data_editor(
        disp_df[['종목명', '현재가', '등락률', '돌파기간', '추정 사유', '최신뉴스', '코드']],
        column_config={
            "추정 사유": st.column_config.TextColumn("추정 사유 (수정 가능)", width="large"),
            "최신뉴스": st.column_config.TextColumn("최신뉴스 헤드라인", width="large"),
            "코드": None 
        },
        disabled=['종목명', '현재가', '등락률', '돌파기간', '최신뉴스'], # 추정 사유 제외 모두 잠금
        hide_index=True, 
        use_container_width=True, 
        key="high_price_editor"
    )

    # --- [명칭 변경 및 에러 트래킹 추가] ---
    if st.button("저장", type="primary"):
        edited_records = edited_df.to_dict(orient='records')
        comment_map = {row['코드']: row['추정 사유'] for row in edited_records}
        
        # 전체 데이터 보존 업데이트
        for item in report_data['results']:
            if item['코드'] in comment_map:
                item['추정 사유'] = comment_map[item['코드']]
                
        json_content = json.dumps(report_data, indent=4, ensure_ascii=False)
        
        with st.spinner("GitHub 서버에 덮어쓰는 중..."):
            success, error_msg = save_to_github(f"data/{file_name}", json_content, f"Update comments via Web UI")
            
            if success:
                st.success("✅ 변경사항이 안전하게 저장되었습니다!")
            else:
                # 권한 등의 에러가 발생하면 어떤 이유인지 화면에 출력해 줌
                st.error(f"❌ 저장 실패: {error_msg}")
