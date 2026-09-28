"""
report_dedupe.py — 같은 레포트가 여러 텔레그램 채널에서 중복 수집되는 것을 걸러낸다.

━━ 왜 필요한가 ━━
레포트를 여러 채널에서 모으다 보니 같은 원문이 두 번 잡힌다. 파일명 기준
중복 제거(batch_report의 seen_files)는 이미 있지만, 채널마다 파일명이 달라
그걸 빠져나간다. 화면에는 같은 종목·증권사 카드가 나란히 두 장 뜬다.

━━ 판정 기준 ━━
증권사·종목이 같다고 바로 중복으로 묶으면 안 된다. 하나증권이 같은 날
종목 리포트와 산업 리포트를 함께 내면 둘 다 같은 종목·증권사지만 엄연히
다른 글이다(실제로 NC·포스코인터내셔널에서 그랬다).

그래서 **제목까지 비슷해야** 중복으로 본다. 실제 수집분으로 재본 분리선:

    진짜 중복   "탄탄한 실적, 재평가로 응답" ↔ "탄탄한 실적, 재평가"   0.84
                "오버행? 4Q…"      ↔ "신한 콥데이 - 오버행? 4Q…"      0.83
    다른 글     셀트리온 두 건                                        0.34
                하나증권 종목 리포트 ↔ 산업 리포트                    0.13

0.40과 0.83 사이가 비어 있어 0.7을 임계값으로 둔다.
"""
import difflib
import re

# 제목에서 지워야 의미가 남는 것들 — 종목코드(007660)와 미국 티커(ETN.US).
_CODE_RE = re.compile(r"\(\s*\d{6}\s*\)|\([A-Za-z]{1,6}\.[A-Za-z]{2}\)")
_NONWORD_RE = re.compile(r"[^\w가-힣]+")

_RATIO = 0.7
# 한쪽이 다른 쪽에 통째로 들어있으면 중복으로 보되, 너무 짧은 제목은 우연히
# 포함될 수 있어 최소 길이를 둔다("호실적" 같은 두세 글자 제목 방어).
_MIN_CONTAIN_LEN = 6


def _norm_title(title, stock=None) -> str:
    """비교용 제목 정규화 — 종목코드·종목명·공백·구두점을 걷어낸다."""
    t = _CODE_RE.sub(" ", str(title or ""))
    if stock:
        t = t.replace(str(stock), " ")
    return _NONWORD_RE.sub("", t).lower()


def _richness(row) -> tuple:
    """어느 쪽을 남길지 정하는 점수 — 내용이 더 채워진 쪽을 남긴다."""
    points = row.get("투자포인트") or []
    return (
        1 if row.get("평가방식") else 0,
        len(points) if isinstance(points, list) else 0,
        len(str(row.get("레포트 제목") or "")),
    )


def is_same_report(a, b) -> bool:
    """두 레포트가 같은 글인지. 증권사·종목이 같고 제목이 사실상 같을 때만 참."""
    if (a.get("종목명"), a.get("증권사")) != (b.get("종목명"), b.get("증권사")):
        return False
    ta = _norm_title(a.get("레포트 제목"), a.get("종목명"))
    tb = _norm_title(b.get("레포트 제목"), b.get("종목명"))
    if not ta or not tb:
        # 제목을 못 뽑은 건 비교할 근거가 없다 — 합치지 않고 그대로 둔다.
        return False
    if len(min(ta, tb, key=len)) >= _MIN_CONTAIN_LEN and (ta in tb or tb in ta):
        return True
    return difflib.SequenceMatcher(None, ta, tb).ratio() >= _RATIO


def dedupe(results):
    """
    중복을 합친 리스트를 돌려준다(원본 순서 유지).

    같은 글로 묶이면 내용이 더 채워진 쪽을 남긴다 — 채널에 따라 한쪽은
    평가방식이나 투자포인트가 비어 오는 경우가 있다.
    """
    if not results:
        return []
    kept = []
    for row in results:
        if not isinstance(row, dict):
            kept.append(row)
            continue
        hit = next((i for i, k in enumerate(kept)
                    if isinstance(k, dict) and is_same_report(k, row)), None)
        if hit is None:
            kept.append(row)
        elif _richness(row) > _richness(kept[hit]):
            kept[hit] = row
    return kept
