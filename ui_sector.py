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
from krx_listing import fetch_krx_marcap
import os
import json
from datetime import datetime, timezone, timedelta

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}
# 💡 네이버 레거시 페이지(finance.naver.com/sise/...)는 2026-09월 폐기됐다.
# 지금은 stock.naver.com SPA로 302 리다이렉트되고 서버가 표를 내려주지 않아
# read_html이 "No tables found"로 떨어졌다 — 섹터 탭이 통째로 빈 화면이 된
# 원인이다. (같은 계열인 item/main.naver도 함께 죽었다.)
#
# 그래서 스크래핑을 걷어내고 직접 계산한다:
#   업종 분류 ← KRX KIND corpList (종목코드 → 업종, 158개 분류)
#   시세     ← krx_listing.fetch_krx_marcap (종목별 등락률 / 시가총액)
# 둘 다 이미 앱이 쓰던 소스라 새 의존이 늘지 않고, 네이버가 또 화면을 바꿔도
# 영향을 받지 않는다.
_KIND_URL = "http://kind.krx.co.kr/corpgeneral/corpList.do?method=download&searchType=13"
# 구성 종목이 너무 적은 업종은 한 종목 급등에 업종 전체가 끌려가 순위가 무의미해진다.
_MIN_MEMBERS = 3


@st.cache_data(ttl=21600, show_spinner=False)
def _industry_map():
    """종목코드 → 업종명. KIND 다운로드(EUC-KR)를 파싱한다."""
    res = requests.get(_KIND_URL, headers=HEADERS, timeout=15)
    res.encoding = "euc-kr"   # charset 헤더가 없어 명시하지 않으면 한글이 깨진다
    df = pd.read_html(io.StringIO(res.text), header=0)[0]
    df = df.rename(columns={"회사명": "Name", "종목코드": "Code", "업종": "업종명"})
    df["Code"] = df["Code"].astype(str).str.strip().str.zfill(6)
    df = df.dropna(subset=["업종명"])
    return dict(zip(df["Code"], df["업종명"].astype(str).str.strip()))


@st.cache_data(ttl=180, show_spinner=False)
def _merged_quotes():
    """종목별 시세 + 업종명을 붙인 표. 업종 집계와 종목 드릴다운이 함께 쓴다."""
    try:
        q = fetch_krx_marcap()
        ind = _industry_map()
    except Exception as e:
        print(f"[ui_sector] 시세/업종 조회 실패: {type(e).__name__}: {e}")
        return None
    df = q.copy()
    df["업종명"] = df["Code"].map(ind)
    df = df.dropna(subset=["업종명"])
    # 스팩은 업종이 '금융 지원 서비스업'으로 묶이는데 거의 움직이지 않아
    # 그 업종 전체를 0% 쪽으로 끌어내린다. 제외한다.
    df = df[~df["Name"].astype(str).str.contains("스팩", na=False)]
    for c in ("ChagesRatio", "Marcap", "Close"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna(subset=["ChagesRatio", "Marcap"])


@st.cache_data(ttl=180, show_spinner=False)
def get_sector_performance():
    """업종명 / 업종코드 / 등락률 / 상승·보합·하락 종목수. 실패하면 None."""
    df = _merged_quotes()
    if df is None or df.empty:
        return None
    try:
        rows = []
        for name, g in df.groupby("업종명"):
            if len(g) < _MIN_MEMBERS:
                continue
            w = g["Marcap"].sum()
            # 시총가중 평균 — 업종 지수가 실제로 움직인 정도에 가깝다.
            # 단순평균을 쓰면 소형주 몇 개가 업종 순위를 좌우한다.
            rate = float((g["ChagesRatio"] * g["Marcap"]).sum() / w) if w else 0.0
            rows.append({
                "업종명": name,
                "등락률": f"{rate:+.2f}%",
                "등락률_num": round(rate, 2),
                "전체": int(len(g)),
                "상승": int((g["ChagesRatio"] > 0).sum()),
                "보합": int((g["ChagesRatio"] == 0).sum()),
                "하락": int((g["ChagesRatio"] < 0).sum()),
                # 드릴다운 키. 예전엔 네이버 업종번호였는데 이제 업종명이 곧 키다.
                "업종코드": name,
            })
        if not rows:
            return None
        return (pd.DataFrame(rows)
                .sort_values("등락률_num", ascending=False)
                .reset_index(drop=True))
    except Exception as e:
        print(f"[ui_sector] 업종 집계 실패: {type(e).__name__}: {e}")
        return None


@st.cache_data(ttl=180, show_spinner=False)
def get_sector_stocks(no: str):
    """해당 업종 소속 종목명 / 현재가 / 등락률. 실패하면 None."""
    df = _merged_quotes()
    if df is None or df.empty:
        return None
    try:
        g = df[df["업종명"] == no]
        if g.empty:
            return None
        # 💡 현재가는 숫자 그대로 돌려준다. 화면 쪽(render_sector_menu)이
        # int()로 다시 포맷하므로 여기서 "2,135" 같은 문자열을 주면 거기서
        # ValueError로 죽는다.
        out = pd.DataFrame({
            "종목명": g["Name"].astype(str),
            "현재가": pd.to_numeric(g["Close"], errors="coerce"),
            "등락률": g["ChagesRatio"].map(lambda v: f"{v:+.2f}%"),
            "등락률_num": g["ChagesRatio"].astype(float),
        })
        return out.sort_values("등락률_num", ascending=False).reset_index(drop=True)
    except Exception as e:
        print(f"[ui_sector] 업종 종목 조회 실패: {type(e).__name__}: {e}")
        return None


_SNAP_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "data", "sector", "last_snapshot.json")
_KST = timezone(timedelta(hours=9))


def _save_snapshot(df: pd.DataFrame) -> None:
    """장중 유효 스냅샷을 디스크에 남긴다. 실패해도 조회를 망치지 않는다."""
    try:
        os.makedirs(os.path.dirname(_SNAP_PATH), exist_ok=True)
        payload = {
            "saved_at": datetime.now(_KST).strftime("%Y-%m-%d %H:%M"),
            "rows": df.to_dict(orient="records"),
        }
        with open(_SNAP_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
    except Exception as e:
        print(f"[ui_sector] 스냅샷 저장 실패(무시): {type(e).__name__}: {e}")


def _load_snapshot():
    """(DataFrame, 저장시각) 또는 (None, None)."""
    try:
        with open(_SNAP_PATH, "r", encoding="utf-8") as f:
            payload = json.load(f)
        rows = payload.get("rows") or []
        if not rows:
            return None, None
        return pd.DataFrame(rows), payload.get("saved_at")
    except Exception:
        return None, None


def get_sector_snapshot():
    """
    화면용 업종 데이터 -> (DataFrame, 기준시각, 지연여부).

    💡 네이버 업종별 시세는 장이 열리기 전에는 모든 업종이 0.00%로 나온다
    (오늘 아직 안 움직였으니 당연하다). 그 화면은 아무 정보가 없어서,
    직전에 받아둔 유효 스냅샷(= 어제 장의 마지막 상태)을 대신 보여준다.

    ⚠️ 스냅샷은 디스크에 두는데 Streamlit Cloud 파일시스템은 재배포 때
    날아간다. 그 경우엔 스냅샷이 없으므로 0.00% 화면을 그대로 보여주되
    "장 시작 전"이라고 분명히 알린다 — 조용히 빈 화면을 주지 않는다.

    get_sector_performance()는 배치(batch_surge_alert)도 쓰므로 반환형을
    바꾸지 않고, 화면용 래퍼를 따로 둔다.
    """
    df = get_sector_performance()
    now_txt = datetime.now(_KST).strftime("%Y-%m-%d %H:%M")

    if df is None or df.empty:
        snap, saved = _load_snapshot()
        return (snap, saved, True) if snap is not None else (None, None, True)

    moved = int((df["등락률_num"] != 0).sum())
    if moved > 0:
        _save_snapshot(df)
        return df, now_txt, False

    # 전 업종이 보합 = 장 시작 전(또는 휴장). 직전 스냅샷으로 대체한다.
    snap, saved = _load_snapshot()
    if snap is not None:
        return snap, saved, True
    return df, now_txt, True


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
    st.caption("KRX 업종분류(KIND) × 종목별 종가 등락률을 시가총액으로 가중해 "
               f"직접 집계합니다 · 구성종목 {_MIN_MEMBERS}개 이상 업종만 · 3분 캐시")

    _, col_r = st.columns([8, 1.5])
    with col_r:
        if st.button("🔄 새로고침", use_container_width=True, key="sector_refresh"):
            get_sector_performance.clear()
            st.rerun()

    with st.spinner("업종 데이터 로딩 중..."):
        df, as_of, stale = get_sector_snapshot()

    if df is None or df.empty:
        st.error("❌ 업종 데이터를 가져오지 못했습니다. 잠시 후 다시 시도해주세요.")
        return

    up_n = int((df["등락률_num"] > 0).sum())
    down_n = int((df["등락률_num"] < 0).sum())
    flat_n = len(df) - up_n - down_n

    # 기준 시각을 항상 보여준다 — 장 시작 전엔 직전 장 마감 상태를 보고 있는
    # 것이므로, 언제 값인지 모르면 오해하기 쉽다.
    if stale:
        badge = ("<span style='background:#6b7a8c;color:#fff;font-size:10.5px;"
                 "font-weight:700;padding:1px 6px;border-radius:3px;margin-left:6px;'>"
                 "장 시작 전 · 직전 장 기준</span>")
    else:
        badge = ""
    st.markdown(
        f"<div style='font-size:12.5px;color:#666;margin:2px 0 6px;'>"
        f"📊 상승 <b>{up_n}</b>개 · 보합 {flat_n}개 · 하락 <b>{down_n}</b>개 "
        f"(전체 {len(df)}개 업종) · 기준 <b>{html.escape(str(as_of or '-'))}</b>{badge}</div>",
        unsafe_allow_html=True,
    )

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
