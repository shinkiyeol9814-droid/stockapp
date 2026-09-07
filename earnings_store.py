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


def _quarter_sort_key(q: str):
    """'2026.2Q' → (2026, 2). 분기미상 등은 맨 뒤로."""
    m = re.match(r"(\d{4})\.(\d)[Qq]", str(q or ""))
    return (int(m.group(1)), int(m.group(2))) if m else (-1, -1)


def available_quarters() -> list[str]:
    """보유 중인 분기 목록 (최신순). 인덱스가 없으면 디렉터리에서 유추."""
    idx = _read_json(INDEX_FILE, None)
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
    rows = _read_json(quarter_path(quarter), None)
    if rows is not None:
        return rows
    return [r for r in _read_json(LEGACY_FILE, []) if (r.get("해당분기") or _UNKNOWN) == quarter]


def load_all() -> list[dict]:
    """모든 분기를 합쳐서 읽는다 (배치 중복제거 / 급등알림 교차확인용)."""
    out = []
    seen_any = False
    for q in available_quarters():
        rows = _read_json(quarter_path(q), None)
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

    _write_json(INDEX_FILE, {
        "quarters": sorted(keep, key=_quarter_sort_key, reverse=True),
        "counts": {q: len(buckets[q]) for q in keep},
        "total": sum(len(buckets[q]) for q in keep),
    })

    # 마이그레이션 완료 후 레거시 단일 파일 제거 — 남겨두면 4MB가 계속
    # 리포지토리에 상주하고, 읽기 경로가 두 갈래로 갈려 혼란만 생긴다.
    if os.path.exists(LEGACY_FILE) and written:
        os.remove(LEGACY_FILE)

    return {"written": written, "unchanged": unchanged, "dropped": dropped}
