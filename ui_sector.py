"""
ui_sector.py — 섹터(업종)별 등락률 탭.
네이버 금융 '업종별 시세'(KRX 공식 업종 분류) 기준으로 장중 어떤 산업군이
많이 오르고/내리고 있는지 한눈에 보여준다.
"""
import streamlit as st
import pandas as pd
import requests
import io
import re

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}
SECTOR_URL = "https://finance.naver.com/sise/sise_group.naver?type=upjong"
SECTOR_DETAIL_URL = "https://finance.naver.com/sise/sise_group_detail.naver?type=upjong&no={no}"


@st.cache_data(ttl=180, show_spinner=False)
def get_sector_performance():
    """업종명 / 업종코드(no) / 등락률 / 상승·보합·하락 종목수. 실패하면 None."""
    try:
        res = requests.get(SECTOR_URL, headers=HEADERS, timeout=10)
        res.encoding = "euc-kr"
        # pd.read_html은 <a> 링크를 버려서 업종코드(no=)를 못 가져오므로,
        # 업종 상세 드릴다운에 필요한 업종명→업종코드 매핑을 정규식으로 따로 추출한다.
        name_to_no = dict(re.findall(r'sise_group_detail\.naver\?type=upjong&no=(\d+)">([^<]+)<', res.text))
        no_by_name = {name: no for no, name in name_to_no.items()}

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
        df["업종코드"] = df["업종명"].map(no_by_name)
        df = df.sort_values("등락률_num", ascending=False).reset_index(drop=True)
        return df
    except Exception:
        return None


@st.cache_data(ttl=180, show_spinner=False)
def get_sector_stocks(no: str):
    """해당 업종코드 소속 종목명 / 현재가 / 등락률. 실패하면 None."""
    try:
        res = requests.get(SECTOR_DETAIL_URL.format(no=no), headers=HEADERS, timeout=10)
        res.encoding = "euc-kr"
        tables = pd.read_html(io.StringIO(res.text))
        df = tables[2].dropna(subset=["종목명"]).copy()
        df = df[["종목명", "현재가", "등락률"]]
        df["등락률_num"] = df["등락률"].astype(str).str.extract(r"([+-]?\d+\.?\d*)").astype(float)
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
    st.markdown("#### 📋 전체 업종 (행 클릭 시 소속 종목 표시)")

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
    event = st.dataframe(
        styled, use_container_width=True, hide_index=True, height=560,
        on_select="rerun", selection_mode="single-row", key="sector_table_select",
    )

    selected_rows = event["selection"]["rows"] if event else []
    if not selected_rows:
        st.caption("👆 업종 행을 클릭하면 소속 종목별 현재가/등락률이 아래에 표시됩니다.")
        return

    sel = df.iloc[selected_rows[0]]
    sel_name, sel_no = sel["업종명"], sel["업종코드"]

    st.markdown(f"#### 🔍 {sel_name} — 소속 종목")
    if not sel_no or (isinstance(sel_no, float) and pd.isna(sel_no)):
        st.warning("이 업종은 종목 상세를 가져올 수 없습니다.")
        return

    with st.spinner(f"{sel_name} 종목 로딩 중..."):
        stock_df = get_sector_stocks(sel_no)

    if stock_df is None or stock_df.empty:
        st.error("❌ 종목 데이터를 가져오지 못했습니다.")
        return

    stock_display = stock_df[["종목명", "현재가", "등락률"]].copy()
    stock_display["현재가"] = stock_display["현재가"].map(
        lambda v: f"{int(v):,}원" if pd.notna(v) else "-"
    )
    stock_styled = stock_display.style.map(_color_change, subset=["등락률"])
    st.dataframe(stock_styled, use_container_width=True, hide_index=True, height=min(400, 40 + 35 * len(stock_display)))
