# JokboDude 확장성 진단 보고서

## 개요 (Executive Summary)
- **Redis에 대한 과도한 파일 의존성**: 현재 업로드/결과 파일을 Redis 해시에 그대로 저장하며, 객체 스토리지 설정이 빠지면 수백 MB~GB의 데이터를 Redis가 직접 보유하게 된다. 저장/조회 시 동기식 블로킹 I/O와 재시도 루프가 FastAPI 이벤트 루프를 붙잡고 있어 동시 사용자 증가 시 웹 레이어가 쉽게 교착된다.【F:storage_manager.py†L197-L306】【F:server/routes/_helpers.py†L79-L157】
- **단일 워커·직렬 처리에 따른 처리량 한계**: Render 배포 설정이 웹 1대·워커 1대(동시 작업 1개)이며, 멀티 API 키 매니저 또한 기본적으로 키당 동시 1건만 허용하도록 잠금되어 있다. 대용량 업로드가 들어오면 큐에 쌓인 다른 작업이 모두 대기하게 되고, Noisy Neighbor 상황이 빈번해질 수 있다.【F:render.yaml†L47-L90】【F:pdf_processor/api/multi_api_manager.py†L119-L213】
- **PDF 파이프라인의 반복 연산과 메모리 관리 미비**: 각 분석 작업이 매번 PDF 페이지 수/청크를 다시 계산하기 위해 파일을 반복해서 열고, 전역 PDF 캐시에는 상한선이 없어 워커 프로세스 메모리가 선형으로 증가한다. 캐시 정리 전략 및 청크 메타데이터 재사용이 필요하다.【F:tasks.py†L35-L214】【F:pdf_processor/pdf/operations.py†L411-L464】【F:pdf_processor/pdf/cache.py†L17-L160】

## I. 코드 내부 문제 분석 (Code-Level Scalability)
### 1. 동시성 및 병렬 처리
- **웹 업로드 경로의 블로킹 I/O**: 업로드된 파일을 `await f.read()`로 메모리에 올린 뒤 동기식 `write_bytes`, `StorageManager.store_file`을 호출한다. `store_file`은 로컬 파일을 다시 읽고 Redis에 `hset`/`expire`를 수행하는 동안 `time.sleep` 기반 재시도가 포함돼 있다. FastAPI의 async 엔드포인트에서 이러한 블로킹 호출이 증가하면 이벤트 루프가 막혀 다른 요청을 처리하지 못한다.【F:server/routes/_helpers.py†L107-L136】【F:storage_manager.py†L197-L263】
  - **개선**: 업로드 수신을 백그라운드 작업 큐로 넘기거나 `run_in_executor`/Streaming을 사용해 파일 쓰기와 Redis 업로드를 별도 스레드로 오프로드한다. 또한 Redis 대신 객체 스토리지에 직접 스트리밍 업로드하도록 변경하면 Redis 호출을 제거할 수 있다.
- **멀티 API 키 매니저의 잠금 구조**: `MultiAPIManager`는 `Condition`과 `per_key_limit`(기본 1)로 키당 단일 작업만 허용하며, 실패 후 60초까지 `wait()`하면서 전체 작업을 블로킹한다. 동시에 `_global_tried_indices`가 활성화되면 동일 작업에서 재시도 가능한 키가 빠르게 고갈된다.【F:pdf_processor/api/multi_api_manager.py†L133-L213】
  - **개선**: `per_key_limit` 기본값을 환경설정 기반으로 늘리고(예: 2~3), 장기 `wait` 대신 즉시 다른 키로 넘어가도록 비동기 큐/비블로킹 구조로 재작성한다. 실패 시 키당 서킷 브레이커를 두고, 전역 잠금 없이 asyncio.Queue 또는 Celery 내 worker 수준에서 분산시키는 것이 바람직하다. 최신 코드베이스는 `google-genai` SDK로 이미 전환되어 있으며, `GEMINI_PER_KEY_CONCURRENCY` 환경변수로 키당 동시 처리 제한을 주입하도록 유지돼 있으므로(웹/워커 모두 동일 변수 사용) 값 상향 시 SDK와 충돌하지 않는다.【F:requirements.txt†L1-L4】【F:pdf_processor/api/client.py†L1-L128】【F:settings.py†L19-L91】
- **Celery 워커 병렬성 부족**: Render 설정에서 `CELERY_CONCURRENCY`를 1로 고정해 단일 작업만 수행한다. 장시간 분석이 실행되면 다른 작업은 모두 큐에서 대기한다.【F:render.yaml†L47-L90】
  - **개선**: CPU/메모리 한도를 고려해 최소 4~6 프로세스로 확장하고, 작업 유형별로 큐를 분리(업로드 정규화, 분석, PDF 생성 등)하여 짧은 작업이 긴 작업에 가로막히지 않도록 한다. `worker_prefetch_multiplier`가 1이므로 큐 분리 후에도 공정성은 유지된다.【F:celeryconfig.py†L59-L154】

### 2. 성능 병목
- **청크 수 재계산의 중복 비용**: `_compute_total_chunks`와 진행률 초기화 구간에서 `split_pdf_for_chunks`를 반복 호출해 매번 PDF 파일을 열어 페이지 수를 계산한다. 한 작업에서 동일한 교안 PDF를 여러 번 열어 O(n×m) 비용이 발생한다.【F:tasks.py†L35-L213】【F:pdf_processor/pdf/operations.py†L411-L464】
  - **개선**: 업로드 시 페이지 수와 청크 정보를 미리 계산해 메타데이터에 저장하거나, `PDFOperations.get_page_count` 결과를 Redis/메모리에 캐시하여 반복 접근을 줄인다.
- **PDF 캐시의 무제한 성장**: `PDFCache` 전역 인스턴스는 LRU/상한이 없고, 워커가 처리하는 모든 PDF 핸들이 닫히지 않으면 메모리를 계속 소비한다.【F:pdf_processor/pdf/cache.py†L17-L160】
  - **개선**: 캐시 항목 수 또는 총 페이지 수에 상한을 두고 LRU 정책을 적용한다. 작업 종료 후 `processor.cleanup_session()`에서 명시적으로 캐시를 비우거나 reference counting을 도입한다.
- **결과 저장의 중복 I/O**: `store_result`는 결과 PDF를 다시 읽어 Redis에 올린다. 대형 파일이 많아지면 Redis I/O와 메모리 압박이 심해진다.【F:storage_manager.py†L487-L575】
  - **개선**: 결과는 반드시 객체 스토리지(R2 등)에 업로드하고 Redis에는 포인터만 저장한다. 이미 제공된 R2 마이그레이션 가이드에 따라 2단계 저장을 완전히 제거한다.【F:docs/R2_MIGRATION.md†L1-L110】

### 3. 리소스 관리
- **Redis를 사실상 파일 스토리지로 사용**: 업로드 파일을 그대로 Redis 해시에 넣고 TTL만 부여한다. Redis 메모리 한계를 초과하면 eviction이 발생해 작업이 실패할 위험이 높다.【F:storage_manager.py†L197-L270】
  - **개선**: 업로드 완료 후 워커가 `save_file_locally`로 내려받았으면 Redis 키를 즉시 삭제하고, S3/R2에 업로드된 객체만 유지한다. Redis는 메타데이터와 진행률 같은 소형 데이터만 저장하도록 설계한다.
- **임시 파일 정리 주기**: 워커는 백그라운드 스레드로 `_cleanup_loop`를 실행하지만 간격이 환경 변수에 의존하고, 실패 시 재시작하지 않는다.【F:tasks.py†L730-L808】
  - **개선**: Celery 비트나 OS 스케줄러를 활용해 확정적인 정리 작업을 수행하고, 정리 실패 시 경고를 발송한다.
- **전역 StorageManager 공유**: FastAPI 애플리케이션이 단일 `StorageManager` 인스턴스를 전역 상태로 유지한다. Redis 장애 시 `use_local_only`가 `True`로 바뀌면 전체 프로세스가 로컬 저장소만 사용하게 되고, 복구되지 않을 수 있다.【F:server/main.py†L16-L64】【F:storage_manager.py†L133-L183】
  - **개선**: 요청마다 짧은-lived 클라이언트를 만들거나, 커넥션 풀을 분리하여 재연결 실패가 전체 앱 상태를 오염시키지 않도록 한다. 재연결 성공 시 `use_local_only`를 되돌리는 로직을 보완한다.

### 4. 에러 핸들링 및 복원력
- **Noisy Neighbor 위험**: 분석 작업은 하나의 Celery 태스크 내부에서 업로드된 모든 기본 문서를 순차 처리한다. 거대한 파일이 포함되면 한 태스크가 수십 분간 실행되며, 워커 수가 적으면 다른 사용자는 대기하게 된다.【F:tasks.py†L174-L310】
  - **개선**: 프라이머리 파일 단위로 서브 태스크를 분할하고, 워커가 여러 파일을 병렬 처리할 수 있도록 한다. `chord` 기반 배치 구현이 이미 있으므로 단일 작업에도 동일한 패턴을 적용한다.
- **재시도 시 장시간 슬립**: `StorageManager`의 `_with_retry`는 `time.sleep`을 사용한다. 웹 경로에서 호출되면 스레드 풀이 잠기고, Celery 태스크에서도 워커 슬롯이 놀게 된다.【F:storage_manager.py†L184-L195】
  - **개선**: 비동기 백오프(예: `asyncio.sleep`) 또는 작업 큐 재시도로 전환하고, 재시도 횟수 초과 시 즉시 실패하여 상위 레이어가 다른 워커로 재분배할 수 있도록 한다.
- **API 키 장애 전파**: 멀티 API 매니저가 특정 키를 rate limit으로 판단하면 10분 쿨다운을 걸고, 모든 키가 소진될 때까지 연쇄 재시도를 계속한다. 키 수가 적은 환경에서는 곧바로 전체 작업이 실패한다.【F:pdf_processor/api/multi_api_manager.py†L81-L205】
  - **개선**: 키 상태를 중앙에서 관찰하고, 한 키에 오류가 집중될 경우 곧바로 운영자 경고를 발송하거나 사용 중단하도록 한다. Google API의 프로젝트별 쿼터 한계를 감안해 요청 분산을 프로젝트 단위로 설계해야 한다는 점을 주지한다.

## II. 코드 외부 환경 문제 분석 (Environment & DevOps Scalability)
### 1. 서버 아키텍처 및 확장성
- **Render 단일 인스턴스 구조**: `render.yaml`은 웹/워커 각각 1대, Redis는 무료·Starter 플랜으로 정의돼 있으며, 오토스케일이나 헬스체크 기반 확장 정책이 없다. 운영 환경에서 세 서비스가 이미 Standard 플랜으로 승격된 상태라면(IaC와 대시보드 간 설정 불일치) 재배포 시 다운그레이드 위험이 크다.【F:render.yaml†L1-L90】
  - **개선**: Render의 autoscaling 플랜을 활용하거나 Kubernetes/Container 기반 배포로 이전해 HPA(수평 확장)을 구성한다. 웹 인스턴스는 최소 2대 이상으로 늘리고, Celery 워커 그룹은 분석 큐/보조 큐로 분리해 작업 우선순위를 제어한다. 또한 IaC 정의를 Standard 플랜으로 갱신하고, Terraform/Render Blueprint 등의 소스 관리와 실제 운영 설정이 일치하는지 정기적으로 검증한다.
- **큐 분리 미흡**: Celery 라우팅은 `analysis` 큐 하나에 모든 분석·집계 작업을 넣는다.【F:celeryconfig.py†L77-L154】
  - **개선**: 업로드 전처리, 분석, PDF 생성, 정리 작업을 개별 큐/워커 풀로 분리하여 리소스를 세분화하고, SLA가 다른 작업을 독립적으로 확장한다.

### 2. 스토리지 확장성 및 안정성
- **Redis 메모리 압박 및 Disk 한계**: Redis 무료 플랜은 메모리와 연결 수가 제한적이며, `render.yaml` 디스크 볼륨도 1GB에 불과하다.【F:render.yaml†L42-L45】
  - **개선**: Redis는 최소 Pro 플랜으로 업그레이드하거나 자체 관리형 Redis Cluster로 이전한다. 동시에 객체 스토리지 사용을 의무화하여 Redis와 디스크 사용량을 최소화한다.
- **R2 마이그레이션 진행 상태 확인 필요**: 문서상 R2 마이그레이션 가이드가 있으나, 실제 설정(`OBJECT_STORE`)이 비어 있으면 여전히 Redis에 파일을 저장한다.【F:storage_manager.py†L37-L70】【F:docs/R2_MIGRATION.md†L1-L110】
  - **개선**: 운영 환경에서 `OBJECT_STORE=s3`를 설정하고, 업로드/결과 파일을 즉시 R2로 보낸 뒤 Redis에는 포인터만 남긴다. 마이그레이션 스크립트로 기존 결과물을 이전하고 로컬 디스크를 비운다.

### 3. 배포 및 유지보수
- **무중단 배포 전략 부재**: 현재 Render 기본 롤링 배포를 사용하면 새로운 이미지를 올리는 동안 단일 인스턴스가 교체되어 짧은 다운타임이 발생할 수 있다.
  - **개선**: Blue-Green 또는 Canary 배포를 설정해 새 버전을 병렬로 띄운 뒤 헬스체크를 통과하면 트래픽을 전환한다. 데이터 스키마 변경 시에는 마이그레이션을 선행하고, Celery 워커 재시작을 단계적으로 수행한다.
- **환경 변수 관리**: 다중 API 키, 객체 스토리지 자격 증명 등 필수 변수의 누락 시 런타임에서 ValueError를 발생시켜 앱이 기동하지 않는다.【F:config.py†L26-L73】
  - **개선**: 배포 시 사전 검증 스크립트를 통해 필수 환경 변수를 확인하고, 누락 시 배포를 중단하도록 CI/CD 파이프라인을 구성한다.

### 4. 모니터링 및 로깅
- **중앙화된 모니터링 부족**: 코드에는 로컬 로거 외에 수집 파이프라인이 없다. Redis, Celery 큐 길이, 처리 시간, API 오류율 등을 관찰할 수 없다.
  - **개선**: Prometheus/Grafana 또는 Render Metrics를 사용해 `/metrics` 엔드포인트를 노출하고, Celery 이벤트로 작업 대기 시간을 추적한다. 로그는 ELK/Cloud Logging 등으로 집계하여 키별 실패, PDF 처리 오류를 경보로 전환한다.
- **사용자 행동 감사**: 토큰 소모/환불 로직이 존재하지만, 실패 시 단순히 로그만 남긴다.【F:tasks.py†L300-L360】
  - **개선**: 감사 로그를 별도 채널(예: BigQuery, DynamoDB)에 기록하고, 실패 시 재시도 또는 수동 조정을 위한 운영 도구를 마련한다.

## 체크리스트 (Action Item Checklist)
| 우선순위 | 항목 | 세부 작업 |
| --- | --- | --- |
| **단기 (1주 이내)** | Redis 파일 저장 해소 | 운영 환경에서 `OBJECT_STORE=s3` 설정, 업로드/결과를 R2로 전환, 워커가 다운로드 후 `file:*` 키 삭제 로직 추가.【F:storage_manager.py†L197-L270】【F:docs/R2_MIGRATION.md†L60-L110】 |
|  | 업로드 경로 비동기화 | `save_files_and_metadata` 내 파일 저장을 백그라운드 스레드/작업으로 분리하고, 요청 처리 스레드를 즉시 반환하도록 수정.【F:server/routes/_helpers.py†L107-L136】 |
|  | 워커 병렬성 증가 | Render 대시보드에서 `CELERY_CONCURRENCY`를 4 이상으로 조정하고, 워커 인스턴스를 2대 이상으로 확장. 큐 분리 계획 수립.【F:render.yaml†L53-L78】 |
| **중기 (1~4주)** | PDF 메타데이터 캐싱 | 업로드 단계에서 페이지 수/청크 메타를 저장하고, 분석 시 재사용하도록 Processor를 리팩터링.【F:tasks.py†L35-L214】 |
|  | MultiAPI 재설계 | 키당 동시 처리량을 높이고, 비블로킹 스케줄링으로 재작성. Rate limit 관측 및 경보 추가.【F:pdf_processor/api/multi_api_manager.py†L133-L213】 |
|  | 캐시/임시파일 관리 | `PDFCache`에 LRU 상한 도입, Celery 종료 시 캐시 정리. `_cleanup_loop`의 실패 감지 및 경보 추가.【F:pdf_processor/pdf/cache.py†L17-L160】【F:tasks.py†L730-L808】 |
| **장기 (4주 이상)** | 아키텍처 확장 | 오토스케일 가능한 인프라(Kubernetes 등)로 이전하고, 웹/워커/배치 서비스 분리. Redis를 전용 캐시/메타데이터 스토어로 축소하고, 장기적으로는 메시지 브로커(RabbitMQ 등) 검토.【F:render.yaml†L1-L90】 |
|  | 모니터링 및 배포 파이프라인 | CI/CD에 환경 변수 검증, 헬스체크, Blue-Green 배포 추가. Prometheus와 Alertmanager를 통해 KPI 모니터링 체계 구축.【F:config.py†L26-L73】 |
|  | 비용 최적화 | 객체 스토리지 수명 주기 정책으로 결과물을 자동 파기하고, Redis 플랜 업그레이드 대비 비용을 분석하여 멀티 테넌시(예: 사용자별 버킷, CDN) 도입 여부 평가. |
|  | Render 플랜 정합성 유지 | 운영에서 Standard 플랜을 사용한다면 `render.yaml`의 plan 값을 `standard`로 갱신하고, 배포 자동화에 drift 감지 단계를 추가.【F:render.yaml†L1-L90】 |

