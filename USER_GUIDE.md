# 🚀 JokboDude 사용자 가이드 - Render 배포 및 운영

## 📋 목차
1. [현재 상태](#현재-상태)
2. [Render 배포 단계별 가이드](#render-배포-단계별-가이드)
3. [환경변수 설정](#환경변수-설정)
4. [배포 후 확인사항](#배포-후-확인사항)
5. [사용 방법](#사용-방법)
6. [트러블슈팅](#트러블슈팅)
7. [비용 관리](#비용-관리)

## 현재 상태

### ✅ 완료된 작업
- Redis 기반 파일 스토리지 시스템 구현 (압축 지원)
- Web/Worker 서비스 간 통신 구조 완성
- 프로페셔널 UI/UX 프론트엔드 (실시간 진행률 표시)
- Multi-API 지원 및 모델 선택 기능
- 파일 크기 검증 (50MB 제한)
- 강화된 에러 핸들링 및 재시도 로직
- 헬스체크 엔드포인트 (`/health`)
- 실시간 진행률 추적 시스템
- 모든 코드가 `render` 브랜치에 푸시됨

### 🔧 시스템 아키텍처
```
사용자 → Web Service → Redis → Worker → Redis → Web Service → 사용자
         (파일 업로드)  (저장)  (처리)   (결과)   (다운로드)
```

## 🆕 최신 개선사항 (2025-08-30)

### 즉시 해결된 문제들
1. ✅ **Lesson-Centric 모드 Redis 지원**: 완전히 구현됨
2. ✅ **웹 서버 엔드포인트 수정**: 모든 모드가 Redis 사용
3. ✅ **결과 파일 다운로드 API**: Redis 기반으로 전환
4. ✅ **강화된 에러 핸들링**: 
   - Redis 연결 실패 시 자동 재시도 (3회, 지수 백오프)
   - 로컬 스토리지 폴백 지원
   - 파일 압축으로 메모리 효율성 향상
5. ✅ **실시간 진행률 추적**: 
   - 각 작업 단계별 진행률 표시
   - 한글 메시지로 현재 상태 안내
6. ✅ **헬스체크 엔드포인트**: `/health`로 시스템 상태 모니터링

### 새로운 기능들
- **Google 로그인 + 세션 쿠키**: GIS 로그인 후 서버가 HttpOnly 세션 쿠키 발급
- **CBT 토큰 과금**: 최초 지급(`CBT_TOKENS_INITIAL`), 청크 처리당 차감(Flash=1/Pro=4 기본)
- **My Jobs**: 최근 작업 목록/진행률/결과 파일 일괄 조회 및 삭제
- **진행률/결과 API 갱신**: `/progress/{job_id}`, `/results/{job_id}`, `/result/{job_id}/{filename}` (인증 필요)
- **자동 압축**: 1MB 이상 파일 자동 압축 (최대 90% 공간 절약)
- **향상된 UI**: 실시간 진행률 바 표시 + 토큰 배지
- **개선된 오류 메시지**: 사용자 친화적 에러 안내

## Render 배포 단계별 가이드

### Step 1: GitHub 준비
```bash
# 이미 완료됨 - render 브랜치가 최신 상태
git checkout render
git pull origin render
```

### Step 2: Render Dashboard 접속
1. [https://dashboard.render.com](https://dashboard.render.com) 로그인
2. 기존 서비스가 있다면 삭제 (깨끗한 시작을 위해)

### Step 3: 새 Blueprint 생성
1. **"New +"** 버튼 클릭
2. **"Blueprint"** 선택
3. GitHub 리포지토리 연결:
   - Repository: `ksw6895/jokbodude`
   - Branch: `render`
   - Blueprint Path: `/render.yaml`

### Step 4: 서비스 확인
자동으로 감지되는 3개 서비스:
- `jokbodude-redis` (Redis 인스턴스)
- `jokbodude-api` (Web 서비스)
- `jokbodude-worker` (Background Worker)

### Step 5: 요금제 선택
**권장 설정:**
- Redis: `Free` (시작용) 또는 `Starter` (프로덕션)
- Web Service: `Starter` ($7/월) - **필수**
- Worker: `Starter` ($7/월) - **필수**

**월 예상 비용: $14 + Redis 비용**

## 환경변수 설정

### 필수 환경변수 (각 서비스별로 설정)

#### 1. jokbodude-api (Web Service)
Dashboard → jokbodude-api → Environment 탭

```bash
# 필수 (하나만 선택)
GEMINI_API_KEY=your_single_api_key_here

# 또는 Multi-API 모드 (더 안정적)
GEMINI_API_KEYS=key1,key2,key3

# 선택사항
GEMINI_MODEL=flash  # 기본 flash (pro도 토큰 기반 과금)
```

#### 2. jokbodude-worker
Dashboard → jokbodude-worker → Environment 탭

**Web Service와 동일한 값 입력:**
```bash
GEMINI_API_KEY=your_single_api_key_here
# 또는
GEMINI_API_KEYS=key1,key2,key3

GEMINI_MODEL=flash
```

### Gemini API 키 발급 방법
1. [Google AI Studio](https://makersuite.google.com/app/apikey) 접속
2. "Create API Key" 클릭
3. 생성된 키 복사
4. Multi-API를 위해 여러 개 생성 가능 (Gmail 계정당 1개)

## 배포 후 확인사항

### 1. 서비스 상태 확인
각 서비스가 "Live" 상태인지 확인:
- ✅ jokbodude-redis: `Running`
- ✅ jokbodude-api: `Live`
- ✅ jokbodude-worker: `Running`

### 2. 웹 서비스 URL 확인
- jokbodude-api 서비스 클릭
- 상단에 표시된 URL 확인
- 예: `https://jokbodude-api.onrender.com`

### 3. 로그 확인
각 서비스의 "Logs" 탭에서:
- 에러 메시지 없는지 확인
- Worker가 "ready" 상태인지 확인

## 사용 방법

### 웹 인터페이스 접속
1. 브라우저에서 `https://jokbodude-api.onrender.com` 접속
2. 상단에서 Google 계정으로 로그인 (화이트리스트 미설정 시 모두 허용)
3. 모델 선택 (Flash/Pro, Pro는 토큰 기반 제어)
4. PDF 파일 업로드:
   - 족보 PDF (여러 개 가능)
   - 강의자료 PDF (여러 개 가능)
5. "Start Analysis" 클릭
6. 처리 완료 후 My Jobs 또는 결과 버튼으로 다운로드

### API 직접 사용 (인증 필요)
```bash
# 1) 로그인 (브라우저 UI에서 Google 로그인 권장)
# 또는 dev-login이 활성화된 경우(로컬 전용):
curl -X POST "http://localhost:8000/auth/dev-login" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "email=user@example.com&password=ADMIN_PASSWORD" -i
# 위 응답의 Set-Cookie 헤더에서 session 쿠키를 추출하여 아래 요청에 사용

# 2) 분석 시작 (세션 쿠키 필요)
curl -X POST "http://localhost:8000/analyze/jokbo-centric?model=flash" \
  -H "Cookie: session=YOUR_SESSION_JWT" \
  -F "jokbo_files=@jokbo/sample.pdf" \
  -F "lesson_files=@lesson/sample.pdf"

# 3) 진행률 확인(세션 쿠키 필요)
curl -H "Cookie: session=YOUR_SESSION_JWT" \
  "http://localhost:8000/progress/{job_id}"

# 4) 결과 파일 목록
curl -H "Cookie: session=YOUR_SESSION_JWT" \
  "http://localhost:8000/results/{job_id}"

# 5) 특정 결과 파일 다운로드
curl -H "Cookie: session=YOUR_SESSION_JWT" -OJ \
  "http://localhost:8000/result/{job_id}/{filename}"
```

## 트러블슈팅

### 문제 0: 로그인 422 (Unprocessable Content)
**원인:** 서버가 폼 파라미터를 기대하는데 다른 포맷으로 보낸 경우
**해결:** `/auth/google`와 `/auth/dev-login`은 `application/x-www-form-urlencoded`로 전송하세요

### 문제 1: "FileNotFoundError"
**해결:** 이미 수정됨. Redis 기반 스토리지로 해결

### 문제 2: "Service Unavailable"
**원인:** Free tier 15분 sleep
**해결:** 
- Starter plan으로 업그레이드
- 또는 첫 요청 시 30초 대기

### 문제 3: "API Key Error"
**확인사항:**
1. 환경변수가 양쪽 서비스에 모두 설정되었는지
2. API 키가 유효한지
3. 쉼표 구분이 올바른지 (Multi-API)

### 문제 4: "Memory Error"
**원인:** 큰 PDF 처리 시 메모리 부족
**해결:**
1. Standard plan으로 업그레이드 (2GB RAM)
2. 또는 PDF 크기 줄이기

### 문제 5: Redis 연결 실패
**확인:**
1. Redis 서비스가 Running 상태인지
2. render.yaml의 서비스 이름이 일치하는지

### 문제 6: 로그인 실패 (401/403)
**확인:**
1. `GOOGLE_OAUTH_CLIENT_ID`가 올바른지 (GIS와 일치)
2. `AUTH_SECRET_KEY`가 설정되어 있는지
3. `ALLOWED_TESTERS`에 이메일이 포함되어 있는지(비어있으면 전체 허용)
4. (로컬) `ALLOW_UNVERIFIED_GOOGLE_TOKENS=true`로 claims-only 검증 허용 여부

## 비용 관리

### 현재 Render Starter Plan 사용 중
현재 Render Starter Plan을 사용 중이시므로, 다음과 같은 구성을 권장합니다:

### 월별 예상 비용 (Render 기준)
| 구성 | 월 비용 | 적합한 용도 | 구체적 내역 |
|------|---------|------------|------------|
| **현재 (Starter)** | **$14** | 개인/소규모 사용 | Web $7 + Worker $7 + Redis Free |
| 권장 (Starter+Redis) | $21 | 안정적 운영 | Web $7 + Worker $7 + Redis Starter $7 |
| 확장 (Standard) | $50 | 중규모 사용 | Web $25 + Worker $25 + Redis Starter |
| 프로 (Pro) | $170 | 대용량/다중 사용자 | Web $85 + Worker $85 + Redis Starter |

### 현재 구성의 제한사항
- **Free Redis**: 메모리 25MB, 데이터 손실 가능
- **Starter 서비스**: 512MB RAM, CPU 0.5

### 추가 DB 서비스 옵션 (필요 시)

현재 Redis Free tier로도 충분하지만, 필요하다면 다음 서비스들을 고려할 수 있습니다:

#### 1. Upstash Redis (Render Redis 대체)
- **비용**: Pay-as-you-go (무료 티어: 10,000 commands/일)
- **장점**: 서버리스, 데이터 영구 저장, 자동 백업
- **추천 이유**: Render Free Redis의 25MB 제한과 데이터 손실 문제 해결

#### 2. Supabase (무료 티어)
- **비용**: 무료 (500MB DB, 1GB 스토리지, 2GB 대역폭)
- **장점**: PostgreSQL DB + 파일 스토리지 통합
- **추천 이유**: 완전 무료로 Redis 보완 가능

#### 3. Cloudflare R2 (대용량 파일 필요 시)
- **비용**: $0.015/GB/월 (10GB 무료)
- **장점**: S3 호환, 무료 대역폭
- **추천 이유**: 결과 PDF 장기 보관용

### 비용 절감 팁
1. **개발/테스트**: Free tier Redis 사용
2. **모델 선택**: Flash-lite 사용 (Gemini API 비용 절감)
3. **Multi-API**: 여러 무료 API 키 사용으로 할당량 분산
4. **자동 정지**: 사용하지 않을 때 서비스 일시 정지

## 모니터링

### Render Dashboard에서 확인
- **Metrics**: CPU, Memory 사용량
- **Logs**: 실시간 로그
- **Events**: 배포 이벤트
- **Disk Usage**: 디스크 사용량 (Web 서비스)

### 알림 설정
1. Settings → Notifications
2. 이메일/Slack 알림 설정
3. 서비스 다운/에러 시 알림

## 업데이트 방법

### 코드 업데이트 시
```bash
git checkout render
git pull origin render
# 코드 수정
git add .
git commit -m "Update: 기능 설명"
git push origin render
```
→ Render가 자동으로 재배포

### 환경변수 변경 시
1. Dashboard → Service → Environment
2. 변수 수정
3. "Save Changes"
4. 서비스 자동 재시작

## 백업 및 복구

### Redis 데이터
- TTL 설정으로 자동 삭제 (1-2시간)
- 중요 데이터는 별도 백업 불필요

### 처리 결과
- 사용자가 다운로드 후 로컬 저장
- 서버에는 임시 저장만

## 보안 고려사항

1. **API 키 보호**
   - 절대 코드에 하드코딩 금지
   - 환경변수로만 관리

2. **파일 크기 제한**
   - 50MB 제한 적용됨
   - DDoS 방지

3. **HTTPS 사용**
   - Render가 자동으로 SSL 제공

## 문의 및 지원

### 문제 발생 시
1. Render Logs 확인
2. GitHub Issues 생성
3. 에러 메시지와 함께 상세 설명

### 추가 개발 요청
- `REMAINING_TASKS.md` 참조
- GitHub Issues에 기능 요청


## 마지막 체크리스트

배포 전 최종 확인:
- [ ] GitHub render 브랜치 최신화
- [ ] Gemini API 키 준비
- [ ] Render 계정 및 결제 수단
- [ ] 테스트용 PDF 파일 준비
- [ ] 환경변수 설정 완료
- [ ] 서비스 모두 Live/Running 상태
- [ ] 웹 URL 접속 확인
- [ ] 테스트 파일 업로드 및 처리 확인

---

**작성일:** 2025-08-11
**버전:** 2.0.0
**작성자:** Claude (Anthropic)
**주요 업데이트:** 
- v2.0.0: 모든 즉시 해결 사항 완료, 진행률 추적, Supabase 통합 가이드
- v1.0.0: 초기 버전
