R2 Migration Guide (Cloudflare R2)

개요
- 목표: 대용량 업로드/결과 PDF를 Redis에서 분리해 외부 오브젝트 스토리지(R2)에 저장하고, Redis는 메타/프로그레스/맵핑만 담당.
- 효과: Redis 메모리 사용 급감, eviction/유실 현상 방지, 비용 절감.

권장 스택
- Object storage: Cloudflare R2 (S3 API 호환)
- SDK: boto3
- Access: presigned URL로 클라이언트가 직접 다운로드 (서버는 권한 확인 + 리다이렉트)

1) Cloudflare R2 준비
- 버킷 생성: 예) `jokbodude`
- S3 API 토큰 생성(중요: 일반 Cloudflare Account API 토큰 아님)
  - 경로: R2 → S3 API Tokens → Create API Token
  - 이름: `jokbodude-service-token`
  - 권한: Object Read & Write
  - 범위: 특정 버킷 선택(`jokbodude`) — 최소 권한 원칙
  - TTL: Forever(운영에서 주기적 교체 권장)
  - Client IP Filtering: 초기에는 공란(렌더/호스팅 egress IP가 고정이 아닐 수 있음). 추후 고정 IP 환경이면 제한 적용 고려.
- Account ID 확인: R2 대시보드 상단에 표시됨. S3 endpoint 도메인에 필요.

2) 환경변수/설정
- `.env`에 아래 추가(또는 Render 대시보드에 환경변수 설정):
  - `OBJECT_STORE=s3`
  - `S3_ENDPOINT_URL=https://<ACCOUNT_ID>.r2.cloudflarestorage.com`
  - `S3_REGION=auto`
  - `S3_BUCKET=jokbodude`
  - `S3_ACCESS_KEY_ID=<R2_S3_ACCESS_KEY>`
  - `S3_SECRET_ACCESS_KEY=<R2_S3_SECRET>`
  - `SIGNED_URL_EXPIRES_SECONDS=600` (10분, 필요 시 조정)
- Redis/TTL 재조정(권장):
  - `FILE_TTL_SECONDS=7200` (포인터 TTL) — 업로드 포인터는 짧게 유지

3) 의존성
- `requirements.txt`에 `boto3>=1.34.0` 추가(이미 반영).

4) 코드 변경 개요(단계별)

4.1) S3 클라이언트 래퍼 추가
- 새 파일: `server/services/storage/object_store.py`
- 역할: 업로드/다운로드/리스트/프리사인 URL/프리픽스 삭제
- boto3 설정 예시:
  ```python
  import boto3
  from botocore.client import Config

  def make_s3_client(endpoint: str, key: str, secret: str, region: str = "auto"):
      return boto3.client(
          "s3",
          endpoint_url=endpoint,
          aws_access_key_id=key,
          aws_secret_access_key=secret,
          region_name=region,
          config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}),
      )
  ```
  - 주의: 버킷명에 `.`(dot) 포함하지 않기(virtual-host 스타일 이슈 회피).

4.2) StorageManager에 오브젝트 스토리지 모드 통합
- 파일: `storage_manager.py`
- 변경 포인트:
  - 초기화 시 `OBJECT_STORE`가 `s3`이면 S3 클라이언트 준비(환경변수로 구성).
  - `store_file` 동작 변경:
    - R2 활성화 시 파일 바이트를 R2에 업로드: key = `uploads/{job_id}/{file_type}/{filename}`
    - Redis에는 해시 바이트 대신 포인터 메타만 저장:
      - 키: `file:{job_id}:{file_type}:{filename}:{hash8}`
      - 필드: `{ "storage": "s3", "bucket": S3_BUCKET, "key": <s3_key>, "original_size": <int> }`
      - TTL: `FILE_TTL_SECONDS`(2–6h 권장)
  - `get_file`/`save_file_locally`:
    - Redis 해시에 `storage=s3`가 있으면 S3에서 다운로드하여 반환/로컬 저장
  - `store_result`:
    - 결과 PDF를 R2에 업로드: key = `results/{job_id}/{filename}`
    - Redis에는 결과 인덱스/포인터만 저장(`result:{job_id}:{filename}`, `result_path:{job_id}:{filename}`)
    - 웹 서비스는 디스크에도 저장 가능하지만(선택), 외부 스토리지에 의존하는 방향 권장
  - `list_result_files`/`delete_result`/`delete_all_results`/`cleanup_job`:
    - 기존 Redis/Disk 로직 유지 + R2 프리픽스(`uploads/{job_id}/`, `results/{job_id}/`) 삭제 추가

4.3) 다운로드 API 변경(리다이렉트)
- 파일: `server/routes/jobs.py`
- `get_result_file`, `get_specific_result_file`에서 소유자 검증 후:
  - 로컬 디스크에 있으면 기존대로 `FileResponse`
  - 없고 R2 포인터가 있으면 presigned GET URL 생성 → 302 리다이렉트(또는 `{url: ...}` JSON 반환)
- 프론트엔드 요청 형태는 그대로 유지. 브라우저는 302를 따라가서 R2에서 직접 다운로드.

4.4) 업로드 키 즉시 삭제(선택)
- 워커가 `save_file_locally(key, local_path)`로 수신 완료 후 `file:*` 키 삭제(best-effort)
- 장점: Redis 메모리 사용 급감. 현재 파이프라인에서는 로컬 파일만 사용하므로 기능 영향 없음.

5) Render 설정 업데이트
- `render.yaml` (권장) 또는 대시보드에서 env 추가:
  - `OBJECT_STORE=s3`
  - `S3_ENDPOINT_URL`, `S3_REGION`, `S3_BUCKET`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `SIGNED_URL_EXPIRES_SECONDS`
- 두 서비스(web/worker)에 동일하게 적용.

6) 데이터 마이그레이션(선택)
- 과거 결과 디스크 → R2 업로드 스크립트(관리자 실행):
  - 입력: `output/results/{job_id}/*.pdf` 경로
  - 업로드 후 Redis 인덱스(포인터) 갱신
  - 테스트 후 로컬 파일 정리

7) 테스트 체크리스트
- 배포 전
  - `/health` Redis/Worker 상태 확인
  - 환경변수 누락 시 앱이 안전하게 기동되는지(OBJECT_STORE 비활성 fallback)
  - presign 생성 유효성(만료/권한/리다이렉트 동작)
- 배포 후
  - 단일/다수 파일 업로드 → 작업 완료 → `/results/{job_id}` 목록 확인 → 다운로드 동작 확인
  - 작업 취소/삭제 시 R2 프리픽스 정리 여부 확인
  - 대량 업로드 시 Redis 메모리/eviction가 사라졌는지 관측

8) 보안/운영 팁
- 최소 권한: Admin 권한 대신 “Object Read & Write” + 특정 버킷 스코프 사용
- 키 보관: Access Key/Secret은 절대 레포에 커밋하지 말고 Render env에만 저장
- 키 롤테이션: 분기별 교체 권장(새 키 배포 → 구 키 제거)
- IP Filter: 고정 egress가 확보되면 토큰 IP 제한 도입 고려
- 버킷 공개 설정: 전체 공개 버킷은 지양. presigned URL로 제한된 시간 동안만 접근 허용

부록) presign 예시 코드
```python
from datetime import timedelta

client = make_s3_client(endpoint, key, secret, region)
url = client.generate_presigned_url(
    ClientMethod="get_object",
    Params={"Bucket": bucket, "Key": s3_key},
    ExpiresIn=expires_seconds,
)
```

변경 요약(다음 작업자 가이드)
- 새 파일: `server/services/storage/object_store.py` (boto3 래퍼)
- `storage_manager.py`에 S3 분기 추가: store_file/get_file/save_file_locally/store_result/list/delete/cleanup
- `server/routes/jobs.py` 다운로드 시 presign 리다이렉트 처리
- `.env(.example)`/`render.yaml` 환경변수 추가
- (선택) 워커에서 업로드 키 즉시 삭제 구현

