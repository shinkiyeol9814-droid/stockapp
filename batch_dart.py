"""DART 재고자산·수주잔고·가동률을 data/dart/{종목코드}.json으로 모은다 (GitHub Actions 전용).

Streamlit Cloud에서는 DART 연결이 막혀 있어 앱(ui_dart_panel)은 이 파일만 읽는다.
사용: python batch_dart.py 005930 ... | --watchlist | 환경변수 CODES(JSON 배열 또는 공백 구분)
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import dart_fin

_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(_DIR, "data", "dart")
WATCHLIST = os.path.join(_DIR, "data", "watchlist", "watchlist.json")
KST = timezone(timedelta(hours=9))
_CODE_RE = re.compile(r"^[0-9A-Z]{6}$")
# 내용이 그대로여도 이 기간이 지나면 updated_at을 다시 써 앱의 신선도 기준(7일) 안에 둔다.
REWRITE_AFTER = timedelta(days=3)

SECTIONS = {
    "inventory_q": lambda code: dart_fin.get_inventory_quarterly(code, quarters=12),
    "inventory_y": lambda code: dart_fin.get_inventory_series(code),
    "reports": lambda code: dart_fin.get_report_series(code, limit=12),
}


def parse_codes(argv, env_codes):
    ap = argparse.ArgumentParser()
    ap.add_argument("codes", nargs="*")
    ap.add_argument("--watchlist", action="store_true")
    args = ap.parse_args(argv)

    raw = list(args.codes)
    env_codes = (env_codes or "").strip()
    if env_codes and env_codes != "null":
        try:
            parsed = json.loads(env_codes)
        except ValueError:
            parsed = None
        raw += parsed if isinstance(parsed, list) else env_codes.replace(",", " ").split()
    if args.watchlist:
        with open(WATCHLIST, encoding="utf-8") as f:
            raw += list(json.load(f))

    codes = []
    for c in raw:
        c = str(c).strip().upper()
        if not _CODE_RE.match(c):
            print(f"::warning::잘못된 종목코드 무시: {c[:20]!r}")
            continue
        if c not in codes:
            codes.append(c)
    return codes


def _path(code):
    return os.path.join(OUT_DIR, f"{code}.json")


def _load(code):
    try:
        with open(_path(code), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _permanent(exc):
    # corp_code 없음 같은 결정적 실패는 저장해서 앱이 같은 요청을 반복하지 않게 한다.
    return isinstance(exc, dart_fin.DartError) and not isinstance(exc, dart_fin.DartTimeout)


def collect(code, prev):
    payload = {"code": code, "corp_name": None, "errors": {}}
    failures = []
    for key, fetch in SECTIONS.items():
        try:
            payload[key] = fetch(code)
            payload["corp_name"] = payload["corp_name"] or payload[key].get("corp_name")
        except Exception as e:
            failures.append(e)
            # 메모리 주소(at 0x…)는 실행마다 달라 내용 비교를 깨뜨리므로 지운다.
            msg = f"{type(e).__name__}: {dart_fin._redact(e)}"
            payload["errors"][key] = re.sub(r" at 0x[0-9a-fA-F]+", "", msg)[:300]
            if prev and key in prev:
                payload[key] = prev[key]
    if prev and not payload["corp_name"]:
        payload["corp_name"] = prev.get("corp_name")
    return payload, failures


def _unchanged(prev, payload):
    def body(d):
        return json.dumps({k: v for k, v in d.items() if k != "updated_at"},
                          sort_keys=True, ensure_ascii=False)
    return body(prev) == body(payload)


def _recent(prev, now):
    try:
        return now - datetime.fromisoformat(prev["updated_at"]) < REWRITE_AFTER
    except (KeyError, TypeError, ValueError):
        return False


def main(argv=None):
    codes = parse_codes(sys.argv[1:] if argv is None else argv, os.environ.get("CODES"))
    if not codes:
        print("[batch_dart] 대상 종목 없음")
        return 0
    if not dart_fin._api_key():
        print("::error::DART_API_KEY 가 설정되지 않았습니다")
        return 1
    try:
        dart_fin.get_corp_map()
    except Exception as e:
        print(f"::error::corp_map 조회 실패: {type(e).__name__}: {dart_fin._redact(e)}")
        return 1

    os.makedirs(OUT_DIR, exist_ok=True)
    counts = {"저장": 0, "변경없음": 0, "실패": 0}
    for i, code in enumerate(codes, 1):
        tag = f"[{i}/{len(codes)}] {code}"
        prev = _load(code)
        payload, failures = collect(code, prev)
        if len(failures) == len(SECTIONS) and not all(_permanent(e) for e in failures):
            counts["실패"] += 1
            print(f"::warning::{tag} 수집 실패, 기존 파일 유지: {payload['errors']}")
            continue
        now = datetime.now(KST)
        if prev and _unchanged(prev, payload) and _recent(prev, now):
            counts["변경없음"] += 1
            print(f"{tag} {payload['corp_name']} 변경 없음")
            continue
        payload["updated_at"] = now.isoformat(timespec="seconds")
        with open(_path(code), "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        counts["저장"] += 1
        print(f"{tag} {payload['corp_name']} 저장 (실패 항목: {', '.join(payload['errors']) or '없음'})")

    print("[batch_dart] " + " / ".join(f"{k} {v}" for k, v in counts.items()))
    return 1 if counts["실패"] and not (counts["저장"] or counts["변경없음"]) else 0


if __name__ == "__main__":
    sys.exit(main())
