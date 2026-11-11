"""
trade_tenday.py — 수출입 탭의 '10일 단위 속보' 화면 (관세청 잠정치).

━━ 왜 따로 있나 ━━
같은 탭의 '품목별 월간'은 관세청 **확정치**(Itemtrade)를 쓰는데 1~2개월 지연된다.
월중에 방향을 보려면 10일 단위 잠정치가 유일한 공개 경로다. 한국에는 **주 단위
수출 통계가 없다** — 유료로도 못 산다. 집계 자체가 10일(순) 단위다:
1~10일은 11일에, 1~20일은 21일에, 월 전체는 익월 1일에 공표된다.

축이 달라 월간 화면과 섞을 수 없어 화면을 나눴다 — 여기는 HS코드가 아니라
**MTI 기준 주요 품목/국가 11개 고정**이고, 월간 화면은 세부품목 76개다.

━━ ⚠️ 10일 단위는 더 세분화할 수 없다 (2026-11-12 확인) ━━
"배터리·화장품은 없나"는 질문이 나오는 자리인데, **없고 넣을 방법도 없다.**
10일 단위로 공개되는 품목은 아래 11개가 전부다:
    수출: 전체 · 반도체 · 철강제품 · 승용차 · 석유제품 · 무선통신기기 ·
          선박 · 자동차부품 · 컴퓨터주변기기 · 정밀기기 · 가전제품
이차전지·화장품·바이오는 이 목록에 없다. data.go.kr 전수 검색에서 관세청의
10일 단위 데이터셋은 **네 개(수출/수입 × 품목/국가)뿐**이고, HS코드별 10일
통계는 아예 공개되지 않는다. 월간 API(`Itemtrade`)도 `strtYymm`/`endYymm`만
받아 월보다 잘게 못 쪼갠다. 다른 소스를 찾아 헤매지 말 것.

그 대신 **2차전지 9품목·화장품 5품목은 `품목별 월간` 화면에 이미 있다**
(`trade_items.py`, HS코드 단위). 세분화가 필요하면 그쪽이다 — 대신 확정치라
1~2개월 늦다. "세분화 ↔ 신속" 둘 중 하나만 고를 수 있는 구조다.

수입·무역수지는 2026-11-12 사용자 요청으로 화면에서 뺐다(수출만). `_SRC`의
수입 두 줄은 남아 있고 활용신청도 되어 있어, 되살리려면 '구분' 선택 상자만
되돌리면 된다.

━━ API (data.go.kr, 전부 무료·자동승인이지만 **각각 따로 활용신청**해야 한다) ━━
    15157908 수출 주요품목별  /1220000/prlstMmUtPrviExpAcrs/getPrlstMmUtPrviExpAcrs
    15157901 수입 주요품목별  /1220000/prlstMmUtPrviImpAcrs/getPrlstMmUtPrviImpAcrs
    15157941 수출 주요국가별  /1220000/cntyMmUtPrviExpAcrs/getCntyMmUtPrviExpAcrs
    15157909 수입 주요국가별  /1220000/cntyMmUtPrviImpAcrs/getCntyMmUtPrviImpAcrs
파라미터는 기존 Itemtrade와 같은 `serviceKey` · `strtYymm` · `endYymm` 셋뿐이다
(numOfRows·pageNo는 받지 않는다). 키는 수출입 탭과 **같은 DATA_GO_KR_KEY**.

━━ 실측으로 확인한 함정 셋 (2026-11-11) ━━
1. **값은 월내 누적이다.** 1~10 → 1~20 → 1~말일로 쌓인 금액이라, 그대로 선으로
   그으면 달마다 톱니가 된다. `_inc` 열이 구간 순증(차분)이다.
2. **한 번에 약 355행이 상한인데, 넘으면 에러가 아니라 조용히 0건이 온다.**
   201701~202611은 355건 ○, 201610~202611은 0건 ×(resultCode는 그대로 00).
   "데이터 없음"으로 잘못 읽기 딱 좋아서 `_TENDAY_SPAN`으로 끊어 받는다.
3. **단위는 천 달러다.** 확정치와 교차검증했다 — 202609 '반도체' 월 합계가
   $60.5B인데 같은 달 HS 8542 확정치가 $43.1B로 1.40배다. MTI '반도체'가
   HS 8542보다 넓으니(개별소자·웨이퍼 포함) 들어맞는다.

이력은 2016년 1월부터다(201501~201503은 0건).
"""
import html
import xml.etree.ElementTree as ET
from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
from requests.adapters import HTTPAdapter

from ui_common import run_with_progress
from ui_mobile import disable_keyboard

_URL = "https://apis.data.go.kr/1220000/{svc}/{op}"

_HTTP = requests.Session()
_HTTP.mount("https://", HTTPAdapter(pool_connections=4, pool_maxsize=8))

# ⚠️ 응답 필드가 itemUsdAmt00~10이고 "몇 번이 무엇인지"는 Swagger에만 적혀 있다.
#    순서를 바꾸면 조용히 다른 품목 값이 찍힌다 — 데이터셋 페이지의 swaggerJson에서
#    꺼낸 순서 그대로다. 추측해서 고치지 말 것.
_SRC = {
    "수출_품목": ("prlstMmUtPrviExpAcrs", "getPrlstMmUtPrviExpAcrs",
                  ("전체", "반도체", "철강제품", "승용차", "석유제품", "무선통신기기",
                   "선박", "자동차부품", "컴퓨터주변기기", "정밀기기", "가전제품")),
    # 수입 둘은 2026-11-12 사용자 요청으로 화면에서 뺐다("무조건 수출만"). 표는
    # 남겨 둔다 — 활용신청은 되어 있고, 되살리려면 render_tenday에 '구분' 선택
    # 상자를 되돌리기만 하면 된다.
    "수입_품목": ("prlstMmUtPrviImpAcrs", "getPrlstMmUtPrviImpAcrs",
                  ("전체", "반도체", "원유", "기계류", "가스", "반도체 제조용장비",
                   "정밀기기", "석유제품", "무선통신기기", "승용차", "석탄")),
    "수출_국가": ("cntyMmUtPrviExpAcrs", "getCntyMmUtPrviExpAcrs",
                  ("전체", "중국", "미국", "유럽연합", "베트남", "홍콩",
                   "일본", "대만", "인도", "싱가포르", "말레이시아")),
    "수입_국가": ("cntyMmUtPrviImpAcrs", "getCntyMmUtPrviImpAcrs",
                  ("전체", "중국", "미국", "유럽연합", "일본", "베트남",
                   "호주", "대만", "사우디아라비아", "러시아연방", "말레이시아")),
}

_START = "201601"     # 실측: 201501~201503은 0건
_SPAN = 96            # 한 달 3행 × 96개월 = 288행 (상한 약 355행)
_COLS = 3             # 카드 한 줄에 3개 (수출입·매크로 탭과 동일)
_SHOW_AFTER = 0.3     # 수출입 탭 공통 — 이 탭은 진행률을 빨리 띄운다


# ── 데이터 ───────────────────────────────────────────────────────────────────
def _ym_add(ym: str, n: int) -> str:
    t = int(ym[:4]) * 12 + int(ym[4:]) - 1 + n
    return f"{t // 12:04d}{t % 12 + 1:02d}"


def _ym_windows(a: str, b: str, span: int = _SPAN) -> list:
    """[a, b]를 span개월짜리 창으로 끊는다 — 행 상한(약 355) 때문."""
    out, cur = [], a
    while cur <= b:
        end = min(_ym_add(cur, span - 1), b)
        out.append((cur, end))
        cur = _ym_add(end, 1)
    return out


def _label(ym: str, dt: str) -> str:
    a, b = dt.split("~")
    return f"{ym[2:4]}년{ym[4:6]}월 {int(a)}~{int(b)}일"


def _fetch_window(api_key: str, kind: str, a: str, b: str) -> list:
    svc, op, names = _SRC[kind]
    r = _HTTP.get(_URL.format(svc=svc, op=op),
                  params={"serviceKey": api_key, "strtYymm": a, "endYymm": b},
                  timeout=25)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}")
    rows = []
    for it in ET.fromstring(r.text).findall(".//item"):
        ym = (it.findtext("priodMon") or "").strip()
        dt = (it.findtext("priodDt") or "").strip()
        if len(ym) != 6 or "~" not in dt:
            continue
        rec = {"ym": ym, "dt": dt}
        for i, nm in enumerate(names):
            raw = (it.findtext(f"itemUsdAmt{i:02d}") or "").replace(",", "").strip()
            rec[nm] = float(raw) / 1000.0 if raw else 0.0   # 천 달러 -> 백만 달러
        rows.append(rec)
    return rows


@st.cache_data(ttl=3600, show_spinner=False)
def _get(api_key: str, kind: str, a: str, b: str):
    """[ym, dt, endd, seq, label, 품목…, 품목_inc…] 정렬된 DataFrame."""
    rows = []
    for wa, wb in _ym_windows(a, b):
        rows += _fetch_window(api_key, kind, wa, wb)
    if not rows:
        # 빈 결과를 돌려주면 TTL 1시간 동안 "데이터 없음"이 굳는다 — 예외로 올린다.
        raise RuntimeError(f"{kind} {a}~{b}: 빈 응답 (행 상한 초과일 수 있다)")
    df = pd.DataFrame(rows).drop_duplicates(subset=["ym", "dt"])
    df["endd"] = df["dt"].str.split("~").str[1].astype(int)
    df = df.sort_values(["ym", "endd"]).reset_index(drop=True)
    df["seq"] = df.groupby("ym").cumcount()
    df["label"] = [_label(r.ym, r.dt) for r in df.itertuples()]
    for c in _SRC[kind][2]:
        df[c + "_inc"] = df[c] - df.groupby("ym")[c].shift(1).fillna(0.0)
    return df


def _prev_year(df, col: str, i: int):
    """i행의 전년 동기값 — 같은 월·같은 구간 순번.

    ⚠️ endd로 맞추면 안 된다. 월 전체 행이 30/31이고 2월은 28/29라 해마다 달라
    매칭이 어긋난다. 구간 순번(seq 0·1·2)은 늘 같다.
    """
    row = df.iloc[i]
    py = f"{int(row['ym'][:4]) - 1}{row['ym'][4:]}"
    m = df[(df["ym"] == py) & (df["seq"] == row["seq"])]
    return float(m.iloc[0][col]) if not m.empty else None


def _yoy(df, col: str, i: int):
    prev = _prev_year(df, col, i)
    if prev is None or prev <= 0:
        return None
    return (float(df.iloc[i][col]) / prev - 1) * 100


def _fmt_pct(v, label: str) -> str:
    if v is None:
        return f"{label} -"
    arrow = "▲" if v > 0 else "▼" if v < 0 else "─"
    a = abs(v)
    return f"{label} {arrow} 999%+" if a >= 999 else f"{label} {arrow} {a:.1f}%"


# ── 차트 ─────────────────────────────────────────────────────────────────────
# 한 달을 막대 하나로 세우고 1~10 / 11~20 / 21~말일을 아래부터 쌓는다(2026-11-11 요청).
# 선 하나로 순증만 긋던 때는 "이번 달이 어디까지 왔나"가 안 보였다 — 월 전체 크기와
# 그 안의 10일치 기여가 한 눈에 같이 들어와야 월중 판단이 된다.
_SEG = ("1~10일", "11~20일", "21일~말일")
_RGB = {"#ef5350": (239, 83, 80), "#1565C0": (21, 101, 192)}
# 아래(이른 구간)가 진하고 위로 갈수록 옅다. ⚠️ 구간 구분에 빨강/파랑을 섞어 쓰면
# 안 된다 — 이 앱에서 두 색은 '올랐다/내렸다'다. 카드 색 **한 가지의 농도**로만 나눈다.
_SEG_ALPHA = (0.95, 0.60, 0.32)


def _rgba(color: str, a: float) -> str:
    r, g, b = _RGB.get(color, (138, 138, 138))
    return f"rgba({r},{g},{b},{a})"


def _bars(df, col: str, color: str, height: int = 110):
    """월별 누적 막대. 한 칸이 한 달, 아래부터 1~10 / 11~20 / 21~말일.

    값은 `_inc`(구간 순증)를 쌓으므로 막대 전체 높이가 그 달의 월 합계가 된다.
    원값(누적)을 그대로 쌓으면 2~3배로 부풀어 오른다 — 거기에 쓰지 말 것.
    진행 중인 달은 받은 구간만 서서 "아직 1~10일까지"가 그림으로 보인다.
    """
    piv = (df.pivot_table(index="ym", columns="seq", values=col + "_inc",
                          aggfunc="first").sort_index())
    x = [f"{ym[2:4]}.{ym[4:6]}" for ym in piv.index]
    month_tot = [float(v) for v in piv.sum(axis=1)]

    fig = go.Figure()
    for sq in range(3):
        if sq not in piv.columns:
            continue
        y = [None if v != v else float(v) for v in piv[sq]]
        fig.add_trace(go.Bar(
            x=x, y=y, name=_SEG[sq], showlegend=False,
            marker=dict(color=_rgba(color, _SEG_ALPHA[sq]), line=dict(width=0)),
            customdata=month_tot,
            hovertemplate=(f"<b>%{{x}}</b> · {_SEG[sq]}<br>$%{{y:,.0f}}M"
                           "<br>그 달 합계 $%{customdata:,.0f}M<extra></extra>"),
        ))
    fig.update_layout(
        barmode="stack", bargap=0.18,
        height=height, margin=dict(l=0, r=4, t=4, b=18),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        dragmode=False, hovermode="closest",
        xaxis=dict(tickmode="array", tickvals=x[::6], ticklen=0,
                   tickfont=dict(size=7, color="#aaa"),
                   showgrid=False, zeroline=False, fixedrange=True),
        yaxis=dict(showticklabels=False, showgrid=False, zeroline=False,
                   rangemode="tozero", fixedrange=True),
    )
    return fig


# ── 화면 ─────────────────────────────────────────────────────────────────────
def clear_cache() -> None:
    _get.clear()


def render_tenday() -> None:
    # ⚠️ st.caption은 마크다운이라 ~로 감싼 구간이 **취소선**이 된다("1~10일은 11일 ·
    #    1~20일"이 통째로 그어졌다, 2026-11-11 실측). 물결표는 백슬래시로 escape한다.
    st.caption("관세청 수출 10일 단위 잠정치 · 1\\~10일은 11일 · 1\\~20일은 21일 · "
               "월 전체는 익월 1일 공표 · 2016년 1월부터 · 1시간 캐시")

    api_key = st.secrets.get("DATA_GO_KR_KEY", "")
    spans = {"1년": 1, "2년": 2, "3년": 3, "5년": 5, "10년": 10}

    c1, c2 = st.columns(2)
    with c1:
        axis = st.selectbox("기준", ["품목별", "국가별"], key="td_axis")
    with c2:
        span = st.selectbox("기간", list(spans), index=2, key="td_span")
    disable_keyboard("td_axis", "td_span")

    now = datetime.now()
    b = f"{now.year}{now.month:02d}"
    a = max(_ym_add(b, -12 * spans[span]), _START)
    kind = f"수출_{'품목' if axis == '품목별' else '국가'}"

    res = run_with_progress({"main": lambda: _get(api_key, kind, a, b)},
                            "10일 단위 잠정치 불러오는 중", show_after=_SHOW_AFTER)
    df = res["main"]
    if isinstance(df, Exception):
        st.error(f"❌ 10일 단위 통계를 가져오지 못했습니다 — {type(df).__name__}: {df}")
        st.caption("data.go.kr에서 해당 API 활용신청이 되어 있는지 확인하세요 "
                   "(품목별·국가별이 각각 별도 신청입니다).")
        return

    names = [n for n in _SRC[kind][2] if n != "전체"]
    k = len(df) - 1

    # ── 수출 전체 (넓은 카드 한 장) ───────────────────────────────────────────
    # 수입·무역수지는 2026-11-12 사용자 요청으로 뺐다. 비워진 자리를 '전체'에 주어
    # 그리드에서 빠져 있던 합계를 큰 막대 한 장으로 띄운다 — 품목 카드가 11개 중
    # 10개뿐이라 "그래서 수출 전체는 얼마냐"가 숫자 한 줄로만 있었다.
    tot = float(df.iloc[k]["전체"])
    t_yoy = _yoy(df, "전체", k)
    t_prev = _prev_year(df, "전체", k)
    t_inc = float(df.iloc[k]["전체_inc"])
    p_inc = float(df.iloc[k - 1]["전체_inc"]) if k >= 1 else None
    t_mom = ((t_inc / p_inc - 1) * 100) if (p_inc and p_inc > 0) else None
    tclr = "#ef5350" if (t_yoy or 0) > 0 else "#1565C0" if (t_yoy or 0) < 0 else "#888"

    with st.container(key="mcard_td_all"):
        prev_line = ("" if t_prev is None else
                     f"<div style='font-size:11px;color:#8b98a5;margin-top:1px;'>"
                     f"전년 같은 구간 ${t_prev:,.0f}M</div>")
        st.markdown(
            f"<div style='font-size:12px;color:#888;margin-bottom:1px;'>"
            f"수출 전체 · <b style='color:#555;'>{df.iloc[k]['label']}</b> 누적</div>"
            f"<div style='font-size:22px;font-weight:700;line-height:1.2;'>"
            f"${tot:,.0f}M</div>"
            f"<div style='font-size:12px;color:{tclr};font-weight:600;'>"
            f"{_fmt_pct(t_yoy, '전년')}  ·  {_fmt_pct(t_mom, '직전 구간')}</div>"
            f"{prev_line}",
            unsafe_allow_html=True,
        )
        st.plotly_chart(_bars(df, "전체", tclr, height=170), use_container_width=True,
                        config={"displayModeBar": False, "scrollZoom": False,
                                "staticPlot": False})
        # 막대 읽는 법은 여기 한 줄로 끝낸다 — '읽는 법' 펼침은 2026-11-12에 뺐다
        # (카드가 스스로 말하는 그림이라 글이 더 길었다).
        st.markdown(
            "<div style='font-size:11px;color:#8b98a5;'>"
            "막대 하나가 <b style='color:#666;'>한 달</b> · 아래부터 "
            "<b style='color:#666;'>1~10일 · 11~20일 · 21일~말일</b>로 쌓아 "
            "전체 높이가 그 달 합계 · 진행 중인 달은 받은 구간까지만</div>",
            unsafe_allow_html=True,
        )

    # ── 품목 / 국가 카드 ─────────────────────────────────────────────────────
    ranked = sorted(names, key=lambda n: (_yoy(df, n, k) is None, -(_yoy(df, n, k) or 0)))

    for start in range(0, len(ranked), _COLS):
        cols = st.columns(_COLS)
        for ci, nm in enumerate(ranked[start:start + _COLS]):
            cur = float(df.iloc[k][nm])
            yoy = _yoy(df, nm, k)
            inc = float(df.iloc[k][nm + "_inc"])
            pinc = float(df.iloc[k - 1][nm + "_inc"]) if k >= 1 else None
            mom = ((inc / pinc - 1) * 100) if (pinc and pinc > 0) else None
            clr = "#ef5350" if (yoy or 0) > 0 else "#1565C0" if (yoy or 0) < 0 else "#888"
            share = f"비중 {cur / tot * 100:.1f}%" if tot > 0 else ""   # tot = 전체
            # 첫 구간(1~10일)은 누적 == 순증이라 같은 숫자를 두 번 적게 된다 —
            # 그때는 구간 순증 줄을 빼고 비중만 남긴다.
            seg = "" if abs(inc - cur) < 0.05 else f"구간 순증 ${inc:,.1f}M"
            foot = " · ".join(x for x in (share, seg) if x)
            with cols[ci], st.container(key=f"mcard_td_{nm}"):
                st.markdown(
                    f"<div style='font-size:12px;color:#888;margin-bottom:1px;'>"
                    f"{html.escape(nm)}</div>"
                    f"<div style='font-size:17px;font-weight:700;line-height:1.2;'>"
                    f"${cur:,.1f}M</div>"
                    f"<div style='font-size:11.5px;color:{clr};font-weight:600;'>"
                    f"{_fmt_pct(yoy, '전년')}  ·  {_fmt_pct(mom, '직전 구간')}</div>"
                    f"<div style='font-size:11px;color:#8b98a5;margin-top:1px;'>"
                    f"{foot}</div>",
                    unsafe_allow_html=True,
                )
                st.plotly_chart(
                    _bars(df, nm, clr),
                    use_container_width=True,
                    # staticPlot은 두지 않는다 — 마우스오버 툴팁까지 죽는다.
                    config={"displayModeBar": False, "scrollZoom": False,
                            "staticPlot": False},
                )

    # 막대 읽는 법은 그리드 바로 위 한 줄과 '읽는 법'에 이미 있다 — 여기까지
    # 적으면 같은 말이 세 번이 된다(화면 공통 규칙).
    st.caption("출처: 관세청 10일 단위 잠정치(data.go.kr) · "
               "품목·국가는 MTI 기준 주요 11개 고정")
