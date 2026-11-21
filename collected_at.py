# -*- coding: utf-8 -*-
"""
배치가 각 소스를 **언제 받았는지** 적어 두는 작은 쪽지. 화면이 날짜 옆에 시각으로 적는다.

왜 필요한가: 데이터 파일(`[[epoch_ms, 값], ...]`)에는 '값의 날짜'만 있고 '우리가 언제
받았는지'가 없다. 시세 카드는 원천이 시각을 주니 `11/20 18:01 기준`으로 이미 적지만,
KRX PER·PBR·외국인 보유·신용 스프레드처럼 **날짜만 있는 일 단위 값**은 화면에 `11/20`만
떠서, 그게 "11/20 밤에 받은 값"인지 "며칠 전에 받아 놓고 그대로 멈춘 값"인지 구분이
안 됐다(2026-11-21 요청).

⚠️ **값이 실제로 바뀐 실행에서만 찍는다**(배치의 `updated[키]`가 True일 때). 받아보기만 한
시각을 매번 찍으면 이 파일이 실행마다 달라져 **그것만으로 main 커밋이 생기고 앱이 재시작**한다
(AGENTS.md '배포 브랜치에 커밋하지 않는 것'). 지금 방식은 어차피 바뀐 파일과 같은 커밋에
실려 가므로 커밋이 늘지 않고, 뜻도 더 분명하다 — **"이 값이 들어온 시각"**이다.
소스가 막히면 시각이 더 이상 앞으로 가지 않아 그 자체가 신호가 된다.

파일은 **쓰는 배치마다 따로** 둔다. 한 파일에 두 워크플로우가 쓰면 서로 다른 시각에
커밋하면서 push가 충돌한다:

    data/macro/collected.json      ← batch_macro.py          (macro_batch.yml)
    data/macro/krx_collected.json  ← index_valuation.py ·
                                     foreign_holding.py      (index_valuation.yml)

⚠️ `data/macro/index_val/` 안에 두지 말 것 — 그 폴더는 `ui_macro._get_index_valuation()`이
`*.json`을 통째로 glob해서 연도별 파일로 파싱한다. 쪽지가 섞이면 밴드가 깨진다.
`krx_collected.json`을 쓰는 워크플로우의 `git add`에 그 경로가 들어 있는지도 확인할 것.
"""

import json
import os
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))

_DIR = os.path.dirname(os.path.abspath(__file__))
MACRO = os.path.join(_DIR, "data", "macro", "collected.json")
KRX = os.path.join(_DIR, "data", "macro", "krx_collected.json")


def load(path: str) -> dict:
    """{키: datetime(KST)}. 파일이 없거나 깨졌으면 빈 dict — 화면은 날짜만 적는다."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    out = {}
    for k, v in raw.items():
        try:
            out[str(k)] = datetime.fromisoformat(str(v)).astimezone(KST)
        except (TypeError, ValueError):
            continue
    return out


def mark(path: str, keys, when: datetime | None = None) -> None:
    """keys(문자열 또는 목록)의 수집 시각을 적는다. 쪽지의 다른 키는 그대로 둔다."""
    if isinstance(keys, str):
        keys = [keys]
    keys = [str(k) for k in (keys or []) if k]
    if not keys:
        return
    stamp = (when or datetime.now(KST)).astimezone(KST).replace(microsecond=0).isoformat()
    cur = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            got = json.load(f)
        if isinstance(got, dict):
            cur = got
    except Exception:
        pass
    for k in keys:
        cur[k] = stamp
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            # indent=0 + sort_keys — 한 줄에 한 키라 diff가 바뀐 줄만 보인다.
            json.dump(cur, f, ensure_ascii=False, indent=0, sort_keys=True)
    except OSError as e:
        print(f"[collected_at] {path} 쓰기 실패: {type(e).__name__}: {e}")
