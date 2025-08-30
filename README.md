# JokboDude - AI 기반 족보 학습 도우미

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)

족보(기출)와 강의자료를 AI로 분석하여 관련 슬라이드와 해설을 묶은 PDF를 생성하는 FastAPI + Celery 기반 서비스입니다. Google Gemini 2.5 Flash와 멀티 API 키 분산을 지원합니다.

## 주요 기능
- AI 연관성 분석: 족보 ↔ 강의 슬라이드 자동 매칭
- 듀얼 모드: Lesson‑centric, Jokbo‑centric
- 대용량 PDF 자동 청크 분할 + 병렬 처리(ETA/청크 진행률)
- 멀티 API 키 분산 처리 및 장애 자동 폴백
- Redis 기반 파일 전달/진행도/결과 저장
- 결과 PDF 생성: 문제/슬라이드/해설 구성, CJK 폰트 내장(렌더링 개선)

## 아키텍처 개요
- `pdf_processor/`: 분석 엔진
  - `core/`(오케스트레이션), `analyzers/`(lesson/jokbo/멀티API), `api/`(Gemini+파일), `parsers/`(응답/병합), `pdf/`(분할/추출), `utils/`
- `server/`: 모듈형 FastAPI 앱(분석/잡/헬스 라우터)
- `web_server.py`: 배포 호환성을 위한 얇은 래퍼(`server.main:create_app`)
- `tasks.py`: Celery 워커(분석 실행, 결과 PDF 저장)
- `pdf_creator.py`: 결과 PDF 생성기
- `storage_manager.py`: Redis 파일/진행도/TTL 관리
- `frontend/`: 정적 UI(`/` 루트 서빙)
- 입력 예시: `jokbo/`, `lesson/` · 출력: `output/`
- 설정: `.env(.example)`, `config.py`, `celeryconfig.py`, `requirements.txt`, `render.yaml`

## 요구사항
- Python 3.8+
- Redis 6+
- Gemini API 키(단일 또는 다중)

## 로컬 실행
1) 가상환경 및 의존성 설치
```
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# .env 필수: GEMINI_API_KEY 또는 GEMINI_API_KEYS, REDIS_URL
```

2) 서버 및 워커 실행(각각 터미널)
```
uvicorn web_server:app --reload             # API (기본 8000)
celery -A tasks:celery_app worker -Q analysis,default --loglevel=info
```

3) 간단 테스트(브라우저 권장)
- 모든 기능은 Google 로그인(또는 dev 로그인)이 필요합니다. 브라우저 UI(`/`)에서 로그인 후 업로드/다운로드를 사용하세요.
- curl 사용 시에도 인증 쿠키가 필요합니다(로그인 없이 호출 불가).
```
# 예시: 로그인 후 동일 브라우저 세션에서 동작합니다.
# curl 예시는 인증 쿠키 없이는 실패합니다.
curl -F "jokbo_files=@jokbo/sample.pdf" -F "lesson_files=@lesson/sample.pdf" \
     "http://localhost:8000/analyze/jokbo-centric?model=flash"
```

### 배치 처리(한 번의 API로 여러 하위 요청)
서버가 요청을 팬아웃하여 각 하위 작업을 격리 실행합니다(요청 간 오염 방지).
```
curl -F "jokbo_files=@jokbo/sample.pdf" -F "lesson_files=@lesson/sample.pdf" \
     "http://localhost:8000/analyze/batch?mode=jokbo-centric&model=flash"
```
- mode=jokbo-centric: 각 족보 파일 × 모든 강의자료로 N개의 하위 작업 실행
- mode=lesson-centric: 각 강의자료 × 모든 족보로 N개의 하위 작업 실행
- 진행/결과는 동일 엔드포인트(`/progress/{job_id}`, `/results/{job_id}`) 사용(로그인 및 소유자만 접근 가능)

배치 스모크 테스트:
```
bash scripts/smoke_batch.sh
```

프런트엔드는 `/`에서 정적 파일로 제공됩니다.

중요: 임시 Job ID 기반의 비로그인 조회 기능은 제거되었습니다. 이제 결과 조회/다운로드는 로그인된 본인 작업에 한해 가능합니다.

### 환경 변수 세팅
자세한 환경 변수 설명과 권장값은 `docs/ENV.md`를 참고하세요. CBT(로그인/토큰 과금) 설정도 포함되어 있습니다.

## CBT(클로즈드 베타 테스트)
CBT 기간에는 Google 로그인(OAuth)과 토큰 기반 사용량 측정을 제공합니다.

- 환경 설정: `.env`에 `GOOGLE_OAUTH_CLIENT_ID`, `AUTH_SECRET_KEY`(필수), 선택 `ALLOWED_TESTERS`(허용 이메일 목록) 설정
- 최초 로그인 시 토큰이 지급됩니다(`CBT_TOKENS_INITIAL`, 기본 200)
- 처리 청크당 토큰 차감: Flash=1, Pro=4(환경 변수로 조정 가능)
- 잔액 부족 시 작업이 중단되며 진행 메시지로 안내됩니다
- 관리자 토큰 관리: `docs/CBT.md` 참조

## 인증/토큰 개요
- 로그인: Google Identity Services(GIS)로 로그인 후, 서버가 세션 쿠키(`session`, HttpOnly)를 발급합니다.
- 화이트리스트: `ALLOWED_TESTERS`에 포함된 이메일만 로그인 허용(비어있으면 제한 없음).
- 토큰: 최초 로그인 시 `CBT_TOKENS_INITIAL`만큼 지급, 처리 청크당 차감(Flash=1, Pro=4 기본값).
- 주요 엔드포인트:
  - `GET /auth/config` 로그인/토큰 UI 설정
  - `POST /auth/google` 폼 `id_token`(x-www-form-urlencoded)
  - `POST /auth/dev-login` 폼 `email`, `password`(옵션, 로컬/개발용)
  - `POST /auth/logout` 로그아웃
  - `GET /me` 현재 세션/잔액 확인
  - 진행/결과/잡: `GET /progress/{job_id}`, `GET /results/{job_id}`, `GET /result/{job_id}/{filename}`

## Render 배포(요약)
1) GitHub 연결 → Blueprint(Render)로 `render.yaml` 사용
2) 환경변수:
```
GEMINI_API_KEY=...        # 또는 GEMINI_API_KEYS=key1,key2,...
GEMINI_MODEL=flash        # 기본 flash (pro도 토큰으로 제어)
REDIS_URL=redis://...
RENDER_STORAGE_PATH=/data/storage  # 디스크 마운트 경로와 동일하게 설정
```

## 동작/품질 메모
- 문제/정답/해설/퀴즈/TBL/기출 형태의 ‘문제 슬라이드’는 관련성에서 제외(프롬프트에 규칙 포함)
- Lesson‑centric 결과에는 슬라이드 중요도(importance_score), Jokbo‑centric에는 관련성 점수(relevance_score)가 포함됩니다
- 결과 PDF는 CJK 폰트를 내장해 한글 렌더링 문제를 최소화합니다(일부 뷰어에서 이모지 제거)

## 보안/구성 팁
- 개인 PDF/비밀키 커밋 금지. `.env`로 관리
- 멀티 키 사용 시 처리량/안정성 향상(`GEMINI_API_KEYS`)
- Render에서는 `REDIS_URL`과 쓰기 가능한 `RENDER_STORAGE_PATH`를 설정하세요
  - Blueprint(render.yaml) 기본값: 디스크를 `/data/storage`에 마운트하고, env도 동일 경로로 설정
  - 디스크를 `/var/data`에 마운트하고 싶다면 mountPath와 env 둘 다 `/var/data`로 맞추세요
- 병렬 처리 관련 환경 변수:
  - `CELERY_CONCURRENCY`: 워커 프로세스 동시성(예: 2~4)
  - `GEMINI_PER_KEY_CONCURRENCY`: 같은 API 키에서 동시 처리 허용 수(기본 1)
  - `GEMINI_PURGE_BEFORE_UPLOAD`: 업로드 전 전체 삭제 플래그(기본 false; 병렬 시 비권장)

## 라이선스
AGPLv3. 네트워크 서비스로 제공 시 수정본 소스 공개 의무가 있습니다. 자세한 내용은 라이선스 전문을 참조하세요.

## 기여
이슈/PR 환영합니다. 개선 아이디어/버그 리포트 부탁드립니다.
