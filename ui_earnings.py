import streamlit as st
import json
import os
import re
from pathlib import Path
from github import Github
from streamlit_autorefresh import st_autorefresh

BASE_DIR = Path(__file__).parent
DATA_FILE = BASE_DIR / "data" / "earnings" / "earnings_data.json"
FAVORITES_FILE = "data/earnings/favorites.json"
LOCAL_FAVORITES_FILE = BASE_DIR / "data" / "earnings" / "favorites.json"

def get_github_repo():
    token = st.secrets.get("GITHUB_TOKEN", "")
    repo_name = st.secrets.get("GITHUB_REPO", "")
    if token and repo_name:
        g = Github(token)
        return g.get_repo(repo_name)
    return None

def load_favorites():
    repo = get_github_repo()
    if repo:
        try:
            file_content = repo.get_contents(str(FAVORITES_FILE))
            data = json.loads(file_content.decoded_content.decode('utf-8'))
            return set(data), True
        except Exception:
            return set(), False
    else:
        if LOCAL_FAVORITES_FILE.exists():
            with open(LOCAL_FAVORITES_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f)), True
        return set(), True

def save_favorites(favorites_set):
    repo = get_github_repo()
    content_str = json.dumps(list(favorites_set), ensure_ascii=False)
    if repo:
        try:
            file = repo.get_contents(str(FAVORITES_FILE))
            repo.update_file(str(FAVORITES_FILE), "Update favorites", content_str, file.sha)
            return True, ""
        except Exception as e:
            try:
                repo.create_file(str(FAVORITES_FILE), "Create favorites", content_str)
                return True, ""
            except Exception as e2:
                return False, str(e2)
    else:
        try:
            LOCAL_FAVORITES_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(LOCAL_FAVORITES_FILE, "w", encoding="utf-8") as f:
                json.dump(list(favorites_set), f, ensure_ascii=False)
            return True, ""
        except Exception as e:
            return False, str(e)

# 💡 [Oracle Point 4] 함수를 루프 밖으로 이동시켜 성능 최적화
def get_growth_color(val):
    if not val: return "#555555"
    if "+" in val or "흑전" in val: return "#FF0000" 
    if "-" in val or "적전" in val or "적자" in val: return "#1E90FF" 
    return "#555555" 

def render_earnings_menu():
    # 💡 [Oracle Point 1] 자동 갱신 횟수를 추적하여 데이터 강제 리로드
    refresh_count = st_autorefresh(interval=3 * 60 * 1000, key="earnings_auto_refresh")
    
    if 'favorites' not in st.session_state or refresh_count > st.session_state.get('last_refresh', -1):
        fav_set, is_safe = load_favorites()
        if not is_safe and 'favorites' in st.session_state:
            st.warning("⚠️ GitHub에서 최신 관심종목을 가져오지 못했습니다. 기존 목록을 유지합니다.")
        else:
            st.session_state.favorites = fav_set
            st.session_state.is_safe_to_save = is_safe
        st.session_state.last_refresh = refresh_count

    st.markdown("""
    <style>
    div[data-testid="stCheckbox"] {
        position: relative;
        z-index: 99;         
        left: 14px;          
        top: 14px;           
        width: 30px;         
    }
    </style>
    """, unsafe_allow_html=True)

    col1, col2 = st.columns([8, 2])
    with col1:
        st.markdown("<div class='main-title'>📈 실적 스크리닝 (AWAKE)</div>", unsafe_allow_html=True)
    with col2:
        if st.button("🔄 새로고침", use_container_width=True):
            st.session_state.pop('favorites', None)
            st.rerun()
            
    if not DATA_FILE.exists():
        st.info("📂 수집된 실적 데이터가 없습니다.")
        return
    
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        results = json.load(f)
        
    if not results:
        st.warning("분석된 실적 데이터가 없습니다.")
        return

    results = sorted(results, key=lambda x: x['발표시간'], reverse=True)
    available_quarters = sorted(list(set([
        row.get('해당분기') for row in results 
        if row.get('해당분기') and "미상" not in row.get('해당분기')
    ])), reverse=True)
    
    if not available_quarters:
        st.warning("표시할 분기 데이터가 없습니다.")
        return
    
    if 'ea_quarter' not in st.session_state: st.session_state.ea_quarter = available_quarters[0]
    if 'ea_keyword' not in st.session_state: st.session_state.ea_keyword = ""
    if 'ea_favs' not in st.session_state: st.session_state.ea_favs = False

    with st.form("earnings_search_form", border=False):
        f_col1, f_col2, f_col3, f_col4 = st.columns([2, 3, 1.2, 1.8])
        with f_col1:
            idx = available_quarters.index(st.session_state.ea_quarter) if st.session_state.ea_quarter in available_quarters else 0
            ui_quarter = st.selectbox("📌 분기 필터", available_quarters, index=idx)
        with f_col2:
            ui_keyword = st.text_input("🔍 종목 검색", value=st.session_state.ea_keyword, placeholder="종목명/코드")
        with f_col3:
            st.markdown("<div style='margin-top: 28px;'></div>", unsafe_allow_html=True)
            search_btn = st.form_submit_button("검색", type="primary", use_container_width=True)
        with f_col4:
            st.markdown("<div style='font-size: 14px; color: #31333F; margin-bottom: 8px;'>⭐ 관심종목만</div>", unsafe_allow_html=True)
            ui_show_favs = st.toggle("hidden_fav_toggle", value=st.session_state.ea_favs, label_visibility="collapsed")

    if search_btn:
        st.session_state.ea_quarter = ui_quarter
        st.session_state.ea_keyword = ui_keyword.strip()
        st.session_state.ea_favs = ui_show_favs
        st.rerun()

    filtered_results = []
    for row in results:
        code = row.get('코드', '')
        if row.get('해당분기') != st.session_state.ea_quarter: continue
        if st.session_state.ea_keyword:
            kw = st.session_state.ea_keyword.replace(" ", "").lower()
            if kw not in row.get('종목명', '').lower() and kw not in code.lower(): continue
        if st.session_state.ea_favs and code not in st.session_state.favorites: continue
        filtered_results.append(row)

    st.markdown("<div style='margin-top: 15px;'></div>")
    st.caption(f"📊 **{st.session_state.ea_quarter}** 기준 총 **{len(filtered_results)}**개의 실적 공시가 있습니다.")
    st.divider()
    st.markdown("<div style='height: 24px;'></div>")
    
    for row in filtered_results:
        corp_name = row.get('종목명', '')
        code = row.get('코드', '')
        pub_time = row.get('발표시간', '')
        op = row.get('영업익', '-')
        gap = row.get('괴리율', '')
        surf_status = row.get('서프_상태', '')
        yoy = row.get('YoY', '')
        qoq = row.get('QoQ', '')
        raw_text = row.get('원문', '')

        # 💡 [Oracle Point 3] 파싱 로직 보완 및 Fallback 설정
        if "없음" in surf_status or not surf_status:
            op_match_pct = re.search(r'영업익.*?\(\s*(?:예상치|컨센서스).*?[,·]\s*([+-]?\d+\.?\d*)%\s*\)', raw_text)
            if op_match_pct:
                gap = op_match_pct.group(1)
                try:
                    gap_f = float(gap)
                    if gap_f >= 10: surf_status = "서프라이즈"
                    elif gap_f <= -10: surf_status = "쇼크"
                    elif gap_f > 0: surf_status = "상회"
                    else: surf_status = "하회"
                except: pass
            else:
                op_match_txt = re.search(r'영업익.*?\(\s*(?:예상치|컨센서스).*?[,·]\s*(흑전|적전|부합)\s*\)', raw_text)
                if op_match_txt:
                    gap = op_match_txt.group(1)
                    if "흑전" in gap: surf_status = "서프라이즈"
                    elif "적전" in gap: surf_status = "쇼크"
                    elif "부합" in gap: surf_status = "부합"
            
            if not surf_status or "없음" in surf_status:
                surf_status = "데이터 분석중"

        is_fav = code in st.session_state.favorites
        new_fav = st.checkbox("", value=is_fav, key=f"fav_btn_{code}", label_visibility="collapsed")
        
        if new_fav != is_fav:
            if not st.session_state.get('is_safe_to_save', False):
                st.error("❌ GitHub 통신 불안정으로 인해 저장이 차단되었습니다. 다시 시도해 주세요.")
            else:
                # 💡 [Oracle Point 2] Rerun 전 세션 즉시 업데이트로 정합성 확보
                if new_fav: st.session_state.favorites.add(code)
                else: st.session_state.favorites.discard(code)
                
                with st.spinner("저장 중..."):
                    # 💡 [Oracle Point 5] 저장 실패 시 사용자에게 알림
                    success, err_msg = save_favorites(st.session_state.favorites)
                    if success: st.rerun()
                    else: st.error(f"❌ 저장 실패: {err_msg}")

        raw_text_html = raw_text.replace('\n', '<br>')
        url_pattern = re.compile(r'(https?://[^\s<]+)')
        raw_text_html = url_pattern.sub(r'<a href="\1" target="_blank" style="color: #1E90FF; font-weight: bold; text-decoration: underline;">\1</a>', raw_text_html)

        status_color = "#FF0000" if "서프라이즈" in surf_status or "상회" in surf_status else \
                       "#1E90FF" if "쇼크" in surf_status or "하회" in surf_status else "#555555"

        op_display = f"<b>{op}억</b>" if op != "-" else "<b>-</b>"
        if str(op).startswith('-'): op_display = f"<b style='color: #1E90FF;'>{op}억</b>"
        
        gap_text = f"<span style='color: {status_color}; font-weight: bold;'>({gap}{'%' if '%' not in str(gap) and gap not in ('흑전','적전','부합') else ''})</span>" if gap else ""
        
        yoy_colored = f"<span style='color: {get_growth_color(yoy)}; font-weight: 600;'>{yoy}</span>" if yoy else "-"
        qoq_colored = f"<span style='color: {get_growth_color(qoq)}; font-weight: 600;'>{qoq}</span>" if qoq else "-"
        growth_html = f"<span style='font-size: 12px; color: #888; margin-left: 10px;'>YoY {yoy_colored} &nbsp;|&nbsp; QoQ {qoq_colored}</span>"

        short_time = pub_time[5:16] if len(pub_time) >= 16 else pub_time
        card_border, card_bg = ('#FFD700', '#FFFDF0') if is_fav else ('#e0e0e0', '#ffffff')

        card_html = f"""
        <details style='margin-top: -36px; margin-bottom: 12px; border: 1px solid {card_border}; border-radius: 8px; background-color: {card_bg}; position: relative; z-index: 1;'>
            <summary style='cursor: pointer; list-style: none; outline: none; padding: 12px 12px 12px 42px; box-sizing: border-box;'>
                <div style='display: flex; flex-direction: column; gap: 6px; width: 100%;'>
                    <div style='display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; width: 100%;'>
                        <span style='font-size: 16px; font-weight: bold; color: #222;'>{corp_name}</span>
                        <span style='color: {status_color}; font-weight: 900; font-size: 12.5px;'>{surf_status}</span>
                        <span style='font-size: 11px; color: #aaa;'>{short_time}</span>
                    </div>
                    <div style='font-size: 14px; color: #333; text-align: left; width: 100%; white-space: normal; line-height: 1.4;'>
                        <span style='color: #888; font-size: 12px;'>OP:</span> {op_display} {gap_text} {growth_html}
                    </div>
                </div>
            </summary>
            <div style='padding: 0px 12px 12px 42px; border-top: 1px dashed #eee; font-size: 13px; color: #444; line-height: 1.6;'>
                {raw_text_html}
            </div>
        </details>
        """
        st.markdown(card_html, unsafe_allow_html=True)
