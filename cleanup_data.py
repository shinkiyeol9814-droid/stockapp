"""
cleanup_data.py — 날짜별로 쌓이는 데이터 파일의 보존 기간 관리.

━━ 왜 필요한가 ━━
`data/broker_report/`와 `data/new_high/`는 배치가 돌 때마다 날짜가 붙은
JSON을 새로 만들고 **한 번도 지우지 않는다**. 현재 218 + 109개이고,
전체 커밋 3,600여 건 중 2,300건이 봇 커밋이라 .git이 이미 80MB다.
Streamlit Cloud 콜드 스타트(clone)와 Actions 체크아웃이 계속 느려진다.

━━ 정책 ━━
기본 보존 기간은 180일. 지금 가진 가장 오래된 파일이 약 150~160일치라
**오늘 실행해도 아무것도 지워지지 않는다** — 앞으로 그 선에서 총량이
멈추게 하는 안전장치다. 더 줄이고 싶으면 RETENTION_DAYS로 조절한다.

  python cleanup_data.py              # 삭제 대상만 출력 (dry-run)
  python cleanup_data.py --apply      # 실제 삭제
  RETENTION_DAYS=90 python cleanup_data.py --apply

⚠️ 이 스크립트는 git 히스토리를 건드리지 않는다. 작업 트리에서 파일을
지울 뿐이므로 과거 커밋에 남은 blob은 그대로다(.git 용량은 지금 당장
줄지 않고, 앞으로 더 늘지 않게 된다). 히스토리까지 줄이려면 별도로
git filter-repo 같은 도구가 필요하고, 그건 되돌릴 수 없는 작업이라
여기서 자동으로 하지 않는다.
"""
import os
import re
import sys
from datetime import datetime, timedelta

RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "180"))

# (디렉터리, 파일명에서 YYYYMMDD를 뽑는 정규식)
TARGETS = [
    ("data/broker_report", re.compile(r"(\d{8})")),
    ("data/new_high", re.compile(r"(\d{8})")),
]

_DIR = os.path.dirname(os.path.abspath(__file__))


def _file_date(name: str, pattern: re.Pattern):
    m = pattern.search(name)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y%m%d")
    except ValueError:
        return None


def collect_stale(cutoff: datetime):
    """보존 기간을 넘긴 파일 목록. (경로, 날짜, 바이트)"""
    stale = []
    for rel_dir, pattern in TARGETS:
        abs_dir = os.path.join(_DIR, rel_dir)
        if not os.path.isdir(abs_dir):
            continue
        for name in os.listdir(abs_dir):
            if not name.endswith(".json"):
                continue
            d = _file_date(name, pattern)
            if d is None:
                # 날짜를 못 읽는 파일은 건드리지 않는다 (정체불명 파일 보호)
                continue
            if d < cutoff:
                path = os.path.join(abs_dir, name)
                stale.append((path, d, os.path.getsize(path)))
    return sorted(stale, key=lambda t: t[1])


def main():
    apply_ = "--apply" in sys.argv
    cutoff = datetime.now() - timedelta(days=RETENTION_DAYS)
    print(f"=== data cleanup (retention={RETENTION_DAYS}d, cutoff={cutoff:%Y-%m-%d}) ===")

    stale = collect_stale(cutoff)
    if not stale:
        print("삭제 대상 없음 - 보존 기간 내 데이터만 있습니다.")
        return

    total = sum(sz for _, _, sz in stale)
    print(f"삭제 대상 {len(stale)}개 / {total/1024/1024:.1f} MB")
    for path, d, sz in stale[:20]:
        print(f"   {d:%Y-%m-%d}  {os.path.relpath(path, _DIR)}  ({sz/1024:.0f} KB)")
    if len(stale) > 20:
        print(f"   ... 외 {len(stale)-20}개")

    if not apply_:
        print("\n(dry-run) 실제로 지우려면 --apply 를 붙여 다시 실행하세요.")
        return

    removed = 0
    for path, _, _ in stale:
        try:
            os.remove(path)
            removed += 1
        except OSError as e:
            print(f"   ⚠️ 삭제 실패 {path}: {e}")
    print(f"\n{removed}개 삭제 완료 ({total/1024/1024:.1f} MB 회수)")


if __name__ == "__main__":
    main()
