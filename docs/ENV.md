# 환경 변수 세팅 가이드

서비스를 실행하기 전에 `.env` 파일을 프로젝트 루트에 생성하세요. 기본 템플릿은 `.env.example`을 복사하여 사용합니다.

```
cp .env.example .env
# 값 채운 뒤 서버/워커 실행
```

## 필수

- `GEMINI_API_KEY` 또는 `GEMINI_API_KEYS`
  - 단일 키 또는 쉼표로 구분된 다중 키 (멀티 API 분산 처리)
  - 예) `GEMINI_API_KEYS=key1,key2,key3`
- `REDIS_URL`
  - 파일/진행률/결과 저장 및 토큰 계정에 사용합니다.
  - 예) `REDIS_URL=redis://localhost:6379/0`

## 권장/일반 설정

- `RENDER_STORAGE_PATH`
  - /tmp 대신 사용할 영속 스토리지 경로. Render/서버 환경에서 디스크 마운트 경로로 지정.
  - 예) `/data/storage`
- `FILE_TTL_SECONDS`
  - 업로드 파일 Redis TTL(초). 기본 86,400(24h). 길게 유지 권장.
- `MAX_PAGES_PER_CHUNK`
  - 대용량 PDF 분할 기준 페이지 수. 기본 30~40 권장.
- 보존 정책(시간)
  - `DEBUG_RETENTION_HOURS` (기본 168)
  - `RESULT_RETENTION_HOURS` (기본 720)
  - `SESSIONS_RETENTION_HOURS` (기본 168)

## CBT(클로즈드 베타) 전용

- `GOOGLE_OAUTH_CLIENT_ID`
  - Google Identity Services에서 발급받은 Client ID. 로그인에 사용.
- `AUTH_SECRET_KEY`
  - 세션 쿠키(JWT) 서명용 시크릿. 길고 랜덤한 문자열 필수.
- `ALLOWED_TESTERS`
  - CBT 허용 이메일 화이트리스트(쉼표 구분). 비어있으면 제한 없음.
- `SESSION_EXPIRES_SECONDS`
  - 세션 쿠키 만료(초). 기본 7일(604800).
- `CBT_TOKENS_INITIAL`
  - 최초 로그인시 지급할 토큰 수. 기본 200.
- `FLASH_TOKENS_PER_CHUNK`, `PRO_TOKENS_PER_CHUNK`
  - 청크 1개 처리당 차감 토큰 수. 기본 flash=1, pro=4.
- `FEEDBACK_FORM_URL`
  - 네비게이션에 표시할 공식 설문 링크(선택).
- 개발 편의(선택):
  - `ALLOW_DEV_LOGIN=true` + `ADMIN_PASSWORD` → `/auth/dev-login` 활성화(로컬 전용)
  - `ALLOW_UNVERIFIED_GOOGLE_TOKENS=true` → 서명 검증 없이 claim만 검증(운영 비권장)
  - `COOKIE_SECURE` → `true`로 설정 시 HTTPS에서만 세션 쿠키 전송(기본 false)
  - `COOKIE_SAMESITE` → 기본 `Lax` (필요 시 `None`/`Strict`로 조정)

## 병렬 처리 관련(선택)

- `CELERY_CONCURRENCY`
  - 워커 프로세스 동시성
- `GEMINI_PER_KEY_CONCURRENCY`
  - 동일 API 키에서의 동시 처리 한도(기본 1)
- `GEMINI_PURGE_BEFORE_UPLOAD`
  - 업로드 전 전체 삭제 플래그(병렬 시 비권장)

## 체크리스트

1) 최소 `GEMINI_API_KEY`(또는 `GEMINI_API_KEYS`), `REDIS_URL` 설정
2) CBT 진행 시 `GOOGLE_OAUTH_CLIENT_ID`, `AUTH_SECRET_KEY` 설정
3) Render 등 배포 환경에서는 `RENDER_STORAGE_PATH`가 쓰기 가능해야 함
4) 토큰 기반 과금으로 pro 모델 접근 제어(비밀번호 제거)
5) 서버/워커 시작:
```
uvicorn web_server:app --reload
celery -A tasks:celery_app worker -Q analysis,default --loglevel=info
```
