# AGENTS.md

AI 코딩 에이전트(Claude Code, Cursor 등)를 위한 프로젝트 가이드입니다.
코드 수정 전 반드시 이 파일을 먼저 읽으세요.

---

## 프로젝트 개요

Streamlit 기반 한국 주식 투자 보조 도구.
Streamlit Cloud에서 호스팅. 실시간 주가·재무·수출 데이터 취합.

### Python 버전 (실측 기준)
| 환경 | 버전 | 비고 |
|------|------|------|
| GitHub Actions 배치 | **3.12** | 모든 워크플로우에 핀 고정 |
| 로컬 개발 | 3.13 | |
| Streamlit Cloud | 앱 설정에서 지정 | 대시보드에서 확인 필요 |

> ⚠️ 이 문서에 한동안 "Python 3.14"로 적혀 있었으나 근거가 확인되지 않았다.
> 배치는 3.12로 통일했고, Cloud 런타임은 Streamlit 앱 설정에서 직접 확인할 것.

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
| `ui_macro.py` | 매크로 지표 탭 (환율/금리/원자재/메모리/미국부채) |
| `ui_earnings.py` | 실적 탭 |
| `ui_report.py` | 레포트 탭 |
| `new_high.py` | 신고가 탭 |
| `ui_sector.py` | 섹터별 등락률 탭 (Top5 + 이슈 뉴스) |
| `ui_trade.py` | 수출입 동향 탭 (관세청 품목별 실적) |
| `trade_items.py` | **수출입 세부품목 카탈로그 (HS코드 ↔ 관련 상장종목, 47개)** |
| `krx_listing.py` | **KRX 종목목록 조회 (다중 소스 폴백 + 디스크 캐시)** |
| `earnings_store.py` | **실적 데이터 저장소 (분기별 파일 분리)** |
| `cleanup_data.py` | 날짜별 데이터 보존 기간 관리 (기본 180일) |

---

## 주요 의존성

```
streamlit, plotly, pandas, yfinance
FinanceDataReader (fdr)          # 한국 주식/FX 데이터
streamlit-aggrid==0.3.4.post3    # 워치리스트 테이블
streamlit-option-menu            # 상단 탭 메뉴
```

### pandas 2.x 호환 주의사항 (aggrid)
- `valueGetter` (JS) 대신 **Python 사전 계산** 사용
- `type="numericColumn"` 컬럼은 반드시 `float64` dtype (None → `float("nan")`)
- aggrid `reload_data=True` 는 편집값을 초기화하므로 변경 시에만 사용

---

## ⚠️ 종목목록 조회는 반드시 `krx_listing`을 쓸 것

`fdr.StockListing('KRX')`를 **직접 호출하지 말 것.** fdr은 KRX가 알려주는
최종 영업일 CSV를 서드파티 캐시 리포지토리에서 받아오는데, 장 마감 직후
그 리포에 당일 파일이 아직 없으면 404로 하드 실패하고 **이전 날짜 폴백이
없다**. 이게 "장 끝나면 가치평가 검색이 안 되는" 증상의 원인이었다.

```python
from krx_listing import fetch_krx_listing   # fdr → 캐시리포(날짜 되짚기) → KIND → 디스크
```

앱에서는 `valuation.get_ticker_listing()`을 쓰면 위 체인이 캐시와 함께 적용된다.
`data/listing/krx_listing.csv`가 최후의 방어선이며 macro 배치가 매일 갱신한다.

### pykrx는 쓰지 말 것
`pykrx`는 이제 `KRX_ID`/`KRX_PW` 환경변수(KRX 계정)를 요구해서 자격증명 없이는
전부 실패한다. `batch_report.py`가 이 때문에 주가 수집이 상시 실패하고 있었다.
대체: `fdr.StockListing('KRX')`가 Code/Name/Close/Marcap을 한 번에 준다.

---

## 데이터 패턴

### 데이터 파일 레이아웃
```
data/
  listing/krx_listing.csv        # 종목목록 시드 (최후 폴백, 배치가 매일 갱신)
  macro/{dram,ddr4,lithium}_cache.json   # 스팟 가격 누적 (예전엔 리포지토리 루트)
  macro/us_debt_cache.json       # 미국 연방부채 (재무부 API, 3년치)
  earnings/index.json            # 보유 분기 목록
  earnings/q_2026_2Q.json        # 분기별 실적 (단일 4MB 파일에서 분리)
  broker_report/*.json           # 날짜별, 180일 보존
  new_high/*.json                # 날짜별, 180일 보존
```

**실적 데이터는 `earnings_store`를 통해서만 읽고 쓴다.** 단일 파일을 매 배치마다
재작성하면 4MB blob이 커밋마다 쌓여 .git이 폭증한다 — 분기별로 나눠 바뀐 파일만 쓴다.

### 캐시 시계열의 타임스탬프 규칙
`data/macro/*_cache.json`은 `[[epoch_ms, 값], ...]` 형식이고, **epoch_ms는
반드시 UTC 자정**이어야 한다. `ui_macro`가 `pd.to_datetime(ts, unit="ms")`로
읽으면 tz 없는 UTC 시각이 나오므로, KST 자정으로 저장하면 화면에 날짜가
하루 밀려(전날 15:00) 표시된다. 날짜 자체는 KST 기준으로 정한다
(러너가 UTC라 `datetime.today()`를 그냥 쓰면 한국 날짜와 어긋남).

```python
batch_macro._date_to_ms(d)          # 배치 쪽
ui_macro._today_ms_utc_midnight()   # 앱 쪽 (같은 규칙)
```

### 리포지토리 비대화 방지
- 날짜별 JSON은 `cleanup_data.py`가 보존 기간(기본 180일)을 넘긴 것을 지운다.
  배치 워크플로우에 이미 연결돼 있다.
- 매일 바뀌는 값(주가/시총)을 커밋되는 파일에 넣지 말 것. 종목목록 시드에
  `Close`/`Marcap`을 빼고 `Code/Name/Market`만 둔 이유가 이것이다.


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
| `_get_export_trend` (trade) | 3600s | 관세청 수출입 데이터 |

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

탭마다 정책이 다르다 — 아래 두 가지를 구분해서 쓸 것.

### 가치평가 탭 — pan/zoom 허용
밸류에이션 밴드 차트는 구간을 확대해 보는 게 목적이라 조작을 열어둔다.

```python
fig.update_layout(dragmode="pan")
st.plotly_chart(fig, config={
    "scrollZoom": True,
    "displayModeBar": "hover",
    "modeBarButtonsToRemove": ["select2d", "lasso2d", "zoom2d"],
})
```

### 매크로 · 수출입 탭 — 조작 잠금, 툴팁만 유지
카드 스파크라인(`ui_macro`)과 수출입 차트(`ui_trade`)는 "한눈에 보는" 용도라
드래그로 틀어지면 오히려 불편하다. **축을 `fixedrange=True`로 잠그는 것이
핵심** — `dragmode=False`만으로는 모드바나 마우스 휠로 여전히 이동/확대가 된다.

```python
fig.update_layout(
    dragmode=False,
    xaxis=dict(..., fixedrange=True),
    yaxis=dict(..., fixedrange=True),
)
st.plotly_chart(fig, config={
    "scrollZoom": False,
    "displayModeBar": False,   # 축이 잠겨 줌/이동 버튼이 전부 무동작
})
```

> ⚠️ **`staticPlot: True`를 쓰지 말 것.** 드래그는 막히지만 마우스오버
> 툴팁까지 같이 죽어서 일자별 수치를 볼 수 없다. `hovermode`는 그대로 두고
> `fixedrange`로만 잠근다.

---

## Secrets 목록

| 키 | 용도 |
|----|------|
| `GH_PAT` / `GITHUB_TOKEN` | GitHub 워치리스트 저장 |
| `DATA_GO_KR_KEY` | 관세청 수출입 통계 API (수출입 탭) |

> 텔레그램 시크릿은 앱에서 더 이상 쓰지 않는다 — 뷰어 탭을 없앴고,
> 수집은 배치(`batch_*`)에서만 한다. 아래 Actions Secrets 참고.

### GitHub Actions Secrets (배치 전용)

| 키 | 용도 |
|----|------|
| `GEMINI_API_KEY_A` | Gemini AI 분석 |
| `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` | 텔레그램 접속 |
| `TELEGRAM_SESSION` / `TELEGRAM_SESSION_BATCH` | 텔레그램 세션 문자열 |
| `SURGE_ALERT_CHAT` | 급등주 알림 대상 (초대 링크 / @유저네임 / 채널 ID) |
| `EARNINGS_CHANNEL` | 실적 공시 수집 채널 |
| `REPORT_CHANNELS_TEXT` | 레포트 텍스트 채널 (쉼표 구분) |
| `REPORT_CHANNELS_PDF` | 레포트 PDF 채널 (쉼표 구분, 비공개 채널 ID 포함) |

> ⚠️ **텔레그램 채널/초대 링크를 코드에 하드코딩하지 말 것.**
> 이 리포지토리는 공개이고, 비공개 그룹 초대 링크는 사실상 공유 비밀이다 —
> 링크를 아는 사람은 누구나 그룹에 입장할 수 있다.
