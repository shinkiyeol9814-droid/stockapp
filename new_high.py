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
GITHUB_BRANCH = "dev" # 캡처화면 기준 브랜치명

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
    """수정된 코멘트를 GitHub 저장소(dev 브랜치)에 직접 커밋"""
    github_token = st.secrets.get("GH_PAT")
    if not github_token:
        st.error("보안 키 누락: Streamlit Secrets에 GH_PAT가 설정되어 있지 않습니다.")
        return False

    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{file_path}"
    headers = {
        "Authorization": f"token {github_token}", 
        "Accept": "application/vnd.github.v3+json"
    }
    
    # 덮어쓰기를 위해 기존 파일의 SHA 해시값 조회
    res = requests.get(url, headers=headers, params={"ref": GITHUB_BRANCH})
    sha = res.json().get('sha') if res.status_code == 200 else None
    
    payload = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode("utf-8"),
        "branch": GITHUB_BRANCH
    }
    if sha: payload["sha"] = sha
    
    put_res = requests.put(url, headers=headers, json=payload)
    return put_res.status_code in [200, 201]

def render_new_high_menu():
    st.markdown("<div style='font-size: 1.4rem; font-weight: bold;'>🚀 주도주 모멘텀 & 코멘트 관리 (V1.5.0)</div>", unsafe_allow_html=True)
    
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
    
    # --- [신규 필터링 UI] ---
    st.markdown("### 🔍 필터 설정")
    periods = ["전체", "1년(52주) 신고가", "6개월 신고가", "3개월 신고가", "1주 신고가"]
    selected_periods = st.multiselect("보고 싶은 신고가 기간을 선택하세요 (미선택 시 전체)", periods, default=["전체"])

    # 필터링 로직
    if "전체" in selected_periods or not selected_periods:
        filtered_df = all_df.copy()
    else:
        filtered_df = all_df[all_df['돌파기간'].isin(selected_periods)].copy()
    # -----------------------

    # 화면 표시용 가공
    disp_df = filtered_df.copy()
    disp_df['현재가'] = disp_df['현재가'].apply(lambda x: f"{int(x):,}원" if str(x).isdigit() else x)
    disp_df['등락률'] = disp_df['등락률'].apply(lambda x: f"{float(x):.2f}%" if not isinstance(x, str) else x)
    
    st.markdown(f"### 📝 분석 결과 리스트 ({len(filtered_df)}건)")
    
    edited_df = st.data_editor(
        disp_df[['종목명', '현재가', '등락률', '돌파기간', '추정 사유', '코드']],
        column_config={
            "추정 사유": st.column_config.TextColumn("추정 사유 (수정 가능)", width="large"),
            "코드": None 
        },
        disabled=['종목명', '현재가', '등락률', '돌파기간'],
        hide_index=True, 
        use_container_width=True, 
        key="high_price_editor"
    )

    if st.button("💾 수정한 코멘트 GitHub에 최종 저장", type="primary"):
        edited_records = edited_df.to_dict(orient='records')
        comment_map = {row['코드']: row['추정 사유'] for row in edited_records}
        
        # 필터링된 상태여도 원본 report_data의 모든 결과를 업데이트하여 전체 데이터 보존
        for item in report_data['results']:
            if item['코드'] in comment_map:
                item['추정 사유'] = comment_map[item['코드']]
                
        json_content = json.dumps(report_data, indent=4, ensure_ascii=False)
        
        with st.spinner("GitHub dev 브랜치에 업데이트 중..."):
            success = save_to_github(f"data/{file_name}", json_content, f"Update AI comments for {file_name}")
            if success:
                st.success("✅ 변경사항이 성공적으로 반영되었습니다!")
            else:
                st.error("❌ 저장 실패.")

    st.markdown("---")
    st.markdown("### 🔍 주요 뉴스 모니터링")
    for _, row in edited_df.iterrows():
        original_row = next((item for item in report_data['results'] if item['코드'] == row['코드']), {})
        with st.expander(f"[{row['종목명']}] {row['돌파기간']} 뉴스 확인"):
            news_text = original_row.get('최신뉴스', '관련 뉴스 없음')
            st.write(f"**📰 뉴스 헤드라인:**\n{news_text}")
