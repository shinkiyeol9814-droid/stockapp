"""
ui_surge.py — 거래대금 급증 스크리너 탭.

같은 시각 최근 20거래일 누적거래대금 중앙값보다 몇 배로 불어난 종목을 하루 네 번
(10:05 · 12:05 · 14:05 · 종가) 잡아낸 결과를 보여준다.

━━ 데이터는 별도 리포가 만든다 ━━
수집·포착은 shinkiyeol9814-droid/kosdaq-surge(GitHub Actions + cron-job.org)가 하고,
결과 JSON을 GitHub Pages로 공개한다. 이 탭은 그 JSON을 읽어 앱 스타일로 그리기만 한다
— 시장 전체 스냅샷(4천 종목 × 하루 4번)을 이 리포에 커밋하면 배치 커밋이 앱을 재시작시키고
리포가 불어난다(AGENTS.md 리포지토리 비대화 방지). 기준 숫자는 index.json의 criteria를 따른다.

일자 파일 구조(kosdaq-surge/surge/screen.py):
  slots.{slot}.hits.{코드} = [배수, 시장 내 대금순위, 등급, 현재가, 등락률, 거래대금(억), 회전율%]
  stocks.{코드} = {n, m(K/Q), p, c, v(억), cap(억), turn, news[제목,언론사,URL,시각], k[[시,고,저,종]...]}
"""
import html

import pandas as pd
import requests
import streamlit as st

from ui_common import page_header
from ui_mobile import disable_keyboard

# Pages가 1순위(CDN), 실패하면 raw. 둘 다 공개 URL이라 토큰이 필요 없다.
_SOURCES = [
    "https://shinkiyeol9814-droid.github.io/kosdaq-surge/data",
    "https://raw.githubusercontent.com/shinkiyeol9814-droid/kosdaq-surge/main/docs/data",
]
_SITE = "https://shinkiyeol9814-droid.github.io/kosdaq-surge/"
SLOTS = ["1005", "1205", "1405", "close"]
LABEL = {"all": "종합", "1005": "10:05", "1205": "12:05", "1405": "14:05", "close": "종가"}
_WD = "월화수목금토일"
_PAGE = 30          # 한 번에 그리는 카드 수 — 카드마다 일봉 SVG가 있어 100장을 한꺼번에 그리면 무겁다

_UP, _DOWN, _FLAT = "#ef5350", "#1565C0", "#888"
_STAR = "#f5a623"


def _fetch(path: str):
    last = None
    for base in _SOURCES:
        try:
            r = requests.get(f"{base}/{path}", timeout=8)
            if r.status_code == 200:
                return r.json()
            last = RuntimeError(f"HTTP {r.status_code}")
        except Exception as e:
            last = e
    raise last


@st.cache_data(ttl=300, show_spinner=False)
def _get_index():
    """일자 목록·시각별 건수·기준값. 실패는 예외로 — 빈 결과를 5분간 붙잡지 않게."""
    return _fetch("index.json")


@st.cache_data(ttl=3600, show_spinner=False)
def _get_day(day: str, updated: str):
    """일자 파일. updated를 키에 넣어, 새 수집이 올라오면(색인 갱신) 자동으로 다시 받는다."""
    return _fetch(f"days/{day}.json")


# ── 카드 조각 ────────────────────────────────────────────────────────────────
def _clr(v):
    return _UP if v > 0 else _DOWN if v < 0 else _FLAT


def _candles(k) -> str:
    """3개월 일봉 미니 차트. 양봉 빨강·음봉 파랑. 요소 수를 줄이려고 색별로 path 하나씩."""
    if not k or len(k) < 2:
        return ""
    W, H = 104.0, 34.0
    n = len(k)
    step = W / n
    bw = max(1.0, step * 0.6)
    lo = min(r[2] for r in k if r[2] > 0)
    hi = max(r[1] for r in k)

    def y(v):
        return H / 2 if hi == lo else 1 + (H - 2) * (1 - (v - lo) / (hi - lo))

    wick = {True: [], False: []}
    body = {True: [], False: []}
    for i, (o, h, l, c) in enumerate(k):
        up = c >= o
        x = i * step + step / 2
        wick[up].append(f"M{x:.1f} {y(h):.1f}V{y(l):.1f}")
        top, bot = y(max(o, c)), y(min(o, c))
        body[up].append(f"M{x - bw / 2:.1f} {top:.1f}h{bw:.1f}v{max(0.8, bot - top):.1f}h{-bw:.1f}z")
    paths = "".join(
        f'<path d="{"".join(wick[u])}" stroke="{col}" stroke-width="0.7" fill="none"/>'
        f'<path d="{"".join(body[u])}" fill="{col}"/>'
        for u, col in ((True, _UP), (False, _DOWN)) if wick[u])
    return (f'<svg width="{W:.0f}" height="{H:.0f}" viewBox="0 0 {W:.0f} {H:.0f}" '
            f'style="flex:none;display:block;" role="img" aria-label="최근 3개월 일봉">{paths}</svg>')


def _dots(on) -> str:
    """잡힌 시각 표시 — 10:05 · 12:05 · 14:05 · 종가 순서의 점 네 개."""
    return "".join(
        f'<span style="display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:2px;'
        + ("background:#555;" if s in on else "box-shadow:inset 0 0 0 1px #bbb;") + '"></span>'
        for s in SLOTS)


def _card(r) -> str:
    """
    한 종목 = 이름·등급·배수 / 시세 한 줄 / 뉴스 한 줄, 오른쪽에 3개월 일봉.
    ⚠️ 마크다운은 4칸 들여쓴 줄을 코드블록으로 보므로 한 줄 문자열로 조립한다(ui_sector와 같은 이유).
    """
    st_ = r["st"]
    name = html.escape(str(st_.get("n", r["code"])))
    mkt = "코스피" if st_.get("m") == "K" else "코스닥"
    stars = (f'<span style="color:{_STAR};font-size:12px;letter-spacing:1px;margin-left:5px;">'
             f'{"★" * r["grade"]}</span>') if r["grade"] else ""
    chg = r["chg"] or 0
    line2a = (f'{r["price"]:,.0f} '
              f'<b style="color:{_clr(chg)};">{"+" if chg > 0 else ""}{chg:.2f}%</b>'
              f' · 대금 {r["value"]:,.1f}억')
    line2b = f'· 회전 {r["turn"]:.1f}% · 시총 {r["cap"]:,.0f}억'
    nw = st_.get("news")
    if nw:
        news = (f'<div style="font-size:11.5px;line-height:1.35;margin-top:3px;white-space:nowrap;'
                f'overflow:hidden;text-overflow:ellipsis;">'
                f'<a href="{html.escape(nw[2])}" target="_blank" rel="noopener noreferrer" '
                f'style="color:#7a8794;text-decoration:none;">📰 {html.escape(nw[0])}</a></div>')
    else:
        news = ""
    link = f"https://finance.naver.com/item/main.naver?code={r['code']}"
    return (
        f'<div style="padding:8px 12px;border-bottom:1px solid #f0f0f0;display:flex;gap:10px;align-items:center;">'
        f'<div style="flex:1;min-width:0;">'
        f'<div style="display:flex;justify-content:space-between;align-items:baseline;gap:8px;">'
        f'<span style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">'
        f'<a href="{link}" target="_blank" rel="noopener noreferrer" '
        f'style="font-size:13.5px;font-weight:600;color:#333;text-decoration:none;">{name}</a>{stars}'
        # 휴대폰에서 줄이 넘치면 회색 코드·시장부터 잘리도록 맨 뒤에 둔다(별이 잘리지 않게)
        f'<span style="font-size:11px;color:#999;margin-left:5px;">{r["code"]} · {mkt}</span></span>'
        f'<span style="font-size:13.5px;font-weight:700;color:#333;white-space:nowrap;">{r["mult"]:,.1f}배</span>'
        f'</div>'
        # 시세 줄은 좁은 화면에서 두 줄로 접히게 둔다 — 한 줄로 자르면 회전율·시총이 잘려 보이지 않았다
        f'<div style="font-size:12px;color:#666;margin-top:2px;line-height:1.45;">'
        f'<span style="white-space:nowrap;">{_dots(r["on"])} {line2a}</span> '
        f'<span style="white-space:nowrap;">{line2b}</span></div>'
        f'{news}</div>'
        f'{_candles(st_.get("k"))}</div>'
    )


def _rules_html(c: dict) -> str:
    eok = lambda w: f"{w / 1e8:,.0f}"
    scale = dict((s, r) for r, s in c["scale"])
    turn = dict((s, p) for p, s in c["turnover"])
    rows = "".join(
        f'<tr><td style="color:{_STAR};padding:3px 10px 3px 0;">{"★" * s}</td>'
        f'<td style="padding:3px 10px 3px 0;">시장 내 대금 {scale.get(s, "-")}위 이내</td>'
        f'<td style="padding:3px 0;">회전율 {turn.get(s, "-")}% 이상</td></tr>'
        for s in (3, 2, 1))
    return (
        f'<div style="font-size:12.5px;color:#555;line-height:1.55;">'
        f'시가총액 <b>{eok(c["min_mcap"])}억</b> 이상 · 최근 <b>{c["base_days"]}거래일</b> 같은 시각 '
        f'누적거래대금 <b>중앙값의 {c["multiple"]:g}배</b> 이상 · 누적거래대금 장중 '
        f'<b>{eok(c["min_value"]["1005"])}억</b> / 종가 <b>{eok(c["min_value"]["close"])}억</b> 이상'
        f'<div style="margin:6px 0 2px;">등급은 두 기준 중 높은 쪽 — 규모(같은 시장 안 거래대금 순위)는 큰 종목에, '
        f'손바뀜(거래대금 ÷ 시총)은 작은 종목에 유리하다. 별이 없으면 배수로는 급증이지만 시장 전체로는 크지 않다는 뜻.</div>'
        f'<table style="border-collapse:collapse;font-size:12.5px;">{rows}</table>'
        f'<div style="margin-top:4px;">점 네 개 = 10:05 · 12:05 · 14:05 · 종가 중 잡힌 시각. '
        f'여러 시각에 걸쳐 잡힐수록 하루 내내 이어진 급증이다.</div></div>'
    )


# ── 표 데이터 ────────────────────────────────────────────────────────────────
def _rows(doc: dict, slot: str) -> list:
    stocks = doc.get("stocks", {})
    slots = doc.get("slots", {})
    hit_on = lambda code: [s for s in SLOTS if code in slots.get(s, {}).get("hits", {})]
    out = []
    if slot == "all":
        for code, st_ in stocks.items():
            on = hit_on(code)
            hs = [slots[s]["hits"][code] for s in on]
            if not hs:
                continue
            out.append({"code": code, "st": st_, "on": on, "cnt": len(on),
                        "grade": max(h[2] for h in hs), "mult": max(h[0] for h in hs),
                        "price": st_.get("p", 0), "chg": st_.get("c", 0), "value": st_.get("v", 0),
                        "turn": st_.get("turn", 0), "cap": st_.get("cap", 0)})
    else:
        for code, h in slots.get(slot, {}).get("hits", {}).items():
            st_ = stocks.get(code, {})
            on = hit_on(code)
            out.append({"code": code, "st": st_, "on": on, "cnt": len(on), "grade": h[2], "mult": h[0],
                        "price": h[3], "chg": h[4], "value": h[5], "turn": h[6], "cap": st_.get("cap", 0)})
    return out


# ── 표(정렬 가능) ─────────────────────────────────────────────────────────────
# 수집 사이트(kosdaq-surge)와 같은 열 구성. st.dataframe은 헤더를 누르면 그 열로
# 정렬되므로 "배수 큰 순 / 회전율 높은 순"을 버튼 없이 바로 볼 수 있다.
# 카드 보기는 그대로 남겼다 — 일봉 캔들(양봉 빨강·음봉 파랑)과 뉴스 링크는
# 표에서 재현할 수 없어서다(LineChartColumn은 선 하나, 셀 안에 링크를 못 넣는다).
_TABLE_COLS = {
    "포착":        dict(kind="text",   label="포착 시각", width="small",
                        help="왼쪽부터 10:05 · 12:05 · 14:05 · 종가. ●는 그 시각에 잡힌 것"),
    "등급":        dict(kind="text",   label="등급", width="small"),
    # 종목명 자체에 네이버 금융 링크를 건다. st.dataframe은 셀에 HTML을 못 넣고
    # LinkColumn의 display_text는 "URL에 적용하는 정규식" 하나뿐이라, 이름을
    # URL 프래그먼트(#종목명)로 실어 보내고 거기서 되뽑는다. 프래그먼트는 서버로
    # 전송되지 않으므로 네이버 요청에는 아무 영향이 없다.
    "종목":        dict(kind="namelink", label="종목", width="medium"),
    "코드":        dict(kind="text",   label="코드", width="small"),
    "시장":        dict(kind="text",   label="시장", width="small"),
    "최근 뉴스":   dict(kind="text",   label="최근 뉴스", width="large"),
    "3개월":       dict(kind="chart",  label="3개월 종가"),
    "배수":        dict(kind="num",    label="배수", fmt="%.1f배", width="small"),
    "현재가":      dict(kind="num",    label="현재가", fmt="%,d", width="small"),
    "등락률":      dict(kind="num",    label="등락률", fmt="%+.2f%%", width="small"),
    "시총(억)":    dict(kind="num",    label="시총(억)", fmt="%,d", width="small"),
    "거래대금(억)": dict(kind="num",   label="거래대금(억)", fmt="%,.1f", width="small"),
    "회전율":      dict(kind="num",    label="회전율", fmt="%.1f%%", width="small"),
}

# 프래그먼트에서 종목명만 떼어내는 패턴 — LinkColumn.display_text에 그대로 넘긴다.
_NAME_FROM_URL = r"#(.+)$"


def _table_df(rows) -> pd.DataFrame:
    out = []
    for r in rows:
        st_ = r["st"]
        nw = st_.get("news")
        closes = [c[3] for c in (st_.get("k") or [])]
        out.append({
            # 점 네 개로 '어느 시각에 잡혔는지'를 그대로 옮긴다(카드의 _dots와 같은 순서).
            "포착": "".join("●" if sl in r["on"] else "○" for sl in SLOTS),
            "등급": "★" * (r["grade"] or 0),
            # 이름이 비면 정규식이 안 걸려 URL이 그대로 보이므로 코드로 메운다.
            "종목": (f"https://finance.naver.com/item/main.naver?code={r['code']}"
                     f"#{str(st_.get('n') or r['code'])}"),
            "코드": r["code"],
            "시장": "코스피" if st_.get("m") == "K" else "코스닥",
            "최근 뉴스": str(nw[0]) if nw else "",
            "3개월": closes or None,
            "배수": float(r["mult"] or 0),
            "현재가": float(r["price"] or 0),
            "등락률": float(r["chg"] or 0),
            "시총(억)": float(r["cap"] or 0),
            "거래대금(억)": float(r["value"] or 0),
            "회전율": float(r["turn"] or 0),
        })
    return pd.DataFrame(out, columns=list(_TABLE_COLS))


def _render_table(rows) -> None:
    df = _table_df(rows)
    cfg = {}
    for col, spec in _TABLE_COLS.items():
        kind, label, width = spec["kind"], spec["label"], spec.get("width")
        if kind == "num":
            cfg[col] = st.column_config.NumberColumn(label, format=spec["fmt"], width=width)
        elif kind == "chart":
            cfg[col] = st.column_config.LineChartColumn(label, width=width)
        elif kind == "namelink":
            cfg[col] = st.column_config.LinkColumn(label, display_text=_NAME_FROM_URL,
                                                   width=width,
                                                   help="누르면 네이버 금융 종목 페이지가 열립니다")
        else:
            cfg[col] = st.column_config.TextColumn(label, width=width, help=spec.get("help"))
    # 등락률만 한국 관례 색을 입힌다(상승 빨강·하락 파랑). 숫자 열에 Styler를
    # 걸어도 헤더 정렬은 그대로 동작한다.
    styled = df.style.map(lambda v: f"color:{_clr(v)};font-weight:600", subset=["등락률"])
    st.dataframe(styled, column_config=cfg, hide_index=True,
                 use_container_width=True,
                 height=min(len(df) * 35 + 38, 760))


# ── 다중 정렬·필터 ──────────────────────────────────────────────────────────
# 정렬 기준은 multiselect에서 고른 순서가 곧 우선순위다(예: 등급 높은순 → 등락률 높은순 =
# 같은 별 안에서 많이 오른 순). 표 보기도 이 순서로 처음 그려지고, 머리글을 누르면 그 열로
# 다시 정렬된다(st.dataframe 기본 동작).
_SORT_FIELDS = {"등급": "grade", "등락률": "chg", "배수": "mult",
                "거래대금": "value", "회전율": "turn", "포착 시각 수": "cnt"}
_SORT_OPTS = [f"{name} {d}" for name in _SORT_FIELDS for d in ("높은순", "낮은순")]
_GRADE_MIN = {"전체": 0, "★1+": 1, "★2+": 2, "★3": 3}
_CHG_DIR = ["전체", "상승", "하락"]


def _apply_sort(rows: list, slot: str, picks: list) -> None:
    """기본 순서를 깐 뒤 고른 기준을 우선순위 낮은 것부터 안정 정렬로 덧씌운다."""
    if slot == "all":
        rows.sort(key=lambda r: (-r["cnt"], -r["mult"]))     # 여러 시각에 걸칠수록 위로
    else:
        rows.sort(key=lambda r: -r["mult"])
    seen, keys = set(), []
    for p in picks:                      # 같은 항목을 두 방향으로 고르면 먼저 고른 쪽만
        name, d = p.rsplit(" ", 1)
        if name not in seen:
            seen.add(name)
            keys.append((_SORT_FIELDS[name], d == "높은순"))
    for field, desc in reversed(keys):
        rows.sort(key=lambda r: r[field] or 0, reverse=desc)


def _sort_filter_controls(slot: str):
    """(정렬 기준 목록, 시장, 최소 등급, 등락 방향, 최소 포착 시각 수)."""
    # ⚠️ 펼침 제목에 현재 상태를 넣으면 고를 때마다 제목이 바뀌어 펼침이 새 요소로 취급돼
    # 닫혀 버린다(key를 줘도 마찬가지 — 하나 고를 때마다 다시 열어야 했다). 제목은 고정하고
    # 지금 적용된 정렬·필터는 아래 요약 줄(_active_text)에 보인다.
    with st.expander("정렬 · 필터", expanded=False, key="surge_sf"):
        picks = st.multiselect(
            "정렬 기준 (고른 순서가 우선순위)", _SORT_OPTS, key="surge_sortkeys",
            placeholder="기본: 여러 시각에 걸친 순 → 배수 큰 순")
        mkt = st.segmented_control("시장", ["전체", "코스피", "코스닥"], default="전체",
                                   key="surge_mkt") or "전체"
        # 등급(4칸)과 등락(3칸)을 한 줄에 두면 휴대폰 폭(375px)에서 '하락'이 화면 밖으로 나가 따로 둔다
        g = st.segmented_control("등급", list(_GRADE_MIN), default="전체", key="surge_fgrade") or "전체"
        d = st.segmented_control("등락", _CHG_DIR, default="전체", key="surge_fchg") or "전체"
        if slot == "all":
            n = st.segmented_control("포착 시각 수", [1, 2, 3, 4], default=1, key="surge_fcnt",
                                     format_func=lambda v: "전체" if v == 1 else f"{v}개 이상") or 1
        else:
            n = 1
    disable_keyboard("surge_sortkeys")
    return picks, mkt, g, d, n


def _active_text(picks, mkt, g, d, n) -> str:
    parts = []
    if picks:
        parts.append("정렬 " + " → ".join(picks))
    if mkt != "전체":
        parts.append(f"{mkt}만")
    if g != "전체":
        parts.append(f"등급 {g}")
    if d != "전체":
        parts.append(f"{d}만")
    if n > 1:
        parts.append(f"{n}개 시각 이상")
    return " · ".join(parts)


def render_surge():
    if page_header("거래대금 급증 스크리너", "surge"):
        _get_index.clear()
        _get_day.clear()
        st.rerun()

    try:
        idx = _get_index()
    except Exception as e:
        st.error(f"❌ 스크리너 데이터를 가져오지 못했습니다: {type(e).__name__}")
        return
    days = idx.get("days") or []
    if not days:
        st.info("아직 수집된 날이 없습니다.")
        return

    c = idx.get("criteria", {})
    st.caption(f"같은 시각 최근 {c.get('base_days', 20)}거래일 누적거래대금 중앙값의 "
               f"{c.get('multiple', 3):g}배 이상 · 하루 4번 수집 · 갱신 {idx.get('updated', '-')}")
    if c:
        with st.expander("포착 기준과 등급", expanded=False):
            st.markdown(_rules_html(c), unsafe_allow_html=True)

    def _day_label(d):
        from datetime import date
        dd = date.fromisoformat(d["d"])
        return f"{d['d'][5:]} ({_WD[dd.weekday()]}) · {d['n'].get('all', 0)}종목"

    by_day = {d["d"]: d for d in days}
    # 일자 · 시장 · 보기를 한 줄에 두면 휴대폰(375px)에서 '표/카드'가 화면 밖으로 나갔다.
    # 시장은 필터라 '정렬 · 필터' 안으로 옮기고, 이 줄엔 일자와 보기만 둔다.
    with st.container(key="inline_surge_opts"):
        c1, c3 = st.columns([1.6, 1], vertical_alignment="center")
        with c1:
            day = st.selectbox("일자", list(by_day), format_func=lambda d: _day_label(by_day[d]),
                               key="surge_day", label_visibility="collapsed")
        with c3:
            view = st.segmented_control("보기", ["표", "카드"], default="표",
                                        key="surge_view", label_visibility="collapsed") or "표"
    disable_keyboard("surge_day")

    try:
        doc = _get_day(day, idx.get("updated", ""))
    except Exception as e:
        st.error(f"❌ {day} 데이터를 가져오지 못했습니다: {type(e).__name__}")
        return

    counts = {s: len(doc.get("slots", {}).get(s, {}).get("hits", {})) for s in SLOTS}
    counts["all"] = len(doc.get("stocks", {}))
    avail = ["all"] + [s for s in SLOTS if s in doc.get("slots", {})]
    slot = st.segmented_control("시각", avail, default="all", key=f"surge_slot_{day}",
                                format_func=lambda s: f"{LABEL[s]} {counts[s]}",
                                label_visibility="collapsed") or "all"

    rows = _rows(doc, slot)
    picks, mkt, glabel, chg_dir, cnt_min = _sort_filter_controls(slot)
    gmin = _GRADE_MIN[glabel]
    active = _active_text(picks, mkt, glabel, chg_dir, cnt_min)
    total = len(rows)
    rows = [r for r in rows
            if (mkt == "전체" or r["st"].get("m") == ("K" if mkt == "코스피" else "Q"))
            and (r["grade"] or 0) >= gmin
            and (chg_dir == "전체" or ((r["chg"] or 0) > 0 if chg_dir == "상승" else (r["chg"] or 0) < 0))
            and r["cnt"] >= cnt_min]
    _apply_sort(rows, slot, picks)

    all4 = sum(1 for r in rows if r["cnt"] == len(SLOTS))
    taken = doc.get("slots", {}).get(slot, {}).get("t", "") if slot != "all" else ""
    note = doc.get("note")
    badge = (f"<span style='display:inline-block;white-space:nowrap;background:#6b7a8c;color:#fff;font-size:10.5px;font-weight:700;"
             f"padding:1px 6px;border-radius:3px;margin-left:6px;'>{html.escape(note)}</span>") if note else ""
    st.markdown(
        f"<div style='font-size:12.5px;color:#666;margin:2px 0 6px;'>"
        f"{LABEL[slot]} 포착 <b>{len(rows)}</b>종목"
        + (f" (필터 전 {total})" if len(rows) != total else "")
        + (f" · 네 시각 모두 <b>{all4}</b>" if slot == "all" else "")
        + (f" · 수집 {html.escape(taken[11:16])}" if taken else "")
        + f"{badge}"
        + (f"<div style='color:#555;margin-top:2px;'>적용 중: {html.escape(active)}</div>" if active else "")
        + "</div>",
        unsafe_allow_html=True,
    )

    if not rows:
        st.info("이 조건에 잡힌 종목이 없습니다.")
        return

    if view == "표":
        _render_table(rows)
        st.caption(f"정렬 · 필터 순서로 표시 · 열 머리글을 누르면 그 열로 다시 정렬 · "
                   f"시세는 네이버 금융 공개 데이터 · 수집 리포 [kosdaq-surge]({_SITE})")
        return

    # 같은 화면(일자·시각·시장·정렬)을 보는 동안만 '더 보기'가 누적된다
    sig = (day, slot, mkt, tuple(picks), gmin, chg_dir, cnt_min)
    if st.session_state.get("_surge_sig") != sig:
        st.session_state["_surge_sig"] = sig
        st.session_state["_surge_limit"] = _PAGE
    limit = st.session_state["_surge_limit"]

    st.markdown(
        "<div style='border:1px solid #eee;border-radius:8px;overflow:hidden;'>"
        + "".join(_card(r) for r in rows[:limit]) + "</div>",
        unsafe_allow_html=True,
    )
    if len(rows) > limit:
        if st.button(f"더 보기 ({len(rows) - limit}종목 남음)", key="surge_more", use_container_width=True):
            st.session_state["_surge_limit"] = limit + _PAGE
            st.rerun()
    st.caption(f"시세는 네이버 금융 공개 데이터 · 수집 리포 [kosdaq-surge]({_SITE})")
