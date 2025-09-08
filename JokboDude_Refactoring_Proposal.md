# JokboDude 리팩토링 제안서

## 1. 개요 및 현재 아키텍처 분석

JokboDude는 FastAPI + Celery + Redis 기반의 비동기 PDF 분석 서비스로, 다음의 강점을 갖고 있습니다.

- 명확한 처리 파이프라인: 업로드 → Redis 저장 → Celery 작업 → PDF 생성 → 결과 보관/다운로드
- 분석 엔진 모듈화: `pdf_processor/` 내 `analyzers/`, `api/`, `parsers/`, `pdf/`로 역할 분리
- 멀티 API 분산: `MultiAPIManager`와 `MultiAPIAnalyzer`로 key 단위 부하분산·페일오버 지원
- 견고한 스토리지 & 진행률: TTL 갱신, 청크 기반 ETA, 취소 플래그, 디스크 백업 등 실전적인 보강
- 프리플라이트(견적/검증) 라우트와 배치 분석 등 실제 운용에 유용한 기능들

개선이 필요한 지점은 다음과 같습니다.

- 중복 코드 다수: `tasks.py` 내 분석 작업들, `server/routes/analyze.py`/`preflight.py`의 업로드·메타 저장 패턴이 반복됨
- 단일 클래스 과책임: `storage_manager.py`가 파일/결과/진행률/유저/테스터/토큰/취소 등 너무 많은 역할을 수행
- 복잡도 높은 함수: `pdf_creator.py`의 `extract_jokbo_question`와 유틸/경로해결/크롭/조립 로직 혼재
- 오케스트레이션 가독성: `PDFProcessor`의 모드/멀티-API별 경로가 유사 패턴으로 분기됨
- 설정 분산: `.env`, `config.py`, `constants.py`, `celeryconfig.py`에 산재, 공용 스키마 부재
- 에러 처리 일관성: `error_handler.py`가 중앙에서 일관되게 사용되지 않음; `try/except` 중첩 다수

리팩토링 목표는 코드 중복 제거, 책임 분리, 복잡도 감소, 설정 통합으로 유지보수성과 확장성을 크게 높이는 것입니다.

---

## 2. 리팩토링 진행도 체크리스트

- [ ] StorageManager 책임 분리 (파일/결과/진행률/유저/권한/토큰/취소)
  - 진행: 기본 래퍼 도입(FileStorage/ResultStore/ProgressTracker/JobRepository) 및 `StorageRegistry` 추가. 호출부 전면 치환과 User/Token/Tester/Cancel 분리는 다음 단계.
- [ ] Celery 작업 로직 통합(모드 전략화) 및 에러 핸들링 정리
  - 진행: `ModeStrategy` + `run_analysis_task` 도입, `run_jokbo_analysis`/`run_lesson_analysis`가 전략 기반 위임. 레거시 경로는 안전한 폴백으로 유지. 에러 데코레이터/공통 처리(취소/타임아웃)는 다음 단계.
- [x] Analyze/Preflight 라우트 공통화(헬퍼 함수/모듈)로 중복 제거
  - 완료: `server/routes/_helpers.py`의 `save_files_and_metadata` 유지 + `save_files_metadata_with_info` 신설로 `/preflight/*` 3종과 `exam-only` 프리플라이트에 적용. 업로드/TTL/메타 저장이 공통화됨.
- [ ] PDFCreator 유틸/경로해결/크롭 파이프라인 분리 및 전략화
- [ ] PDFProcessor 멀티-API 오케스트레이션 제너릭화
- [x] 설정 Pydantic Settings로 통합(환경·모델·Redis·Celery·기능플래그)
  - 완료: `settings.py` 추가, `config.py`/`celeryconfig.py`/`server/core.py` 주입화, `requirements.txt` 업데이트.
- [ ] 중앙 에러 핸들링 적용(데코레이터/헬퍼) + CancelledError 체계화
- [ ] 로깅 표준화(구조적 로그, 컨텍스트 포함) 및 레벨/핵심지표 정돈
- [ ] 스모크/엔드포인트 테스트 추가 및 스크립트 정리

---

## 3. 주요 리팩토링 제안 (테마별)

### 3.1. StorageManager 책임 분리

* 문제점:
  - `storage_manager.py`가 파일 저장/로딩, 결과 보존, 진행률/ETA, 작업-유저 매핑, 유저 프로필/토큰, 테스터 allowlist, 취소 플래그 등 광범위 책임을 모두 보유.
  - 테스트·교체·확장 난이도 상승, 단일 변경이 광범위 영향.

* 개선 방안:
  - `server/services/` 또는 `infra/` 디렉터리로 기능별 클래스 분리 및 컴포지션 적용.
    - `FileStorage`: 업로드 파일의 Redis 저장/TTL/로컬 세이프가드, `save_file_locally`, `verify_file_available`
    - `ResultStore`: 결과 PDF의 Redis+디스크 이중 보관, 경로 인덱스, 나열/삭제
    - `ProgressTracker`: `init_progress`, `increment_chunk`, `finalize_progress`, `get_progress`
    - `JobRepository`: 작업 메타 저장/조회, job↔user 매핑, 태스크 id 매핑, `cleanup_job`
    - `UserRegistry`: 유저 프로필/목록, 이메일 인덱스
    - `TokenService`: 토큰 잔액, 증감, 소비(원자적)
    - `TesterAllowlist`: 테스터 관리
    - `CancellationService`: 취소 요청/확인/해제
  - 상위 `StorageFacade` 또는 `StorageRegistry`로 필요한 서비스를 주입해 기존 호출부 호환 레이어 제공.

* 기대 효과:
  - 변경 영향 범위 축소, 테스트 단위 축소, 역할별 교체/확장 용이.
  - 라우트/작업 코드가 더 간단해지고 의도가 명확해짐.

* 코드 예시:
    * Before (발췌):
      ```python
      # storage_manager.py 일부 (파일/결과/진행률/유저/토큰/테스터/취소가 한 클래스에 혼재)
      class StorageManager:
          def store_file(...): ...
          def get_file(...): ...
          def store_result(...): ...
          def get_result_path(...): ...
          def init_progress(...): ...
          def increment_chunk(...): ...
          def store_job_metadata(...): ...
          def add_user_job(...): ...
          def get_user_tokens(...): ...
          def consume_user_tokens(...): ...
          def add_tester(...): ...
          def request_cancel(...): ...
          # ...기타 다수
      ```
    * After (제안):
      ```python
      # server/services/storage/file_storage.py
      class FileStorage:
          def store(self, path: Path, job_id: str, kind: str) -> str: ...
          def fetch(self, key: str) -> bytes | None: ...
          def save_locally(self, key: str, target: Path) -> Path: ...
          def refresh_ttls(self, keys: list[str], ttl: int | None = None) -> None: ...

      # server/services/storage/result_store.py
      class ResultStore:
          def put(self, job_id: str, pdf_path: Path) -> str: ...
          def get_path(self, job_id: str, fname: str) -> Path | None: ...
          def list(self, job_id: str) -> list[str]: ...
          def delete(self, job_id: str, fname: str) -> bool: ...
          def delete_all(self, job_id: str) -> int: ...

      # server/services/storage/progress.py
      class ProgressTracker:
          def init(self, job_id: str, total_chunks: int, message: str = "") -> None: ...
          def tick(self, job_id: str, inc: int = 1, message: str | None = None) -> None: ...
          def finalize(self, job_id: str, message: str = "완료") -> None: ...
          def get(self, job_id: str) -> dict | None: ...

      # server/services/storage/jobs.py
      class JobRepository:
          def set_metadata(self, job_id: str, meta: dict) -> None: ...
          def get_metadata(self, job_id: str) -> dict | None: ...
          def bind_task(self, job_id: str, task_id: str) -> None: ...
          def get_task(self, job_id: str) -> str | None: ...
          def add_user_job(self, user_id: str, job_id: str) -> None: ...
          def get_user_jobs(self, user_id: str, limit: int = 50) -> list[str]: ...
          def owner_of(self, job_id: str) -> str | None: ...
          def unlink_user_job(self, user_id: str, job_id: str) -> bool: ...
          def cleanup(self, job_id: str) -> None: ...

      # server/services/storage/users.py
      class UserRegistry: ...  # 프로필/목록/이메일 인덱스
      class TokenService: ...  # 잔액/증감/소비
      class TesterAllowlist: ...
      class CancellationService: ...

      # server/services/storage/facade.py
      class StorageFacade:
          def __init__(self, files: FileStorage, results: ResultStore, progress: ProgressTracker,
                       jobs: JobRepository, users: UserRegistry, tokens: TokenService,
                       testers: TesterAllowlist, cancel: CancellationService):
              self.files = files; self.results = results; self.progress = progress
              self.jobs = jobs; self.users = users; self.tokens = tokens
              self.testers = testers; self.cancel = cancel
      ```

### 3.2. Celery 작업 추상화 및 통합

* 문제점:
  - `run_jokbo_analysis`와 `run_lesson_analysis`가 거의 동일한 흐름을 모드만 달리 하여 반복 구현: 업로드 복원, TTL, 진행률 초기화, 분석 호출, PDF 생성, 결과 저장, 경고 집계, 취소 체크 등.
  - `try/except`가 중첩되고 일부 로직(예: TTL refresh)이 중복 호출.

* 개선 방안:
  - 모드 전략(Strategy)로 분석/생성 함수를 주입하는 제너릭 작업 함수로 통합.
  - 공통 전처리·후처리(입력 복원/진행률/취소/클린업/결과 저장)를 한 곳에서 수행.
  - 예외/취소/SoftTimeLimitExceeded 처리를 데코레이터/헬퍼로 공통화.

* 기대 효과:
  - 유지보수 비용 감소, 새로운 모드(exam-only/partial) 추가 시 재사용성↑.

* 코드 예시:
    * Before (발췌):
      ```python
      # tasks.py
      @celery_app.task(name="tasks.run_jokbo_analysis")
      def run_jokbo_analysis(job_id: str, model_type: str = None, multi_api: Optional[bool] = None):
          # ... 업로드 복원, TTL refresh, 진행률 초기화, analyze_jokbo_centric(_multi_api), create_jokbo_centric_pdf ...
          # ... 결과 저장, 경고 집계, finalize_progress ...

      @celery_app.task(name="tasks.run_lesson_analysis")
      def run_lesson_analysis(job_id: str, model_type: str = None, multi_api: Optional[bool] = None):
          # 거의 동일 로직, analyze_lesson_centric(_multi_api) & create_lesson_centric_pdf만 다름
      ```
    * After (제안):
      ```python
      # tasks/analysis.py
      from dataclasses import dataclass

      @dataclass
      class ModeStrategy:
          mode: str                         # "jokbo-centric" | "lesson-centric"
          primary_kind: str                 # "jokbo" | "lesson"
          secondary_kind: str               # "lesson" | "jokbo"
          analyze_fn: callable              # (processor, prim_path, other_paths, api_keys?, multi?) -> dict
          create_pdf_fn: callable           # (creator, primary_path, analysis_result, out_path, secondary_dir) -> None
          output_name_fn: callable          # (primary_path: Path) -> str

      def run_analysis_task(job_id: str, model_type: str | None, multi_api: bool | None, strategy: ModeStrategy):
          # 1) 취소/메타/TTL/임시폴더/로컬 복원/진행률 초기화 공통 구현
          # 2) processor/model/creator 초기화
          # 3) for each primary file: 취소 체크 → 분석(analyze_fn) → PDF 생성(create_pdf_fn)
          # 4) 결과 저장(ResultStore) + 경고 집계
          # 5) finalize_progress + payload 반환

      # tasks.py 등록만 분기
      @celery_app.task(name="tasks.run_jokbo_analysis")
      def run_jokbo_analysis(job_id: str, model_type: str = None, multi_api: Optional[bool] = None):
          return run_analysis_task(job_id, model_type, multi_api, strategies.jokbo_centric())

      @celery_app.task(name="tasks.run_lesson_analysis")
      def run_lesson_analysis(job_id: str, model_type: str = None, multi_api: Optional[bool] = None):
          return run_analysis_task(job_id, model_type, multi_api, strategies.lesson_centric())
      ```

### 3.3. Analyze/Preflight 라우트 중복 제거

* 문제점:
  - `/analyze/jokbo-centric`, `/analyze/lesson-centric`, `/analyze/batch`, `/analyze/partial-jokbo`, `/analyze/exam-only`에서 파일 저장/검증/TTL/메타 구성/토큰 확인/작업 시작 패턴이 반복.
  - `/preflight/*` 라우트에서도 유사한 업로드·메타 저장 로직 반복.

* 개선 방안:
  - `server/routes/_helpers.py`(가칭)로 업로드 저장, TTL refresh, 메타 생성/저장, 토큰 확인, 태스크 전송, job↔task 바인딩을 함수화.
  - 기존 엔드포인트 시그니처·경로는 유지(프런트와의 호환), 내부 구현만 공통 헬퍼 호출로 치환.

* 기대 효과:
  - 라우트 가독성 향상, 파라미터 처리 일관성, 사이드 이펙트/버그 감소.

* 코드 예시:
    * Before (발췌):
      ```python
      # server/routes/analyze.py
      for f in jokbo_files + lesson_files:
          if f.size and f.size > MAX_FILE_SIZE: ...
      with tempfile.TemporaryDirectory() as temp_dir:
          # 각 파일 저장 + store_file + verify_file_available 반복 ...
      storage_manager.refresh_ttls(...)
      metadata = {...}; storage_manager.store_job_metadata(job_id, metadata)
      # 토큰 확인 및 add_user_job, send_task, set_job_task 등 반복
      ```
    * After (제안):
      ```python
      # server/routes/_helpers.py
      def save_files_and_metadata(request, jokbo_files, lesson_files, mode, model, multi_api, min_rel, user_id) -> tuple[str, dict]:
          # 사이즈 검증 → temp 저장 → FileStorage.store → verify → TTL → meta 구성/저장 → user job 등록
          return job_id, metadata

      def start_job(job_id: str, mode: str, model: str, multi_api: bool) -> str:
          # Celery task 전송 + task id 바인딩 후 반환

      # server/routes/analyze.py 내부
      job_id, meta = save_files_and_metadata(...)
      task_id = start_job(job_id, mode="jokbo-centric", model=model, multi_api=effective_multi)
      return {"job_id": job_id, "task_id": task_id}
      ```

### 3.4. PDFCreator 복잡도 감소(전략/파이프라인화)

* 문제점:
  - `pdf_creator.py`의 `_resolve_jokbo_path`, `_resolve_lesson_path` 유틸이 클래스 내부 캐시·정규화·로깅까지 포함.
  - `extract_jokbo_question`는 페이지 결정/마커 탐지/크롭/중간페이지 삽입/에러 폴백이 한 메서드에 혼재되어 조건 분기가 많음.

* 개선 방안:
  - 경로 해석은 `pdf_processor/pdf/resolver.py`(신규) 모듈로 이동하여 단위 테스트 가능하게 분리.
  - `extract_jokbo_question`를 파이프라인 함수로 분해:
    - `decide_end_page(...)` → `crop_start_page(...)` → `insert_middle_pages(...)` → `crop_end_page(...)` → `assemble_question_doc(...)`
  - 또는 간단한 전략 패턴 적용: SamePageNextMarkerStrategy / MultiPageStrategy / DefaultSinglePageStrategy 등으로 분기 책임을 외부화.

* 기대 효과:
  - 테스트 용이성↑, 장애 지점(크롭 실패 시 폴백 등)을 명확히 분리, 로깅 포인트/재시도 정책 명확화.

* 코드 예시:
    * Before (발췌):
      ```python
      # pdf_creator.py
      def extract_jokbo_question(...):
          # end_page 결정 + 마커 기반 크롭 + 중간 페이지 삽입 + end 크롭 + 폴백 삽입 ...
      ```
    * After (제안):
      ```python
      # pdf_processor/pdf/resolver.py
      class PDFNameResolver: ...  # 기존 정규화/캐시/접두사 제거 로직 이동

      # pdf_creator/questions.py
      def decide_end_page(ctx) -> int: ...
      def crop_start_page(ctx) -> Path | None: ...
      def insert_middle_pages(ctx, doc) -> None: ...
      def crop_end_page(ctx) -> Path | None: ...
      def assemble_question_doc(ctx) -> fitz.Document: ...
      ```

### 3.5. PDFProcessor 멀티-API 오케스트레이션 정리

* 문제점:
  - `analyze_lesson_centric_multi_api`와 `analyze_jokbo_centric_multi_api`가 유사한 분배/청크/머지 패턴으로 중복 구현.
  - 분기 로직이 길어 가독성 저하.

* 개선 방안:
  - 모드·주체(청크 대상)·결과 머지 함수를 파라미터화한 제너릭 멀티-API 루틴으로 통합.
  - 청크 추출/정리/진행률 업데이트/스테이터스 로깅은 공통화.

* 기대 효과:
  - 코드 길이와 중복 감소, 멀티-API 정책 변경(쿨다운/병렬도) 반영 포인트 단일화.

* 코드 예시:
    * After (제안 스케치):
      ```python
      # pdf_processor/core/processor.py
      def analyze_multi_api(self, mode: str, primary_paths: list[str], secondary_path: str,
                            chunk_on: str, api_keys: list[str]) -> dict:
          # chunk_on in {"lesson", "jokbo", "none"}
          # 1) chunk 계획 수립 → extract_pages 일괄 → 작업 리스트 구성
          # 2) MultiAPIManager.distribute_tasks(...)로 분배 (on_progress에서 ProgressTracker.tick)
          # 3) 모드별 result merger 주입
          return merged
      ```

### 3.6. 설정 및 의존성 관리(Pydantic Settings)

* 문제점:
  - `.env`, `config.py`, `constants.py`, `celeryconfig.py` 전반에 설정 값 산재.
  - 런타임 해석/기본값/검증·변환이 일관되지 않음.

* 개선 방안:
  - `settings.py`에 Pydantic `BaseSettings` 도입: Redis/Celery/Model/Feature/Paths/TTL/보안 등 스키마 정의.
  - `config.py`는 Gemini SDK 래핑/모델 빌더로 축소하고, 값은 `settings`에서 주입.
  - `constants.py`는 “프롬프트/템플릿”과 “튜닝 상수”를 분리: `prompts/*.md.j2`(문자열 리소스), `app_constants.py`(수치/기능플래그).

* 기대 효과:
  - 설정 변경 추적/검증 용이, 환경 이식성 향상, 런타임에서의 예외 감소.

* 코드 예시:
  ```python
  # settings.py
  from pydantic_settings import BaseSettings
  class Settings(BaseSettings):
      # Redis
      REDIS_URL: str = "redis://localhost:6379/0"
      FILE_TTL_SECONDS: int = 86400
      # Celery
      CELERY_SOFT_TIME_LIMIT: int = 86400
      CELERY_TIME_LIMIT: int = 90000
      # Model
      GEMINI_MODEL: str = "flash"
      GEMINI_API_KEYS: list[str]
      DISABLE_SAFETY_FILTERS: bool = True
      GEMINI_PER_KEY_CONCURRENCY: int = 1
      GEMINI_RATE_LIMIT_COOLDOWN_SECS: int = 30
      # Features
      ALLOW_DEV_LOGIN: bool = False
      # Paths
      RENDER_STORAGE_PATH: str | None = None
      TMPDIR: str | None = None
      class Config:
          env_file = ".env"
          env_nested_delimiter = "__"

  settings = Settings()
  ```

### 3.7. 견고성 및 에러 핸들링

* 문제점:
  - `try/except`가 깊게 중첩, 모드/단계별 에러 정책(재시도/쿨다운/부분완료)이 흩어져 있음.
  - `error_handler.py`가 표준화된 사용처로 확산되지 않음.

* 개선 방안:
  - 표준 데코레이터 `@handle_task_errors` 도입: CancelledError, SoftTimeLimitExceeded, 일반 예외 별 처리 공통화.
  - API 호출/파일/풋프린트 에러는 `ErrorHandler`의 카테고리 함수로 변환하여 응답 페이로드/로그 일관화.
  - 사용자 취소/토큰 부족 등 “협업 취소” 시나리오는 `CancellationService` + 명시적 예외로 통일.

* 기대 효과:
  - 예외 흐름이 명확해지고, 관측 가능성(로그/메트릭) 증가, 회귀 버그 감소.

* 코드 예시:
  ```python
  # server/utils/errors.py
  from functools import wraps
  from celery.exceptions import Ignore, SoftTimeLimitExceeded
  from pdf_processor.utils.exceptions import CancelledError
  from error_handler import ErrorHandler

  def handle_task_errors(func):
      @wraps(func)
      def wrapper(*args, **kwargs):
          try:
              return func(*args, **kwargs)
          except CancelledError:
              # progress.finalize("취소됨") 등 공통 처리 + REVOKED 상태 업데이트
              raise Ignore()
          except SoftTimeLimitExceeded:
              # progress.finalize("취소됨") + REVOKED(timeout)
              raise Ignore()
          except Exception as e:
              ErrorHandler.log_exception(func.__name__, e, debug=False)
              raise
      return wrapper
  ```

---

## 4. 추가 개선 사항

- 로깅 표준화: `logger.info/debug/warn/error`에 컨텍스트 포함(job_id, mode, file, chunk idx, api_key_idx). JSON 로깅 옵션과 레벨 가이드 설정.
- 성능/안정성:
  - `tasks.py`에서 중복 TTL refresh 제거, 파일 루프마다 개별 refresh 대신 배치 `refresh_ttls` 우선.
  - 멀티-API 병렬도는 `settings.GEMINI_PER_KEY_CONCURRENCY`로 중앙 제어.
  - `PDFOperations` 호출에서 반복 I/O 최소화(페이지 카운트 캐시 활용).
- 테스트 전략:
  - 엔드포인트 스모크 테스트(`tests/test_routes_smoke.py`): 업로드→상태→결과 파일 존재 확인 루틴.
  - 분석 병합/파서 테스트(`tests/test_parsers_merge.py`): 최소 JSON 샘플로 머지/필터링 경계값 검증.
  - PDFCreator 유닛 테스트: `decide_end_page` / `resolver`의 규칙 테스트.
- 문서화/Examples:
  - `server/routes/_helpers.py` 사용 가이드, `services/storage/*` 각 컴포넌트 책임 정의.
  - 운영 가이드: 토큰 정책, 프리플라이트→스타트 플로우, 취소/클린업 시나리오.

---

## 5. 결론 및 권장 진행 순서

우선순위와 단계적 적용을 권장합니다.

1) 토대 정리(1주)
   - Pydantic Settings 도입(`settings.py`) 및 `config.py`/`celeryconfig.py` 주입화
   - Storage 분리: FileStorage/ResultStore/ProgressTracker/JobRepository 등 기본 4축 도입
   - 공통 라우트 헬퍼 작성 및 `/analyze/*`, `/preflight/*` 내부 치환

2) 작업/오케스트레이션(1~2주)
   - Celery 작업 제너릭화(+ 에러 데코레이터) 및 멀티-API 오케스트레이션 제너릭 함수 추출
   - 진행률/취소/경고 집계 공통화, TTL/복원/클린업 정리

3) PDF 생성 파이프라인(1~2주)
   - Resolver 모듈 이관, `extract_jokbo_question` 파이프라인 분해 또는 전략화
   - 단위 테스트 추가 및 리그레션 스냅샷 생성

4) 에러/로깅/문서(1주)
   - 중앙 에러 핸들링 적용, 구조적 로깅 도입, 운영 가이드/개발자 가이드 보완

이 순서는 리스크를 낮추고 가시적인 품질 향상을 빠르게 제공하도록 설계되었습니다. 단계별로 PR을 작게 유지하고, 스모크 테스트와 실제 PDF 소량으로 수시 검증하시길 권장드립니다.

---

## 6. 진행 상황 업데이트 및 코멘트 (2025-09-08)

최근 반영 사항 요약:

- Preflight 라우트 공통화: `server/routes/_helpers.py`에 `save_files_metadata_with_info`를 추가해 `/preflight/jokbo-centric`, `/preflight/lesson-centric`, `/preflight/partial-jokbo`, `/preflight/exam-only`의 업로드/TTL/메타 저장 로직을 공통 처리했습니다. `exam-only`는 그룹 수(`question_groups`) 추정 로직을 빌더로 주입하여 기존 동작을 유지합니다.
- Celery 작업 전략화(1차): `tasks.py`에 `ModeStrategy`와 `run_analysis_task`를 도입했습니다. `run_jokbo_analysis`/`run_lesson_analysis`는 전략 경로로 위임하며, 문제 발생 시 레거시 구현으로 폴백합니다. TTL 중복 갱신 제거 등 공통 흐름 정리.
- 진행률 산정 일관화: 청크 수 계산을 "Primary 개수 × Lesson 청크 합"으로 일원화하는 헬퍼를 추가해 양 모드의 기존 정책을 유지하면서 코드 중복을 제거했습니다.

주의/검증 포인트:

- API 표면: 엔드포인트 경로/파라미터/응답 형태는 변경하지 않았습니다. 프런트엔드와의 호환성에 영향이 없도록 유지했습니다.
- Preflight metadata: `preflight_files`와 `preflight_stats`가 메타데이터에 저장되도록 정리되었습니다. `/jobs/{job_id}/start` 플로우는 그대로 동작합니다.
- 작업 폴백: 새 전략 경로에 예기치 못한 문제가 생겨도 레거시 경로가 살아있어 리스크를 줄였습니다. 추후 안정화가 되면 레거시 제거를 권장합니다.

다음 단계 제안:

- 에러 핸들링 데코레이터 도입: `CancelledError`/`SoftTimeLimitExceeded`/일반 예외 공통 처리 데코레이터를 추가하고 작업에 적용.
- Storage 서비스 래퍼 전환: 라우트/작업에서 `StorageManager` 직접 호출 대신 `StorageRegistry`의 `files/results/progress/jobs`로 점진적 이관.
- 배치 분석 정리: `tasks.batch_analyze_single`와 `/analyze/batch`도 전략 기반 공통 경로 일부를 사용하도록 정리.
- PDFCreator 파이프라인화: 경로 Resolver 모듈 이관 및 `extract_jokbo_question` 단계 분해.
