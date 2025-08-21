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
- `web_server.py`: FastAPI 앱(업로드, 상태, 결과, 헬스)
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

3) 간단 테스트(curl)
```
curl -F "jokbo_files=@jokbo/sample.pdf" -F "lesson_files=@lesson/sample.pdf" \
     "http://localhost:8000/analyze/jokbo-centric?model=flash"

# 진행/결과 조회
curl http://localhost:8000/progress/<job_id>
curl http://localhost:8000/results/<job_id>
curl -OJ http://localhost:8000/result/<job_id>/<filename>
```

### 배치 처리(한 번의 API로 여러 하위 요청)
서버가 요청을 팬아웃하여 각 하위 작업을 격리 실행합니다(요청 간 오염 방지).
```
curl -F "jokbo_files=@jokbo/sample.pdf" -F "lesson_files=@lesson/sample.pdf" \
     "http://localhost:8000/analyze/batch?mode=jokbo-centric&model=flash"
```
- mode=jokbo-centric: 각 족보 파일 × 모든 강의자료로 N개의 하위 작업 실행
- mode=lesson-centric: 각 강의자료 × 모든 족보로 N개의 하위 작업 실행
- 진행/결과는 동일 엔드포인트(`/progress/{job_id}`, `/results/{job_id}`) 사용

배치 스모크 테스트:
```
bash scripts/smoke_batch.sh
```

프런트엔드는 `/`에서 정적 파일로 제공됩니다.

## Render 배포(요약)
1) GitHub 연결 → Blueprint(Render)로 `render.yaml` 사용
2) 환경변수:
```
GEMINI_API_KEY=...        # 또는 GEMINI_API_KEYS=key1,key2,...
GEMINI_MODEL=flash        # 모델은 flash로 고정
REDIS_URL=redis://...
RENDER_STORAGE_PATH=/var/data  # 쓰기 가능 경로
```

## 동작/품질 메모
- 문제/정답/해설/퀴즈/TBL/기출 형태의 ‘문제 슬라이드’는 관련성에서 제외(프롬프트에 규칙 포함)
- Lesson‑centric 결과에는 슬라이드 중요도(importance_score), Jokbo‑centric에는 관련성 점수(relevance_score)가 포함됩니다
- 결과 PDF는 CJK 폰트를 내장해 한글 렌더링 문제를 최소화합니다(일부 뷰어에서 이모지 제거)

## 보안/구성 팁
- 개인 PDF/비밀키 커밋 금지. `.env`로 관리
- 멀티 키 사용 시 처리량/안정성 향상(`GEMINI_API_KEYS`)
- Render에서는 `REDIS_URL`과 쓰기 가능한 `RENDER_STORAGE_PATH`를 설정하세요
- 병렬 처리 관련 환경 변수:
  - `CELERY_CONCURRENCY`: 워커 프로세스 동시성(예: 2~4)
  - `GEMINI_PER_KEY_CONCURRENCY`: 같은 API 키에서 동시 처리 허용 수(기본 1)
  - `GEMINI_PURGE_BEFORE_UPLOAD`: 업로드 전 전체 삭제 플래그(기본 false; 병렬 시 비권장)

## 라이선스
AGPLv3. 네트워크 서비스로 제공 시 수정본 소스 공개 의무가 있습니다. 자세한 내용은 라이선스 전문을 참조하세요.

## 기여
이슈/PR 환영합니다. 개선 아이디어/버그 리포트 부탁드립니다.
