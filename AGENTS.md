# AGENTS.md

AI 코딩 에이전트(Claude Code, Cursor 등)를 위한 프로젝트 가이드입니다.
코드 수정 전 반드시 이 파일을 먼저 읽으세요.

---

## 프로젝트 개요

Streamlit 기반 한국 주식 투자 보조 도구.
Streamlit Cloud (Python 3.14)에서 호스팅. 실시간 주가·재무·수출 데이터 취합.

---

## ⚠️ 최우선 컨벤션: 색상 규칙

**한국 주식 시장 색상 — 절대 예외 없이 적용:**

| 상태 | 색상 | hex 코드 |
|------|------|----------|
| 상승 / 양수(+) | **빨간색** | `#ef5350` |
| 하락 / 음수(-) | **파란색** | `#1565C0` |
| 보합 / 0 | 회색 | `#888888` |

### 적용 방법별 예시

```python
# Python HTML (st.markdown)
clr = "#ef5350" if v > 0 else "#1565C0" if v < 0 else "#888"

# AG Grid cellStyle (JsCode)
"if (v > 0) return {color:'#ef5350'}; if (v < 0) return {color:'#1565C0'};"

# Plotly 트레이스
color = "#ef5350" if is_up else "#1565C0"

# ❌ 절대 금지
st.metric(..., delta_color="normal")   # Streamlit 기본색 = 초록/빨강
st.metric(..., delta_color="inverse")  # 여전히 초록 포함
# ✅ 대신 커스텀 HTML 사용
```

---

## 파일 구조

| 파일 | 역할 |
|------|------|
| `app.py` | 메인 진입점, 탭 라우팅, 전역 CSS, autorefresh |
| `valuation.py` | 가치평가 탭 UI + 공유 데이터 함수(`get_hybrid_financials` 등) |
| `ui_watchlist.py` | 워치리스트 탭 (AG Grid 섹터별 테이블) |
| `ui_macro.py` | 매크로 지표 & 수출 동향 탭 |
| `ui_earnings.py` | 실적 탭 |
| `ui_report.py` | 레포트 탭 |
| `ui_telegram.py` | 텔레그램 뷰어 탭 |
| `new_high.py` | 신고가 탭 |

---

## 주요 의존성

```
streamlit, plotly, pandas, yfinance
FinanceDataReader (fdr)          # 한국 주식/FX 데이터
pykrx                            # KRX 상장 정보
streamlit-aggrid==0.3.4.post3    # 워치리스트 테이블
streamlit-option-menu            # 상단 탭 메뉴
```

### Python 3.14 + pandas 2.x 호환 주의사항 (aggrid)
- `valueGetter` (JS) 대신 **Python 사전 계산** 사용
- `type="numericColumn"` 컬럼은 반드시 `float64` dtype (None → `float("nan")`)
- aggrid `reload_data=True` 는 편집값을 초기화하므로 변경 시에만 사용

---

## 데이터 패턴

### 워치리스트 저장 (GitHub)
- 파일: `data/watchlist/watchlist.json` in `GITHUB_REPO`
- 인증: `st.secrets["GH_PAT"]` 또는 `st.secrets["GITHUB_TOKEN"]`
- JSON 스키마: `{종목코드: {method, multiple, sector}}`

### 캐시 전략

| 함수 | TTL | 이유 |
|------|-----|------|
| `get_live_price` | 60s | 실시간 주가 |
| `load_watchlist` | 60s | GitHub API 속도 제한 |
| `get_watch_financials` | 3600s | 재무데이터 변동 적음 |
| `_get_price_history` (macro) | 300s | 시장 지표 |
| `_get_trade_data` (macro) | 3600s | 수출 데이터 |

### 워치리스트 Session State 네이밍

| 키 | 내용 |
|----|------|
| `wl_m_{code}` | 평가방식 (method) |
| `wl_x_{code}` | 목표배수 (multiple) |
| `wl_s_{code}` | 섹터 (sector) |
| `_wlc_{code}` | 병렬 로딩 완료 마킹 |
| `_wl_pending` | 다음 런 적용할 변경값 `{code: (m, x, s)}` |
| `_wl_fresh` | GitHub 캐시 지연 우회용 즉시 반영 watchlist dict |

### 2-Rerun 패턴 (편집값 즉시 반영)

AG Grid 편집 → 업사이드 즉시 재계산을 위한 패턴:

1. **편집 감지** → `_wl_pending` 설정 + `st.rerun()`
2. **다음 런 시작** → `pending = st.session_state.pop("_wl_pending", {})` →
   세션 상태 업데이트 → df 재계산 → `force_reload=True` 로 그리드 갱신

---

## 차트 설정 (Plotly)

```python
# 기본 설정 — 항상 pan 모드, scrollZoom 허용
fig.update_layout(dragmode="pan")
st.plotly_chart(fig, config={
    "scrollZoom": True,
    "displayModeBar": "hover",
    "modeBarButtonsToRemove": ["select2d", "lasso2d", "zoom2d"],
})
```

---

## Secrets 목록

| 키 | 용도 |
|----|------|
| `GH_PAT` / `GITHUB_TOKEN` | GitHub 워치리스트 저장 |
| `TELEGRAM_API_ID` | 텔레그램 뷰어 |
| `TELEGRAM_API_HASH` | 텔레그램 뷰어 |
| `DATA_GO_KR_KEY` | 관세청 수출입 통계 API (매크로 탭) |
