"""
ui_dart_panel.py — 가치평가 화면 하단의 「재고자산 · 수주잔고 추이」 패널.

dart_fin이 숫자를 만들고, 여기서는 그리기만 한다.

수주잔고는 회사마다 공시 양식이 달라 못 뽑거나 일부만 뽑히는 경우가 있다
(예: 한국항공우주는 주요 계약이 중첩 테이블로 들어가 있어 부분값만 잡힌다).
그래서 값을 그냥 보여주지 않고,

  · 어느 보고서에서 몇 건을 집계했는지 함께 적고
  · DART 원문 링크를 걸어 바로 대조할 수 있게 하고
  · 연매출 대비 지나치게 작으면 "부분 집계 의심" 경고를 띄운다

숫자를 못 믿을 상황을 조용히 숨기지 않는 게 이 패널의 설계 의도다.
"""
import html

import plotly.graph_objects as go
import streamlit as st

import dart_fin

# 한국 시장 관행: 증가/양수는 빨강, 감소/음수는 파랑.
_UP, _DOWN = "#ef5350", "#1565C0"
_DART_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={}"


@st.cache_data(ttl=43200, show_spinner=False)
def _utilization(code: str):
    return dart_fin.get_utilization(code)


@st.cache_data(ttl=43200, show_spinner=False)
def _inventory(code: str):
    return dart_fin.get_inventory_series(code)


@st.cache_data(ttl=43200, show_spinner=False)
def _inventory_q(code: str):
    return dart_fin.get_inventory_quarterly(code, quarters=12)


@st.cache_data(ttl=43200, show_spinner=False)
def _backlog(code: str):
    # 재고 차트가 12분기라 x축 범위를 맞춘다. 8 → 12로 늘려도 실측 0.2~0.4초
    # 차이라(원문 다운로드가 병렬) 굳이 아낄 이유가 없었다.
    return dart_fin.get_backlog_series(code, limit=12)


def _jo(v):
    """원 단위 금액을 조/억으로. 수주잔고는 조 단위가 보통이라 둘을 나눈다."""
    if v is None:
        return "-"
    if abs(v) >= 1e12:
        return f"{v / 1e12:,.2f}조"
    return f"{v / 1e8:,.0f}억"


def _delta_html(cur, prev, label):
    if cur is None or prev in (None, 0):
        return ""
    pct = (cur - prev) / abs(prev) * 100
    color = _UP if pct >= 0 else _DOWN
    sign = "+" if pct >= 0 else ""
    return (f"<span style='color:{color};font-weight:700;font-size:12px;'>"
            f"{sign}{pct:,.1f}%</span>"
            f"<span style='color:#999;font-size:11px;'> ({label})</span>")


def _q_label(period: str) -> str:
    """'2026.06' -> '26.2Q'. 재고 차트와 x축 표기를 같은 모양으로 맞춘다."""
    try:
        y, m = period.split(".")
        return f"{y[2:]}.{(int(m) + 2) // 3}Q"
    except Exception:
        return period


def _area_trace(xs, scaled, raw, label, hover_x="%{x}"):
    """
    재고자산·수주잔고가 같은 모양으로 보이도록 만든 공용 면적 꺾은선.

    두 차트가 나란히 놓이는데 하나는 막대, 하나는 선이면 비교가 안 된다.
    한 곳에서만 만들어야 나중에 한쪽만 바뀌는 일이 없다.
    """
    rising = len(scaled) > 1 and scaled[-1] >= scaled[0]
    color = _UP if rising else _DOWN
    fill = "rgba(239,83,80,0.10)" if rising else "rgba(21,101,192,0.10)"
    return go.Scatter(
        x=xs, y=scaled, mode="lines+markers", name=label,
        line=dict(color=color, width=2), marker=dict(size=5),
        fill="tozeroy", fillcolor=fill,
        customdata=[_jo(v) for v in raw],
        hovertemplate=f"{hover_x}<br>{label} %{{customdata}}<extra></extra>",
    )


def _lock(fig, height):
    """매크로 차트와 동일하게 드래그는 막고 마우스오버는 살린다."""
    fig.update_xaxes(fixedrange=True)
    fig.update_yaxes(fixedrange=True)
    fig.update_layout(
        height=height, dragmode=False, hovermode="x unified",
        margin=dict(l=0, r=10, t=10, b=20),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        showlegend=False, xaxis=dict(showgrid=False),
        yaxis=dict(showgrid=True, gridcolor="rgba(0,0,0,0.06)"),
    )
    return fig


def _header(title, value, delta_html, note=""):
    st.markdown(
        f"<div style='font-size:12px;color:#888;margin-bottom:1px;'>{title}</div>"
        f"<div style='font-size:20px;font-weight:800;line-height:1.15;'>{value} "
        f"{delta_html}</div>"
        f"<div style='font-size:11px;color:#aaa;margin-bottom:4px;'>{note}</div>",
        unsafe_allow_html=True,
    )


def _render_inventory(data, quarterly: bool):
    rows = data.get("rows") or []
    if not rows:
        st.caption("재고자산 데이터를 찾지 못했습니다.")
        return None

    last = rows[-1]
    # 💡 분기 모드에서 직전 분기와 비교하면 계절성이 그대로 튀어나온다.
    # 4개 분기 전(같은 분기)과 비교해야 YoY가 된다.
    step = 4 if quarterly else 1
    base = rows[-1 - step] if len(rows) > step else (rows[-2] if len(rows) > 1 else None)
    label = "YoY" if (base is not None and len(rows) > step) else "직전"

    if quarterly:
        when = f"{last['기간']}"
        # 수주잔고 경고가 연매출 기준이라 분기에서는 TTM을 넘겨준다.
        annual_rev = last.get("TTM매출")
    else:
        when = f"{last['연도']}년"
        annual_rev = last.get("매출액")

    _header("재고자산", _jo(last["재고자산"]),
            _delta_html(last["재고자산"], base["재고자산"] if base else None, label),
            f"{when} · {'연결' if last['기준'] == 'CFS' else '별도'}기준")

    xs = [r["기간"] if quarterly else str(r["연도"]) for r in rows]
    fig = go.Figure()
    # 💡 원 단위 그대로 그리면 Plotly가 축을 "200B, 400B"로 붙인다. 억 단위로
    # 변환해서 그리고 축에 '억'을 달아야 한국 사용자가 바로 읽는다.
    raw = [r["재고자산"] for r in rows]
    fig.add_trace(_area_trace(xs, [v / 1e8 for v in raw], raw, "재고자산",
                              "%{x}" if quarterly else "%{x}년"))
    fig.update_yaxes(ticksuffix="억", tickformat=",.0f")
    # 💡 금액만 보면 '재고가 늘었다'가 성장 때문인지 안 팔려서인지 구분이 안 된다.
    # 매출 대비 비율을 겹쳐 그려야 그 판단이 된다.
    ratio = [r["비율"] for r in rows]
    if any(v is not None for v in ratio):
        rname = "재고/TTM매출" if quarterly else "매출대비"
        # 재고자산이 이제 선이라, 비율선은 점선 회색으로 눌러 구분한다.
        fig.add_trace(go.Scatter(
            x=xs, y=ratio, name=rname, yaxis="y2", mode="lines",
            line=dict(color="#8a8a8a", width=1.3, dash="dot"),
            connectgaps=True,
            hovertemplate=rname + " %{y:.1f}%<extra></extra>",
        ))
        fig.update_layout(yaxis2=dict(overlaying="y", side="right",
                                      showgrid=False, ticksuffix="%",
                                      fixedrange=True))
    st.plotly_chart(_lock(fig, 190), use_container_width=True,
                    config={"displayModeBar": False, "scrollZoom": False})
    if quarterly:
        st.caption("회색 점선은 재고 ÷ 최근 4개 분기 매출(TTM). "
                   "당분기 매출로 나누면 값이 4배로 튀어 연간과 비교가 안 됩니다.")
    return {"매출액": annual_rev}


def _render_backlog(data, inv_last):
    rows = data.get("rows") or []
    if not rows:
        st.caption("이 종목은 수주잔고를 공시하지 않습니다. "
                   "(수주산업이 아닌 경우 정상입니다)")
        return

    last = rows[-1]
    prev = rows[-2] if len(rows) > 1 else None
    # 💡 reversed가 중요하다. 정방향으로 next()를 쓰면 같은 분기 중 '가장 오래된'
    # 것을 집는다 — 8분기일 땐 우연히 1년 전이었지만 12분기로 늘리자 3년 전과
    # 비교해 현대로템이 +4.8%가 아닌 +58.8%로 표시됐다.
    yoy = next((r for r in reversed(rows) if r["기간"][5:] == last["기간"][5:]
                and r["기간"] < last["기간"]), None)
    _header("수주잔고", _jo(last["수주잔고"]),
            _delta_html(last["수주잔고"],
                        (yoy or prev)["수주잔고"] if (yoy or prev) else None,
                        "YoY" if yoy else "전분기"),
            f"{html.escape(last['보고서'])} · {last['건수']}개 항목 집계")

    xs = [_q_label(r["기간"]) for r in rows]
    ys = [r["수주잔고"] for r in rows]
    fig = go.Figure(_area_trace(xs, [v / 1e12 for v in ys], ys, "수주잔고"))
    fig.update_yaxes(ticksuffix="조", tickformat=",.0f")
    st.plotly_chart(_lock(fig, 190), use_container_width=True,
                    config={"displayModeBar": False, "scrollZoom": False})

    # 부분 집계 경고 — 연매출의 절반도 안 되는 수주잔고는 표를 덜 읽었다는 신호다.
    rev = (inv_last or {}).get("매출액")
    if rev and last["수주잔고"] < rev * 0.5:
        st.caption("⚠️ 연매출 대비 수주잔고가 작습니다. 공시 양식에 따라 일부 "
                   "품목만 집계됐을 수 있으니 원문을 확인해주세요.")
    if data.get("dropped"):
        st.caption(f"ℹ️ 공시 양식이 달라 값이 온전치 않은 {data['dropped']}개 "
                   f"기간은 그래프에서 제외했습니다.")
    _render_breakdown(last.get("내역"), last["수주잔고"])
    st.markdown(
        f"<a href='{_DART_URL.format(last['rcept_no'])}' target='_blank' "
        f"style='font-size:11px;color:#1565C0;text-decoration:none;'>"
        f"🔗 DART 원문에서 확인</a>", unsafe_allow_html=True)


def _render_breakdown(items, total):
    """
    수주잔고를 부문/품목별로 쪼개 보여준다.

    회사가 쪼개는 단위 자체가 제각각이다 — 한화시스템은 방산/ICT/기타 3개인데
    현대로템은 개별 계약 45건이다. 그래서 '부문'이라고 단정하지 않고 금액순
    상위만 보여주고 나머지는 묶는다.
    """
    if not items or len(items) < 2:
        return
    top = sorted(items, key=lambda x: -x["금액"])[:6]
    rest = sum(x["금액"] for x in sorted(items, key=lambda x: -x["금액"])[6:])
    if rest > 0:
        top.append({"이름": f"기타 {len(items) - 6}건", "금액": rest})
    base = max(x["금액"] for x in top) or 1

    with st.expander(f"부문·품목별 내역 ({len(items)}건)", expanded=False):
        for x in top:
            share = (x["금액"] / total * 100) if total else 0
            pct_w = max(1.5, x["금액"] / base * 100)
            st.markdown(
                f"<div style='display:flex;align-items:center;gap:6px;margin:2px 0;'>"
                f"<div style='flex:0 0 38%;font-size:11.5px;color:#444;"
                f"overflow:hidden;text-overflow:ellipsis;white-space:nowrap;'"
                f" title='{html.escape(x['이름'])}'>{html.escape(x['이름'])}</div>"
                f"<div style='flex:1;background:#f0f0f0;border-radius:2px;height:11px;'>"
                f"<div style='width:{pct_w:.1f}%;background:{_UP};height:11px;"
                f"border-radius:2px;'></div></div>"
                f"<div style='flex:0 0 84px;text-align:right;font-size:11px;"
                f"color:#666;'>{_jo(x['금액'])} ({share:.0f}%)</div></div>",
                unsafe_allow_html=True,
            )


def _render_utilization(data):
    rows = (data or {}).get("rows") or []
    total = (data or {}).get("total")
    if not rows and total is None:
        st.caption("이 종목은 가동률을 공시하지 않습니다. "
                   "(제조업이 아니거나 공시 양식이 다른 경우입니다)")
        return

    head = total if total is not None else (
        sum(r["가동률"] for r in rows) / len(rows) if rows else None)
    label = "전사 평균" if total is not None else "부문 단순평균"
    _header("가동률", f"{head:.1f}%" if head is not None else "-", "",
            f"{html.escape(str(data.get('보고서', '-')))} · {label}")

    # 💡 100%를 넘는 값이 흔하다(초과가동). 막대는 100 기준으로 그리되 넘치는
    # 만큼은 색을 바꿔 눈에 띄게 한다 — 잘라버리면 호황 신호가 사라진다.
    for r in rows[:8]:
        v = r["가동률"]
        w = min(v, 100.0)
        over = v > 100.0
        st.markdown(
            f"<div style='display:flex;align-items:center;gap:6px;margin:3px 0;'>"
            f"<div style='flex:0 0 42%;font-size:11.5px;color:#444;"
            f"overflow:hidden;text-overflow:ellipsis;white-space:nowrap;'"
            f" title='{html.escape(r['부문'])}'>{html.escape(r['부문'])}</div>"
            f"<div style='flex:1;background:#f0f0f0;border-radius:2px;height:12px;'>"
            f"<div style='width:{w:.1f}%;background:{_UP if over else '#5b8def'};"
            f"height:12px;border-radius:2px;'></div></div>"
            f"<div style='flex:0 0 52px;text-align:right;font-size:11.5px;"
            f"font-weight:700;color:{_UP if over else '#333'};'>{v:.1f}%</div></div>",
            unsafe_allow_html=True,
        )
    if any(r["가동률"] > 100 for r in rows[:8]):
        st.caption("빨간색은 100% 초과 가동 — 설비가 이미 꽉 찼다는 뜻입니다.")


def render_dart_panel(stock_code: str):
    """가치평가 화면 하단에 붙는 진입점. 실패해도 위쪽 차트를 망치지 않는다."""
    if not stock_code:
        return
    st.markdown("---")
    # 💡 기간 선택을 왼쪽 칼럼 안에 두면 그 칼럼만 아래로 밀려서 두 차트가
    # 세로로 어긋난다. 칼럼 밖(헤더 줄)에 둬야 좌우 높이가 맞는다.
    hc1, hc2 = st.columns([3, 1])
    with hc1:
        st.markdown("<div style='font-size:1.1rem;font-weight:700;padding-top:4px;'>"
                    "📦 재고자산 · 수주잔고 추이 <span style='font-size:11px;"
                    "color:#999;font-weight:400;'>DART 공시 기준</span></div>",
                    unsafe_allow_html=True)

    if not dart_fin._api_key():
        st.info("DART_API_KEY가 설정되어 있지 않습니다. "
                "Secrets에 키를 넣으면 재고자산·수주잔고 추이가 표시됩니다.")
        return

    with hc2:
        period = st.radio("재고 기간", ["분기", "연간"], index=0, horizontal=True,
                          key=f"inv_period_{stock_code}",
                          label_visibility="collapsed")
    quarterly = (period == "분기")

    c1, c2 = st.columns(2)
    inv_last = None
    with c1:
        try:
            with st.spinner("재고자산 조회 중..."):
                data = _inventory_q(stock_code) if quarterly else _inventory(stock_code)
                inv_last = _render_inventory(data, quarterly)
        except Exception as e:
            st.caption(f"재고자산 조회 실패: {type(e).__name__}")
            print(f"[ui_dart_panel] 재고자산 실패 {stock_code}: {e}")
    with c2:
        try:
            with st.spinner("수주잔고 조회 중..."):
                _render_backlog(_backlog(stock_code), inv_last)
        except Exception as e:
            st.caption(f"수주잔고 조회 실패: {type(e).__name__}")
            print(f"[ui_dart_panel] 수주잔고 실패 {stock_code}: {e}")

    st.markdown("<div style='height:6px;'></div>", unsafe_allow_html=True)
    uc1, _uc2 = st.columns(2)
    with uc1:
        try:
            with st.spinner("가동률 조회 중..."):
                _render_utilization(_utilization(stock_code))
        except Exception as e:
            st.caption(f"가동률 조회 실패: {type(e).__name__}")
            print(f"[ui_dart_panel] 가동률 실패 {stock_code}: {e}")
