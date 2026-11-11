"""
trade_tenday.py — 수출입 탭의 '10일 단위 속보' 화면 (관세청 잠정치).

━━ 왜 따로 있나 ━━
같은 탭의 '품목별 월간'은 관세청 **확정치**(Itemtrade)를 쓰는데 1~2개월 지연된다.
월중에 방향을 보려면 10일 단위 잠정치가 유일한 공개 경로다. 한국에는 **주 단위
수출 통계가 없다** — 유료로도 못 산다. 집계 자체가 10일(순) 단위다:
1~10일은 11일에, 1~20일은 21일에, 월 전체는 익월 1일에 공표된다.

축이 달라 월간 화면과 섞을 수 없어 화면을 나눴다 — 여기는 HS코드가 아니라
**MTI 기준 주요 품목/국가 11개 고정**이고, 월간 화면은 세부품목 76개다.

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
def _spark(df, col: str, color: str, n: int = 36):
    """구간 순증 스파크라인. 축은 fixedrange로 잠그고 툴팁은 남긴다.

    누적값을 그대로 그으면 달마다 올라갔다 0으로 떨어지는 톱니가 된다 —
    선은 순증, 큰 숫자는 누적이고 툴팁에 둘 다 적는다.
    """
    d = df.tail(n)
    x = list(d["label"])
    y = [float(v) for v in d[col + "_inc"]]
    fill = {"#ef5350": "rgba(239,83,80,0.12)",
            "#1565C0": "rgba(21,101,192,0.12)"}.get(color, "rgba(158,158,158,0.12)")
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=x, y=y, mode="lines", line=dict(color=color, width=1.6),
        fill="tozeroy", fillcolor=fill, showlegend=False,
        customdata=[float(v) for v in d[col]],
        hovertemplate=("<b>%{x}</b><br>구간 순증 <b>$%{y:,.0f}M</b>"
                       "<br>누적 $%{customdata:,.0f}M<extra></extra>"),
    ))
    if x:
        fig.add_trace(go.Scatter(
            x=[x[-1]], y=[y[-1]], mode="markers",
            marker=dict(color=color, size=6, line=dict(color="#fff", width=1)),
            showlegend=False, hoverinfo="skip",
        ))
    fig.update_layout(
        height=110, margin=dict(l=0, r=4, t=4, b=18),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        dragmode=False, hovermode="x",
        xaxis=dict(tickmode="array", tickvals=x[::9], ticklen=0,
                   tickfont=dict(size=7, color="#aaa"),
                   showgrid=False, zeroline=False, fixedrange=True),
        yaxis=dict(showticklabels=False, showgrid=False, zeroline=False,
                   rangemode="tozero", fixedrange=True),
    )
    return fig


# ⚠️ 본문에 백틱(코드 스팬)을 쓰지 말 것 — 한글이 들어가면 글자가 쪼그라들어
#    오히려 안 읽힌다(모바일 실측). 물결표는 취소선이 되므로 \~로 escape한다.
_HELP = r"""
**값은 월내 누적입니다.** 1\~10일 → 1\~20일 → 1\~말일 순으로 쌓인 금액이고,
카드의 큰 숫자가 그 누적값입니다. 선은 차분해 낸 **구간 순증**이라 10일치씩
끊어서 보는 그림입니다.

**비교는 전년 동기끼리 합니다.** 수출은 계절성과 조업일수 영향이 커서 직전
구간과만 비교하면 방향이 뒤집힙니다 — 추석이 낀 달의 1\~10일이 대표적입니다.
그래서 카드 색은 전년 **같은 월·같은 구간** 기준입니다. 보조로 적는 '직전 구간'은
바로 앞 10일치와의 비교인데, 말일 구간만 10\~11일이라 길이가 하루 다를 수 있습니다.

**잠정치입니다.** 통관 기준으로 뒤에 정정되고, 확정치는 '품목별 월간' 화면
쪽입니다. 품목·국가도 MTI 기준 주요 11개 고정이라 세부품목 단위로는 못 쪼갭니다.
"""


# ── 화면 ─────────────────────────────────────────────────────────────────────
def clear_cache() -> None:
    _get.clear()


def render_tenday() -> None:
    # ⚠️ st.caption은 마크다운이라 ~로 감싼 구간이 **취소선**이 된다("1~10일은 11일 ·
    #    1~20일"이 통째로 그어졌다, 2026-11-11 실측). 물결표는 백슬래시로 escape한다.
    st.caption("관세청 10일 단위 잠정치 · 1\\~10일은 11일 · 1\\~20일은 21일 · "
               "월 전체는 익월 1일 공표 · 2016년 1월부터 · 1시간 캐시")

    api_key = st.secrets.get("DATA_GO_KR_KEY", "")
    spans = {"1년": 1, "2년": 2, "3년": 3, "5년": 5, "10년": 10}

    c1, c2, c3 = st.columns(3)
    with c1:
        flow = st.selectbox("구분", ["수출", "수입"], key="td_flow")
    with c2:
        axis = st.selectbox("기준", ["품목별", "국가별"], key="td_axis")
    with c3:
        span = st.selectbox("기간", list(spans), index=2, key="td_span")
    disable_keyboard("td_flow", "td_axis", "td_span")

    now = datetime.now()
    b = f"{now.year}{now.month:02d}"
    a = max(_ym_add(b, -12 * spans[span]), _START)
    kind = f"{flow}_{'품목' if axis == '품목별' else '국가'}"

    # 요약 줄의 무역수지 때문에 수출·수입 '전체'를 늘 같이 받는다(캐시 1시간이라
    # 화면을 오가도 다시 받지 않는다).
    jobs = {
        "main": lambda: _get(api_key, kind, a, b),
        "수출": lambda: _get(api_key, "수출_품목", a, b),
        "수입": lambda: _get(api_key, "수입_품목", a, b),
    }
    res = run_with_progress(jobs, "10일 단위 잠정치 불러오는 중", show_after=_SHOW_AFTER)
    bad = [k for k, v in res.items() if isinstance(v, Exception)]
    if bad:
        err = res[bad[0]]
        st.error(f"❌ 10일 단위 통계를 가져오지 못했습니다 — {type(err).__name__}: {err}")
        st.caption("data.go.kr에서 해당 API 활용신청이 되어 있는지 확인하세요 "
                   "(수출·수입 × 품목별·국가별이 각각 별도 신청입니다).")
        return

    df, exp, imp = res["main"], res["수출"], res["수입"]
    names = [n for n in _SRC[kind][2] if n != "전체"]

    # ── 요약: 최신 구간의 수출 · 수입 · 무역수지 ──────────────────────────────
    i = len(exp) - 1
    row = exp.iloc[i]
    jm = imp.index[(imp["ym"] == row["ym"]) & (imp["seq"] == row["seq"])]
    j = int(jm[0]) if len(jm) else None

    e_cur, e_yoy = float(row["전체"]), _yoy(exp, "전체", i)
    i_cur = float(imp.iloc[j]["전체"]) if j is not None else None
    i_yoy = _yoy(imp, "전체", j) if j is not None else None
    bal = (e_cur - i_cur) if i_cur is not None else None
    e_prev = _prev_year(exp, "전체", i)
    i_prev = _prev_year(imp, "전체", j) if j is not None else None
    bal_prev = (e_prev - i_prev) if (e_prev is not None and i_prev is not None) else None

    st.markdown(
        f"<div style='font-size:12px;color:#888;margin:2px 0 8px;'>"
        f"최신 <b style='color:#555;'>{row['label']}</b> · "
        f"전년 같은 월·같은 구간 대비 · 통관 잠정치</div>",
        unsafe_allow_html=True,
    )

    def _sumcard(title, val, yoy):
        clr = "#ef5350" if (yoy or 0) > 0 else "#1565C0" if (yoy or 0) < 0 else "#888"
        v = "-" if val is None else f"${val:,.0f}M"
        return (f"<div style='font-size:12px;color:#888;margin-bottom:1px;'>{title}</div>"
                f"<div style='font-size:19px;font-weight:700;line-height:1.2;'>{v}</div>"
                f"<div style='font-size:11.5px;color:{clr};font-weight:600;'>"
                f"{_fmt_pct(yoy, '전년')}</div>")

    s1, s2, s3 = st.columns(3)
    with s1:
        st.markdown(_sumcard("수출 전체", e_cur, e_yoy), unsafe_allow_html=True)
    with s2:
        st.markdown(_sumcard("수입 전체", i_cur, i_yoy), unsafe_allow_html=True)
    with s3:
        # 수지는 음수가 될 수 있어 증감률 대신 전년 동기와의 차액으로 적는다 —
        # 전년이 적자였으면 %가 부호까지 뒤집혀 읽힌다.
        d = (bal - bal_prev) if (bal is not None and bal_prev is not None) else None
        bclr = "#ef5350" if (bal or 0) > 0 else "#1565C0" if (bal or 0) < 0 else "#888"
        dtxt = "전년 -" if d is None else f"전년 {'+' if d >= 0 else '−'}${abs(d):,.0f}M"
        bval = "-" if bal is None else f"${bal:,.0f}M"
        st.markdown(
            f"<div style='font-size:12px;color:#888;margin-bottom:1px;'>무역수지</div>"
            f"<div style='font-size:19px;font-weight:700;line-height:1.2;color:{bclr};'>"
            f"{bval}</div>"
            f"<div style='font-size:11.5px;color:#8b98a5;font-weight:600;'>{dtxt}</div>",
            unsafe_allow_html=True,
        )

    with st.expander("읽는 법", expanded=False):
        st.markdown(_HELP)

    # ── 품목 / 국가 카드 ─────────────────────────────────────────────────────
    k = len(df) - 1
    tot = float(df.iloc[k]["전체"]) or 0.0
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
            share = f" · 비중 {cur / tot * 100:.1f}%" if tot > 0 else ""
            with cols[ci]:
                st.markdown(
                    f"<div style='font-size:12px;color:#888;margin-bottom:1px;'>"
                    f"{html.escape(nm)}</div>"
                    f"<div style='font-size:17px;font-weight:700;line-height:1.2;'>"
                    f"${cur:,.1f}M</div>"
                    f"<div style='font-size:11.5px;color:{clr};font-weight:600;'>"
                    f"{_fmt_pct(yoy, '전년')}  ·  {_fmt_pct(mom, '직전 구간')}</div>"
                    f"<div style='font-size:11px;color:#8b98a5;margin-top:1px;'>"
                    f"구간 순증 ${inc:,.1f}M{share}</div>",
                    unsafe_allow_html=True,
                )
                st.plotly_chart(
                    _spark(df, nm, clr),
                    use_container_width=True,
                    # staticPlot은 두지 않는다 — 마우스오버 툴팁까지 죽는다.
                    config={"displayModeBar": False, "scrollZoom": False,
                            "staticPlot": False},
                )

    st.caption("출처: 관세청 10일 단위 잠정치(data.go.kr) · "
               "숫자는 월내 누적, 선은 구간 순증 · 품목·국가는 MTI 기준 주요 11개 고정")
