# Orbit 대시보드 프론트 재설계 — 전수 가이드

> 대상 독자: 이 프로젝트의 프론트를 이어서 작업할 AI/사람(예: Codex).
> 목적: 2026-07-12 세션에서 수행한 "무스크롤 단일 화면 + 반응형" 재설계의
> **원칙 · 기법 · 코드 계약 · 검증 방법**을 그대로 재현·확장할 수 있게 전수한다.
>
> 함께 읽을 것: `CLAUDE_HANDOFF.md`(제품 방향/백엔드/배포), 이 문서(프론트 방법론).

---

## 0. 한 줄 요약

**페이지는 절대 스크롤되지 않는다. 넘치는 내용은 "세부 박스" 안에서만 스크롤된다.**
이걸 CSS override로 억지로 맞추지 말고, 아래의 "고정 뷰포트 그리드" 골격으로 구조적으로 보장한다.

---

## 1. 왜 다시 짰는가 (문제 정의)

이전 프론트는 피드백을 빠르게 반영하려고 `styles.css` 하단에 레이아웃 규칙을 계속
덧씌웠다. 그 결과:

- 같은 selector가 여러 번 `!important`로 재정의됨
- `@media` 규칙끼리 충돌
- 해상도별로 카드 겹침 / 상단 1열 깨짐 / 큰 화면 하단 공백 / 가로 스크롤 반복
- 구조 자체가 `max-width` + 세로로 흐르는 페이지라 "한 화면 무스크롤"이 **원천적으로 불가능**

**교훈: override를 한 겹 더 얹지 마라.** 레이아웃이 흔들리면 "규칙을 추가"하는 게 아니라
"골격을 고친다". 아래 골격이 그 골격이다.

---

## 2. 핵심 기법 — 무스크롤을 "구조적으로" 보장하는 법

이 4가지 조합이 전부다. 하나라도 빠지면 페이지 스크롤이 새어 나온다.

```css
/* (1) 셸을 뷰포트에 못박는다 */
.app-shell { height: 100dvh; overflow: hidden; }

/* (2) 세로를 grid의 fr로 나눈다 — auto(고정 영역) + 1fr(나머지 전부) */
.content { display: grid; grid-template-rows: auto minmax(0, 1fr); }

/* (3) 모든 패널은 자기 밖으로 안 넘친다 */
.panel { min-height: 0; overflow: hidden; }

/* (4) 넘치는 리스트는 "그 박스 안에서만" 스크롤 */
.watch-table, .daytrade-table, .long-term-list { min-height: 0; overflow-y: auto; }
```

### 왜 `min-height: 0`이 필수인가 (가장 흔한 함정)

Flex/Grid 자식의 기본 `min-height`는 `auto`라서 **콘텐츠보다 작아지길 거부한다.**
그래서 리스트가 길면 자식이 부모를 밀어내 페이지가 스크롤된다.
`min-height: 0`을 줘야 자식이 부모 안으로 압축되고, 그때 `overflow-y:auto`가 발동해
**페이지가 아니라 박스가** 스크롤된다. 이 한 줄이 전체 기법의 심장이다.

### 결과
어떤 해상도에서도:
- 페이지 세로 스크롤 = 0 (데스크톱)
- 가로 스크롤 = 0
- 카드 겹침 = 0 (겹침은 `position:absolute`나 음수 margin에서 오는데, 전부 grid로만 배치)

---

## 3. 레이아웃 골격 (Overview = 홈)

```
app-shell (100dvh, overflow:hidden)  ── grid-cols: [sidebar] [1fr]
├─ sidebar   (≥1024px 고정, 이하 off-canvas 드로어)
└─ main      ── grid-rows: [topbar 64px] [1fr]
   ├─ topbar
   └─ content ── grid-rows: [auto] [1fr]   ← (2)번 기법
      ├─ hero-layout       (상단 4칸 1줄)
      └─ operations-grid   (운영 2열)
```

### 3.1 hero-layout (상단 4영역)
`[인사] [총 투자자산(dark)] [오늘의 손익] [연결배지 3개 세로]`

```css
.hero-layout {
  display: grid;
  grid-template-columns:
    minmax(220px, 0.92fr)   /* 인사 */
    minmax(340px, 1.5fr)    /* 자산 (가장 큼) */
    minmax(210px, 0.82fr)   /* 손익 */
    minmax(190px, 0.66fr);  /* 배지 */
  gap: clamp(12px, 1vw, 18px);
  align-items: stretch;     /* 카드 높이 균일 */
}
```
`minmax(최소, fr)` 패턴이 핵심: 큰 화면에선 비율(fr)대로, 좁아지면 최소폭까지 버티다가
브레이크포인트에서 열 수를 접는다.

### 3.2 operations-grid (운영 영역)
`grid-template-areas`로 배치 → 겹침 불가능, 순서 바꾸기 쉬움.

```css
.operations-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.04fr) minmax(0, 0.96fr);
  grid-template-rows: minmax(0, 1fr) minmax(0, auto);
  grid-template-areas:
    "bot analysis"
    "bot longterm";   /* bot이 왼쪽 2행을 span */
}
.bot-panel     { grid-area: bot; }
.watch-panel   { grid-area: analysis; }
.long-term-card{ grid-area: longterm; }
```
`minmax(0, ...)`의 `0`이 중요 — 안 주면 트랙이 콘텐츠 최소폭 때문에 안 줄어들어 넘친다.

### 3.3 Quant Lab (2페이지)
같은 `.content` 안에 `.strategy-control-page`를 두고 `body[data-page]`로 토글.
페이지 전환 로직은 `app.js`의 `openPage()`가 `document.body.dataset.page`를 바꾸는 것뿐:

```css
.strategy-control-page { display: none; }
body[data-page="quant"] .hero-layout,
body[data-page="quant"] .operations-grid { display: none; }
body[data-page="quant"] .strategy-control-page { display: flex; }
```

---

## 4. 반응형 전략

브레이크포인트에서 **열 수만 바꾼다.** override를 쌓지 않는다.

| 폭 | hero | operations | 셸 |
|---|---|---|---|
| ≥1180 | 4열 1줄 | 2열 | 고정 100dvh, 무스크롤 |
| 1024–1180 | 2열 | 2열 | 고정, 무스크롤 |
| <1024 | 2열/1열 | 1열 스택 | `height:auto` + **페이지 스크롤 허용** |
| ≤760 | 1열 | 1열 | 스택 |
| ≤440 | 1열 | 1열 | 폰 미세조정 |

### 원칙
- **데스크톱(≥1024)**: 무스크롤이 최우선 → 고정 뷰포트 유지.
- **좁은 화면(<1024)**: 물리적으로 다 못 담음 → 세로 스택 + 페이지 스크롤을 **허용**한다.
  억지로 우겨넣어 글자를 깨알같이 만들지 않는다. `.app-shell { height:auto; overflow:visible }`로
  자연 흐름 전환하고 사이드바는 off-canvas 드로어(`.sidebar.open`)로.

### `clamp()`로 세로도 적응시킨다
높이가 짧은 노트북(768/800)에서도 안 깨지게 폰트·여백·카드 높이를 `clamp()`로 준다.

```css
.content { gap: clamp(12px, 1.5vh, 20px); padding: clamp(14px, 2vh, 26px) clamp(16px, 2vw, 34px); }
.hero-layout > .card { min-height: clamp(118px, 13.5vh, 152px); }
.panel-header h2 { font-size: clamp(16px, 1.5vw, 19px); }
```
`vh` 단위를 섞으면 뷰포트 높이에 따라 자동으로 줄어든다.

### 짧은 높이 전용 미디어쿼리
높이가 부족할 때만 추가로 조이는 블록을 둔다(폭이 아니라 **높이** 기준!):

```css
@media (min-width: 1024px) and (max-height: 840px) { /* 흔한 노트북 768/800 */
  .content { gap: 10px; }
  .risk-row p { display: none; }         /* 보조 설명줄부터 숨김 */
  .decision-card { padding: 11px 13px; }
}
```

---

## 5. 디자인 시스템 (화이트 + 골드)

토큰은 `styles.css` 최상단 `:root`. 색을 하드코딩하지 말고 토큰을 쓴다.

```css
--app-bg:#f4efe3;  --panel:#fffdf7;  --panel-2:#fbf5e6;   /* 배경/카드 */
--ink:#22190e;  --ink-soft:#4c4234;  --muted:#8a7c65;     /* 텍스트 위계 */
--line:#ece0c6;                                            /* 경계선 */
--gold:#c39a34;  --gold-deep:#96721c;  --gold-soft:#f4e6bc;  --gold-tint:#faf3dd;
--green:#4f7a4b; --green-tint:#eaf1e2;   /* 이익(양수) */
--red:#c25948;   --red-tint:#f6e6e0;     /* 손실(음수) */
--dark-1:#241f16; --dark-2:#14110c;      /* dark 자산카드 */
--radius:16px; --radius-sm:11px; --shadow-soft: 0 1px 2px .., 0 8px 20px -16px ..;
```

규칙:
- **양수=green, 음수=red.** app.js가 `.positive-text` / `.negative-text` 클래스를 토글하므로
  이 두 클래스는 항상 유지.
- **dark 자산카드**(`--dark-1→--dark-2` 그라디언트 + 골드 텍스트)는 화면의 포컬 포인트. 유지.
- 그림자는 은은하게(`--shadow-soft`). `!important` 금지.
- 숫자는 크고 tight하게(`letter-spacing:-.03~-.04em`).
- 폰트: `DM Sans` + `Noto Sans KR`(index.html에서 로드).

---

## 6. app.js 코드 계약 ⚠️ (HTML 편집 시 반드시 지킬 것)

**app.js를 안 읽고 HTML을 고치면 대시보드가 조용히 멈춘다.** 이유:

### 6.1 최상위(try 밖)에서 직접 참조하는 셀렉터 → 없으면 스크립트 전체 중단
```
.toast  .mobile-menu  .add-btn  .strategy-btn  #botToggle
```
이 요소들이 없으면 `document.querySelector(...).addEventListener()`가 `TypeError`를 던지고,
그 아래의 `loadDashboard()` 등 초기화가 **전부 실행 안 됨**. → 항상 존재시키거나, 참조부에
`?.`(optional chaining)을 붙여라. (이번에 `.add-btn` 등은 `?.`로 방어함.)

### 6.2 함수 안에서 가드 없이 쓰는 셀렉터 → 데이터 로드시 조용히 실패
`loadDashboard()`/`renderPaperSummary()` 등이 가드 없이 쓰는 ID(없으면 throw, 단 상위 try로
삼켜져 "데이터가 안 뜸"으로 나타남). 다음 ID는 **홈에서 유지**:
```
#totalAssets #totalReturn #dailyProfit #dailyReturn #apiConnection
#botStatus #analysisUpdatedAt #analysisPulseBar #analysisCycleCopy
#decisionCard #decisionMode #decisionReason #decisionAction
#paperMonthProfit #paperWeekProfit #paperTodayProfit
#dayTradeStatus #holdingsTable #symbolDetailCard #longTermHoldings
#slackConnection #deployConnection #marketClockLabel #marketClockTime
#paperOrders   ← renderPaperOrders가 참조. 화면에서 빼려면 hidden으로 남겨둘 것
```

### 6.3 요소를 제거하고 싶으면 → JS에 null 가드를 먼저 넣어라
이번에 홈의 "운영 상태 줄(ops-status-strip)"을 없애면서 `#healthToss` 등 5개를 지웠다.
`loadHealthStatus()`가 이들을 가드 없이 만지면 **connection 배지를 매 주기 `{}`로 리셋하는
버그**가 생겼다. 그래서 각 접근을 `if (el) el.textContent = ...`로 감쌌다.
→ **"HTML에서 요소를 뺀다 = app.js에서 그 셀렉터를 가드한다"**를 한 세트로 처리.

### 6.4 이번 세션에서 실제로 넣은 app.js 패치 (참고)
- `loadHealthStatus()` 안 `#health*` 5개에 `if (el)` 가드.
- `renderPaperOrders()` 첫 줄에 `#paperOrders` null 가드(+ 없으면 `renderDayTradeStatus`만 호출).
- 죽은 핸들러 제거: `.strategy-btn → #strategySettings` 스크롤(존재하지 않던 대상).
- **인사말 동적화** `updateGreeting()`: KST 시각으로 아침/오후/저녁 + 영문 날짜를
  `#greetingDate`/`#greetingTitle`에 세팅. `updateMarketClock` 옆에서 호출 + 60초 주기.
- **단타 현황 5행화**: `renderDayTradeStatus`의 `slice(0,3)`→`slice(0,5)`,
  빈 상태 placeholder 3개→5개, `renderPaperOrders`의 `slice(-3)`→`slice(-5)`.

---

## 7. 까다로운 부분 — 세부 박스의 flex 동작 (단타 현황 "5개 고정")

요구: "단타 현황에서 한 번에 종목 5개가 보이게."
고정 높이 패널 안에서 특정 리스트만 N행 보이게 하려면 flex 세팅이 미묘하다.

```css
/* 리스트를 '내용만큼만' 커지게 (grow 금지), 좁으면 줄고 스크롤 */
.daytrade-status { flex: 0 1 auto; min-height: 0; }        /* 부모: grow X, shrink O */
.daytrade-table  { flex: 1 1 auto; min-height: 0; overflow-y: auto; }
.strategy-btn    { margin-top: 2px; }                       /* CTA는 내용 바로 뒤 */
```

함정과 해법:
- ❌ `flex: 1`(grow)로 주면 → 큰 모니터(1080)에서 리스트가 남는 공간을 다 먹어
  **5행 아래에 거대한 빈 공백**이 생긴다.
- ❌ `.strategy-btn { margin-top: auto }`(하단 고정)로 주면 → 내용이 짧을 때
  **행과 CTA 사이 중간 공백**이 생겨 깨져 보인다.
- ✅ 해법: 리스트는 grow 안 함(`flex:0 1`) → 큰 화면에선 정확히 5행 높이, 남는 여백은
  카드 하단 패딩으로. 짧은 화면에선 `shrink`+`overflow`로 축소·스크롤 → CTA 항상 보임.

행 개수를 픽셀로 맞출 때: 행 패딩/폰트/간격을 `clamp` 없이 소폭 조정하며
**브라우저에서 실측**해 5행이 "완전히" 보이는지 확인한다(§8). 흔한 높이(1080/900/864)에서
5행, 그보다 짧으면 자동 축소가 목표.

---

## 8. 검증 방법 (반드시 브라우저 실측) — 이게 핵심 노하우

스크린샷만 믿지 말고 **DOM을 계측**한다. 서버 없이도 정적 서버로 레이아웃 검증 가능:

```bash
python3 -m http.server 4173 --bind 127.0.0.1   # index.html 정적 서빙 (API는 404여도 됨)
# app.js의 fetch 실패는 try/catch로 삼켜지므로 레이아웃 검증엔 충분
```

브라우저 콘솔(또는 MCP 브라우저)에서 각 해상도마다 아래 지표를 확인:

```js
JSON.stringify({
  pageScroll: document.documentElement.scrollHeight - window.innerHeight,  // 데스크톱: 0 이어야
  xScroll:    document.documentElement.scrollWidth  - window.innerWidth,   // 항상 0 이어야
  // 핵심 요소 존재 확인 (app.js 계약)
  botToggle: !!document.querySelector('#botToggle'),
  strategyBtn: !!document.querySelector('.strategy-btn'),
})
```

5행 실측 예:
```js
const dt = document.querySelector('.daytrade-table'), dr = dt.getBoundingClientRect();
[...dt.children].filter(r => {
  const rr = r.getBoundingClientRect();
  return rr.top >= dr.top-1 && rr.bottom <= dr.bottom+1;   // 완전히 보이는 행만 카운트
}).length;   // == 5 목표
```

### 필수 확인 폭 (각각 pageScroll=0(데스크톱)/xScroll=0/겹침 없음)
`1920 · 1600 · 1536×864 · 1440×900 · 1366×768 · 1280×800 · 1024×768 · 820 · 390`
+ **콘솔 에러 0** (app.js ID 누락 throw 없음) + Quant Lab 전환/복귀 정상.

주의: 일부 브라우저 캡처는 dpr=2에서 스크린샷이 좌상단만 그려지는 글리치가 있다.
**시각이 이상하면 위 JS 계측치를 신뢰**하라(레이아웃은 정상인 경우가 많다).

---

## 9. 절대 규칙 (다음 작업자에게)

1. **override를 쌓지 마라.** 레이아웃이 흔들리면 규칙 추가가 아니라 골격 수정.
2. **`min-height:0` + `overflow:auto`** 없이 "무스크롤"을 논하지 마라.
3. **HTML에서 요소를 빼면 app.js에서 그 셀렉터를 같이 가드**하라(§6). 세트로 움직인다.
4. **겹침 방지 = 절대배치 금지.** 배치는 grid `template-areas`/`minmax(0,fr)`로만.
5. **색은 토큰으로.** 양수 green / 음수 red 클래스 유지.
6. **바꾸면 8절대로 실측.** 스크린샷 감이 아니라 숫자로 확인.
7. **금지선(CLAUDE_HANDOFF 10절)**: 시크릿/.env 커밋 금지, 실주문·자동주문 연결 금지(PAPER 유지),
   Slack webhook 노출 이력 있음 → rotate 권장.

---

## 10. 변경 파일 & 백업

| 파일 | 변경 |
|---|---|
| `index.html` | 고정 뷰포트 골격으로 구조 재작성, app.js 참조 ID 전부 보존 |
| `styles.css` | **전체 재작성**(override 더미 → 14섹션 clean 구조) |
| `app.js` | 최소 패치(§6.4): health 가드, paperOrders 가드, 인사말 동적화, 5행화 |

백업: `index.html.bak` · `styles.css.bak` · `app.js.bak` (원본 복구용).
이 폴더는 git 저장소가 아님 → 원본 리포(`RogersTobus/Toss`)에 반영 시 커밋 전 시크릿 스테이징 여부 확인.

---

## 11. styles.css 섹션 지도 (빠른 탐색용)

파일 상단 주석에 목차가 있다. 순서:
`1 토큰 · 2 리셋 · 3 app-shell(고정뷰포트) · 4 사이드바 · 5 topbar · 6 content그리드 ·
7 hero · 8 operations그리드 · 9 bot패널 · 10 watch패널 · 11 장기카드 · 12 Quant페이지 ·
13 switch/toast · 14 반응형`

레이아웃을 고칠 땐 **해당 섹션만** 손대고, 반응형은 14번 블록에서 열 수만 바꾼다.
