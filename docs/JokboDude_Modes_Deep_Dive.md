# JokboDude 4가지 모드 동작 설명서

- 버전 범위: 현재 저장소 기준
- 핵심 구성요소: 분석기(Analyzers), PDF 오케스트레이션(Core/Processor), 멀티 API 매니저(API/Multi-API), 파서(Parsers), PDF 생성기(PDFCreator), 스토리지(StorageManager), FastAPI 라우트(server/)

## 전체 개요

- 입력 업로드/보관: 업로드된 PDF는 요청 단위 job_id로 Redis에 저장하고 TTL을 가진 키로 관리한다. 처리 시에는 임시 디렉터리(TMPDIR)에 내려받아 사용한다.
- 청크 분할: 기본은 강의자료(lesson) PDF를 최대 30페이지 단위로 분할해 병렬 처리한다. 예외로 Exam-only는 “질문 그룹(예: 20문항)” 기준으로 분할한다.
- 분석 호출: Gemini API에 PDF(들) + 프롬프트를 업로드 후 JSON 응답을 받는다. 모든 업로드는 호출 종료 시 안전하게 삭제한다.
- 응답 정제/보정: JSON 파싱·복원·정규화, 페이지 오프셋 보정과 범위 클램핑, 관련성 점수 필터링, 중복 제거를 수행한다.
- 멀티 API 분산: 여러 API 키를 라운드로빈 + 쿨다운 정책으로 배분해 병렬 처리 및 장애 시 페일오버한다.
- 결과 병합: 청크별·파일별 결과를 모드별 규칙으로 병합한다.
- PDF 생성: 분석 결과를 바탕으로 최종 PDF를 만든다. 생성 중 경계(다음 문항 시작) 추정·파일명 정합성·페이지 유효성 등을 보정한다.
- 산출물 저장/정리: 결과 PDF는 결과 디렉토리에 저장하고 Redis에도 인덱스/바이너리를 보관한다. 디버그/세션 파일은 보존시간을 두고 주기적으로 정리한다.

파일 경로 레퍼런스
- 업로드/분석/머지/생성 흐름: `pdf_processor/core/processor.py:29`
- 멀티 API 분산: `pdf_processor/api/multi_api_manager.py:102`
- 결과 병합: `pdf_processor/parsers/result_merger.py:15`
- PDF 생성기: `pdf_creator.py:1`
- 응답 파서/보정: `pdf_processor/parsers/response_parser.py:1`
- 스토리지: `storage_manager.py:1`
- 라우트(분석/진행/결과): `server/routes/analyze.py:1`, `server/routes/jobs.py:1`

---

## 청크 전략과 보정

### 기본 청크 기준
- 강의자료 PDF 페이지 수가 30페이지를 초과하면 청크로 나눈다.
  - 분할 함수: `pdf_processor/pdf/operations.py:130` `split_pdf_for_chunks`
  - 결정 로직: analyzers/*.py의 `_should_chunk_lesson`에서 `page_count > 30` (예: `lesson_centric.py:109`, `jokbo_centric.py:113`)

### 청크 오프셋 보정
- Lesson-centric: 청크 내 1-based 페이지를 원본 PDF의 `(start_page-1)`만큼 오프셋 적용해 원복. 청크 범위를 벗어나는 slide는 폐기. `lesson_centric.py:118`, `lesson_centric.py:240`
- Jokbo-centric: `question.related_lesson_slides[].lesson_page`에 오프셋/클램핑 적용, 비정상 페이지 드롭 후 `(lesson_filename, lesson_page)` 단위로 중복 제거. `jokbo_centric.py:119`, `jokbo_centric.py:200`

### 병합 시 중복 제거
- Jokbo-centric: 페이지별 질문 리스트 병합 정렬 + 중복 탐지·제거. `result_merger.py:49`
- Lesson-centric: `(lesson_filename, lesson_page)` 키로 슬라이드 중복 제거. `result_merger.py:86`

### Exam-only 전용 청크
- 질문 기반 그룹(기본 20문항)으로 분할. `tasks.py:1130`, `pdf_processor/pdf/operations.py:520` `split_by_question_groups`

---

## 멀티 API 할당과 진행 방식

### 분배/스케줄링
- 키 상태 추적/쿨다운: 429(쿼터) 시 단기 쿨다운(기본 30초), 동일 오류 3회 이상 반복 시 장기 쿨다운(10분). `pdf_processor/api/multi_api_manager.py:42, 67, 75`
- 라운드로빈 + 동시성 제한: 키당 동시 처리량은 `GEMINI_PER_KEY_CONCURRENCY`(기본 1)로 제한. `_acquire_api_index*` 계열로 가용 키에서 슬롯 할당. `multi_api_manager.py:118, 169`
- 페일오버: `execute_with_failover`가 한 작업당 키를 최대 1회씩 시도. 프롬프트 블록 감지 시 “블록 모드”로 전환해 빠르게 중단. `multi_api_manager.py:153, 200`

### 작업 단위
- Lesson-centric(멀티): lesson이 청크될 경우 각 청크를 한 작업으로 분배(조합은 “jokbo × lesson청크”). `processor.py:174`
- Jokbo-centric(멀티): 모든 lesson의 청크를 전역 작업 목록으로 만들어 키 효율을 극대화. 결과는 lesson별 인덱스로 그룹화 후 병합. `processor.py:211`
- Partial-jokbo(멀티): jokbo 단위로 분배(각 jokbo는 독립 분석). `analyzers/multi_api_analyzer.py:510`
- Exam-only(멀티): jokbo 청크(질문 그룹) 단위로 분배. `tasks.py:1158`

### 진행률
- 총 청크 수 산정: (주체 파일 수 × 모든 lesson의 청크 합). `tasks.py:29`
- on_progress 콜백으로 청크 단위 완료시 증가. `multi_api_manager.py:312`, `analyzers/multi_api_analyzer.py:332`
- 100%는 finalize 시점에 고정(중간에는 최대 99%). `storage_manager.py:682`

---

## 데이터 저장 위치와 수명

### 입력/중간/결과 저장소
- 업로드 파일(입력 PDF): Redis 해시 키로 저장(HSET data + 압축 플래그), TTL 기본 24시간. `storage_manager.py:166`
  - 키 형태: `file:{job_id}:{kind}:{filename}:{hash8}`
- 임시 로컬 파일(분석용): 각 작업에서 TemporaryDirectory(TMPDIR 하위)에 다운로드 후 사용. `tasks.py:101`
- 청크 결과/디버그(디스크): `output/temp/sessions/{session_id}/chunks/{mode}-{stem}/chunk_###.json`. `analyzers/multi_api_analyzer.py:377`
- API 원문 응답(디스크): `output/debug/{timestamp}_{mode}_{names}_response.json`. `analyzers/base.py:58`
- 디버그 JSON(옵션, Redis): `debug:{job_id}:{name}`. `analyzers/jokbo_centric.py:414`, `lesson_centric.py:145`
- 결과 PDF(디스크+Redis): `results/{job_id}/{filename}`에 저장하면서 Redis에도 결과 바이너리+경로 인덱싱. `storage_manager.py:292`

### 정리(쓰기/삭제) 정책
- Gemini 업로드 파일: 호출 종료 즉시 삭제(추적된 파일만). `analyzers/base.py:214`, `api/file_manager.py:102`
- 세션/디버그: 서버 시작 시 보존 기간(기본 디버그 168h, 결과 720h) 초과분 정리. `server/main.py:18`
- 전체 작업 정리: `/jobs/{job_id}` 삭제 시 Redis 키+로컬 결과 디렉토리 제거. `server/routes/jobs.py:268`, `storage_manager.py:548`

---

## 모드 A: 족보 중심(Jokbo-Centric)

### 입력/출력
- 입력: jokbo N개, lesson M개
- 출력: jokbo 파일별로 PDF 1개씩 생성(파일명: `jokbo_centric_{jokbo_stem}_all_lessons.pdf`)

### 처리 흐름(싱글 키)
- 각 lesson을 필요시 청크로 분할 → 각 lesson(또는 청크)별로 jokbo와 함께 API 호출 → 모든 lesson 결과를 jokbo 기준으로 병합.
  - 시작점: `pdf_processor/core/processor.py:126`
  - 내부 청크 경로: `pdf_processor/analyzers/jokbo_centric.py:119`

### 처리 흐름(멀티 키)
- 모든 lesson들의 청크를 “전역 작업 목록”으로 구성해 API 키에 분산(키 효율 극대화). `processor.py:211`
- 각 청크 결과를 `(lesson_idx, result)`로 회수 → lesson별 그룹 → per-lesson 청크 병합 → 최종 jokbo 결과 병합. `processor.py:252`

### 결과 병합 규칙
- 같은 `jokbo_page` 내 질문들을 모으고, `related_lesson_slides`는 `(lesson_filename, lesson_page)` 중복 제거 후 관련성 점수 기준 정렬 및 필터링. `jokbo_centric.py:446`

### PDF 생성
- 질문 정렬/경계 추정(`next_question_start`, 페이지 내 번호 순서) 후 질문 이미지 → 관련 슬라이드 삽입 → 해설 페이지 삽입. `pdf_creator.py:861`
- `lesson_filename` 정규화 및 경로 해결(접두사 “강의자료_” 같은 AI-표기 제거). `pdf_creator.py:240`

### 보정/오류 대비
- `lesson_page` 오프셋/클램프 및 청크 범위 외 페이지 드롭. `jokbo_centric.py:200`
- 응답 품질 휴리스틱·부분 파싱·중복 제거·잘못된 페이지 필터링. `response_parser.py:640`, `response_parser.py:280`
- 파일명 정규화/일치 채택으로 잘못된 파일 참조 최소화. `pdf_creator.py:240`, `pdf_creator.py:120`
- Gemini 업로드-삭제 안전화(키별 FileManager로 교차 삭제 403 회피). `api/file_manager.py:15`

#### 다중 jokbo × 다중 lesson 상세(Q&A)
- 분석 단위 분리: 작업 수준에서 “각 jokbo 파일”을 1차 루프 주체로 삼아 완전히 독립된 분석을 수행하고 PDF를 각각 생성한다(혼선 없음). `tasks.py:164`
- 멀티 API 할당: 모든 lesson 청크를 전역 큐에 넣고 라운드로빈으로 키 배분 후 페일오버. `processor.py:211`, `multi_api_manager.py:169`
- PDF 병합 혼선 방지: `lesson_filename`을 원본으로 통일(normalize)하고, `(lesson_filename, lesson_page)` 키로 병합·중복 제거한다. `analyzers/multi_api_analyzer.py:364`, `result_merger.py:86`
- 페이지 보정: 청크 시작페이지 오프셋만큼 `lesson_page`에 더해 원본 좌표계로 환산하고, 원본 페이지 수로 클램핑한다(청크 바깥은 폐기). `jokbo_centric.py:200`
- API 오류 대비: 프롬프트 블락 감지 시 전역 블락 모드 전환(무의미한 재시도 방지), 429 쿨다운, 서버/네트워크 오류 카테고리별 재시도, 청크 재분할(토큰 초과 완화). `multi_api_manager.py:153`, `analyzers/multi_api_analyzer.py:406`

---

## 모드 B: 강의 중심(Lesson-Centric)

### 입력/출력
- 입력: lesson K개, jokbo L개
- 출력: lesson 파일별 PDF 1개씩 생성(파일명: `filtered_{lesson_stem}_all_jokbos.pdf`)

### 처리 흐름(싱글 키)
- lesson이 크면 청크 단위로 분석 → jokbo들을 순차로 연결 → 청크 병합 후 슬라이드별 관련 jokbo 질의 선별. `processor.py:103`, `lesson_centric.py:115`

### 처리 흐름(멀티 키)
- lesson이 청크되면 `청크 × jokbo` 단위로 분산(청크 재시도·실패 분할 포함). `processor.py:174`, `analyzers/multi_api_analyzer.py:293`
- 청크가 없으면 `jokbo×lesson` 쌍을 키에 분산. `processor.py:196`

### 병합 규칙(lesson 페이지 기준)
- 각 슬라이드에 대해 관련 jokbo 질문을 모으고, `min_relevance`(기본 80점) 이상만 유지. 파서 단계에서도 80 미만 제거. `processor.py:310`, `response_parser.py:680`
- 최종 PDF는 원본 lesson 페이지를 모두 포함(매칭 없으면 표시만), 슬라이드별로 상위 1개(또는 제한) 질문만 삽입. `processor.py:280`, `pdf_creator.py:834`

### 보정/오류 대비
- 청크 범위 외 `lesson_page` 폐기, 오프셋 환산. `lesson_centric.py:240`
- `jokbo_page` 유효성 검사/클램프 후 무효 질문 제거. `lesson_centric.py:101`
- JSON 복원/부분 파싱, 중복 제거, 점수 필터링. `response_parser.py:1`, `response_parser.py:640`

---

## 모드 C: Partial-Jokbo(부분 족보)

### 목적
- jokbo에서 각 문제의 페이지 범위를 추출(슬라이드 매칭 없음), 간단한 설명 포함

### 입력/출력
- 입력: jokbo P개 + lesson Q개(참조)
- 출력: 작업당 PDF 1개(`partial_jokbo.pdf`)

### 처리 흐름
- 각 jokbo를 독립적으로 분석해 `{question_number, page_start, next_question_start, explanation}` 목록을 얻는다. `analyzers/partial_jokbo.py:37`
- 멀티 키: jokbo 파일 단위로 분배, 결과를 통합. `analyzers/multi_api_analyzer.py:510`
- PDF 생성: 각 문제 영역을 크롭하여 `[문제 페이지] → [설명 페이지]` 순서로 구성. `pdf_creator.py:1126`

### 보정/오류 대비
- 페이지 경계 추정: 페이지 내 질문 번호 마커 감지(텍스트→OCR)로 시작/다음 시작 위치 추정. `pdf_processor/pdf/operations.py:700`, `pdf_processor/pdf/operations.py:770`
- 감지 실패 시 보수적 폴백(전체 페이지 포함 등)으로 누락 방지. `pdf_creator.py:984`

---

## 모드 D: Exam-Only(해설집 생성)

### 목적
- jokbo만 입력 받아, 문제·정답·해설(배경지식/오답해설 포함) 중심 PDF 생성

### 입력/출력
- 입력: jokbo S개
- 출력: jokbo 파일별 PDF 1개(파일명: `exam_only_{stem}.pdf`)

### 처리 흐름
- 질문 검출로 20문항 그룹 등의 청크화 → 청크별 분석 → 질문 목록 통합 → 문제 이미지 + 해설 페이지 구성. `tasks.py:1120`, `analyzers/exam_only.py:16`
- 청크 페이지 오프셋 적용(`page_start`, `next_question_start`). `analyzers/exam_only.py:59`

### 보정/오류 대비
- 품질 체크 후 재시도(토큰 제한/빈 응답/블락 처리), 멀티 키 페일오버, 청크 분할 재시도. `api/client.py:178`, `analyzers/multi_api_analyzer.py:406`

---

## 혼선을 막는 설계 포인트

- 파일명/경로 정규화
  - `lesson_filename`을 항상 원본 이름으로 덮어써서 tmp/접두사 이름이 PDF 병합에 영향을 주지 않도록 보정. `analyzers/multi_api_analyzer.py:364`
  - PDFCreator가 jokbo/lesson 디렉토리에서 접두사/스페이스/하이픈 제거 등 엄격한 동치 규칙으로 파일을 찾음(模糊 매칭 금지). `pdf_creator.py:240`, `pdf_creator.py:120`
- 페이지 유효성/정렬
  - 클램프, 범위 외 제거, 숫자화 후 정렬, `(lesson_filename, lesson_page)`·`(jokbo_page)` 키 기반 중복 제거. `result_merger.py:49`, `result_merger.py:86`
- 설명/정답 일관성
  - jokbo 병합 시 선택된 상위 슬라이드의 출처 설명/정답을 질문 본문에도 반영해 모순을 방지. `jokbo_centric.py:494`
- 멀티 API 순서 보존
  - 청크 결과는 `(인덱스, 결과)`로 회수하여 원래 순서대로 정렬한 뒤 병합한다. `analyzers/multi_api_analyzer.py:327`

---

## 오류·품질 대응

- JSON 파싱·복원
  - 코드펜스 제거, 스마트 따옴표→ASCII, 트레일링 콤마 제거, NaN→null, 부분 파싱(페이지 단위 복원) 등. `response_parser.py:1`
- 결과 품질 휴리스틱
  - “의심스러운 결과” 감지 시 재시도(설명/답변/슬라이드 불충분 등). `analyzers/base.py:298`, `response_parser.py:866`
- 멀티 API 페일오버
  - 키별 오류 카테고리 분류(429/401/5xx/네트워크/블락)와 쿨다운/1회 시도 제한/블락 모드. `multi_api_manager.py:153`
- 토큰 제한·긴 문서
  - 실패 청크를 절반으로 나눠 재분석한 뒤 병합(어댑티브 리트라이). `analyzers/multi_api_analyzer.py:406`
- 취소·시간 제한
  - Redis cancel 플래그를 청크·파일 경계에서 협조적으로 확인. `lesson_centric.py:124`, `jokbo_centric.py:133`
- 업로드 정리
  - 호출 후 업로드 파일 즉시 삭제, 교차 키 컨텍스트에서도 403을 성공 처리로 간주해 누수 방지. `api/file_manager.py:141`

---

## 추가로 알아두면 좋은 사항

- 관련성 임계치(min_relevance): 쿼리/폼으로 받은 값(0..110)을 분석기에 주입해 하위 점수 연결을 제거한다(기본 80). `processor.py:81`
- 진행률/토큰 회계: 총 청크에 비례해 퍼센트를 계산하며 모델에 따른 청크당 토큰 비용을 추정·차감(옵션). `storage_manager.py:706`, `server/routes/preflight.py:12`
- 환경 변수
  - `GEMINI_PER_KEY_CONCURRENCY`: 키당 동시 처리량(기본 1)
  - `GEMINI_RATE_LIMIT_COOLDOWN_SECS`: 429 쿨다운(기본 30초)
  - `FILE_TTL_SECONDS`: 업로드 TTL(기본 24h)
  - `RESULT_RETENTION_HOURS`: 결과 보존(기본 720h)
  - `GEMINI_PURGE_BEFORE_UPLOAD`: 업로드 전 전량 정리(기본 false)
- API 요약(보호된 엔드포인트, 쿠키 세션 필요)
  - 분석: `POST /analyze/jokbo-centric` | `/lesson-centric` | `/partial-jokbo` | `/exam-only`
  - 상태/진행/결과: `GET /status/{task_id}` | `GET /progress/{job_id}` | `GET /results/{job_id}`
- 디버깅 팁
  - `output/debug/` 이하 원문 응답/로그 확인
  - `output/temp/sessions/{job}/chunks/` 이하 청크별 정규화 결과 확인
  - `/admin/storage-stats`로 디스크 점검

---

## 파일/코드 레퍼런스

- PDFProcessor(오케스트레이션): `pdf_processor/core/processor.py:29`
- Jokbo-Centric Analyzer: `pdf_processor/analyzers/jokbo_centric.py:19`
- Lesson-Centric Analyzer: `pdf_processor/analyzers/lesson_centric.py:17`
- Partial-Jokbo Analyzer: `pdf_processor/analyzers/partial_jokbo.py:14`
- Exam-Only Analyzer: `pdf_processor/analyzers/exam_only.py:13`
- Multi-API Manager: `pdf_processor/api/multi_api_manager.py:102`
- Response Parser: `pdf_processor/parsers/response_parser.py:1`
- Result Merger: `pdf_processor/parsers/result_merger.py:15`
- PDF Creator: `pdf_creator.py:1`
- Storage Manager: `storage_manager.py:1`
- FastAPI Routes: `server/routes/analyze.py:1`, `server/routes/jobs.py:1`

