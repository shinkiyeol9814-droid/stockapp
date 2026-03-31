import streamlit as st
import pandas as pd
import json
import os
import requests
import base64

GITHUB_REPO = "shinkiyeol9814-droid/stockapp" # ★ 본인의 깃허브 경로로 반드시 수정!
GITHUB_TOKEN = st.secrets.get("GH_PAT")

def get_latest_report():
    data_path = "data/"
    if not os.path.exists(data_path): return None, None
    files = [f for f in os.listdir(data_path) if f.startswith("report_") and f.endswith(".json")]
    if not files: return None, None
    latest_file = sorted(files, reverse=True)[0]
    with open(os.path.join(data_path, latest_file), "r", encoding="utf-8") as f:
        return json.load(f), latest_file

def save_to_github(file_path, content, message):
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{file_path}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    
    res = requests.get(url, headers=headers)
    sha = res.json().get('sha') if res.status_code == 200 else None
    
    payload = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode("utf-8"),
        "branch": "dev" # 대상 브랜치
    }
    if sha: payload["sha"] = sha
    
    put_res = requests.put(url, headers=headers, json=payload)
    return put_res.status_code in [200, 201]

def render_new_high_menu():
    st.markdown("<div style='font-size: 1.4rem; font-weight: bold;'>🚀 주도주 모멘텀 & 코멘트 관리</div>", unsafe_allow_html=True)
    
    report_data, file_name = get_latest_report()
    
    if not report_data:
        st.warning("분석된 데이터가 없습니다. GitHub Actions 배치가 실행될 때까지 기다려주세요.")
        return

    st.info(f"📅 최종 분석 시점: {report_data.get('analysis_time', 'N/A')} (소스: 텔레그램 + Gemini)")
    
    df = pd.DataFrame(report_data['results'])
    
    st.markdown("### 📝 분석 결과 리스트")
    st.caption("💡 '추정 사유' 셀을 더블클릭하여 내용을 수정하고 아래 버튼으로 저장하세요.")
    
    edited_df = st.data_editor(
        df[['종목명', '현재가', '등락률', 'PER', '추정 사유', '코드']],
        column_config={
            "추정 사유": st.column_config.TextColumn("분석 코멘트 (수정 가능)", width="large"),
            "코드": None 
        },
        hide_index=True, use_container_width=True, key="high_price_editor"
    )

    if st.button("💾 수정한 코멘트 GitHub에 최종 저장", type="primary"):
        if not GITHUB_TOKEN:
            st.error("GitHub Token (GH_PAT)이 Streamlit Secrets에 설정되지 않았습니다.")
            return
            
        report_data['results'] = edited_df.to_dict(orient='records')
        json_content = json.dumps(report_data, indent=4, ensure_ascii=False)
        
        with st.spinner("GitHub 저장소에 업데이트 중..."):
            success = save_to_github(f"data/{file_name}", json_content, f"Update comments for {file_name}")
            if success:
                st.success("✅ 변경사항이 성공적으로 반영되었습니다!")
                st.cache_data.clear()
            else:
                st.error("❌ 저장 실패. 로그를 확인하세요.")
