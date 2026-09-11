"""DART 재고자산·수주잔고·가동률을 data/dart/{종목코드}.json으로 모은다 (GitHub Actions 전용).

Streamlit Cloud에서는 DART 연결이 막혀 있어 앱(ui_dart_panel)은 이 파일만 읽는다.
사용: python batch_dart.py 005930 ... | --watchlist [--max-minutes N] | 환경변수 CODES(JSON 배열 또는 공백 구분)
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

import dart_fin

_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(_DIR, "data", "dart")
WATCHLIST = os.path.join(_DIR, "data", "watchlist", "watchlist.json")
KST = timezone(timedelta(hours=9))
_CODE_RE = re.compile(r"^[0-9A-Z]{6}$")
# 내용이 그대로여도 이 기간이 지나면 updated_at을 다시 써 앱의 신선도 기준(7일) 안에 둔다.
REWRITE_AFTER = timedelta(days=3)
# 워치리스트 실행은 이보다 최근 파일을 건너뛴다 — 시간 예산으로 끊긴 실행을 다음 실행이 이어받는다.
WATCHLIST_FRESH = timedelta(hours=20)
# 종목 하나의 상한(초). 스레드만 버리면 백그라운드에서 계속 돌아 뒤 종목까지 느려지므로 프로세스째 끊는다.
STOCK_TIMEOUT = 240
MAX_CONSECUTIVE_FAIL = 3
EXIT_SAVED, EXIT_FAILED, EXIT_UNCHANGED = 0, 2, 3

SECTIONS = {
    "inventory_q": lambda code: dart_fin.get_inventory_quarterly(code, quarters=12),
    "inventory_y": lambda code: dart_fin.get_inventory_series(code),
    "reports": lambda code: dart_fin.get_report_series(code, limit=12),
}


def parse_args(argv, env_codes):
    ap = argparse.ArgumentParser()
    ap.add_argument("codes", nargs="*")
    ap.add_argument("--watchlist", action="store_true")
    ap.add_argument("--max-minutes", type=float, default=45.0)
    ap.add_argument("--one", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    raw = list(args.codes)
    env_codes = (env_codes or "").strip()
    if env_codes and env_codes != "null" and not args.one:
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
    return args, codes


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


def _recent(data, now, window):
    try:
        return now - datetime.fromisoformat(data["updated_at"]) < window
    except (KeyError, TypeError, ValueError):
        return False


def run_one(code):
    """종목 하나를 수집해 저장한다. 부모 프로세스가 시간 상한과 함께 호출한다."""
    prev = _load(code)
    payload, failures = collect(code, prev)
    if len(failures) == len(SECTIONS) and not all(_permanent(e) for e in failures):
        print(f"::warning::{code} 수집 실패, 기존 파일 유지: {payload['errors']}")
        return EXIT_FAILED
    now = datetime.now(KST)
    if prev and _unchanged(prev, payload) and _recent(prev, now, REWRITE_AFTER):
        print(f"{code} {payload['corp_name']} 변경 없음")
        return EXIT_UNCHANGED
    payload["updated_at"] = now.isoformat(timespec="seconds")
    # 임시 파일에 쓴 뒤 교체한다 — 쓰는 도중 종료돼도 반쯤 쓴 JSON이 커밋되지 않게.
    tmp = _path(code) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    os.replace(tmp, _path(code))
    print(f"{code} {payload['corp_name']} 저장 (실패 항목: {', '.join(payload['errors']) or '없음'})")
    return EXIT_SAVED


def main(argv=None):
    args, codes = parse_args(sys.argv[1:] if argv is None else argv, os.environ.get("CODES"))
    if args.one:
        return run_one(args.one) if _CODE_RE.match(args.one) else 1
    if not codes:
        print("[batch_dart] 대상 종목 없음")
        return 0
    if not dart_fin._api_key():
        print("::error::DART_API_KEY 가 설정되지 않았습니다")
        return 1
    os.makedirs(OUT_DIR, exist_ok=True)
    try:
        dart_fin.get_corp_map()
    except Exception as e:
        print(f"::error::corp_map 조회 실패: {type(e).__name__}: {dart_fin._redact(e)}")
        return 1

    now = datetime.now(KST)
    todo = [c for c in codes if not (args.watchlist and _recent(_load(c), now, WATCHLIST_FRESH))]
    counts = {"저장": 0, "변경없음": 0, "실패": 0, "건너뜀(최근)": len(codes) - len(todo), "미처리": 0}
    timings = []
    started = time.time()
    consecutive = 0
    for i, code in enumerate(todo, 1):
        if time.time() - started > args.max_minutes * 60:
            counts["미처리"] = len(todo) - i + 1
            print(f"::warning::시간 예산 {args.max_minutes:.0f}분 초과, 남은 {counts['미처리']}종목은 다음 실행에서 이어서 수집")
            break
        t = time.time()
        try:
            rc = subprocess.run([sys.executable, os.path.abspath(__file__), "--one", code],
                                timeout=STOCK_TIMEOUT).returncode
        except subprocess.TimeoutExpired:
            rc = None
        elapsed = time.time() - t
        timings.append((elapsed, code))
        if rc in (EXIT_SAVED, EXIT_UNCHANGED):
            counts["저장" if rc == EXIT_SAVED else "변경없음"] += 1
            consecutive = 0
        else:
            counts["실패"] += 1
            consecutive += 1
            if rc != EXIT_FAILED:  # EXIT_FAILED는 자식이 이미 사유를 경고로 남겼다
                reason = f"{STOCK_TIMEOUT}초 초과로 종료" if rc is None else f"비정상 종료(코드 {rc})"
                print(f"::warning::{code} {reason}")
        print(f"[{i}/{len(todo)}] {code} rc={rc} {elapsed:.0f}s", flush=True)
        if consecutive >= MAX_CONSECUTIVE_FAIL:
            counts["미처리"] = len(todo) - i
            print(f"::error::{MAX_CONSECUTIVE_FAIL}종목 연속 실패, DART 장애나 요청 제한으로 보고 중단")
            break

    slow = ", ".join(f"{c} {s:.0f}s" for s, c in sorted(timings, reverse=True)[:5]) or "-"
    summary = " / ".join(f"{k} {v}" for k, v in counts.items())
    print(f"::notice title=DART batch::{summary} · 소요 {time.time() - started:.0f}s · 느린 종목: {slow}")
    return 1 if counts["실패"] and not (counts["저장"] or counts["변경없음"]) else 0


if __name__ == "__main__":
    sys.exit(main())
