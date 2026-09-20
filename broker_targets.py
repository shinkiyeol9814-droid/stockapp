"""
broker_targets.py — 증권사 레포트에서 목표주가만 뽑아 종목별로 취합한다.

data/broker_report/*.json 은 원래 "레포트 요약" 탭이 날짜별로 하나씩 읽는
용도였다. 여기서는 전체 파일을 한 번에 훑어 종목명 → 레포트 목록 인덱스를
만들어, 가치평가 탭에서 "이 종목을 증권사들은 얼마로 보나"를 바로 띄운다.

전수 스캔은 237개 파일 / 13,262건 기준 0.13초라 캐시 한 번이면 충분하다.
"""
import glob
import json
import os
import re
import statistics
from datetime import datetime, timedelta

import streamlit as st


def _digits(v) -> int:
    """'123,000원' → 123000. 값이 없거나 'None' 문자열이면 0."""
    return int(re.sub(r"[^\d]", "", str(v or ""))) if re.sub(r"[^\d]", "", str(v or "")) else 0


def _clean_name(v) -> str:
    """'삼성전자(005930)' 처럼 코드가 붙어 오는 경우가 있어 떼어낸다."""
    return str(v or "").split("(")[0].strip()


@st.cache_data(ttl=900, show_spinner=False)
def _target_index() -> dict:
    """
    종목명 → [{증권사, 목표주가, 발행일자, 제목, 평가방식}] 인덱스.

    발행일자가 비어 있는 레포트가 전체의 5% 정도 있어, 그럴 때는 파일의
    analysis_time(수집 시각)을 대신 쓴다. 정렬·중복 제거의 기준이라 비워 두면
    "가장 최신 레포트"를 고를 수 없다.
    """
    index: dict[str, list] = {}
    for path in glob.glob("data/broker_report/*.json"):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue

        if isinstance(data, dict):
            results, fallback_date = data.get("results") or [], str(data.get("analysis_time") or "")[:10]
        else:
            results = data if isinstance(data, list) else []
            m = re.search(r"(\d{4})(\d{2})(\d{2})_\d{4}", os.path.basename(path))
            fallback_date = "-".join(m.groups()) if m else ""

        for r in results:
            if not isinstance(r, dict):
                continue
            price = _digits(r.get("목표주가"))
            name = _clean_name(r.get("종목명"))
            if not price or not name:
                continue
            index.setdefault(name, []).append({
                "증권사": str(r.get("증권사") or "미상"),
                "목표주가": price,
                "발행일자": str(r.get("발행일자") or fallback_date)[:10],
                "제목": str(r.get("레포트 제목") or ""),
                "평가방식": r.get("평가방식") or "",
            })
    return index


def get_broker_targets(corp_name: str, months: int = 12) -> list:
    """
    한 종목의 증권사별 최신 목표주가. 같은 증권사가 여러 번 냈으면 최신 1건만
    남긴다 — 안 그러면 레포트를 자주 내는 대형사가 컨센서스를 끌고 간다.
    """
    name = _clean_name(corp_name)
    if not name:
        return []
    rows = list(_target_index().get(name, []))
    if not rows:
        return []

    if months:
        cutoff = (datetime.today() - timedelta(days=31 * months)).strftime("%Y-%m-%d")
        recent = [r for r in rows if r["발행일자"] >= cutoff]
        rows = recent or rows  # 전부 오래됐으면 차라리 옛 자료라도 보여준다

    latest: dict[str, dict] = {}
    for r in sorted(rows, key=lambda x: x["발행일자"]):
        latest[r["증권사"]] = r  # 날짜 오름차순이라 마지막에 남는 게 최신
    return sorted(latest.values(), key=lambda x: x["목표주가"], reverse=True)


def summarize(rows: list) -> dict:
    """컨센서스(중앙값)·최고·최저. 중앙값을 쓰는 건 이상치 1건에 안 흔들리라고."""
    prices = [r["목표주가"] for r in rows]
    return {
        "count": len(rows),
        "median": int(statistics.median(prices)) if prices else 0,
        "max": max(prices) if prices else 0,
        "min": min(prices) if prices else 0,
        "latest": max((r["발행일자"] for r in rows), default=""),
    }
