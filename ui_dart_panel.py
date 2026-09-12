"""
ui_dart_panel.py — 가치평가 화면 하단의 「재고자산 · 수주잔고 추이」 패널.

숫자는 GitHub Actions 배치(batch_dart.py)가 data/dart/{종목코드}.json으로 만들어 두고,
여기서는 읽어서 그리기만 한다 — Streamlit Cloud에서는 DART 연결 자체가 막혀 있다.

수주잔고는 회사마다 공시 양식이 달라 못 뽑거나 일부만 뽑히는 경우가 있다
(예: 한국항공우주는 주요 계약이 중첩 테이블로 들어가 있어 부분값만 잡힌다).
그래서 값을 그냥 보여주지 않고,

  · 어느 보고서에서 몇 건을 집계했는지 함께 적고
  · DART 원문 링크를 걸어 바로 대조할 수 있게 하고
  · 연매출 대비 지나치게 작으면 "부분 집계 의심" 경고를 띄운다

숫자를 못 믿을 상황을 조용히 숨기지 않는 게 이 패널의 설계 의도다.
"""
import html
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone

import plotly.graph_objects as go
import requests
import streamlit as st

# 한국 시장 관행: 증가/양수는 빨강, 감소/음수는 파랑.
_UP, _DOWN = "#ef5350", "#1565C0"
_DART_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={}"

_REPO = "shinkiyeol9814-droid/stockapp"
_BRANCH = "main"
_LOCAL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "dart")
_KST = timezone(timedelta(hours=9))
_CODE_RE = re.compile(r"^[0-9A-Z]{6}$")
_STALE = timedelta(days=7)      # 이보다 오래된 파일은 보여주되 배치에 갱신을 요청한다
_REQUEST_COOLDOWN = 600         # 같은 종목 재요청 간격(초) — 재실행마다 워크플로가 쌓이지 않게
_POLL_SECONDS = 20
_GIVE_UP_SECONDS = 600


# ─────────────────────────────────────────────────────────────────────────────
# 데이터 — 배치가 커밋한 파일 읽기 / 배치 요청
# ─────────────────────────────────────────────────────────────────────────────
def _token():
    try:
        return st.secrets.get("GH_PAT") or st.secrets.get("GITHUB_TOKEN", "")
    except Exception:
        return ""


def _fetch(code):
    """GitHub 최신 커밋 우선(배치 직후에도 바로 보이게), 토큰이 없거나 API가 실패하면 로컬 체크아웃."""
    tok = _token()
    if tok:
        try:
            r = requests.get(
                f"https://api.github.com/repos/{_REPO}/contents/data/dart/{code}.json",
                headers={"Authorization": f"token {tok}",
                         "Accept": "application/vnd.github.raw+json"},
                params={"ref": _BRANCH}, timeout=7)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 404:
                return None
            print(f"[ui_dart_panel] {code} 파일 조회 HTTP {r.status_code}")
        except Exception as e:
            print(f"[ui_dart_panel] {code} 파일 조회 실패: {type(e).__name__}: {e}")
    try:
        with open(os.path.join(_LOCAL_DIR, f"{code}.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


@st.cache_data(ttl=300, show_spinner=False)
def _load(code):
    return _fetch(code)


def _updated_at(data):
    try:
        return datetime.fromisoformat(data["updated_at"])
    except (KeyError, TypeError, ValueError):
        return None


def _is_stale(data):
    ts = _updated_at(data)
    return ts is None or datetime.now(_KST) - ts > _STALE


def _missing_docs(data):
    """배치가 시간 안에 못 받아 다음 수집으로 넘긴 정기보고서 원문 수."""
    return ((data or {}).get("reports") or {}).get("pending") or 0


def _retryable(data):
    """다시 요청할 만한 수집 실패가 있는지. DartError(DART에 없는 종목 등)는 재시도해도 같다."""
    errors = (data or {}).get("errors") or {}
    return _missing_docs(data) > 0 or any(not str(v).startswith("DartError:") for v in errors.values())


def _request_batch(code):
    tok = _token()
    if not tok:
        return False, "GH_PAT 없음"
    try:
        r = requests.post(
            f"https://api.github.com/repos/{_REPO}/dispatches",
            headers={"Authorization": f"token {tok}", "Accept": "application/vnd.github+json"},
            json={"event_type": "run_dart_batch", "client_payload": {"codes": [code]}},
            timeout=7)
    except Exception as e:
        return False, type(e).__name__
    if r.status_code == 204:
        return True, ""
    # 403/404는 토큰에 이 레포 Contents 쓰기 권한이 없을 때 난다.
    return False, f"HTTP {r.status_code}"


@st.fragment(run_every=_POLL_SECONDS)
def _poll(code, requested_at, has_data):
    """배치 결과가 커밋될 때까지 이 조각만 주기적으로 다시 그린다."""
    elapsed = int(time.time() - requested_at)
    ts = _updated_at(_fetch(code) or {})
    if ts and ts.timestamp() >= requested_at - 60:
        # 반영 완료를 기록한다 — 원문이 아직 남은 결과면 need가 계속 참이라, 이게 없으면 폴링↔재실행이 무한 반복된다.
        st.session_state[f"_dart_done_{code}"] = requested_at
        _load.clear()
        st.rerun()
    if elapsed > _GIVE_UP_SECONDS:
        st.session_state[f"_dart_giveup_{code}"] = requested_at
        st.rerun()
    if has_data:
        st.caption(f"🔄 최신 공시로 갱신 중… ({elapsed}초 경과)")
    else:
        st.info(f"⏳ DART 데이터를 수집하고 있습니다… {elapsed}초 경과 (보통 1~2분). "
                f"완료되면 자동으로 표시됩니다.")


# ─────────────────────────────────────────────────────────────────────────────
# 그리기
# ─────────────────────────────────────────────────────────────────────────────
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


def _area_trace(xs, scaled, raw, label, hover_x="%{x}", fmt=None):
    """
    재고자산·수주잔고가 같은 모양으로 보이도록 만든 공용 면적 꺾은선.

    두 차트가 나란히 놓이는데 하나는 막대, 하나는 선이면 비교가 안 된다.
    한 곳에서만 만들어야 나중에 한쪽만 바뀌는 일이 없다.
    """
    # 💡 결측(None)을 건너뛰고 첫/마지막 실측값으로 방향을 정한다.
    # 부문을 골라 보면 그 부문이 없던 분기가 None으로 남는데, 예전 코드는
    # scaled[0]을 그대로 비교해서 첫 분기가 비어 있으면 TypeError로 죽었다
    # (HD현대중공업에서 '조 선'을 고르면 바로 재현됐다).
    real = [v for v in scaled if v is not None]
    rising = len(real) > 1 and real[-1] >= real[0]
    color = _UP if rising else _DOWN
    fill = "rgba(239,83,80,0.10)" if rising else "rgba(21,101,192,0.10)"
    _fmt = fmt or _jo
    return go.Scatter(
        x=xs, y=scaled, mode="lines+markers", name=label,
        line=dict(color=color, width=2), marker=dict(size=5),
        fill="tozeroy", fillcolor=fill,
        customdata=[("-" if v is None else _fmt(v)) for v in raw],
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


_LAGS = {"래깅 없음": 0, "1분기 래깅": 1, "2분기 래깅": 2, "3분기 래깅": 3}


def _next_periods(rows, k):
    """마지막 분기 다음의 k개 분기 라벨. 재고를 뒤로 밀어도 최신 재고가 화면에서 사라지지 않게 x축을 늘린다."""
    y, q = rows[-1].get("_y"), rows[-1].get("_q")
    if y is None or q is None:
        return []
    out = []
    for _ in range(k):
        q += 1
        if q > 4:
            y, q = y + 1, 1
        out.append(f"{str(y)[2:]}.{q}Q")
    return out


def _render_inventory(data, quarterly: bool, code: str):
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

    lag = 0
    if quarterly:
        # 재고는 매출보다 먼저 움직인다(수집 종목 48개 중 23개에서 '다음 분기 매출'과 상관이 가장 높았다).
        # 그래서 재고를 k분기 뒤로 밀어 "그 재고가 만든 매출"과 짝지어 볼 수 있게 한다.
        lag = _LAGS[st.selectbox("재고 래깅", list(_LAGS), index=0,
                                 key=f"inv_lag_{code}", label_visibility="collapsed")]

    xs = ([r["기간"] for r in rows] + _next_periods(rows, lag)) if quarterly else [str(r["연도"]) for r in rows]
    inv = [None] * lag + [r["재고자산"] for r in rows]
    rev = [r.get("매출액") for r in rows] + [None] * lag
    # 회전율은 두 계열의 비율이라 래깅을 걸면 값 자체가 바뀐다 — 매출(t) ÷ 재고(t-lag).
    # 짝지을 매출이 아직 없는 최근 lag개 분기는 비워 둔다.
    turn = [(rv / iv) if (rv and iv) else None for rv, iv in zip(rev, inv)]

    fig = go.Figure()
    # 💡 원 단위 그대로 그리면 Plotly가 축을 "200B, 400B"로 붙인다. 억 단위로
    # 변환해서 그리고 축에 '억'을 달아야 한국 사용자가 바로 읽는다.
    fig.add_trace(_area_trace(xs, [None if v is None else v / 1e8 for v in inv], inv, "재고자산",
                              "%{x}" if quarterly else "%{x}년"))
    fig.data[0].connectgaps = False
    fig.add_trace(go.Scatter(
        x=xs, y=[None if v is None else v / 1e8 for v in rev], name="매출", mode="lines",
        line=dict(color="#1565C0", width=1.6), connectgaps=False,
        customdata=[("-" if v is None else _jo(v)) for v in rev],
        hovertemplate="매출 %{customdata}<extra></extra>",
    ))
    fig.update_yaxes(ticksuffix="억", tickformat=",.0f")
    # 💡 금액만 보면 '재고가 늘었다'가 성장 때문인지 안 팔려서인지 구분이 안 된다.
    # 회전율을 겹쳐 그려야 그 판단이 된다.
    if any(v is not None for v in turn):
        unit = "회/분기" if quarterly else "회/년"
        fig.add_trace(go.Scatter(
            x=xs, y=turn, name="회전율", yaxis="y2", mode="lines",
            line=dict(color="#8a8a8a", width=1.3, dash="dot"), connectgaps=False,
            hovertemplate="회전율 %{y:.2f}" + unit + "<extra></extra>",
        ))
        fig.update_layout(yaxis2=dict(overlaying="y", side="right",
                                      showgrid=False, ticksuffix="회",
                                      fixedrange=True))
    _lock(fig, 215)
    fig.update_layout(showlegend=True, margin=dict(l=0, r=10, t=24, b=20),
                      legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="left",
                                  x=0, font=dict(size=10)))
    st.plotly_chart(fig, use_container_width=True,
                    config={"displayModeBar": False, "scrollZoom": False})
    if quarterly:
        base = "회전율 = 당분기 매출 ÷ 재고 (매출 기준, 회/분기). "
        st.caption(base + (
            f"래깅 {lag}분기 — 재고를 {lag}분기 뒤로 밀어 그 재고가 만든 매출과 짝지었습니다. "
            f"최근 {lag}개 분기는 짝지을 매출이 아직 없어 회전율이 비어 있습니다."
            if lag else
            "재고가 매출보다 먼저 움직이는 편이라, 래깅을 걸면 같은 재고를 이후 매출과 비교할 수 있습니다."))
    else:
        st.caption("회전율 = 연매출 ÷ 기말 재고 (매출 기준, 회/년).")
    return {"매출액": annual_rev}


def _seg_name(it):
    return it.get("이름") or it.get("부문") or ""


def _norm(name: str) -> str:
    """
    부문 이름 비교용 정규화.

    💡 같은 부문을 분기마다 다르게 띄어 쓴다. HD현대중공업은 '조선'과
    '조 선'을 섞어 쓰는데, 그대로 두면 셀렉터에 같은 부문이 두 번 뜨고
    각각은 절반의 분기만 값이 있어 그래프가 끊긴다.
    """
    return "".join((name or "").split())


def _pick_segment(rows, key, label, code):
    """
    부문 선택 셀렉터. 나뉘어 있지 않으면 아예 그리지 않는다.

    반환값은 (정규화 키 또는 None, 전체 이름 목록, 화면 표기용 이름).
    비교는 정규화 키로, 화면 표시는 원래 표기로 해야 '조 선'을 고른 사람이
    헤더에서 '조선'을 보고 갸웃하지 않는다.
    """
    display = {}   # 정규화 키 -> 화면에 쓸 이름(최신 표기)
    for r in rows:
        for it in (r.get(key) or []):
            nm = _seg_name(it)
            if nm:
                display[_norm(nm)] = nm   # 뒤(최신)가 이기도록 덮어쓴다
    if len(display) < 2:
        return None, list(display.values()), None
    # 최신 기간에서 큰 것부터 보이도록 정렬
    latest = {_norm(_seg_name(it)): (it.get("금액") or it.get("가동률") or 0)
              for it in (rows[-1].get(key) or [])}
    keys = sorted(display, key=lambda k: -latest.get(k, 0))
    names = [display[k] for k in keys]
    # 💡 위젯 키는 재실행 사이에 안정적이어야 한다. id(rows)를 쓰면 매번
    # 새 위젯이 되어 고른 부문이 곧바로 '전체'로 되돌아간다.
    sel = st.selectbox(label, ["전체"] + names, index=0,
                       key=f"seg_{label}_{code}", label_visibility="collapsed")
    if sel == "전체":
        return None, names, None
    return _norm(sel), names, sel


def _render_backlog(data, inv_last, code):
    rows = data.get("backlog") or []
    if not rows:
        st.caption("이 종목은 수주잔고를 공시하지 않습니다. "
                   "(수주산업이 아닌 경우 정상입니다)")
        return

    seg, names, seg_label = _pick_segment(rows, "내역", "수주잔고 부문", code)

    # 선택한 부문만 뽑아낸다. 어떤 기간엔 그 부문이 없을 수 있어 None을 남기고
    # 선으로 이어 그린다(connectgaps) — 0으로 채우면 없던 급락이 생긴다.
    def value_of(r):
        if seg is None:
            return r["수주잔고"]
        for it in (r.get("내역") or []):
            if _norm(it.get("이름")) == seg:
                return it["금액"]
        return None

    pairs = [(r, value_of(r)) for r in rows]
    shown = [(r, v) for r, v in pairs if v is not None]
    if not shown:
        st.caption("선택한 부문의 값을 찾지 못했습니다.")
        return

    last, last_v = shown[-1]
    prev = shown[-2] if len(shown) > 1 else None
    yoy = next(((r, v) for r, v in reversed(shown)
                if r["기간"][5:] == last["기간"][5:] and r["기간"] < last["기간"]), None)
    base = yoy or prev
    _header(f"수주잔고{'' if seg is None else ' · ' + html.escape(seg_label)}",
            _jo(last_v),
            _delta_html(last_v, base[1] if base else None, "YoY" if yoy else "전분기"),
            f"{html.escape(last['보고서'])} · {last['건수']}개 항목 집계")

    xs = [_q_label(r["기간"]) for r, _ in pairs]
    ys = [v for _, v in pairs]
    plot = [None if v is None else v / 1e12 for v in ys]
    tr = _area_trace(xs, plot, ys, "수주잔고")
    tr.connectgaps = True
    fig = go.Figure(tr)
    fig.update_yaxes(ticksuffix="조", tickformat=",.0f")
    st.plotly_chart(_lock(fig, 190), use_container_width=True,
                    config={"displayModeBar": False, "scrollZoom": False})

    rev = (inv_last or {}).get("매출액")
    if seg is None and rev and last_v < rev * 0.5:
        st.caption("⚠️ 연매출 대비 수주잔고가 작습니다. 공시 양식에 따라 일부 "
                   "품목만 집계됐을 수 있으니 원문을 확인해주세요.")
    if data.get("dropped"):
        st.caption(f"ℹ️ 공시 양식이 달라 값이 온전치 않은 {data['dropped']}개 "
                   f"기간은 그래프에서 제외했습니다.")
    if seg is None:
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


def _render_utilization(data, code):
    """부문별 가동률 추이. 재고/수주잔고와 같은 꺾은선으로 그린다."""
    periods = data.get("util") or []
    if not periods:
        st.caption("이 종목은 가동률을 공시하지 않습니다. "
                   "(제조업이 아니거나 공시 양식이 다른 경우입니다)")
        return

    seg, names, seg_label = _pick_segment(periods, "rows", "가동률 부문", code)

    def value_of(p):
        if seg is None:
            # 전사 합계가 공시돼 있으면 그걸, 없으면 부문 단순평균.
            if p.get("total") is not None:
                return p["total"]
            rs = p.get("rows") or []
            return sum(r["가동률"] for r in rs) / len(rs) if rs else None
        for r in (p.get("rows") or []):
            if _norm(r["부문"]) == seg:
                return r["가동률"]
        return None

    pairs = [(p, value_of(p)) for p in periods]
    shown = [(p, v) for p, v in pairs if v is not None]
    if not shown:
        st.caption("선택한 부문의 가동률을 찾지 못했습니다.")
        return

    last, last_v = shown[-1]
    yoy = next(((p, v) for p, v in reversed(shown)
                if p["기간"][5:] == last["기간"][5:] and p["기간"] < last["기간"]), None)
    prev = shown[-2] if len(shown) > 1 else None
    base = yoy or prev
    note = (f"{html.escape(str(last.get('보고서', '-')))}"
            f" · {'전사 평균' if last.get('total') is not None else '부문 단순평균'}"
            if seg is None else html.escape(str(last.get("보고서", "-"))))
    _header(f"가동률{'' if seg is None else ' · ' + html.escape(seg_label)}",
            f"{last_v:.1f}%",
            _delta_html(last_v, base[1] if base else None, "YoY" if yoy else "직전"),
            note)

    xs = [_q_label(p["기간"]) for p, _ in pairs]
    ys = [v for _, v in pairs]
    tr = _area_trace(xs, ys, ys, "가동률", fmt=lambda v: f"{v:.1f}%")
    tr.connectgaps = True
    fig = go.Figure(tr)
    # 💡 y축을 0부터 열어둔다. 100 근처만 확대하면 60%와 100%가 비슷해 보여
    # "설비가 놀고 있다"는 신호가 죽는다. 100% 기준선을 같이 그린다.
    top = max([v for v in ys if v is not None] + [100]) * 1.12
    fig.update_yaxes(range=[0, top], ticksuffix="%", tickformat=",.0f")
    fig.add_hline(y=100, line=dict(color="#bbb", width=1, dash="dot"))
    st.plotly_chart(_lock(fig, 190), use_container_width=True,
                    config={"displayModeBar": False, "scrollZoom": False})

    if len(names) > 1 and seg is None:
        st.caption(f"부문 {len(names)}개의 평균입니다. 위 선택 상자로 개별 "
                   f"부문만 볼 수 있습니다. 점선은 100%(만가동) 기준선입니다.")


def _section(what, code, render, section, err):
    """저장된 항목 하나를 그린다. 수집 실패나 깨진 파일이 위쪽 가치평가 화면을 망치지 않게 한다."""
    if section is None:
        st.caption(f"{what} 데이터를 수집하지 못했습니다." + (f" ({err[:150]})" if err else ""))
        return None
    try:
        return render(section)
    except Exception as e:
        st.caption(f"{what} 표시 실패: {type(e).__name__}")
        print(f"[ui_dart_panel] {what} 표시 실패 {code}: {type(e).__name__}: {e}")
        return None


def render_dart_panel(stock_code: str):
    """가치평가 화면 하단에 붙는 진입점. 실패해도 위쪽 차트를 망치지 않는다."""
    if not stock_code:
        return
    code = str(stock_code).strip().upper()
    valid = bool(_CODE_RE.match(code))
    data = _load(code) if valid else None
    ts = _updated_at(data) if data else None

    st.markdown("---")
    # 💡 기간 선택을 왼쪽 칼럼 안에 두면 그 칼럼만 아래로 밀려서 두 차트가
    # 세로로 어긋난다. 칼럼 밖(헤더 줄)에 둬야 좌우 높이가 맞는다.
    hc1, hc2 = st.columns([3, 1])
    with hc1:
        when = f" · {ts:%m/%d %H:%M} 수집" if ts else ""
        st.markdown("<div style='font-size:1.1rem;font-weight:700;padding-top:4px;'>"
                    "📦 재고자산 · 수주잔고 추이 <span style='font-size:11px;"
                    f"color:#999;font-weight:400;'>DART 공시 기준{when}</span></div>",
                    unsafe_allow_html=True)
    if not valid:
        st.caption("종목코드 형식이 올바르지 않아 DART 데이터를 조회할 수 없습니다.")
        return

    req_key, giveup_key = f"_dart_req_{code}", f"_dart_giveup_{code}"
    requested_at = st.session_state.get(req_key)
    gave_up = requested_at is not None and st.session_state.get(giveup_key) == requested_at
    need = data is None or _is_stale(data) or _retryable(data)

    request_err = ""
    if need and not gave_up and (requested_at is None
                                 or time.time() - requested_at > _REQUEST_COOLDOWN):
        ok, request_err = _request_batch(code)
        if ok:
            requested_at = st.session_state[req_key] = time.time()
    pending = (need and requested_at is not None and not gave_up
               and st.session_state.get(f"_dart_done_{code}") != requested_at)

    if gave_up:
        st.warning("DART 수집이 10분 넘게 끝나지 않았습니다. GitHub Actions의 "
                   "'DART Inventory & Backlog Batch' 실행 결과를 확인해주세요.")
        if st.button("DART 수집 다시 요청", key=f"dart_retry_{code}"):
            st.session_state.pop(req_key, None)
            st.session_state.pop(giveup_key, None)
            st.rerun()
    elif need and request_err:
        st.caption(f"DART 수집 요청 실패 ({request_err}) — Streamlit Secrets의 "
                   f"GH_PAT에 이 레포 쓰기 권한이 있는지 확인해주세요.")

    if data is None:
        if pending:
            _poll(code, requested_at, False)
        return
    if pending:
        _poll(code, requested_at, True)

    with hc2:
        period = st.radio("재고 기간", ["분기", "연간"], index=0, horizontal=True,
                          key=f"inv_period_{code}", label_visibility="collapsed")
    quarterly = (period == "분기")
    errors = data.get("errors") or {}
    inv_key = "inventory_q" if quarterly else "inventory_y"
    reports = data.get("reports")

    c1, c2 = st.columns(2)
    with c1:
        inv_last = _section("재고자산", code, lambda s: _render_inventory(s, quarterly, code),
                            data.get(inv_key), errors.get(inv_key))
    with c2:
        _section("수주잔고", code, lambda s: _render_backlog(s, inv_last, code),
                 reports, errors.get("reports"))

    st.markdown("<div style='height:6px;'></div>", unsafe_allow_html=True)
    uc1, _uc2 = st.columns(2)
    with uc1:
        _section("가동률", code, lambda s: _render_utilization(s, code),
                 reports, errors.get("reports"))

    if _missing_docs(data):
        st.caption(f"ℹ️ 정기보고서 원문 {_missing_docs(data)}건은 아직 수집되지 않아 수주잔고·가동률 "
                   f"추이에서 빠져 있습니다. 다음 수집 때 이어서 반영됩니다.")
    retained = [k for k in errors if data.get(k) is not None]
    if retained:
        st.caption("ℹ️ 최근 수집에서 일부 항목이 실패해 그 항목은 이전 수집값을 표시합니다.")
