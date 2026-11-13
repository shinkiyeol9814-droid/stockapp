"""
ui_company.py — 기업분석 탭.

사용자가 노션에서 관리하는 기업분석 DB를 앱 안에 그대로 띄운다(2026-10-30, 섹터별 탭 자리).

━━ 노션을 iframe에 넣을 수 있는 주소는 하나뿐이다 (2026-10-30 응답 헤더 실측) ━━
  app.notion.com/p/…, www.notion.so/…        X-Frame-Options: SAMEORIGIN → 못 넣음(로그인도 필요)
  {워크스페이스}.notion.site/{페이지}          같은 이유로 못 넣음(웹에 게시해도)
  {워크스페이스}.notion.site/ebd/{페이지ID}     frame-ancestors https: http: → 넣을 수 있음
마지막이 노션 "웹에 게시 → 임베드" 주소다. 그러려면 DB를 웹에 게시해야 하고, 게시하면
링크를 아는 누구나 볼 수 있다 — 그래서 주소는 공개 리포가 아니라 Streamlit Secrets
NOTION_EMBED_URL에 둔다. 게시 페이지 주소(…notion.site/제목-ID)를 넣어도 /ebd/ 주소로 바꿔 쓴다.
노션 "임베드" 메뉴가 복사해 주는 <iframe src="…"> 코드 통째로 넣어도, ID가 하이픈 섞인
UUID 꼴이어도 된다. 값이 있는데 못 쓰면 이유를 화면에 보인다(값 자체는 보이지 않는다).

━━ 느린 건 노션 쪽이다 (2026-11-04 실측) ━━
임베드 문서(19KB)는 CDN에서 0.1~0.2초에 오지만, 그 안에서 노션 앱이 JS 181개(풀면 약 18MB)를 받아
실행한 뒤에야 DB 데이터(약 130KB)를 요청한다 — 캐시가 있어도 표가 뜨기까지 약 2.5초, 휴대폰·첫 방문은
더 길다. JS는 1년 immutable 캐시라 미리 받아 두기(warm-up)는 첫 방문에만 효과가 있어 하지 않았다.
iframe 안 진행 상황은 다른 출처라 읽을 수 없으므로, 진행률은 경과 시간으로 추정해 올리다가(최대 95%)
iframe load 뒤 데이터 요청 몫(0.9초)을 더 기다려 100%로 닫는다.

━━ 다른 탭에서 쓰는 종목 목록 (notion_db_index, 2026-11-13) ━━
신고가 표가 "노션 DB에 있는 종목"을 링크하려고 DB 행(종목명·등급·페이지 주소)을 읽는다. 공식 API는
통합(integration) 토큰이 따로 필요해서, 웹에 게시된 페이지가 스스로 부르는 공개 엔드포인트
({워크스페이스}.notion.site/api/v3 — loadCachedPageChunkV2로 DB 블록 → queryCollection으로 행)를 쓴다.
게시를 끄면 빈 목록이 되고(링크만 사라짐), 문서화된 API가 아니라 노션이 바꾸면 깨질 수 있다 — 그때도
표는 링크 없이 그대로 뜬다. 행 페이지는 {워크스페이스}.notion.site/{페이지ID}로 공개 열람된다.

Secrets가 없으면 새 탭으로 여는 버튼만 보인다(NOTION_DB_URL, 없으면 아래 기본 링크 —
워크스페이스 링크라 로그인한 본인만 열린다).
"""
import html
import re
import uuid

import requests
import streamlit as st
import streamlit.components.v1 as components

from ui_common import page_header

_DEFAULT_URL = ("https://app.notion.com/p/f421915b8386403298f400aa1c74c0fe"
                "?v=0b94653f9226464fb504d30d3bc3ddec&pvs=12")


def _secret(key: str) -> str:
    try:
        return (st.secrets.get(key) or "").strip()
    except Exception:       # secrets.toml이 아예 없는 로컬 실행
        return ""


_ID = r"(?<![0-9a-f])([0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12})(?![0-9a-f])"


def _embed_url(raw: str) -> str:
    """notion.site 게시·임베드 주소(또는 그 주소가 든 <iframe> 코드)를 /ebd/ 주소로.
    notion.site가 아닌 주소는 iframe에 넣을 수 없어 ""."""
    m = re.search(r"https://([\w-]+)\.notion\.site/([^\s\"'<>]*)", raw or "", re.I)
    if not m:
        return ""
    path, _, query = m.group(2).lower().partition("?")
    ids = re.findall(_ID, path)
    if not ids:
        return ""
    v = re.search(r"(?:^|[&;])v=" + _ID, query)      # DB는 보기(view)를 지정해야 그 표가 나온다
    out = f"https://{m.group(1)}.notion.site/ebd/{ids[-1].replace('-', '')}"
    return out + (f"?v={v.group(1).replace('-', '')}" if v else "")


def _why_not(raw: str) -> str:
    """시크릿 값이 있는데 임베드 주소로 못 바꾼 이유 — 값은 드러내지 않고 종류만."""
    if re.search(r"\.notion\.site/", raw, re.I):
        return "notion.site 주소이긴 한데 페이지 ID(32자리)를 찾지 못했습니다."
    host = re.search(r"https?://([^/\s\"'<>]+)", raw)
    if host and re.search(r"(^|\.)notion\.(so|com)$", host.group(1), re.I):
        return (f"{host.group(1)} 주소는 노션이 다른 사이트 안에 띄우는 것을 막아 둔 주소입니다. "
                "노션에서 <b>공유 → 게시 → 웹에 게시</b>를 켠 뒤 나오는 "
                "<code>…notion.site/…</code> 주소(또는 임베드 코드)를 넣어 주세요.")
    return "노션 웹 게시 주소(<code>https://….notion.site/…</code>) 형태가 아닙니다."


def _notion_ids(raw: str):
    """시크릿 값 → (워크스페이스 주소, DB 블록 ID, 보기 ID|None). 못 읽으면 None."""
    url = _embed_url(raw)
    m = re.match(r"(https://[\w-]+\.notion\.site)/ebd/([0-9a-f]{32})(?:\?v=([0-9a-f]{32}))?", url)
    return (m.group(1), m.group(2), m.group(3)) if m else None


def _rec(rm: dict, table: str, rid: str) -> dict:
    v = (rm.get(table, {}).get(rid) or {}).get("value") or {}
    return v.get("value", v)        # 응답이 value를 한 번 더 감싸는 경우가 있다


@st.cache_data(ttl=600, show_spinner=False)
def notion_db_index() -> dict:
    """{공백 뺀 종목명: (행 페이지 주소, 등급|"")} — 게시된 기업분석 DB의 모든 행.
    시크릿이 없으면 {}. 네트워크·응답 오류는 예외로(빈 결과를 10분 붙잡지 않게)."""
    ids = _notion_ids(_secret("NOTION_EMBED_URL"))
    if not ids:
        return {}
    ws, block, view = ids
    hdr = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
    r = requests.post(f"{ws}/api/v3/loadCachedPageChunkV2", headers=hdr, timeout=15,
                      json={"page": {"id": str(uuid.UUID(block))}, "limit": 30,
                            "cursor": {"stack": []}, "verticalColumns": False})
    r.raise_for_status()
    b = _rec(r.json().get("recordMap", {}), "block", str(uuid.UUID(block)))
    coll, space = b.get("collection_id"), b.get("space_id")
    view = str(uuid.UUID(view)) if view else (b.get("view_ids") or [None])[0]
    if not (coll and space and view):
        raise RuntimeError("노션 DB 블록에서 collection/view를 찾지 못함")
    r = requests.post(f"{ws}/api/v3/queryCollection?src=initial_load", headers=hdr, timeout=30,
                      json={"collection": {"id": coll, "spaceId": space},
                            "collectionView": {"id": view, "spaceId": space},
                            "loader": {"type": "reducer", "searchQuery": "", "userTimeZone": "Asia/Seoul",
                                       "reducers": {"collection_group_results":
                                                    {"type": "results", "limit": 5000}}}})
    r.raise_for_status()
    d = r.json()
    rm = d.get("recordMap", {})
    schema = _rec(rm, "collection", coll).get("schema", {})
    grade_key = next((k for k, v in schema.items() if v.get("name") == "등급"), None)
    text = lambda v: "".join(str(x[0]) for x in (v or []) if x)
    out = {}
    for rid in d["result"]["reducerResults"]["collection_group_results"]["blockIds"]:
        props = _rec(rm, "block", rid).get("properties", {})
        name = re.sub(r"\s+", "", text(props.get("title")))
        if name:
            out[name] = (f"{ws}/{rid.replace('-', '')}", text(props.get(grade_key)) if grade_key else "")
    return out


# ── 노션 등급 열 (다른 탭이 가져다 쓴다) ────────────────────────────────────
# 신고가 표가 먼저 쓰던 모양을 2026-11-14에 섹터·거래급증·투신·실적으로 넓혔다.
# 표 셀에는 HTML을 못 넣고 LinkColumn이 글자를 정하는 수단은 URL에 거는 정규식
# 하나뿐이라, 등급을 프래그먼트(#A)로 실어 보내고 거기서 되뽑는다
# (종목명 링크와 같은 방법 — ui_common.naver_link 머리말 참고).
NOTION_GRADE_HELP = "노션 기업분석 DB에 있는 종목 — 등급이 보이고, 누르면 그 페이지가 열린다"


def notion_grade_map() -> dict:
    """{공백 뺀 종목명: (주소, 등급)}. 실패는 삼킨다 — 등급은 덤이라 노션이 막혔다고
    표가 통째로 안 뜨면 안 된다(notion_db_index 자체는 예외를 올린다)."""
    try:
        return notion_db_index()
    except Exception as e:
        print(f"[ui_company] 노션 등급 조회 실패(열은 비워 둔다): {type(e).__name__}: {e}")
        return {}


def notion_grade_links(names) -> list:
    """종목명 목록 -> '주소#등급' 목록. 노션은 한 번만 읽는다. 없으면 빈 문자열."""
    m = notion_grade_map()
    out = []
    for n in names:
        hit = m.get("".join(str(n).split()))
        # 등급이 비어 있어도 링크는 살린다 — 정규식이 안 걸리면 URL이 통째로 보인다.
        out.append(f"{hit[0]}#{hit[1] or '노션'}" if hit else "")
    return out


def notion_grade_column(label: str = "노션", width="small"):
    """표의 등급 열. 호출부가 column_config에 그대로 넣는다."""
    return st.column_config.LinkColumn(label, display_text=r"#(.+)$", width=width,
                                       help=NOTION_GRADE_HELP)


_FRAME_H = 900

# 진행률 모양은 st.progress(run_with_progress)와 같게 — 글자 + 얇은 빨간 막대.
# 막대는 iframe 위에 겹쳐 띄우고 끝나면 사라져, 화면이 아래로 밀리지 않는다.
_FRAME_HTML = """<!doctype html><html><head><meta charset="utf-8"><style>
html,body{margin:0;padding:0;background:#fff;}
#wrap{position:relative;}
#nf{display:block;width:100%;height:__H__px;border:0;}
#prog{position:absolute;left:0;right:0;top:0;padding:10px 2px;background:#fff;transition:opacity .3s;
  font:14px "Source Sans Pro","Source Sans 3",-apple-system,"Malgun Gothic",sans-serif;color:#31333f;}
#track{height:.5rem;border-radius:.5rem;background:#f0f2f6;margin-top:6px;overflow:hidden;}
#bar{height:100%;width:0;background:#ff4b4b;border-radius:.5rem;transition:width .2s linear;}
</style></head><body><div id="wrap">
<iframe id="nf" src="__SRC__" allow="fullscreen" referrerpolicy="no-referrer-when-downgrade"></iframe>
<div id="prog"><span id="lbl">노션 기업분석 DB 불러오는 중 0%</span><div id="track"><div id="bar"></div></div></div>
</div><script>
const t0 = performance.now(), bar = document.getElementById("bar"), lbl = document.getElementById("lbl"),
      prog = document.getElementById("prog");
let done = false;
function set(p) { bar.style.width = p + "%"; lbl.textContent = "노션 기업분석 DB 불러오는 중 " + Math.floor(p) + "%"; }
(function tick() {
  if (done) return;
  set(95 * (1 - Math.exp(-(performance.now() - t0) / 2500)));   // 2.5초 60% · 5초 82% · 8초 91%
  setTimeout(tick, 150);
})();
document.getElementById("nf").addEventListener("load", () => setTimeout(() => {
  done = true; set(100);
  setTimeout(() => { prog.style.opacity = 0; setTimeout(() => prog.remove(), 300); }, 250);
}, 900));                                                        // load 뒤 DB 데이터 요청 몫(실측 0.8초)
</script></body></html>"""


def render_company():
    page_header("기업분석", "company", refresh=False)
    raw = _secret("NOTION_EMBED_URL")
    embed = _embed_url(raw)
    open_url = _secret("NOTION_DB_URL") or _DEFAULT_URL

    if embed:
        # 노션 화면을 그대로 띄운다. 스크롤은 iframe 안에서 — 높이를 화면에 가깝게 잡는다.
        # 같은 html이면 다시 실행돼도 컴포넌트가 그대로 남아 노션을 다시 불러오지 않는다.
        components.html(_FRAME_HTML.replace("__H__", str(_FRAME_H)).replace("__SRC__", html.escape(embed)),
                        height=_FRAME_H)
        st.link_button("노션에서 열기 (편집)", open_url, icon=":material/open_in_new:")
        return

    if raw:
        msg = ("Secrets의 <code>NOTION_EMBED_URL</code> 값을 화면에 띄울 수 없습니다 — "
               + _why_not(raw) + " 그 전까지는 새 탭으로 엽니다.")
    else:
        msg = ("노션 DB를 이 화면에 바로 띄우려면 노션에서 <b>웹에 게시 → 임베드</b> 주소를 만들어 "
               "Secrets의 <code>NOTION_EMBED_URL</code>에 넣어 주세요. 그 전까지는 새 탭으로 엽니다.")
    st.markdown(f"<div style='font-size:12.5px;color:#666;margin:2px 0 6px;'>{msg}</div>",
                unsafe_allow_html=True)
    st.link_button("노션 기업분석 DB 열기", open_url, icon=":material/open_in_new:",
                   type="primary", width="stretch")
