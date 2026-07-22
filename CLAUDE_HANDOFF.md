# Claude Code 인수인계 문서 — Toss / Orbit 투자 대시보드

## 2026-07-20 일일 진입 한도 영구 해제

- 한국·미국 정규장 메인 PAPER의 **일일 진입 횟수 제한은 앞으로 사용하지 않는다.**
- 이미 여러 번 진입했더라도 청산 후 점수·유동성·추세 조건을 다시 통과하면 같은 장에서 재진입할 수 있다.
- 시작자금 100만 원, 종목당 최대 30%, 동시 보유 최대 3개, 총 미청산 위험 1%, 연속 손절 대기, 매수 즉시 -0.5% 보호매도는 계속 유지한다.
- `maxDailyOrders`는 과거 설정 파일 호환을 위해 남겨 두지만 실제 신규 진입 차단에는 사용하지 않는다.
- 손실을 만회하기 위한 무조건 진입은 금지하며 기존 점수·후보 필터를 통과한 PAPER 신호만 주문한다. 실제 주문은 계속 전송하지 않는다.

## 2026-07-20 PAPER 일 손실 거래잠금 영구 해제

- PAPER 표본을 빠르게 축적하기 위해 통합 누적손실 −0.8%에 도달해도 하루 전체 신규 진입을 잠그지 않는다.
- −1.0% 누적손실에 따른 계좌 전체 강제청산도 사용하지 않고 손익 관찰값으로만 남긴다.
- 이 원칙은 한국·미국 정규장에 모두 적용하며, 일일 진입 횟수 무제한 원칙과 함께 유지한다.
- +1%는 진입 허용 기준이 아니라 진입 후 수익관리 목표다. 점수·유동성·추세 조건을 통과한 후보는 장중 즉시 PAPER 진입할 수 있다.
- 개별 포지션 −0.5% 보호매도, 동시 3종목, 종목당 최대 30%, 총 미청산 위험 1%, 동일 종목 청산 후 10분 대기와 연속 손절 대기는 계속 유지한다.
- 누적손실을 이유로 점수 기준을 낮추거나 조건 미달 종목에 진입하지 않는다. 실제 주문은 계속 금지한다.

작성일: 2026-07-12 KST  
저장소: `https://github.com/RogersTobus/Toss.git`  
AWS Lightsail 운영 주소: `http://54.180.202.165:4173/`  
현재 브랜치: `main`  
프론트 작업 기준 HEAD: `main`의 최신 배포 커밋을 `GET /api/health`의 `version`으로 확인

## 2026-07-22 1분봉 장중 재생 백테스트

- 한국·미국 정규장 밖에서 토스 캔들 API의 과거 1분봉을 순환 수집해 `context-edge-v3` 전략을 재생한다.
- 현재 거래대금 상위 50종목을 시장별 4종목씩 순환하며 원시 캔들은 저장하지 않고 최대 5,000개의 거래 결과만 `learning_state.json`에 보존한다.
- −0.5% 손절, +1% 50% 부분익절, 고점 대비 −0.5% 추적손절, 마감청산과 시장별 비용을 동일하게 적용한다.
- 같은 1분봉에서 목표와 손절이 모두 관찰되면 체결 순서를 알 수 없으므로 손절 우선으로 계산한다.
- 결과는 시간순 학습 60%·검증 20%·최종 보류 20%로 분리하고 한국·미국 지표를 별도 제공한다.
- 현재 유동성 종목을 과거에도 사용하므로 선택 편향이 있으며 후보 제거 자료일 뿐 자동 승격이나 메인/10억 확률에 반영하지 않는다.

## 2026-07-20 Slack 운영로그 단일화

- `SLACK_LOG_WEBHOOK_URL`이 설정되어 있으면 긴급 알림, 장 마감 결산, 30분 운영 리포트를 모두 해당 운영로그 채널로 전송한다.
- 기존 `SLACK_ALERT_WEBHOOK_URL`, `SLACK_REPORT_WEBHOOK_URL`은 운영로그 웹훅이 없을 때만 호환용 fallback으로 사용한다.
- 메시지 종류와 중복방지 키는 그대로 유지하므로 한 채널 안에서도 긴급/결산/운영 리포트를 구분할 수 있다.
- 실제 webhook 값은 저장소에 커밋하지 않는다.

## 2026-07-20 SHADOW PAPER 병렬 학습

- 실운용에 가까운 메인 PAPER는 기존대로 시작자금 100만 원, 종목당 최대 30%, 최대 3포지션, 총 미청산 위험 1%를 유지한다.
- 한국·미국 정규장에서 필터와 진입점수를 통과한 모든 신호는 별도 `shadow_paper_state.json`에 기록한다.
- SHADOW PAPER는 자금과 포지션 슬롯을 소비하지 않으며 메인 손익, 일일 손실 한도, 복리 잔액, 10억 달성 확률 계산에서 완전히 제외한다.
- 동일 시장·종목은 동시에 하나의 SHADOW 표본만 허용하고, 청산 뒤 10분간 중복 신호를 막는다.
- 청산 규칙은 메인과 같은 −0.5% 손실선, +1% 50% 부분확정 후 고점 대비 −0.5% 추적, 3분 시간청산, 마감 5분 전 청산을 사용한다.
- 거래비용은 한국 0.3%, 미국 0.4%로 보수적으로 차감한다. SHADOW 결과는 연구 근거 가중치 0.30으로 표시하며 최소 100건 전에는 주전 전략으로 승격하지 않는다.
- 대시보드는 개별 SHADOW 종목 목록 대신 활성 수, 오늘 완료, 누적 표본, 비용후 승률·평균 수익률만 표시한다. 한국/미국 분리 통계는 API `paperSummary.shadowPaper.byMarket`에 제공한다.
- 파일은 최근 종료 표본 5,000건과 현재 활성 표본만 보존한다.

## 2026-07-19 현재 운영 기준

- 거래는 한국·미국 정규장만 PAPER로 진행한다. 프리마켓·애프터마켓 신규 진입은 사용하지 않는다.
- 모든 신규 PAPER 매수는 평균 체결가 대비 정확히 -0.5%에 보호매도를 즉시 등록한다.
- 보호매도 발동가는 정확히 -0.5%지만 갭·급락 시 체결가는 최초 관측 가능한 불리한 가격으로 보수적으로 기록한다.
- +1% 도달 시 50%를 부분익절하고, 잔여 50%는 진입가 이상을 바닥으로 고점 대비 -0.5% 추적매도한다.
- 익일 보유 전략이 비활성인 동안에는 정규장 마감 5분 전에 잔여 PAPER 포지션을 청산한다.
- 거래 학습의 성공/실패는 비용 전 수익이 아니라 시장별 왕복비용을 차감한 순손익으로 판정한다.
- 정규장 PAPER 주문은 100만 원 한도, 종목당 최대 30%, 최대 3종목, 총 미청산 위험 1% 이내로 제한한다.
- 매 거래일 목표금은 전일까지의 비용 후 누적자산 × 1%로 계산하고, 다음 포지션 배정에도 누적자산을 사용해 복리로 운용한다.
- 현황 화면의 `10억 달성 가능성`은 새 제한운용 이후 비용 후 표본만 사용한 보수적 휴리스틱이다. 표본 수·평균 순수익·손익계수·최대낙폭·복리자산을 함께 반영하며, 100건 전에는 1%를 넘지 않고 성과 악화 시 하락할 수 있다.
- 한국·미국 합산 비용 후 일손실 -0.8%에서 신규 진입을 차단하고, 시장별 2회 연속 손실 시 10분 대기한다.
- 무제한 표본 연구는 장외 과거데이터 연구에만 허용하며 정규장 주문 제한을 우회하지 않는다.
- 현황 UX는 `현황 / 전략 / 기록` 3개 화면으로 간소화되어 있다.
- 성과는 한국·미국, 전략, 점수 구간, 시간대, 시장 환경별로 분리한다.
- 모든 전략 평가는 수수료·세금·스프레드·슬리피지 추정치를 뺀 비용 후 성과를 사용한다.
- 후보 전략은 100건 이상과 주전 대비 평균손익·손익비·최대낙폭을 통과해야 하며 자동 승격하지 않는다.
- 일·주·월봉 연구 후보는 3분 단타 주전과 시간축이 달라 직접 승격할 수 없다.
- 현재 데이터에서는 고득점 구간의 비용 후 성과가 더 나쁜 `점수 역전`이 확인되어 경고만 표시하고 점수식을 자동 변경하지 않는다.
- 분석 지연, 손실 감시 오류, 보호주문 누락 시 신규 진입을 자동 차단한다.
- 시장 환경은 연준, 미국 노동통계국, 한국은행, UN News 공식 RSS를 기본으로 사용한다. GDELT는 호출 가능할 때만 보조한다.
- 뉴스·거시경제 판정은 진입 당시 스냅샷으로 저장하고 환경별 PAPER 성과 비교에만 사용한다. 매수 점수에는 직접 반영하지 않는다.
- 핵심 API 응답은 최근 기록과 미청산 포지션 위주로 제한하고, 매매일지 중복 폴링을 하지 않는다.
- 관련 API: `GET /api/macro-context`, `POST /api/strategy/candidates/approve`.

## 0. 가장 중요한 결론

이 프로젝트의 백엔드/API/Slack/AWS 자동배포 쪽은 어느 정도 동작 가능한 상태입니다.  
문제는 최근 프론트 레이아웃 작업입니다.

최근 프론트 커밋들이 누적 CSS override 방식으로 진행되면서 `styles.css` 하단에 서로 충돌하는 레이아웃 규칙이 많이 쌓였습니다. 이 때문에 해상도별로 카드가 겹치거나, 1열/2열 전환이 의도와 다르게 발생하거나, 큰 화면에서 하단 공백/겹침이 생깁니다.

Claude Code는 프론트 레이아웃을 계속 덧씌우지 말고, `index.html` 구조와 `styles.css`를 한 번 정리하는 방향으로 진행하는 것이 좋습니다.

권장 작업:

1. `server.py`, `scripts/`, `.env.example`, Slack/API 관련 백엔드는 최대한 유지
2. `index.html`, `styles.css`, 필요 시 `app.js`의 화면 전환/DOM selector만 정리
3. 특히 `styles.css`는 뒤쪽 override를 계속 추가하지 말고, 레이아웃 섹션을 재작성하거나 새 CSS 구조로 정리
4. 화면 검증은 최소 4개 폭에서 확인
   - 1920px 이상
   - 1440px 근처
   - 1180px 근처
   - 820px 이하

## 1. 사용자가 원하는 제품 방향

사용자는 단순 포트폴리오 뷰가 아니라, “운영 가능한 투자 시스템 대시보드”를 원합니다.

큰 방향:

- 장기 지수투자는 미국 3대 지수 중심으로 매일 적립식 운용
- 단타 트레이딩은 며칠간 모의투자로 운용
- 단타 목표는 하루 +1% 수익
- 손실선은 현재 UI 기준 -0.5%
- 시스템이 한국장과 미국장을 시간에 맞춰 자동 전환
- 실시간 분석을 계속 수행
- 장마감/운영/긴급 알림은 Slack으로 받기
- 완성 후에는 전체 프로세스를 다시 만들지 않고 “전략만 수정”하는 형태가 이상적

사용자의 UX 선호:

- 한 화면에서 핵심 운영 상태가 보여야 함
- 스크롤을 많이 내려 확인하는 구조를 싫어함
- 너무 개발자스럽거나 조잡한 회색 와이어프레임 UI를 싫어함
- 화이트 + 골드 톤을 선호
- 카드 간 정렬, 비율, 여백이 안정적이어야 함
- “시키는 대로 박아넣기”보다 용도와 비율에 맞게 판단해 배치하길 원함

## 2. 현재 기능 개요

### 2.1 Toss Securities Open API

환경변수:

- `TOSS_CLIENT_ID`
- `TOSS_CLIENT_SECRET`
- `TOSS_ACCOUNT_SEQ` optional

관련 파일:

- `server.py`
- `.env.example`

주요 API 호출 위치:

- 계좌 목록: `/api/v1/accounts`
- 보유 종목: `/api/v1/holdings`
- 환율: `/api/v1/exchange-rate`
- 시장 캘린더: `/api/v1/market-calendar/KR`, `/api/v1/market-calendar/US`
- 랭킹/종목 분석: `/api/v1/rankings`, `/api/v1/stocks`

장외 연구 운영 원칙:

- 한국 정규장 중에는 미국 전체 종목 연구를 계속한다.
- 미국 정규장 중에는 한국 전체 종목 연구를 계속한다.
- 두 정규장이 모두 닫히면 한국·미국 전체 종목을 함께 연구한다.
- `research_universe.json`의 전체 상장 종목을 5분 주기 묶음으로 순환하며 일·주·월봉을 분석한다.
- 연구 결과는 후보 전략 근거로만 저장하고 현재 주전 전략에 즉시 반영하지 않는다.
- 반복 패턴은 `candidateStrategyRegistry`에 시장·시간봉별로 누적하며 같은 종목의 재연구는 중복 가산하지 않는다.
- 후보는 최소 100건, 10종목, 비용 후 양의 평균수익, 승률 55%, 손익비 1.2, 평균 최대낙폭 -25% 이내를 모두 통과해야 기존 전략과 비교할 수 있다.
- 화면에는 한국·미국 전체 순환률, 완주 횟수, 누적 후보 수와 비교 준비 후보 수를 표시한다.

대시보드 API:

- `GET /api/dashboard`
- `GET /api/health`
- `GET /api/analysis/status`
- `POST /api/analysis/start`
- `POST /api/analysis/stop`
- `GET /api/strategy/config`
- `POST /api/strategy/config`
- `POST /api/slack/test`

### 2.2 Slack

Slack은 운영로그 채널 하나로 통합합니다.

- 통합 목적지: `SLACK_LOG_WEBHOOK_URL`
- 긴급 알림·결산 리포트·운영 로그가 모두 이 웹훅으로 전송됩니다.
- `SLACK_ALERT_WEBHOOK_URL`과 `SLACK_REPORT_WEBHOOK_URL`은 통합 웹훅이 없을 때만 fallback으로 사용합니다.

환경변수:

- `SLACK_ALERT_ENABLED`
- `SLACK_ALERT_WEBHOOK_URL`
- `SLACK_REPORT_ENABLED`
- `SLACK_REPORT_WEBHOOK_URL`
- `SLACK_LOG_ENABLED`
- `SLACK_LOG_WEBHOOK_URL`

주의:

- 사용자가 실제 Slack webhook URL을 대화 중에 노출했습니다.
- 문서나 코드에 실제 webhook 값을 절대 커밋하지 마세요.
- 가능하면 사용자에게 webhook rotate를 권장하는 것이 안전합니다.

Slack 관련 구현:

- `server.py`
  - `slack_status()`
  - `send_slack()`
  - `handle_paper_alert()`
  - `/api/slack/test`
- `app.js`
  - `renderSlackConnection()`
  - Slack test button handler

### 2.3 AWS Lightsail 배포

인스턴스:

- 이름: `Toss-trading`
- OS: Ubuntu
- 사용자: `ubuntu`
- 앱 경로: `/home/ubuntu/Toss`
- 외부 주소: `http://54.180.202.165:4173/`

systemd:

- `toss.service`
- `toss-autodeploy.service`
- `toss-autodeploy.timer`

관련 파일:

- `scripts/install_lightsail.sh`
- `scripts/deploy.sh`
- `scripts/toss.service`
- `scripts/toss-autodeploy.service`
- `scripts/toss-autodeploy.timer`
- `DEPLOYMENT.md`

운영 명령:

```bash
cd ~/Toss
git pull origin main
sudo systemctl restart toss.service
```

상태 확인:

```bash
sudo systemctl status toss.service
sudo systemctl status toss-autodeploy.timer
sudo journalctl -u toss.service -n 80 --no-pager
sudo journalctl -u toss-autodeploy.service -n 80 --no-pager
```

과거 문제:

- `fatal: detected dubious ownership in repository at '/home/ubuntu/Toss'`
- 해결 의도:

```bash
sudo git config --system --add safe.directory /home/ubuntu/Toss
sudo chown -R ubuntu:ubuntu /home/ubuntu/Toss
```

## 3. 최근 커밋 흐름

중요 커밋:

```text
d159bbc Stabilize dashboard grid structure
5ad5e9c Lock dashboard responsive grid areas
e27056e Harden responsive card layout
0353f1f Unify holdings cards with analysis layout
eff27b6 Remove extra trading dashboard widgets
74dc667 Separate live dashboard and strategy control
dddf54c Match dashboard frame layout
f0c84bb Add Slack test workflow
2a28e15 Polish dashboard typography
54dd953 Run Lightsail auto deploy as ubuntu
76f5d79 Show AWS sync time in header
03d8749 Register Lightsail repo as safe directory
a7a4c40 Make dashboard layout responsive
396e97c Harden Lightsail auto deploy
020d574 Show latest change summary in dashboard
8d2e153 Fit dashboard into one screen
de5c06e Show Slack webhook status in header
76d21c7 Add Slack notification routing
a4785aa Auto refresh browser after deployment
4242a9a Add Lightsail auto deployment
7b776af Allow dashboard server external binding
```

프론트 관련 주의:

- `dddf54c` 이후의 프론트 커밋들은 사용자의 피드백을 빠르게 반영하려고 CSS를 계속 덧씌운 결과입니다.
- 특히 `styles.css` 뒤쪽에 `Frame layout v1`, `Responsive hardening`, `Layout lock`, `Stable dashboard frame` 같은 override 블록이 누적되어 있습니다.
- Claude Code는 이 블록들을 그대로 믿고 또 덧붙이지 말고, 레이아웃 체계를 정리하는 것을 권장합니다.

상대적으로 유지 가치가 높은 커밋:

- `f0c84bb Add Slack test workflow`
- `76d21c7 Add Slack notification routing`
- `4242a9a Add Lightsail auto deployment`
- `396e97c Harden Lightsail auto deploy`
- `54dd953 Run Lightsail auto deploy as ubuntu`
- `7b776af Allow dashboard server external binding`

프론트만 되돌려서 재구성하고 싶다면 참고:

```bash
git show f0c84bb:index.html
git show f0c84bb:styles.css
git show f0c84bb:app.js
```

다만 `app.js`에는 이후에 추가된 전략 페이지/단타 현황 렌더링 함수가 있으므로, 무작정 전체 rollback 하지 말고 필요한 로직만 선별하세요.

## 4. 현재 프론트 파일 상태

### 4.1 `index.html`

현재 큰 구조:

- `.app-shell`
  - `.sidebar`
  - `main`
    - `.topbar`
    - `.content`
      - `.hero-layout`
        - `.hero-welcome`
        - `.balance-card`
        - `.daily-card`
        - `.connection-stack`
      - `.ops-status-strip`
      - `.operations-grid`
        - `.bot-panel`
        - `.watch-panel`
        - `.long-term-card`
      - `.strategy-control-page`

주의:

- `.ops-status-strip`는 CSS로 숨겨져 있습니다.
- 과거에는 `.main-grid`, `.bottom-grid` 래퍼가 있었으나 최근 커밋에서 제거했습니다.
- `strategy-control-page`는 Quant Lab 클릭 시 보이는 2페이지 의도입니다.

### 4.2 `styles.css`

가장 큰 문제 파일입니다.

문제:

- 여러 시점의 레이아웃 override가 하단에 누적
- 같은 selector가 여러 번 `!important`로 재정의
- `@media` 규칙이 서로 충돌
- 정상 데스크톱 폭에서 1열로 접히거나 카드가 겹치는 문제가 반복됨

권장:

1. 기존 CSS를 전부 이해하려고 하기보다, 아래 영역을 기준으로 재구성
   - base tokens
   - shell/sidebar/topbar
   - hero grid
   - operations grid
   - cards
   - responsive breakpoints
2. 레이아웃 관련 `!important` 최소화
3. 가능하면 하단 override 블록을 걷어내고 명확한 순서로 재작성
4. `grid-template-areas`를 쓰려면 HTML 직접 자식 구조를 유지

### 4.3 `app.js`

기능적으로는 유지 가치가 있습니다.

주요 함수:

- `renderLongTermHoldings(items)`
- `renderHoldings(items)`
- `renderScannerResults(items)`
- `renderDayTradeStatus(orders)`
- `renderPaperSummary(state)`
- `renderSlackConnection(slack)`
- `renderDeployConnection(deploy)`
- `loadDashboard()`
- `loadHealth()`
- `loadAnalysisStatus()`
- `openPage(page)`

주의:

- `document.querySelector(".strategy-btn").addEventListener(...)`는 첫 번째 `.strategy-btn`만 잡습니다. 현재 `data-open-page` 버튼 로직과 섞여 있으므로 정리 권장.
- `#strategySettings`는 UI에서 제거됐지만 일부 함수는 아직 존재합니다. optional chaining으로 큰 에러는 없지만 죽은 코드입니다.
- `#healthAnalysis`, `#healthToss` 등은 `.ops-status-strip`가 숨겨져도 DOM에는 있어 health update가 계속 됩니다. 제거하려면 JS도 같이 정리해야 합니다.

## 5. 사용자가 원하는 최종 화면 구조

### 5.1 상단 1열: 어떤 해상도에서도 안정적이어야 함

사용자가 “첫 번째 사진처럼 1열이 깨지지 않게”라고 요청한 구조:

```text
[인사 카드] [총 투자자산 카드] [오늘의 손익 카드] [상태 배지 3개 세로]
```

상태 배지:

- Toss Securities API
- Slack Webhooks
- AWS Sync

요구:

- 큰 화면에서는 반드시 한 줄
- 카드 높이 균일
- 카드 간 간격 균일
- 오른쪽 상태 배지는 세로 3개
- 좁아지면 자연스럽게 2열 또는 1열로 접히되 카드가 겹치면 안 됨

권장 CSS 방향:

```css
.hero-layout {
  display: grid;
  grid-template-columns:
    minmax(260px, 0.95fr)
    minmax(420px, 1.35fr)
    minmax(260px, 0.8fr)
    minmax(220px, 0.55fr);
  gap: 20px;
  align-items: stretch;
}

@media (max-width: 1280px) {
  .hero-layout {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 760px) {
  .hero-layout {
    grid-template-columns: 1fr;
  }
}
```

### 5.2 운영 영역

원하는 개념:

```text
왼쪽: 단타 트레이딩
오른쪽 위: 실시간 분석현황
오른쪽 아래: 내 보유 장기투자 종목
```

또는 사용자가 원하면:

```text
왼쪽 위: 실시간 분석현황
오른쪽 위: 단타 트레이딩
아래: 장기투자 종목
```

중요한 건 겹치지 않는 것과, 큰 화면 하단에서 깨지지 않는 것입니다.

현재 의도는:

```css
.operations-grid {
  display: grid;
  grid-template-columns: minmax(0, .98fr) minmax(0, 1.02fr);
  grid-template-areas:
    "bot analysis"
    "bot longterm";
}

.bot-panel { grid-area: bot; }
.watch-panel { grid-area: analysis; }
.long-term-card { grid-area: longterm; }
```

하지만 실제 화면에서 여전히 깨짐이 보고됐습니다.  
Claude는 실제 브라우저에서 확인 후 breakpoint와 카드 높이를 재조정하세요.

### 5.3 단타 현황 카드

목적:

- 현재 투자 종목
- 현재 상태
- 현재 수익률

사용자는 이 카드가 단타 현황 역할만 하길 원합니다.

표 구조:

```text
단타 현황                         실시간 포지션
종목             현재 상태          현재 수익률
삼성전자         보유중             +0.32%
...
```

비어 있을 때:

```text
대기             분석 대기          -
```

### 5.4 장기투자 종목 카드

목적:

- 장기 지수투자 보유 현황만 간결하게 표시

대상:

- 나스닥
- S&P 500
- 다우존스

실제 Toss holdings에서 들어오는 예시:

- `TIGER 미국S&P500(H)`
- `TIGER 미국나스닥100(H)`
- `TIGER 미국배당다우존스`

표시 형태:

```text
내 보유 장기투자 종목
TIGER 미국S&P500(H)          365주 · +6.88%
TIGER 미국나스닥100(H)       146주 · +12.25%
TIGER 미국배당다우존스       100주 · +14.23%
```

사용자 피드백:

- 종목명 글자 크기는 너무 크면 안 됨
- 오른쪽에 과한 공백도 싫어함
- 카드 높이가 필요 이상으로 길면 안 됨

### 5.5 실시간 분석현황 카드

목적:

- 실시간 분석 현황을 가장 빠르게 확인
- 종목별 현재가, 분석 상태, 판단 근거 표시

표 구조:

```text
LIVE ANALYSIS
실시간 분석현황                     LIVE
종목             현재가        분석 상태       판단 근거
...
```

비어 있을 때:

```text
종목을 선택하면 기술적 분석이 열립니다
거래대금 순위, 당일 흐름, 변동성, 진입 판단을 한 번에 봅니다.
```

### 5.6 전략 설정 컨트롤타워

이건 홈 1페이지에 욱여넣지 말고 2페이지/Quant Lab으로 분리하는 방향입니다.

사용자 의도:

- 전략 텍스트
- 현재 전략이 수익에 유효하게 먹히는지 판단
- AI 조언
- 활성화 토글

예시:

```text
전략 설정 컨트롤타워

활성화된 전략
1  단타 추세 추종     거래대금과 당일 추세가 동시에 붙는 종목만 후보로 올림     AI 조언     활성화
2  손실 제한 방어     일 손실선 -0.5%와 연속 손실 제한 확인                    AI 조언     활성화
3  장마감 결산        한국장/미국장 종료 후 진입/보류 판단 리포트               AI 조언     활성화
```

## 6. 사용자가 제거 요청한 요소

아래 요소들은 홈에서 제거 요청됨:

- 보유 포지션 / 오늘 진입 / 평균 손익률 / 리포트 카드
- 안전장치 정상 범위 박스
- 설정 저장 박스
- 손실 한도 진행률 박스
- 토스 API / Slack 리포트 / 라이브 분석 / 서버 실행 / 최근 변경 상태 줄

주의:

- 상단 우측 연결 배지 3개는 유지 의도
  - Toss Securities API
  - Slack Webhooks
  - AWS Sync

## 7. 현재 알려진 깨짐

사용자가 최근 캡처로 지적한 문제:

1. 상단 1열이 특정 해상도에서 깨짐
   - 인사 카드, 자산 카드, 오늘 손익, 연결 배지가 의도대로 한 줄에 유지되지 않음
2. 중간 영역에서 장기투자 카드와 실시간 분석현황 카드가 겹침
3. 큰 해상도에서 하단이 깨지고 빈 배경이 과하게 생김
4. 사이드바가 있는 상태에서 content 폭 계산이 불안정
5. `.operations-grid`의 1열 전환 breakpoint가 사용자 기대보다 빨리 작동하거나, 반대로 큰 화면에서 겹침이 발생

핵심 원인 후보:

- `styles.css` 하단 override 누적
- `!important` 남발
- 과거 `.main-grid`, `.bottom-grid`, `display: contents` 잔재와 새 grid-area 규칙 충돌
- 카드별 `min-height`, `height`, `overflow` 규칙 충돌
- hero grid의 최소폭 합계가 사이드바 제외 실제 viewport보다 클 때 자연스럽게 줄지 못함

## 8. Claude에게 권장하는 실제 작업 순서

### Step 1. 현재 파일 백업/상태 확인

```bash
git status
git log --oneline -n 20
```

### Step 2. 프론트 구조 확인

```bash
sed -n '1,280p' index.html
tail -n 500 styles.css
```

### Step 3. CSS 정리 전략 선택

권장 1안: 가장 안전

- `styles.css`를 통째로 정리하지 말고, 새 파일 `layout.css`를 만들고 `index.html`에서 마지막에 로드
- 기존 CSS와 충돌하지 않게 레이아웃 핵심만 새 파일에서 명확히 관리
- 예:

```html
<link rel="stylesheet" href="styles.css?v=..." />
<link rel="stylesheet" href="layout.css?v=claude1" />
```

권장 2안: 더 깔끔하지만 리스크 있음

- `styles.css` 전체를 섹션별로 재작성
- 과거 override 모두 제거
- UI 전체 회귀 테스트 필요

### Step 4. 레이아웃 검증

반드시 확인할 폭:

- 1920x1080
- 1600x900
- 1440x900
- 1280x800
- 1180x800
- 820x900
- 390x844

검증 기준:

- 가로 스크롤 없음
- 카드 겹침 없음
- 상단 1열은 큰 화면에서 한 줄 유지
- 큰 화면에서 운영 영역은 2열 유지
- 1180px 이하에서만 1열 전환
- 장기투자 카드가 실시간 분석 카드 위로 튀지 않음
- 단타 현황 세 번째 컬럼이 카드 밖으로 나가지 않음

## 9. 배포/검증 명령

로컬 문법 검사:

```bash
node --check app.js
python3 -m py_compile server.py
```

이 환경에서는 Node가 시스템에 없을 수 있습니다. Codex 환경에서는 다음 경로를 사용했습니다:

```bash
/Users/youngjun/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node --check Toss/app.js
PYTHONPYCACHEPREFIX=/tmp/toss_pycache python3 -m py_compile Toss/server.py
```

Git:

```bash
git add index.html styles.css app.js
git commit -m "..."
git push origin main
```

AWS:

```bash
cd ~/Toss
git pull origin main
sudo systemctl restart toss.service
```

브라우저:

```text
http://54.180.202.165:4173/
```

캐시 문제 방지:

- CSS 링크 query string을 올릴 것
  - 예: `styles.css?v=layoutframe5`
- 또는 hard refresh

## 10. 중요한 주의사항

### 10.1 실제 주문 금지

현재 단타는 모의투자/PAPER 모드가 기본입니다.  
사용자는 며칠 동안 모의투자로 운용하겠다고 했습니다.  
실제 주문 API를 연결하거나 자동 주문을 보내는 변경은 별도 확인 없이 하지 마세요.

### 10.2 비밀값 커밋 금지

절대 커밋하면 안 되는 값:

- Toss Client ID
- Toss Client Secret
- Slack webhook URL
- Kakao REST API Key / refresh token
- `.env`

### 10.3 사용자의 작업 방식

사용자는 빠르게 결과를 확인하고 피드백합니다.  
하지만 프론트는 지금처럼 작은 수정을 계속 덧씌우면 더 깨집니다.  
Claude는 “이번에는 구조를 정리해야 한다”고 명확히 설명하고 진행하는 것이 좋습니다.

## 11. Claude에게 전달할 한 줄 요약

이 프로젝트는 백엔드/Slack/AWS 자동배포는 살리고, 프론트 대시보드 레이아웃은 최근 누적 override 때문에 불안정하므로 `index.html` 구조와 CSS 레이아웃 체계를 정리해야 합니다. 목표는 큰 화면에서 상단 4영역 1줄, 운영 영역 2열, 좁은 화면에서만 안전한 1열 전환이며, 어떤 해상도에서도 카드 겹침/가로 스크롤/과한 공백이 없어야 합니다.
# 2026-07-20 미국장 적용 · 비용 후 전역학습 및 반복오답 차단

- 모든 PAPER 청산은 `netReturnRate` 기준으로 전역 점수 모델을 즉시 수정한다. 과거에 거래키만 처리 표시하고 점수를 동결했던 v1 상태는 최초 실행 시 전체 거래원장으로 한 번 재구축한다.
- 한국·미국의 점수 구성 최대치는 각 시장 프로필을 유지하지만, 학습된 유동성·모멘텀·안정성 가중치와 진입 기준은 전 종목·전 시장에 공통 적용한다.
- 시장·점수구간별 비용 후 기대값을 검사한다. 20건 이상이고 전체 평균이 왕복비용 절반보다 더 나쁘며 최근 10건도 음수인 구간은 회복 확인 전 메인 PAPER 진입에서 제외하고 SHADOW PAPER로만 계속 표본을 쌓는다.
- 일반 종목과 레버리지·인버스를 구분한다. 레버리지·인버스는 진입평가에서 4점 감점하고 계산된 비중의 50%만 사용한다.
- 모든 청산 뒤 기본 10분 회전은 유지한다. 동일 종목에서 비용 후 2연패가 발생하면 60분 동안 해당 종목만 재검증한다. 일일 진입 횟수 제한이나 일일 손실 거래잠금은 다시 만들지 않았다.
- 매수 즉시 평균 체결가 대비 −0.5% PAPER 보호매도, 최대 3포지션, 종목당 최대 30%, 총 미청산 위험 1%, PAPER 전용 원칙은 그대로 유지한다.

# 2026-07-21 비용 후 기대값 중심 진입 구조 개편

- 표시 점수는 후보 순위에만 사용한다. 메인 PAPER 진입은 시장별 점수구간과 시간대의 비용 후 전체 8건·최근 5건이 모두 양수 방향인지 별도로 확인한다.
- 전체 평균과 최근 평균이 함께 0 이하인 점수구간 또는 시간대는 메인 진입에서 제외하지만, SHADOW PAPER 표본은 계속 수집한다. 일일 진입 횟수 제한은 다시 만들지 않았다.
- 레버리지·인버스는 단순 감점만으로 진입하지 않는다. 해당 시장 레버리지군 SHADOW 표본이 최소 8건이고 전체·최근 5건 평균이 모두 양수일 때만 메인으로 승격하며, 승격 후에도 계산 비중의 절반만 사용한다.
- `TQQQ`, `SQQQ`, `SOXL`, `SOXS`, `SPXL`, `SPXS`, `UPRO`, `QLD`, `QID`, `TECL`, `TECS`, `NVDL`, `TSLL` 등 약어형 미국 레버리지·인버스 종목을 명시적으로 분류한다. 한국 상품은 종목명의 레버리지·인버스·2X·3X 표기를 함께 사용한다.
- 한국장 개장 초반이나 미국 개장 초반처럼 비용 후 음수 표본이 누적된 구간은 자동으로 그림자 전용이 된다. 성과가 회복되면 같은 증거 규칙으로 다시 메인 후보가 될 수 있다.
- 매수 즉시 평균 체결가 대비 −0.5% PAPER 보호매도, 최대 3포지션, 종목당 최대 30%, 총 미청산 위험 1%, PAPER 전용 원칙은 그대로 유지한다.

# 2026-07-21 상황별 검증 전략 `context-edge-v3`

- 이번 주 비용 후 승률 21.7%, PF 0.19로 악화되었고 레버리지·인버스가 손실의 77%를 차지했다. 고정 3분 시간청산은 8전 8패였으므로 새 전략에서는 사용하지 않는다.
- 과거 전략 표본과 새 전략 표본을 섞지 않는다. 새 SHADOW 표본에는 `engineVersion`, 시장 시간대, 일반/레버리지 분류, 당일 등락 구간을 저장한다.
- 동일한 시장·시간대·상품군·당일 등락 구간의 최근 최대 40건만 평가한다. 최소 12건, 비용 후 승률 40% 이상, 전체·최근 8건 기대값 양수, PF 1.15 이상을 모두 통과해야 메인 PAPER로 승격한다.
- 승격 직후에는 계산 비중의 35%, 20건부터 65%, 40건부터 100%를 사용한다. 종목당 최대 30%와 총 미청산 위험 1%는 그대로 적용된다.
- 레버리지·인버스는 최소 20건, 승률 45% 이상, PF 1.30 이상을 추가로 요구하고 승격 후에도 일반 계산 비중의 50%만 사용한다.
- 새 전략 청산은 −0.5% 예약 보호매도, +1% 50% 부분익절 후 −0.5% 추적손절, 마감청산만 사용한다. PAPER 전용, 일일 진입 횟수 무제한, 일일 손실 거래잠금 없음은 유지한다.

# 2026-07-22 장중 리플레이 v2 및 `context-edge-v4`

- v1 장중 리플레이는 536건, 승률 36.2%, 비용 후 평균 −0.337%, PF 0.41로 부적합 판정했다. 미국 홀드아웃 40건만 양수였지만 학습·검증 구간이 모두 음수라 승격 근거로 사용하지 않는다.
- v4 실시간 후보는 한국 +0.5~+6%, 미국 +0.8~+8%의 통제된 양의 당일 추세만 허용한다. 고순위여도 무추세·하락·과열 후보는 메인과 SHADOW 진입 전에 제외한다.
- v2 1분봉 리플레이는 거래소 현지 정규장만 사용한다. 정규장 20분 이후 SMA5>SMA20, 가격>VWAP, 5분 모멘텀, 직전 5분 고점 돌파, 상대 거래량, 강한 종가 위치를 모두 확인한다.
- 미국 거래일은 뉴욕 현지 날짜로 묶어 한국 자정에 한 세션이 분리되던 오류를 수정했다. 레버리지·인버스는 별도 검증 전 v2 표준 표본에서 제외하고 종목·거래일당 독립 표본 1건만 기록한다.
- v1 결과는 `baseline`으로 보존하되 v2 원시 표본과 섞지 않는다. v2도 자동 승격하지 않으며 실시간 `context-edge-v4` SHADOW 표본의 비용 후 평균, 최근 평균, 승률, PF 조건을 별도로 통과해야 한다.
- 1GB Lightsail의 재시작 루프를 막기 위해 시장당 배치 1종목, 캔들 4페이지, 원장 1,200건, 시작 180초, 반복 15분으로 제한했다. 오프마켓 연구와 리플레이는 동시에 실행하지 않고 대시보드 응답에서 원시 리플레이 원장을 복제하지 않는다.
- 매수 즉시 평균 체결가 대비 −0.5% PAPER 보호매도, 최대 3포지션, 종목당 최대 30%, 총 미청산 위험 1%, PAPER 전용, 일일 진입 횟수 무제한, 일일 손실 거래잠금 없음은 변경하지 않는다.
