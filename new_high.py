import streamlit as st
import pandas as pd
import json
import os
import requests
import base64

# ---------------------------------------------------------
# GitHub 저장소 설정 (기열님 환경 맞춤형)
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
    """수정된 코멘트를 GitHub 저장소(main 브랜치)에 직접 커밋"""
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
    st.markdown("<div style='font-size: 1.4rem; font-weight: bold;'>🚀 주도주 모멘텀 & 코멘트 관리 (V1.4.0)</div>", unsafe_allow_html=True)
    st.info("💡 매일 15:40, 20:20에 시총 1,000억 이상 종목을 전수 분석하며, 텔레그램/뉴스 기반 AI 요약본을 제공합니다.")
    
    report_data, file_name = get_latest_report()
    
    if not report_data:
        st.warning("분석된 데이터가 없습니다. 장 마감 후 자동 배치가 실행될 때까지 기다려주세요.")
        return

    st.success(f"📅 **최종 분석 시점:** {report_data.get('analysis_time', 'N/A')} (소스: 네이버뉴스+텔레그램+Gemini)")
    
    if not report_data.get('results'):
        st.write("선택된 기간 내 조건을 만족하는 주도주가 없습니다.")
        return

    df = pd.DataFrame(report_data['results'])
    
    # 데이터 포맷팅
    df['현재가'] = df['현재가'].apply(lambda x: f"{int(x):,}원")
    df['등락률'] = df['등락률'].apply(lambda x: f"{x:.2f}%")
    
    st.markdown("### 📝 분석 결과 리스트")
    st.caption("🖱️ '추정 사유(AI 코멘트)' 셀을 더블클릭하여 내용을 직접 수정할 수 있습니다.")
    
    # 데이터 에디터 UI 적용
    edited_df = st.data_editor(
        df[['종목명', '현재가', '등락률', '돌파기간', '추정 사유', '코드']],
        column_config={
            "추정 사유": st.column_config.TextColumn("추정 사유 (수정 가능)", width="large"),
            "돌파기간": st.column_config.TextColumn("신고가 기준", width="medium"),
            "코드": None  # 코드는 UI에서 숨김
        },
        hide_index=True, 
        use_container_width=True, 
        key="high_price_editor"
    )

    # 코멘트 저장 버튼
    if st.button("💾 수정한 코멘트 GitHub에 최종 저장", type="primary"):
        # 수정된 데이터프레임을 다시 딕셔너리로 변환
        updated_results = edited_df.to_dict(orient='records')
        
        # 누락된 데이터(뉴스, PER 등)를 원본에서 가져와서 다시 합침
        for i, row in enumerate(updated_results):
            original_row = next((item for item in report_data['results'] if item['코드'] == row['코드']), {})
            row['최신뉴스'] = original_row.get('최신뉴스', '')
            row['PER'] = original_row.get('PER', '')
            
        report_data['results'] = updated_results
        json_content = json.dumps(report_data, indent=4, ensure_ascii=False)
        
        with st.spinner("GitHub 메인 브랜치에 업데이트 중..."):
            success = save_to_github(f"data/{file_name}", json_content, f"Update AI comments for {file_name}")
            if success:
                st.success("✅ 변경사항이 성공적으로 반영되었습니다! (새로고침 시 적용)")
            else:
                st.error("❌ 저장 실패. Streamlit Secrets의 GH_PAT 권한 또는 네트워크 상태를 확인하세요.")

    st.markdown("---")
    st.markdown("### 🔍 주요 뉴스 모니터링")
    for _, row in edited_df.iterrows():
        original_row = next((item for item in report_data['results'] if item['종목명'] == row['종목명']), {})
        with st.expander(f"[{row['종목명']}] {row['돌파기간']} - {row['추정 사유']}"):
            st.write(f"**📰 요약 헤드라인:** {original_row.get('최신뉴스', 'N/A')}")
