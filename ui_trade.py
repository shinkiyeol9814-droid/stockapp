"""
ui_trade.py — 수출입 동향 탭 (관세청 품목별 수출입실적).

원래 매크로 탭 안의 라디오 토글 한 칸이었는데, 시장 지표와 성격이 달라
(정부 API · 월 단위 · 품목별) 별도 메뉴로 분리했다.

데이터 소스: 관세청_품목별 수출입실적 (data.go.kr, DATA_GO_KR_KEY 필요).
수출·수입·무역수지를 지표로 골라 월별/분기별로 본다.
"""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import requests
import html
import concurrent.futures
import statistics
import xml.etree.ElementTree as ET
from datetime import datetime



# 세부품목 카탈로그(HS코드 ↔ 관련 상장종목)는 trade_items.py 에 분리했다 —
# 70여 개라 여기 두면 화면 로직이 안 보인다.
from trade_items import TRADE_ITEMS, themes, lookup, item_count

_API_BASE = "https://apis.data.go.kr/1220000/Itemtrade/getItemtradeList"
# 국가 차원이 필요할 때 쓰는 엔드포인트 (품목 × 국가).
_NITEM_BASE = "https://apis.data.go.kr/1220000/nitemtrade/getNitemtradeList"


def _month_list(n_months: int) -> list[str]:
    """최근 n_months개월의 YYYYMM 문자열 (오래된 순)."""
    now = datetime.today()
    out = []
    for i in range(n_months - 1, -1, -1):
        tm = now.year * 12 + (now.month - 1) - i
        out.append(f"{tm // 12}{tm % 12 + 1:02d}")
    return out


def _year_windows(months: list[str]) -> list[tuple[str, str]]:
    """
    조회 구간을 1년 이하 창으로 쪼갠다.

    💡 이 API는 1년을 넘는 구간을 "시작과 종료의 조회기간은 1년이내 기간만
    가능합니다"로 거부한다. 반면 1년 이내면 월별 행을 한 번에 다 주므로
    36개월을 3회로 끝낼 수 있다 — 예전엔 월마다 1회씩 36회를 불렀다.
    """
    return [(months[i], months[min(i + 11, len(months) - 1)])
            for i in range(0, len(months), 12)]


def _fetch_code_range(api_key: str, code: str, a: str, b: str, country):
    """
    HS코드 하나의 [a, b] 구간 월별 실적 -> {"YYYYMM": [수출M, 수입M]}.
    country를 주면 국가 차원이 있는 엔드포인트(nitemtrade)로 간다.

    ⚠️ 응답에 섞여오는 집계 행을 반드시 걸러야 한다:
      · hsCode/hsCd 가 "-" 인 행 (10자리가 아니다)
      · year 가 "총계" 인 행
    안 걸러내면 합계가 정확히 2배로 뛴다.
    """
    url = _NITEM_BASE if country else _API_BASE
    out = {}
    try:
        r = requests.get(
            url,
            params={"serviceKey": api_key, "strtYymm": a, "endYymm": b,
                    "hsSgn": code, "numOfRows": 9999, "pageNo": 1},
            timeout=25,
        )
        if r.status_code != 200:
            return out
        for item in ET.fromstring(r.text).findall(".//item"):
            ym_raw = (item.findtext("year", "") or "").strip()
            if "." not in ym_raw:            # "총계" 등
                continue
            hs = (item.findtext("hsCode", "") or item.findtext("hsCd", "") or "").strip()
            if len(hs) != 10:
                continue
            if country and (item.findtext("statCd", "") or "").strip() != country:
                continue
            ym = ym_raw.replace(".", "")     # "2026.07" -> "202607"
            e = float((item.findtext("expDlr", "0") or "0").replace(",", ""))
            m = float((item.findtext("impDlr", "0") or "0").replace(",", ""))
            row = out.setdefault(ym, [0.0, 0.0])
            row[0] += e / 1_000_000
            row[1] += m / 1_000_000
    except Exception as e:
        # 조용히 넘기면 빈 그래프가 "데이터 없음"처럼 보인다 —
        # 실제로 이 except가 NameError를 삼켜 국가 옵션이 통째로
        # 비어 나온 사고가 있었다. 최소한 로그는 남긴다.
        print(f"[ui_trade] {code} {a}~{b} 조회 실패: {type(e).__name__}: {e}")
    return out


@st.cache_data(ttl=3600, show_spinner=False)
def get_theme_trends(theme: str, n_months: int = 36, country=None):
    """
    테마 안 모든 품목의 월별 시계열을 한 번에 -> {품목명: DataFrame}.

    품목별로 따로 부르는 게 자연스럽지만 같은 HS코드를 공유하거나 조회 구간이
    겹쳐 낭비가 크다. 테마의 고유 코드 × 1년 창으로 한 번씩만 받아 재조립한다
    (조선·기계 = 코드 20개 × 3창 = 60회. 월별 호출이면 720회였다).
    """
    api_key = st.secrets.get("DATA_GO_KR_KEY", "")
    items = TRADE_ITEMS.get(theme, {})
    if not api_key or not items:
        return {}

    months = _month_list(n_months)
    windows = _year_windows(months)
    codes = sorted({c for codes, _ in items.values() for c in codes})

    per_code = {c: {} for c in codes}
    jobs = [(c, a, b) for c in codes for a, b in windows]
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as ex:
        futs = {ex.submit(_fetch_code_range, api_key, c, a, b, country): c
                for c, a, b in jobs}
        for f in concurrent.futures.as_completed(futs):
            c = futs[f]
            for ym, (e, m) in f.result().items():
                row = per_code[c].setdefault(ym, [0.0, 0.0])
                row[0] += e
                row[1] += m

    out = {}
    for name, (item_codes, _stocks) in items.items():
        rows = []
        for ym in months:
            e = sum(per_code.get(c, {}).get(ym, [0.0, 0.0])[0] for c in item_codes)
            m = sum(per_code.get(c, {}).get(ym, [0.0, 0.0])[1] for c in item_codes)
            rows.append(dict(ym=ym, label=f"{ym[2:4]}년{ym[4:]}월",
                             total=e, imports=m, balance=e - m))
        # 확정 통계는 1~2개월 지연 공표된다 — 아직 안 나온 꼬리 달을 자른다.
        # 그대로 두면 선이 0으로 떨어져 "수출이 끊긴" 것처럼 보인다.
        while rows and rows[-1]["total"] == 0 and rows[-1]["imports"] == 0:
            rows.pop()
        if rows:
            out[name] = pd.DataFrame(rows)
    return out


@st.cache_data(ttl=3600, show_spinner=False)
def get_country_options(theme: str, ref_months: int = 3):
    """
    테마 전체의 주요 수출 대상국 -> [(국가코드, "중국 (72%)"), ...] 비중 내림차순.
    최근 몇 달을 합산해 뽑는다 — 한 달만 보면 선적 시점 때문에 순위가 튄다.
    """
    api_key = st.secrets.get("DATA_GO_KR_KEY", "")
    items = TRADE_ITEMS.get(theme, {})
    if not api_key or not items:
        return []
    codes = sorted({c for codes, _ in items.values() for c in codes})
    months = _month_list(ref_months + 1)[:-1]      # 당월 제외
    a, b = months[0], months[-1]

    def _one(code):
        acc = {}
        try:
            r = requests.get(
                _NITEM_BASE,
                params={"serviceKey": api_key, "strtYymm": a, "endYymm": b,
                        "hsSgn": code, "numOfRows": 9999, "pageNo": 1},
                timeout=25,
            )
            if r.status_code != 200:
                return acc
            for item in ET.fromstring(r.text).findall(".//item"):
                if "." not in (item.findtext("year", "") or ""):
                    continue
                cc = (item.findtext("statCd", "") or "").strip()
                nm = (item.findtext("statCdCntnKor1", "") or "").strip()
                if not cc or cc == "-" or not nm or nm == "-":
                    continue
                v = float((item.findtext("expDlr", "0") or "0").replace(",", ""))
                row = acc.setdefault(cc, [nm, 0.0])
                row[1] += v
        except Exception as e:
            print(f"[ui_trade] 국가옵션 {code} 조회 실패: {type(e).__name__}: {e}")
        return acc

    totals = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as ex:
        for acc in ex.map(_one, codes):
            for cc, (nm, v) in acc.items():
                row = totals.setdefault(cc, [nm, 0.0])
                row[1] += v

    grand = sum(v[1] for v in totals.values())
    if grand <= 0:
        return []
    out = []
    for cc, (nm, v) in sorted(totals.items(), key=lambda kv: -kv[1][1])[:15]:
        pct = v / grand * 100
        if pct < 0.5:
            break
        out.append((cc, f"{nm} ({pct:.0f}%)"))
    return out


# ── 차트 ─────────────────────────────────────────────────────────────────────
def _make_card_sparkline(df, note=None):
    """
    카드용 소형 스파크라인. 매크로 탭 카드와 같은 형식으로 맞췄다 —
    여러 품목을 한 화면에 늘어놓고 "무엇이 튀는지" 훑는 게 목적이라,
    개별 차트를 크게 그리는 것보다 작은 걸 많이 보여주는 쪽이 맞다.

    축은 fixedrange로 잠그고 hovermode는 남긴다(월별 수치는 마우스오버로).
    """
    y = list(df["total"])
    x = list(df["label"])
    up = len(y) > 1 and y[-1] >= y[0]
    color = "#ef5350" if up else "#1565C0"
    fill_c = "rgba(239,83,80,0.12)" if up else "rgba(21,101,192,0.12)"

    cust = list(zip(df["imports"], df["balance"]))

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=x, y=y, mode="lines",
        line=dict(color=color, width=1.6),
        fill="tozeroy", fillcolor=fill_c,
        customdata=cust, showlegend=False,
        hovertemplate=("<b>%{x}</b><br>수출 <b>$%{y:,.1f}M</b>"
                       "<br>수입 $%{customdata[0]:,.1f}M"
                       "<br>수지 $%{customdata[1]:,.1f}M<extra></extra>"),
    ))
    fig.add_trace(go.Scatter(
        x=[x[-1]], y=[y[-1]], mode="markers",
        marker=dict(color=color, size=6, line=dict(color="#fff", width=1)),
        showlegend=False, hoverinfo="skip",
    ))

    # 연 경계에만 눈금 — 카드가 작아서 월 라벨을 다 찍으면 뭉갠다
    tickvals = [lb for lb in x if lb.endswith("01월")] or x[::12]

    fig.update_layout(
        height=110, margin=dict(l=0, r=4, t=4, b=18),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        dragmode=False, hovermode="x",
        xaxis=dict(showticklabels=True, tickmode="array", tickvals=tickvals,
                   tickfont=dict(size=7, color="#aaa"), ticklen=0,
                   showgrid=False, zeroline=False, fixedrange=True),
        yaxis=dict(showticklabels=False, showgrid=False, zeroline=False,
                   rangemode="tozero", fixedrange=True),
    )
    # 💡 특이사항 배지는 차트 안에 넣지 않는다 — 최근이 고점이면 선이 바로
    # 우상단으로 올라와 배지와 겹친다. 카드 헤더(HTML)에서 그린다.
    # (note 인자는 호출부 호환을 위해 남겨두되 여기서는 쓰지 않는다.)
    return fig


# ── 렌더링 ────────────────────────────────────────────────────────────────────
def _trend_note(series):
    """
    "N개월 내 최대 / N개월 만에 최대" 배지 문구.
    텔레그램 리서치 채널의 "역대 최대" 코멘트와 같은 개념이고, 매크로 탭
    마일스톤과 같은 방식이다: 현재값을 넘어선 값이 마지막으로 나온 시점부터의
    경과 기간을 센다.

    ⚠️ 조회 구간이 곧 판정 범위다 — "역대"라고 쓰지 않고 기간을 문구에 담는다.
    """
    vals = [v for v in series if v is not None]
    if len(vals) < 4:
        return None
    last = vals[-1]
    if last <= 0:
        return None
    span = len(vals)
    higher = [i for i, v in enumerate(vals[:-1]) if v > last]
    if not higher:
        return f"{span}개월 최대", "#ef5350"
    gap = span - 1 - higher[-1]
    if gap >= 3:
        return f"{gap}개월 만에 최대", "#ef5350"
    lower = [i for i, v in enumerate(vals[:-1]) if v < last]
    if not lower:
        return f"{span}개월 최저", "#1565C0"
    gap = span - 1 - lower[-1]
    if gap >= 3:
        return f"{gap}개월 만에 최저", "#1565C0"
    return None


def _mom(df):
    """전월 대비 증감률(%). 직전 달이 0이면 None."""
    if len(df) < 2:
        return None
    prev = float(df.iloc[-2]["total"])
    if prev <= 0:
        return None
    return (float(df.iloc[-1]["total"]) / prev - 1) * 100


def _coverage(df, window: int = 12) -> int:
    """
    최근 window개월 중 "의미 있는" 실적이 있었던 달 수.

    💡 단순히 0 초과로 세면 0.1M짜리 잡음 달까지 세어버린다. 반도체 레이저장비가
    그랬다 — 대부분 0이고 중위값이 0.1M인데 최신값이 80M이라 "중위대비
    +82,456%"로 상단을 독점했다. 구간 최대값의 5% 이상인 달만 센다.
    """
    vals = list(df["total"])[-window:]
    mx = max(vals) if vals else 0
    if mx <= 0:
        return 0
    thr = mx * 0.05
    return sum(1 for v in vals if v >= thr)


def _spike(df):
    """
    "튀는 정도" — 최신값을 직전 12개월 중위값과 비교한 배수.

    💡 MoM으로 정렬하면 직전 달이 우연히 0에 가까웠던 품목이 +65,000% 같은
    값으로 상단을 독점한다(실제로 반도체 레이저장비가 그랬다: 최근 6개월이
    0,0,0,15,0,80). 중위값 기준은 그런 한 달짜리 잡음에 흔들리지 않는다.
    """
    vals = list(df["total"])
    if len(vals) < 4:
        return None
    hist = [v for v in vals[:-1][-12:] if v > 0]
    if not hist:
        return None
    med = statistics.median(hist)
    if med <= 0:
        return None
    return vals[-1] / med - 1


def _fmt_pct(v):
    """증감률 표기. 세 자릿수를 넘어가면 숫자보다 잡음이라 잘라 보여준다."""
    if v is None:
        return "전월비 -"
    arrow = "▲" if v > 0 else "▼" if v < 0 else "─"
    a = abs(v)
    if a >= 999:
        return f"{arrow} 999%+ 전월"
    return f"{arrow} {a:.1f}% 전월"


# 화면 고정값 — 요청에 따라 선택 UI를 두지 않는다.
# 36개월로 잡은 이유: 마일스톤 판정 범위가 곧 조회 구간이라, 짧으면
# "N개월 만에 최대"가 너무 쉽게 뜬다.
_MONTHS = 36
_COLS = 3          # 카드 한 줄에 3개 (매크로 탭과 동일)


def render_trade():
    st.markdown(
        "<div style='font-size:1.4rem;font-weight:bold;margin-bottom:4px;'>🚢 수출입 동향</div>",
        unsafe_allow_html=True,
    )
    st.caption(
        f"관세청 품목별 수출입실적 · 세부품목 {item_count()}개 · "
        f"월별 수출 {_MONTHS}개월 · 전월비 큰 순 · 1시간 캐시"
    )

    if not st.secrets.get("DATA_GO_KR_KEY", ""):
        st.warning(
            "수출 데이터를 표시하려면 관세청 공공데이터포털 API 키가 필요합니다. "
            "[data.go.kr](https://www.data.go.kr) 에서 "
            "관세청_통관기준 수출입 실적 API를 신청한 뒤, 발급된 키를 "
            "Streamlit 시크릿에 DATA_GO_KR_KEY 로 추가하세요."
        )
        st.stop()

    # ── 테마 · 국가 선택 ──────────────────────────────────────────────────────
    # 💡 버튼 11개로 깔아봤더니 화면 위쪽을 두 줄이나 차지했다 — 셀렉터로 되돌린다.
    c1, c2 = st.columns(2)
    with c1:
        theme = st.selectbox("테마", themes(), key="trade_theme")

    opts = get_country_options(theme)
    labels = ["전체"] + [lb for _, lb in opts]
    code_by_label = {lb: cc for cc, lb in opts}
    with c2:
        picked_country = st.selectbox("수출 대상국", labels,
                                      key=f"trade_country_{theme}")
    country = code_by_label.get(picked_country)
    where = "전체" if not country else picked_country.split(" (")[0]

    with st.spinner(f"{theme} 품목별 수출 데이터 로딩 중..."):
        trends = get_theme_trends(theme, _MONTHS, country)

    if not trends:
        st.error("API에서 데이터를 가져오지 못했습니다. API 키와 네트워크 상태를 확인해주세요.")
        return

    # 데이터가 희박한 품목은 따로 뺀다 — 최근 1년 중 절반도 실적이 없으면
    # 스파크라인이 0에 붙은 선이 되어 볼 가치가 없고, 정렬도 망친다
    # (HS코드가 너무 좁게 잡힌 품목에서 생긴다).
    dense = {k: v for k, v in trends.items() if _coverage(v) >= 6}
    sparse = {k: v for k, v in trends.items() if _coverage(v) < 6}

    # 12개월 중위값 대비 급등 순 — "무엇이 튀는지"가 이 화면의 목적이다
    ranked = sorted(
        dense.items(),
        key=lambda kv: (_spike(kv[1]) if _spike(kv[1]) is not None else -1e9),
        reverse=True,
    )

    latest_label = max(df.iloc[-1]["label"] for df in trends.values())
    st.markdown(
        f"<div style='font-size:12px;color:#888;margin:2px 0 8px;'>"
        f"대상국 <b style='color:#555;'>{where}</b> · "
        f"최신 <b style='color:#555;'>{latest_label}</b> · "
        f"관세청 확정 통계는 통상 1~2개월 지연 공표</div>",
        unsafe_allow_html=True,
    )

    for row_start in range(0, len(ranked), _COLS):
        cols = st.columns(_COLS)
        for ci, (name, df) in enumerate(ranked[row_start:row_start + _COLS]):
            _, stocks = lookup(theme, name)
            last = df.iloc[-1]
            mom = _mom(df)
            note = _trend_note(list(df["total"]))
            clr = "#ef5350" if (mom or 0) > 0 else "#1565C0" if (mom or 0) < 0 else "#888"
            arrow = "▲" if (mom or 0) > 0 else "▼" if (mom or 0) < 0 else "─"
            mom_txt = _fmt_pct(mom)
            sp = _spike(df)
            if sp is not None and sp >= 0.3:
                mom_txt += f"  ·  중위대비 +{sp * 100:.0f}%"

            with cols[ci]:
                badge_html = ""
                if note:
                    _txt, _clr = note
                    badge_html = (
                        f"<span style='background:{_clr};color:#fff;font-size:9.5px;"
                        f"font-weight:700;padding:1px 5px;border-radius:3px;"
                        f"margin-left:6px;vertical-align:middle;'>{_txt}</span>"
                    )
                st.markdown(
                    f"<div style='font-size:12px;color:#888;margin-bottom:1px;'>"
                    f"{html.escape(name)}{badge_html}</div>"
                    f"<div style='font-size:17px;font-weight:700;line-height:1.2;'>"
                    f"${last['total']:,.1f}M</div>"
                    f"<div style='font-size:11.5px;color:{clr};font-weight:600;'>{mom_txt}</div>"
                    f"<div style='font-size:10.5px;color:#7d8a97;margin-top:1px;"
                    f"white-space:nowrap;overflow:hidden;text-overflow:ellipsis;'"
                    f" title='{html.escape(stocks)}'>{html.escape(stocks)}</div>",
                    unsafe_allow_html=True,
                )
                st.plotly_chart(
                    _make_card_sparkline(df, note),
                    use_container_width=True,
                    # staticPlot은 두지 않는다 — 마우스오버 툴팁까지 죽는다.
                    config={"displayModeBar": False, "scrollZoom": False,
                            "staticPlot": False},
                )

    if sparse:
        with st.expander(f"데이터가 희박한 품목 {len(sparse)}개 (최근 1년 실적 6개월 미만)"):
            st.caption(
                "HS코드가 좁게 잡혀 대부분의 달에 실적이 없는 품목입니다. "
                "추세로 읽기 어려워 위 그리드에서 제외했습니다."
            )
            for name, df in sparse.items():
                last = df.iloc[-1]
                _, stocks = lookup(theme, name)
                st.markdown(
                    f"<div style='font-size:12.5px;margin:2px 0;'>"
                    f"<b>{html.escape(name)}</b> &nbsp; "
                    f"<span style='color:#555;'>{last['label']} ${last['total']:,.1f}M</span> &nbsp; "
                    f"<span style='color:#8b98a5;font-size:11px;'>{html.escape(stocks)}</span></div>",
                    unsafe_allow_html=True,
                )
