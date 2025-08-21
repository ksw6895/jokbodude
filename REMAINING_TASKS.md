# 🔧 JokboDude 남은 작업 - 개발자 가이드

## 📋 목차
1. [현재 시스템 상태](#현재-시스템-상태)
2. [즉시 해결 필요 사항](#즉시-해결-필요-사항)
3. [기능 개선 사항](#기능-개선-사항)
4. [성능 최적화](#성능-최적화)
5. [확장 기능](#확장-기능)
6. [코드 품질 개선](#코드-품질-개선)
7. [배포 및 운영](#배포-및-운영)

## 현재 시스템 상태

### 아키텍처 개요
```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Frontend  │────▶│ Web Service │────▶│    Redis    │
│   (HTML)    │     │  (FastAPI)  │     │  (Storage)  │
└─────────────┘     └─────────────┘     └─────────────┘
                            │                    ▲
                            ▼                    │
                    ┌─────────────┐             │
                    │   Celery    │─────────────┘
                    │   Worker    │
                    └─────────────┘
```

### 핵심 파일 구조
```
jokbodude/
├── web_server.py          # FastAPI 웹 서버
├── tasks.py               # Celery 워커 태스크
├── storage_manager.py     # Redis 스토리지 관리
├── pdf_processor/         # PDF 처리 엔진
│   ├── core/
│   │   └── processor.py   # 메인 처리 로직
│   ├── analyzers/         # 분석 모드별 구현
│   ├── api/              # Gemini API 관리
│   └── parsers/          # 응답 파싱
├── pdf_creator.py        # PDF 생성
├── frontend/
│   └── index.html        # 웹 인터페이스
└── render.yaml           # Render 배포 설정
```

## 즉시 해결 필요 사항

### 1. Lesson-Centric 모드 Redis 지원 미완성 ⚠️
**파일:** `tasks.py`
**라인:** 111-150 (대략)
**문제:** `run_lesson_analysis` 함수가 아직 이전 방식 사용 중

**수정 필요 코드:**
```python
@celery_app.task(name="tasks.run_lesson_analysis")
def run_lesson_analysis(job_id: str, model_type: str = None):
    """Run lesson-centric analysis"""
    storage_manager = StorageManager()
    
    try:
        # Get job metadata from Redis (jokbo_analysis와 동일한 패턴)
        metadata = storage_manager.get_job_metadata(job_id)
        if not metadata:
            raise Exception(f"Job metadata not found for {job_id}")
        
        # 이하 jokbo_analysis와 유사하게 구현
        # lesson_paths를 순회하며 처리
        # 각 lesson에 대해 create_lesson_centric_pdf 호출
```

### 2. Web Server Lesson-Centric 엔드포인트 수정 필요 ⚠️
**파일:** `web_server.py`
**라인:** 111-140 (대략)
**문제:** `analyze_lesson_centric` 함수가 이전 방식 사용

**수정 필요:**
- jokbo-centric과 동일한 패턴으로 수정
- Redis를 통한 파일 저장 구현

### 3. 결과 파일 목록 API 수정 ⚠️
**파일:** `web_server.py`
**함수:** `list_result_files`, `get_specific_result_file`
**문제:** 아직 로컬 파일 시스템 참조

**수정 방향:**
```python
@app.get("/results/{job_id}")
def list_result_files(job_id: str):
    # Redis에서 결과 키 목록 가져오기
    result_keys = storage_manager.redis_client.keys(f"result:{job_id}:*")
    # 파일명 추출 및 반환
```

## 기능 개선 사항

### 1. 에러 핸들링 강화 🔴
**우선순위: 높음**

**현재 문제점:**
- Redis 연결 실패 시 적절한 에러 메시지 없음
- 파일 업로드 실패 시 롤백 없음
- Gemini API 실패 시 재시도 로직 부족

**개선 방안:**
```python
# storage_manager.py에 추가
class StorageManager:
    def __init__(self, redis_url: str = None):
        try:
            self.redis_client = redis.from_url(redis_url)
            self.redis_client.ping()  # 연결 테스트
        except redis.ConnectionError:
            # Fallback to local storage or raise appropriate error
            logger.error("Redis connection failed, using local storage")
            self.use_local_only = True

    def store_file_with_retry(self, file_path, job_id, file_type, max_retries=3):
        for attempt in range(max_retries):
            try:
                return self.store_file(file_path, job_id, file_type)
            except Exception as e:
                if attempt == max_retries - 1:
                    raise
                time.sleep(2 ** attempt)  # Exponential backoff
```

### 2. 프로그레스 트래킹 개선 🟡
**우선순위: 중간**

**구현 필요:**
```python
# tasks.py에 추가
def update_progress(job_id: str, progress: int, message: str):
    storage_manager.redis_client.hset(
        f"progress:{job_id}",
        mapping={
            "progress": progress,
            "message": message,
            "timestamp": datetime.now().isoformat()
        }
    )

# 처리 중 호출
update_progress(job_id, 25, "파일 분석 시작")
update_progress(job_id, 50, "AI 처리 중")
update_progress(job_id, 75, "PDF 생성 중")
update_progress(job_id, 100, "완료")
```

**Frontend 업데이트:**
```javascript
// 실시간 진행률 확인
async function checkProgress(jobId) {
    const res = await fetch(`/progress/${jobId}`);
    const data = await res.json();
    updateProgressBar(data.progress, data.message);
}
```

### 3. 파일 검증 강화 🟡
**우선순위: 중간**

**추가 필요 검증:**
- PDF 파일 유효성 (실제 PDF인지)
- 페이지 수 제한 (예: 500페이지)
- 파일명 sanitization
- 중복 파일 체크

```python
# web_server.py에 추가
import PyPDF2

def validate_pdf(file_content: bytes) -> bool:
    try:
        pdf = PyPDF2.PdfReader(io.BytesIO(file_content))
        if len(pdf.pages) > 500:
            raise ValueError("PDF exceeds 500 pages")
        return True
    except:
        return False
```

## 성능 최적화

### 1. Redis 메모리 최적화 🟠
**문제:** 큰 PDF 파일들이 Redis 메모리 초과 가능

**해결 방안 1: 압축 사용**
```python
import zlib

def store_file_compressed(self, file_path: Path, job_id: str, file_type: str):
    with open(file_path, 'rb') as f:
        content = f.read()
    
    # 압축
    compressed = zlib.compress(content, level=6)
    compression_ratio = len(compressed) / len(content)
    
    if compression_ratio < 0.9:  # 10% 이상 압축되면 사용
        self.redis_client.hset(
            f"file:{job_id}:{file_type}",
            mapping={
                "data": compressed,
                "compressed": "true",
                "original_size": len(content)
            }
        )
```

**해결 방안 2: 청크 분할**
```python
def store_large_file(self, file_path: Path, job_id: str, chunk_size=5*1024*1024):
    # 5MB 청크로 분할
    chunks = []
    with open(file_path, 'rb') as f:
        while chunk := f.read(chunk_size):
            chunk_id = str(uuid.uuid4())
            self.redis_client.setex(f"chunk:{chunk_id}", 3600, chunk)
            chunks.append(chunk_id)
    
    # 메타데이터 저장
    self.redis_client.hset(
        f"file:{job_id}:{file_path.name}",
        mapping={"chunks": json.dumps(chunks)}
    )
```

### 2. 병렬 처리 개선 🟠
**현재:** 순차 처리
**개선:** 동시 처리

```python
# tasks.py 개선
from concurrent.futures import ThreadPoolExecutor

def process_multiple_files(file_paths, processor):
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = []
        for path in file_paths:
            future = executor.submit(process_single_file, path, processor)
            futures.append(future)
        
        results = [f.result() for f in futures]
    return results
```

### 3. 캐싱 전략 🟢
**우선순위: 낮음**

```python
# 결과 캐싱
def get_cached_result(file_hash: str):
    cached = redis_client.get(f"cache:result:{file_hash}")
    if cached:
        logger.info(f"Cache hit for {file_hash}")
        return cached
    return None

# 파일 해시 생성
def get_file_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
```

## 확장 기능

### 1. S3/R2 스토리지 지원 🌟
**장점:** 대용량 파일, 영구 저장, 비용 효율

```python
# storage_backends.py
import boto3

class S3StorageBackend:
    def __init__(self, bucket_name, aws_access_key, aws_secret_key):
        self.s3 = boto3.client('s3', 
            aws_access_key_id=aws_access_key,
            aws_secret_access_key=aws_secret_key
        )
        self.bucket = bucket_name
    
    def store_file(self, file_path: Path, key: str):
        self.s3.upload_file(str(file_path), self.bucket, key)
        return f"s3://{self.bucket}/{key}"
    
    def get_file(self, key: str) -> bytes:
        response = self.s3.get_object(Bucket=self.bucket, Key=key)
        return response['Body'].read()
```

### 2. 사용자 인증 시스템 🌟
```python
# auth.py
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
import jwt

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

async def get_current_user(token: str = Depends(oauth2_scheme)):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        user_id = payload.get("sub")
        if user_id is None:
            raise HTTPException(status_code=401)
        return user_id
    except jwt.JWTError:
        raise HTTPException(status_code=401)

# 엔드포인트에 적용
@app.post("/analyze/jokbo-centric")
async def analyze_jokbo_centric(
    current_user: str = Depends(get_current_user),
    ...
):
```

### 3. 웹훅 알림 🌟
```python
# notifications.py
import httpx

async def send_webhook(url: str, job_id: str, status: str):
    async with httpx.AsyncClient() as client:
        await client.post(url, json={
            "job_id": job_id,
            "status": status,
            "timestamp": datetime.now().isoformat()
        })

# tasks.py에서 사용
async def notify_completion(job_id: str):
    webhook_url = storage_manager.get_job_metadata(job_id).get("webhook_url")
    if webhook_url:
        await send_webhook(webhook_url, job_id, "completed")
```

### 4. 배치 처리 API 🌟
```python
@app.post("/analyze/batch")
async def analyze_batch(
    batch_file: UploadFile = File(...),
    model: str = Query("flash")
):
    # CSV 또는 JSON으로 여러 작업 정의
    batch_data = parse_batch_file(batch_file)
    
    tasks = []
    for job in batch_data:
        task = celery_app.send_task(
            "tasks.run_batch_analysis",
            args=[job]
        )
        tasks.append(task.id)
    
    return {"batch_id": str(uuid.uuid4()), "tasks": tasks}
```

## 코드 품질 개선

### 1. 타입 힌팅 완성 🔵
```python
from typing import Dict, List, Optional, Union, Any
from pathlib import Path

def process_file(
    file_path: Union[str, Path],
    options: Optional[Dict[str, Any]] = None
) -> Dict[str, Union[str, int, List[str]]]:
    ...
```

### 2. 로깅 시스템 개선 🔵
```python
# logging_config.py
import logging
from logging.handlers import RotatingFileHandler

def setup_logging():
    logger = logging.getLogger("jokbodude")
    logger.setLevel(logging.INFO)
    
    # 파일 핸들러
    file_handler = RotatingFileHandler(
        "jokbodude.log",
        maxBytes=10485760,  # 10MB
        backupCount=5
    )
    
    # 포맷터
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    file_handler.setFormatter(formatter)
    
    logger.addHandler(file_handler)
    return logger
```

### 3. 테스트 코드 작성 🔵
```python
# tests/test_storage_manager.py
import pytest
from storage_manager import StorageManager

@pytest.fixture
def storage_manager():
    return StorageManager("redis://localhost:6379/1")  # Test DB

def test_store_and_retrieve_file(storage_manager, tmp_path):
    # 테스트 파일 생성
    test_file = tmp_path / "test.pdf"
    test_file.write_bytes(b"test content")
    
    # 저장
    key = storage_manager.store_file(test_file, "test_job", "test")
    
    # 검색
    content = storage_manager.get_file(key)
    assert content == b"test content"

def test_job_metadata(storage_manager):
    metadata = {"test": "data"}
    storage_manager.store_job_metadata("test_job", metadata)
    
    retrieved = storage_manager.get_job_metadata("test_job")
    assert retrieved == metadata
```

### 4. 문서화 개선 🔵
```python
def analyze_jokbo_centric(
    lesson_paths: List[str],
    jokbo_path: str
) -> Dict[str, Any]:
    """
    족보 중심 분석을 수행합니다.
    
    Args:
        lesson_paths: 강의자료 PDF 파일 경로 목록
        jokbo_path: 분석할 족보 PDF 파일 경로
    
    Returns:
        분석 결과 딕셔너리:
        {
            "connections": [...],  # 연결 정보
            "error": str,          # 에러 발생 시
            "metadata": {...}      # 메타데이터
        }
    
    Raises:
        FileNotFoundError: 파일을 찾을 수 없을 때
        ValueError: 잘못된 PDF 파일일 때
        APIError: Gemini API 호출 실패 시
    
    Example:
        >>> result = analyze_jokbo_centric(
        ...     ["lecture1.pdf", "lecture2.pdf"],
        ...     "exam.pdf"
        ... )
        >>> print(result["connections"])
    """
```

## 배포 및 운영

### 1. 환경별 설정 분리 🟣
```python
# config/settings.py
from pydantic import BaseSettings

class Settings(BaseSettings):
    # 공통 설정
    app_name: str = "JokboDude"
    
    # 환경별 설정
    redis_url: str
    gemini_api_key: str
    storage_backend: str = "redis"  # redis, s3, local
    
    # 개발/프로덕션 구분
    debug: bool = False
    
    class Config:
        env_file = ".env"
        env_prefix = "JOKBO_"

# 환경별 파일
# .env.development
# .env.production
# .env.test
```

### 2. 헬스체크 엔드포인트 🟣
```python
@app.get("/health")
async def health_check():
    checks = {
        "web": "healthy",
        "redis": "unknown",
        "worker": "unknown"
    }
    
    # Redis 체크
    try:
        storage_manager.redis_client.ping()
        checks["redis"] = "healthy"
    except:
        checks["redis"] = "unhealthy"
    
    # Worker 체크
    try:
        result = celery_app.control.inspect().active()
        checks["worker"] = "healthy" if result else "unhealthy"
    except:
        checks["worker"] = "unhealthy"
    
    status_code = 200 if all(v == "healthy" for v in checks.values()) else 503
    return JSONResponse(content=checks, status_code=status_code)
```

### 3. 메트릭 수집 🟣
```python
# metrics.py
from prometheus_client import Counter, Histogram, generate_latest

# 메트릭 정의
job_counter = Counter('jokbodude_jobs_total', 'Total number of jobs', ['status'])
processing_time = Histogram('jokbodude_processing_seconds', 'Processing time')

# 사용
job_counter.labels(status='started').inc()

@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type="text/plain")
```

### 4. 자동 정리 작업 🟣
```python
# cleanup_task.py
@celery_app.task
def cleanup_old_data():
    """1시간마다 실행되는 정리 작업"""
    # 오래된 Redis 키 삭제
    for key in redis_client.scan_iter("file:*"):
        ttl = redis_client.ttl(key)
        if ttl == -1:  # TTL 없음
            redis_client.expire(key, 3600)
    
    # 오래된 로컬 파일 삭제
    storage_path = Path("/tmp/storage")
    for job_dir in storage_path.iterdir():
        if job_dir.is_dir():
            age = time.time() - job_dir.stat().st_mtime
            if age > 7200:  # 2시간 이상
                shutil.rmtree(job_dir)

# Celery beat 스케줄
from celery.schedules import crontab

celery_app.conf.beat_schedule = {
    'cleanup-old-data': {
        'task': 'cleanup_task.cleanup_old_data',
        'schedule': crontab(minute=0),  # 매 시간
    },
}
```

## 보안 개선 사항

### 1. 입력 검증 강화 🔒
```python
from pydantic import BaseModel, validator

class AnalysisRequest(BaseModel):
    model: str
    webhook_url: Optional[str] = None
    
    @validator('model')
    def validate_model(cls, v):
        if v != 'flash':
            raise ValueError('Invalid model (only flash supported)')
        return v
    
    @validator('webhook_url')
    def validate_webhook(cls, v):
        if v and not v.startswith('https://'):
            raise ValueError('Webhook must use HTTPS')
        return v
```

### 2. Rate Limiting 🔒
```python
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["100 per hour"]
)

@app.post("/analyze/jokbo-centric")
@limiter.limit("10 per minute")
async def analyze_jokbo_centric(...):
```

### 3. 파일 업로드 보안 🔒
```python
ALLOWED_EXTENSIONS = {'.pdf'}
BLOCKED_FILENAMES = {'../', '..\\', '/etc/', 'C:\\'}

def secure_filename(filename: str) -> str:
    # 위험한 문자 제거
    filename = re.sub(r'[^\w\s.-]', '', filename)
    
    # 경로 탐색 방지
    for blocked in BLOCKED_FILENAMES:
        if blocked in filename:
            raise ValueError("Invalid filename")
    
    # 확장자 확인
    if not any(filename.endswith(ext) for ext in ALLOWED_EXTENSIONS):
        raise ValueError("Only PDF files allowed")
    
    return filename
```

## 모니터링 및 알림

### 1. Sentry 통합 📊
```python
import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.celery import CeleryIntegration

sentry_sdk.init(
    dsn="your-sentry-dsn",
    integrations=[
        FastApiIntegration(transaction_style="endpoint"),
        CeleryIntegration()
    ],
    traces_sample_rate=0.1,
)
```

### 2. 사용량 추적 📊
```python
# usage_tracking.py
def track_usage(user_id: str, job_id: str, file_count: int, total_size: int):
    redis_client.hincrby(f"usage:{user_id}:daily", "jobs", 1)
    redis_client.hincrby(f"usage:{user_id}:daily", "files", file_count)
    redis_client.hincrby(f"usage:{user_id}:daily", "bytes", total_size)
    
    # 일일 리셋
    redis_client.expire(f"usage:{user_id}:daily", 86400)

def check_usage_limit(user_id: str) -> bool:
    usage = redis_client.hgetall(f"usage:{user_id}:daily")
    
    # 기본 제한
    MAX_DAILY_JOBS = 100
    MAX_DAILY_BYTES = 500 * 1024 * 1024  # 500MB
    
    if int(usage.get(b'jobs', 0)) >= MAX_DAILY_JOBS:
        return False
    if int(usage.get(b'bytes', 0)) >= MAX_DAILY_BYTES:
        return False
    
    return True
```

## 긴급 수정 필요 체크리스트

### 즉시 수정 (배포 전 필수)
- [ ] `tasks.py`의 `run_lesson_analysis` Redis 지원
- [ ] `web_server.py`의 `analyze_lesson_centric` Redis 지원
- [ ] 결과 다운로드 API Redis 대응

### 1주일 내 수정 권장
- [ ] 에러 핸들링 강화
- [ ] 진행률 트래킹 구현
- [ ] 파일 검증 강화
- [ ] 헬스체크 엔드포인트

### 장기 개선 사항
- [ ] S3/R2 스토리지 지원
- [ ] 사용자 인증 시스템
- [ ] 테스트 코드 작성
- [ ] 모니터링 통합

## 개발 환경 설정

### 로컬 개발 환경
```bash
# Redis 실행
docker run -d -p 6379:6379 redis:alpine

# 환경변수 설정
export REDIS_URL=redis://localhost:6379/0
export GEMINI_API_KEY=your_key_here
export RENDER_STORAGE_PATH=/tmp/storage

# 서비스 실행
# Terminal 1: Web
uvicorn web_server:app --reload

# Terminal 2: Worker
celery -A tasks worker --loglevel=info

# Terminal 3: Redis Monitor
redis-cli monitor
```

### 디버깅 팁
1. Redis 데이터 확인: `redis-cli keys "*"`
2. Celery 태스크 상태: `celery -A tasks inspect active`
3. FastAPI 문서: `http://localhost:8000/docs`

---

**작성일:** 2025-08-11
**버전:** 1.0.0
**작성자:** Claude (Anthropic)
**대상:** 차세대 AI 코딩 에이전트
