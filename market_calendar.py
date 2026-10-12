"""
market_calendar.py — 한국 증시(KRX) 개장일 판정. 배치가 휴장일에 헛도는 걸 막는다.

━━ 왜 필요한가 ━━
배치 대부분이 외부 크론(cron-job.org)으로 요일·공휴일 구분 없이 돈다. 그래서
2026-10-09(한글날, 휴장)에도 신고가 분석이 돌아 Gemini를 부르고 "10/9 신고가"
파일을 만들었고, 급등주 알림은 일요일 하루에만 288번(5분마다) 돌았다.

━━ 판정 규칙 ━━
  · 주말
  · 공휴일 — holidays.KR. 2026년 1~8월 실제 KOSPI 거래기록과 대조해 완전히 일치했다
    (지방선거일 6/3, 제헌절 7/17 포함). exchange_calendars의 XKRX 달력은 이 둘을
    빠뜨려 쓰지 않는다.
  · 12/31 연말 휴장 — 공휴일이 아니라 holidays.KR에 없어서 따로 둔다.
  · EXTRA_CLOSED / EXTRA_OPEN — 정부가 임시공휴일을 급히 지정했는데 라이브러리가
    아직 모를 때 여기에 날짜를 넣는다.

⚠️ 확인이 실패하면(라이브러리 미설치 등) **개장일로 간주한다.** 달력 오류로 평일
수집이 통째로 멈추는 쪽이, 휴장일에 한 번 헛도는 쪽보다 훨씬 나쁘다.

날짜는 항상 KST 기준이다(러너는 UTC라 datetime.today()를 그냥 쓰면 하루 어긋난다).
"""
import os
import sys
from datetime import date, datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))

# 라이브러리에 아직 반영 안 된 임시공휴일 등 — date(YYYY, M, D) 형태로 추가
EXTRA_CLOSED: dict = {}     # 예) {date(2026, 10, 2): "임시공휴일"}
EXTRA_OPEN: set = set()     # 라이브러리가 휴장으로 잘못 아는 날


def today_kst() -> date:
    return datetime.now(KST).date()


def closed_reason(d: date | None = None) -> str | None:
    """휴장이면 사유 문자열, 개장일이면 None."""
    d = d or today_kst()
    if d in EXTRA_OPEN:
        return None
    if d in EXTRA_CLOSED:
        return EXTRA_CLOSED[d]
    if d.weekday() >= 5:
        return "주말"
    if (d.month, d.day) == (12, 31):
        return "연말 휴장"
    try:
        import holidays
        name = holidays.KR(years=d.year).get(d)
        return name or None
    except Exception as e:
        print(f"⚠️ 공휴일 달력 확인 실패 — 개장일로 간주합니다: {type(e).__name__}: {e}")
        return None


def is_krx_open(d: date | None = None) -> bool:
    return closed_reason(d) is None


def previous_open_day(d: date | None = None) -> date:
    """d 이전(당일 제외)의 가장 가까운 개장일."""
    d = d or today_kst()
    for i in range(1, 30):
        p = d - timedelta(days=i)
        if is_krx_open(p):
            return p
    return d - timedelta(days=1)


def skip_if_closed(label: str, d: date | None = None) -> None:
    """
    휴장일이면 사유를 남기고 프로세스를 정상 종료(exit 0)한다.

    수동 실행(workflow_dispatch)은 막지 않는다 — 사람이 일부러 누른 건 이유가 있다.
    GitHub가 러너에 GITHUB_EVENT_NAME을 넣어 주므로 워크플로우는 손대지 않아도 된다.
    로컬에서 강제로 돌리려면 FORCE_RUN=1.
    """
    if os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch" or os.environ.get("FORCE_RUN") == "1":
        return
    d = d or today_kst()
    reason = closed_reason(d)
    if reason:
        print(f"🛑 {label}: {d}({reason})은 휴장일이라 건너뜁니다.")
        # annotation은 공개 API로도 읽혀, 로그를 안 열어도 왜 비었는지 보인다.
        print(f"::notice title=KRX 휴장::{label} 건너뜀 — {d} {reason}")
        sys.exit(0)


if __name__ == "__main__":
    d = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else today_kst()
    r = closed_reason(d)
    print(f"{d}: {'휴장 (' + r + ')' if r else '개장'}")
