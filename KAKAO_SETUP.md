# KakaoTalk 나에게 보내기 설정

Orbit은 한국장/미국장 종료 시 단타 장마감 리포트를 카카오톡 `나와의 채팅`으로 보낼 수 있습니다.

## 1. Kakao Developers 앱 준비

1. https://developers.kakao.com 에 접속합니다.
2. 내 애플리케이션에서 앱을 생성합니다.
3. 앱 키 메뉴에서 `REST API 키`를 확인합니다.
4. 제품 설정 > 카카오 로그인에서 활성화합니다.
5. Redirect URI를 등록합니다. 예: `http://localhost:4173/kakao/callback`
6. 동의항목에서 카카오톡 메시지 전송 권한을 설정합니다.

## 2. refresh token 발급

카카오 OAuth 인증으로 `talk_message` 권한을 받은 뒤 refresh token을 발급받아야 합니다.
발급된 값은 절대 GitHub에 올리지 말고 `.env`에만 넣습니다.

```env
KAKAO_REPORT_ENABLED=true
KAKAO_REST_API_KEY=카카오_REST_API_키
KAKAO_REFRESH_TOKEN=카카오_REFRESH_TOKEN
```

## 3. 동작 방식

- 시장이 열려 있다가 닫히면 장마감 리포트를 생성합니다.
- `KAKAO_REPORT_ENABLED=true`이고 카카오 키/token이 있으면 카카오톡 나에게 보내기로 발송합니다.
- 설정이 없으면 리포트만 `report_state.json`에 저장하고 화면에 대기 상태로 표시합니다.
- `report_state.json`은 `.gitignore`에 포함되어 GitHub에 올라가지 않습니다.

## 4. 리포트 예시

```text
[Orbit 단타 장마감 리포트]
시장: 한국장
일시: 2026-07-10 15:30

오늘 단타 수익: -8,870원 (-0.43%)
이번주 단타 수익: -8,870원 (-0.43%)
이번달 단타 수익: -8,870원 (-0.43%)

오늘 모의 진입: 3건
보유 포지션: 3개
대표 종목: 삼성전자, 삼성전기, KODEX 레버리지
상태: 장마감 리포트 생성 완료
```


## 5. Orbit에서 자동 연결

`.env`에 `KAKAO_REST_API_KEY`를 넣고 서버를 재시작한 뒤, 화면의 `카카오톡 연결하기` 버튼을 누릅니다.
카카오 동의 화면에서 메시지 권한을 허용하면 Orbit이 refresh token을 `.env`에 자동 저장하고 `KAKAO_REPORT_ENABLED=true`로 바꿉니다.

Redirect URI는 기본값으로 아래 주소를 사용합니다.

```text
http://127.0.0.1:4173/kakao/callback
```

카카오 개발자센터에도 같은 Redirect URI를 등록해야 합니다.


## Client Secret을 켠 경우

카카오 로그인 > 보안에서 Client Secret이 활성화되어 있으면 `.env`에 아래 값도 추가해야 합니다.
끄고 사용할 수도 있지만, 켜져 있다면 token 발급 요청에 반드시 포함되어야 합니다.

```env
KAKAO_CLIENT_SECRET=카카오_CLIENT_SECRET
```

`Bad client credentials`가 나오면 아래 둘 중 하나를 확인합니다.

1. `.env`의 `KAKAO_REST_API_KEY`가 앱 키 메뉴의 `REST API 키`인지 확인
2. Client Secret이 켜져 있다면 `KAKAO_CLIENT_SECRET`도 `.env`에 추가
