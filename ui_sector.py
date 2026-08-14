"""
ui_sector.py — 섹터(업종)별 등락률 탭.
네이버 금융 '업종별 시세'(KRX 공식 업종 분류) 기준으로 장중 어떤 산업군이
많이 오르고/내리고 있는지 한눈에 보여준다.
"""
import streamlit as st
import pandas as pd
import requests
import io

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}
SECTOR_URL = "https://finance.naver.com/sise/sise_group.naver?type=upjong"


@st.cache_data(ttl=180, show_spinner=False)
def get_sector_performance():
    """업종명 / 등락률 / 상승·보합·하락 종목수. 실패하면 None."""
    try:
        res = requests.get(SECTOR_URL, headers=HEADERS, timeout=10)
        res.encoding = "euc-kr"
        tables = pd.read_html(io.StringIO(res.text))
        df = tables[0]
        df.columns = ["업종명", "등락률", "전체", "상승", "보합", "하락", "그래프"]
        df = df.dropna(subset=["업종명"]).copy()
        df["등락률_num"] = (
            df["등락률"].astype(str).str.replace("%", "", regex=False)
            .str.replace("+", "", regex=False).astype(float)
        )
        for c in ["전체", "상승", "보합", "하락"]:
            df[c] = df[c].fillna(0).astype(int)
        df = df.sort_values("등락률_num", ascending=False).reset_index(drop=True)
        return df
    except Exception:
        return None


def _spotlight_card(row):
    up = row["등락률_num"] > 0
    clr = "#ef5350" if up else ("#1565C0" if row["등락률_num"] < 0 else "#888")
    arrow = "▲" if up else ("▼" if row["등락률_num"] < 0 else "─")
    # 💡 마크다운은 4칸 이상 들여쓰기된 줄을 코드블록으로 인식해 HTML을 그대로
    # 텍스트로 찍어버린다 — 여러 줄에 걸친 들여쓰기 f-string 대신 들여쓰기
    # 없는 한 줄짜리 문자열로 만들어야 카드 전체가 실제 HTML로 렌더링된다.
    return (
        f'<div style="display:flex;justify-content:space-between;align-items:center;'
        f'padding:8px 12px;border-bottom:1px solid #f0f0f0;">'
        f'<span style="font-size:13.5px;font-weight:600;color:#333;">{row["업종명"]}</span>'
        f'<span style="font-size:13.5px;font-weight:700;color:{clr};">{arrow} {row["등락률"]}</span>'
        f'</div>'
    )


def render_sector_menu():
    st.markdown("<div class='main-title'>🏭 섹터별 등락률</div>", unsafe_allow_html=True)
    st.caption("네이버 금융 업종별 시세(KRX 공식 업종 분류) 기준 · 3분 캐시")

    _, col_r = st.columns([8, 1.5])
    with col_r:
        if st.button("🔄 새로고침", use_container_width=True, key="sector_refresh"):
            get_sector_performance.clear()
            st.rerun()

    with st.spinner("업종 데이터 로딩 중..."):
        df = get_sector_performance()

    if df is None or df.empty:
        st.error("❌ 업종 데이터를 가져오지 못했습니다. 잠시 후 다시 시도해주세요.")
        return

    up_n = int((df["등락률_num"] > 0).sum())
    down_n = int((df["등락률_num"] < 0).sum())
    flat_n = len(df) - up_n - down_n
    st.caption(f"📊 상승 {up_n}개 · 보합 {flat_n}개 · 하락 {down_n}개 (전체 {len(df)}개 업종)")

    top5 = df.head(5)
    bottom5 = df.tail(5).iloc[::-1]

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("#### 🔥 상승 TOP 5")
        st.markdown(
            "<div style='border:1px solid #eee;border-radius:8px;overflow:hidden;'>"
            + "".join(_spotlight_card(r) for _, r in top5.iterrows())
            + "</div>",
            unsafe_allow_html=True,
        )
    with c2:
        st.markdown("#### ❄️ 하락 TOP 5")
        st.markdown(
            "<div style='border:1px solid #eee;border-radius:8px;overflow:hidden;'>"
            + "".join(_spotlight_card(r) for _, r in bottom5.iterrows())
            + "</div>",
            unsafe_allow_html=True,
        )

    st.divider()
    st.markdown("#### 📋 전체 업종")

    display_df = df[["업종명", "등락률", "상승", "보합", "하락"]].rename(
        columns={"등락률": "등락률(%)", "상승": "상승종목", "보합": "보합종목", "하락": "하락종목"}
    )

    def _color_change(val):
        try:
            v = float(str(val).replace("%", "").replace("+", ""))
            if v > 0:
                return "color:#ef5350;font-weight:700"
            if v < 0:
                return "color:#1565C0;font-weight:700"
        except Exception:
            pass
        return ""

    styled = display_df.style.map(_color_change, subset=["등락률(%)"])
    st.dataframe(styled, use_container_width=True, hide_index=True, height=560)
