"""
earnings_store.py — 실적 데이터 저장소 (분기별 파일 분리).

━━ 왜 쪼개나 ━━
예전에는 모든 분기를 `data/earnings/earnings_data.json` 한 파일에 담았다.
파일이 약 4MB인데(대부분 `원문` — 한글은 UTF-8에서 3바이트),
배치가 돌 때마다 **파일 전체를 재작성**해서 커밋했다. 실적 배치는 하루에도
여러 번 돌기 때문에, 커밋마다 4MB짜리 새 blob이 git에 쌓였다 —
리포지토리 .git이 80MB까지 불어난 가장 큰 원인이다.

분기별로 나누면
  · 배치는 실제로 바뀐 분기 파일만 다시 쓴다 → 커밋 delta가 1/3 이하로 줄고,
    과거 분기 파일은 한 번 쓰이면 다시 안 바뀌므로 git이 더 이상 안 불어난다.
  · UI는 선택된 분기 파일만 읽는다 → 실적 탭 진입이 빨라진다.

━━ 어디에 두나 (2026-11-16~) ━━
분기 파일·index.json·batch_config.json은 **batch-state 브랜치**에 있다. main에 두면
배치가 30분마다 커밋하면서 Streamlit Cloud가 그때마다 앱을 재시작한다 — 평일 하루
20번이었다(급등알림 상태·관심종목을 옮긴 것과 같은 이유, surge_batch.yml 주석 참고).

그래서 읽기가 두 갈래다. **분기하는 기준은 로컬 파일이 있느냐 하나다**:
  · 있으면 로컬 → 배치 러너(워크플로우가 batch-state에서 받아 둔다)와 로컬 개발
  · 없으면 batch-state에서 받는다 → 앱(Streamlit Cloud)
받은 내용을 EARNINGS_DIR에 쓰지 말 것 — 그 순간 '로컬이 있다'가 되어 영영 그 값에
묶인다. 실패 대비 사본은 `.cache/`에 따로 두고, 조회가 **실패했을 때만** 쓴다.

리포가 공개라 토큰 없이 raw로 받힌다(2026-11-16 실측: 1.8MB 0.19초, 1.1MB 0.13초).
토큰이 있으면 Contents API를 먼저 쓴다 — raw는 CDN이 몇 분 캐시해서 그만큼 늦는다.

━━ 호환성 ━━
기존 단일 파일(`earnings_data.json`)이 남아 있으면 그대로 읽어들여
분기별 파일로 옮긴다(마이그레이션). 분기별 파일이 아직 없는 배포에서도
UI가 죽지 않도록 읽기 경로는 항상 레거시 폴백을 갖는다.
"""
import hashlib
import json
import os
import re

_DIR = os.path.dirname(os.path.abspath(__file__))
EARNINGS_DIR = os.path.join(_DIR, "data", "earnings")
LEGACY_FILE = os.path.join(EARNINGS_DIR, "earnings_data.json")
INDEX_FILE = os.path.join(EARNINGS_DIR, "index.json")

# 보관할 분기 수. 12분기(3년)면 충분히 넉넉해서 지금 데이터는 하나도 안 지워진다
# (현재 3분기치 보유). 이후로도 파일 총량이 이 선에서 멈춘다.
KEEP_QUARTERS = int(os.environ.get("EARNINGS_KEEP_QUARTERS", "12"))

_UNKNOWN = "분기미상"


def quarter_path(quarter: str) -> str:
    """'2026.2Q' → data/earnings/q_2026_2Q.json (파일명 안전 문자만 남긴다)."""
    q = str(quarter or _UNKNOWN)
    safe = re.sub(r"[^0-9A-Za-z]+", "_", q).strip("_")
    if not safe:
        # 💡 '분기미상'처럼 ASCII가 하나도 없는 라벨은 sanitize 후 빈 문자열이 되어
        # 서로 같은 파일명(q_.json)으로 충돌한다 — 짧은 해시로 고유성을 보장한다.
        safe = "h" + hashlib.md5(q.encode("utf-8")).hexdigest()[:8]
    return os.path.join(EARNINGS_DIR, f"q_{safe}.json")


def _read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # 💡 indent 없이 compact로 저장 — 들여쓰기 공백만으로 15%가 낭비됐다.
    # 사람이 직접 읽는 파일이 아니고, 필요하면 UI/도구로 보면 된다.
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))


# ── batch-state에서 읽기 (앱 경로) ──────────────────────────────────────────
_REPO = os.environ.get("EARNINGS_REPO") or "shinkiyeol9814-droid/stockapp"
_BRANCH = os.environ.get("EARNINGS_BRANCH") or "batch-state"
_RAW = "https://raw.githubusercontent.com/{repo}/{branch}/data/earnings/{name}"
_API = "https://api.github.com/repos/{repo}/contents/data/earnings/{name}"
_CACHE_DIR = os.path.join(EARNINGS_DIR, ".cache")
_TIMEOUT = 8
_FAIL = object()        # "못 받았다" — "없다"(None)와 구분해야 사본을 쓸지 정할 수 있다
_token = os.environ.get("GH_PAT") or os.environ.get("GITHUB_TOKEN") or ""


def set_token(tok: str) -> None:
    """앱이 st.secrets의 토큰을 한 번 넘겨 준다 — 있으면 CDN을 안 거쳐 항상 최신이다."""
    global _token
    _token = str(tok or "")


def _fetch_remote(name: str):
    """JSON / 없으면 None / 못 받았으면 _FAIL.

    ⚠️ **raw의 404는 믿지 말 것.** CDN이 직전 404를 몇 분 물고 있어서, 방금 올린
    파일이 한동안 없는 것처럼 보인다(2026-11-16 실측: batch-state에 올린 직후 raw는
    계속 404인데 Contents API는 바로 200). 새 분기 파일이 처음 생길 때 딱 걸리는
    자리라, raw가 404면 API로 한 번 더 확인하고 나서 '없다'로 친다.
    """
    import requests

    api_hdr = {"Accept": "application/vnd.github.raw"}
    api = (_API.format(repo=_REPO, name=name), api_hdr, {"ref": _BRANCH}, True)
    rawu = (_RAW.format(repo=_REPO, branch=_BRANCH, name=name), {}, None, False)
    if _token:
        # 토큰이 있으면 API가 먼저다 — 항상 최신이고 시간당 5,000건이다.
        tries = [(api[0], dict(api_hdr, Authorization=f"token {_token}"), api[2], True), rawu]
    else:
        tries = [rawu, api]          # 익명 API는 시간당 60건이라 404 확인용으로만

    missing = False
    for url, headers, params, authoritative in tries:
        try:
            r = requests.get(url, headers=headers, params=params, timeout=_TIMEOUT)
        except Exception as e:
            print(f"[earnings_store] {name} 조회 실패: {type(e).__name__}: {e}")
            continue
        if r.status_code == 404:
            if authoritative:
                return None      # 그 분기가 없는 것 — 사본으로 되살리면 안 된다
            missing = True
            continue
        if r.status_code != 200:
            print(f"[earnings_store] {name} 조회 HTTP {r.status_code}")
            continue
        try:
            return r.json()
        except ValueError:
            print(f"[earnings_store] {name} JSON 파싱 실패 ({len(r.content)}바이트)")
    return None if missing else _FAIL


def _save_cache(name: str, data) -> None:
    # 읽기 전용 파일시스템 등에서 실패해도 조회 자체는 성공했으니 조용히 넘어간다.
    try:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        with open(os.path.join(_CACHE_DIR, name), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    except OSError:
        pass


def _read_store(name: str, default):
    """로컬에 있으면 로컬, 없으면 batch-state. 못 받으면 마지막 사본."""
    local = os.path.join(EARNINGS_DIR, name)
    if os.path.exists(local):
        return _read_json(local, default)
    got = _fetch_remote(name)
    if got is _FAIL:
        return _read_json(os.path.join(_CACHE_DIR, name), default)
    if got is None:
        return default
    _save_cache(name, got)
    return got


def _quarter_sort_key(q: str):
    """'2026.2Q' → (2026, 2). 분기미상 등은 맨 뒤로."""
    m = re.match(r"(\d{4})\.(\d)[Qq]", str(q or ""))
    return (int(m.group(1)), int(m.group(2))) if m else (-1, -1)


def available_quarters() -> list[str]:
    """보유 중인 분기 목록 (최신순). 인덱스가 없으면 디렉터리에서 유추."""
    idx = _read_store("index.json", None)
    if isinstance(idx, dict) and idx.get("quarters"):
        qs = list(idx["quarters"])
    else:
        qs = []
        if os.path.isdir(EARNINGS_DIR):
            for name in os.listdir(EARNINGS_DIR):
                if name.startswith("q_") and name.endswith(".json"):
                    rows = _read_json(os.path.join(EARNINGS_DIR, name), [])
                    if rows:
                        qs.append(rows[0].get("해당분기") or _UNKNOWN)
        if not qs:
            # 레거시 단일 파일 폴백
            qs = sorted(
                {r.get("해당분기") or _UNKNOWN for r in _read_json(LEGACY_FILE, [])},
                key=_quarter_sort_key,
                reverse=True,
            )
    return sorted(set(qs), key=_quarter_sort_key, reverse=True)


def load_quarter(quarter: str) -> list[dict]:
    """한 분기만 읽는다 (UI가 쓰는 경로). 분기 파일이 없으면 레거시에서 걸러낸다."""
    rows = _read_store(os.path.basename(quarter_path(quarter)), None)
    if rows is not None:
        return rows
    return [r for r in _read_json(LEGACY_FILE, []) if (r.get("해당분기") or _UNKNOWN) == quarter]


def load_all() -> list[dict]:
    """모든 분기를 합쳐서 읽는다 (배치 중복제거 / 급등알림 교차확인용)."""
    out = []
    seen_any = False
    for q in available_quarters():
        rows = _read_store(os.path.basename(quarter_path(q)), None)
        if rows is not None:
            seen_any = True
            out.extend(rows)
    if not seen_any:
        return _read_json(LEGACY_FILE, [])
    return out


def save_all(rows: list[dict]) -> dict:
    """
    전체 레코드를 분기별 파일로 나눠 저장한다.
    **내용이 바뀐 분기 파일만 다시 쓴다** — 안 바뀐 파일을 건드리지 않는 것이
    git 커밋 delta를 줄이는 핵심이다.
    """
    buckets: dict[str, list] = {}
    for r in rows:
        buckets.setdefault(r.get("해당분기") or _UNKNOWN, []).append(r)

    # 보관 기간 초과 분기는 제외 (분기미상은 항상 유지 — 양이 적고 재파싱 단서가 됨)
    real = sorted([q for q in buckets if q != _UNKNOWN], key=_quarter_sort_key, reverse=True)
    keep = set(real[:KEEP_QUARTERS]) | ({_UNKNOWN} if _UNKNOWN in buckets else set())
    dropped = [q for q in buckets if q not in keep]

    written, unchanged = [], []
    for q in keep:
        path = quarter_path(q)
        new_rows = buckets[q]
        if _read_json(path, None) == new_rows:
            unchanged.append(q)
            continue
        _write_json(path, new_rows)
        written.append(q)

    for q in dropped:
        path = quarter_path(q)
        if os.path.exists(path):
            os.remove(path)

    # 💡 counts도 quarters와 같은 순서로 쓴다. keep이 집합이라 그냥 순회하면
    # 실행마다 키 순서가 뒤바뀌어, 값이 전혀 안 변해도 파일이 달라진 것처럼
    # 보인다. 이 배치는 30분마다 도는데 그때마다 index.json이 diff로 잡혀
    # main에 커밋이 쌓였고, 그 push가 Streamlit Cloud 재배포를 일으켰다
    # (실적 데이터는 그대로인데 앱만 하루 20~30번 재시작).
    ordered = sorted(keep, key=_quarter_sort_key, reverse=True)
    _write_json(INDEX_FILE, {
        "quarters": ordered,
        "counts": {q: len(buckets[q]) for q in ordered},
        "total": sum(len(buckets[q]) for q in ordered),
    })

    # 마이그레이션 완료 후 레거시 단일 파일 제거 — 남겨두면 4MB가 계속
    # 리포지토리에 상주하고, 읽기 경로가 두 갈래로 갈려 혼란만 생긴다.
    if os.path.exists(LEGACY_FILE) and written:
        os.remove(LEGACY_FILE)

    return {"written": written, "unchanged": unchanged, "dropped": dropped}
