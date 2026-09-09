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
import html
import urllib.parse
import xml.etree.ElementTree as ET
import concurrent.futures

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


def _fetch_news(sector_name: str, n: int):
    """업종 관련 뉴스 헤드라인 (제목, 링크) n개. 실패하면 빈 리스트."""
    try:
        query = urllib.parse.quote(f"{sector_name} 업종 주가")
        url = f"https://news.google.com/rss/search?q={query}&hl=ko&gl=KR&ceid=KR:ko"
        res = requests.get(url, timeout=5)
        root = ET.fromstring(res.text)
        out = []
        for item in root.findall(".//item")[:n]:
            t, l = item.find("title"), item.find("link")
            if t is not None and l is not None and t.text and l.text:
                out.append((t.text, l.text))
        return out
    except Exception:
        return []


@st.cache_data(ttl=1800, show_spinner=False)
def get_sector_news_bulk(names: tuple, n: int = 1) -> dict:
    """
    여러 업종의 헤드라인을 한 번에 -> {업종명: [(제목, 링크), ...]}.

    💡 업종마다 따로 부르면 Top5 x 2 = 10회를 순차로 기다린다(각 ~0.3s).
    구글 뉴스 RSS는 키가 필요 없고 가벼워서 병렬로 묶으면 체감이 사라진다.
    캐시도 "이번 Top10 묶음" 단위로 하나만 잡힌다. 30분 캐시.

    금융 뉴스는 등락 사유가 제목에 그대로 들어있어(예: "전기장비주, AI
    데이터센터 전력 수요에 급등") AI 요약 없이 헤드라인만으로 충분하다.
    """
    out = {nm: [] for nm in names}
    if not names:
        return out
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        futs = {ex.submit(_fetch_news, nm, n): nm for nm in names}
        for f in concurrent.futures.as_completed(futs):
            out[futs[f]] = f.result()
    return out


def _spotlight_card(row, news):
    """
    업종 카드 = 업종명 + 등락률 + **사유 한 줄**.

    💡 예전엔 등락률만 있고 사유는 화면 아래 "이슈 정리" 섹션에 따로 있었다.
    숫자를 보고 이유를 찾으려면 눈이 두 번 움직여야 해서, 헤드라인을 카드
    안으로 넣고 별도 섹션은 없앴다.

    ⚠️ 마크다운은 4칸 이상 들여쓴 줄을 코드블록으로 인식해 HTML을 그대로
    텍스트로 찍는다 — 들여쓰기 없는 한 줄짜리 문자열로 조립해야 한다.
    """
    up = row["등락률_num"] > 0
    clr = "#ef5350" if up else ("#1565C0" if row["등락률_num"] < 0 else "#888")
    arrow = "▲" if up else ("▼" if row["등락률_num"] < 0 else "─")

    if news:
        title, link = news[0]
        # 뉴스 제목·링크는 외부 입력 — HTML 삽입 전 이스케이프 (XSS 방지)
        reason = (
            f'<div style="font-size:11.5px;line-height:1.35;margin-top:3px;">'
            f'<a href="{html.escape(link)}" target="_blank" rel="noopener noreferrer" '
            f'style="color:#7a8794;text-decoration:none;">📰 {html.escape(title)}</a></div>'
        )
    else:
        reason = ('<div style="font-size:11.5px;color:#bbb;margin-top:3px;">'
                  '관련 뉴스 없음</div>')

    return (
        f'<div style="padding:9px 12px;border-bottom:1px solid #f0f0f0;">'
        f'<div style="display:flex;justify-content:space-between;align-items:center;">'
        f'<span style="font-size:13.5px;font-weight:600;color:#333;">{html.escape(str(row["업종명"]))}</span>'
        f'<span style="font-size:13.5px;font-weight:700;color:{clr};white-space:nowrap;">'
        f'{arrow} {html.escape(str(row["등락률"]))}</span>'
        f'</div>{reason}</div>'
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

    # Top10의 헤드라인을 한 번에 병렬로 받아 카드에 같이 싣는다
    names = tuple(list(top5["업종명"]) + list(bottom5["업종명"]))
    with st.spinner("업종 이슈 확인 중..."):
        news_map = get_sector_news_bulk(names)

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("#### 🔥 상승 TOP 5")
        st.markdown(
            "<div style='border:1px solid #eee;border-radius:8px;overflow:hidden;'>"
            + "".join(_spotlight_card(r, news_map.get(r["업종명"], []))
                      for _, r in top5.iterrows())
            + "</div>",
            unsafe_allow_html=True,
        )
    with c2:
        st.markdown("#### ❄️ 하락 TOP 5")
        st.markdown(
            "<div style='border:1px solid #eee;border-radius:8px;overflow:hidden;'>"
            + "".join(_spotlight_card(r, news_map.get(r["업종명"], []))
                      for _, r in bottom5.iterrows())
            + "</div>",
            unsafe_allow_html=True,
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

    # 💡 소속 종목 드릴다운은 남겼지만 접어 뒀다 — "Top5만 표시" 요청에 맞게
    # 화면은 Top5 + 이슈로 끝나되, 특정 업종을 파보고 싶을 때는 열 수 있게.
    st.divider()
    with st.expander("🔍 특정 업종의 소속 종목 보기", expanded=False):
        sel_name = st.selectbox("업종 선택", df["업종명"].tolist(), key="sector_pick")
        sel_no = df[df["업종명"] == sel_name].iloc[0]["업종코드"]

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
        st.dataframe(stock_display.style.map(_color_change, subset=["등락률"]),
                     use_container_width=True, hide_index=True,
                     height=min(400, 40 + 35 * len(stock_display)))
