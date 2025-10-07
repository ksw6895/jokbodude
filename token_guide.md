# JokboDude 토큰 시스템 개편 가이드 (사후 사용량 기반)

**문서 목적**: 현재의 부정확한 '예측 기반 토큰 차감' 모델을 폐기하고, 실제 Gemini API 사용량을 **근거 데이터**로 하여 환산된 **JokboDude 내부 토큰(과금 단위)**을 **사후 차감**하는 신뢰도 높은 과금 시스템으로 전환하기 위한 기술 가이드입니다.

**핵심 목표**:

1. **정확성**: 실제 API 호출로 발생한 **사용량(usage_metadata)**을 기반으로 **JokboDude 토큰**을 환산·차감합니다.
2. **투명성**: 사용자는 작업 완료 후 소모된 **JokboDude 토큰**을 명확히 확인할 수 있습니다.
3. **단순성**: 서버 리소스(CPU, 메모리 등) 비용은 과금 모델에서 제외하고, 오직 **Gemini API 사용에 근거해 산출된 내부 토큰**에만 집중합니다.
4. **견고성**: 재시도, 실패 등 다양한 시나리오에서도 사용량이 누락되거나 중복 계산되지 않도록 합니다.

> **용어 정의**
>
> * **JokboDude 토큰(JD 토큰)**: JokboDude의 **내부 과금 단위**입니다. 충전·잔액·차감 모두 이 단위를 기준으로 표시합니다.
> * **Gemini 사용량**: API 응답의 `usage_metadata`(입력/출력/세부 토큰 카운트 등)로 관측되는 **실제 모델 사용량**입니다. **과금 그 자체가 아니라**, JD 토큰으로의 환산을 위한 **근거 데이터**입니다.

---

## Part 1: 아키텍처 변경 원칙

### 1.1. 과금 범위: JD 토큰(내부 단위) 한정

서버 운영 비용(파일 처리, 저장, CPU 등)은 Gemini API 호출 비용에 비해 매우 적으므로 과금 대상에서 제외합니다. 오직 **Gemini 사용량을 근거로 환산된 JD 토큰**만이 과금의 기준이 됩니다.

### 1.2. 사용량 측정과 환산의 기준 (Ground Truth → JD 토큰)

Gemini API 응답의 `usage_metadata`는 모델 서버가 직접 계산한 가장 정확한 **사용량 지표**입니다. **JD 토큰은 이 사용량을 ‘환산 규칙’에 따라 내부 단위로 변환한 값**입니다.

* 핵심 사용량 지표

  * `response.usage_metadata.prompt_token_count`: **입력** 토큰 수
  * `response.usage_metadata.candidates_token_count`: **출력** 토큰 수
  * `response.usage_metadata.total_token_count`: **입력+출력 합계(기본 집계 기준)**
  * *(확장 가능)* `tool_use_prompt_token_count`, `thoughts_token_count`(thinking 모델), `prompt_tokens_details`/`cache_tokens_details`/`candidates_tokens_details` 등

* **환산 원칙**

  * **입력/출력/캐시 토큰 등 세부 지표를 반영**해 JD 토큰으로 변환하는 `BillingConverter`(설계 예시 아래 참조)를 둡니다.
  * 단순 1:1이 아니라, **모델·캐시 사용 여부** 등에 따른 가중치(내부 요율표)를 적용할 수 있습니다.
  * 스트리밍 시 `usage_metadata`는 **마지막 청크**에만 포함될 수 있으므로 **스트림 종료 시점**에 최종 사용량을 집계합니다.

### 1.3. Preflight 단계의 역할 재정의

`preflight`는 더 이상 토큰 사용량을 '예측'하지 않습니다. 대신, 사용자에게 작업의 규모를 알려주고 시작 여부를 '확인'받는 단계로 역할이 변경됩니다.

* **제공 정보**: "총 X개의 파일, Y개의 페이지가 Z개의 청크로 나뉘어 처리됩니다. 분석을 시작하시겠습니까?"
* **제거 정보**: "예상 소모 토큰"과 관련된 모든 UI 및 백엔드 로직.
* **내부 검증(선택)**: `countTokens`로 **입력 길이 가드**만 수행(과금/표시에는 미사용).

---

## Part 2: 단계별 코드 구현 가이드

### Step 1: `GeminiAPIClient` 수정 - **사용량** 반환

**목표**: 모든 `generate_content` 호출이 **응답 객체**와 함께 **API 사용량(예: `total_token_count`)**을 반환합니다. (여기서 반환하는 값은 **JD 토큰이 아니라 원시 사용량**입니다.)

**대상 파일**: `pdf_processor/api/client.py`

**수정 지침**:

1. `generate_content`의 반환 타입은 `Tuple[Any, int]`로 유지하되, `int`는 **API 사용량(total_token_count)**입니다.
2. 성공 응답 직후 `response.usage_metadata.total_token_count`를 추출합니다.
3. `usage_metadata`가 비어 있으면 `0`으로 처리합니다(스트리밍 중간 청크 대비).
4. 스트리밍 사용 시 **마지막 청크에서만** 집계합니다.

```python
# In: pdf_processor/api/client.py -> GeminiAPIClient class

def generate_content(...) -> Tuple[Any, int]:  # (response, api_usage_tokens)
    # ... 기존 재시도 루프 ...
    try:
        response = fut.result(timeout=req_timeout)

        api_usage_tokens = 0
        try:
            usage = getattr(response, "usage_metadata", None)
            if usage:
                api_usage_tokens = getattr(usage, "total_token_count", 0)
        except Exception:
            api_usage_tokens = 0

        return response, api_usage_tokens

    except Exception as e:
        # ... 기존 예외 처리 ...

# 최종 실패 시
raise ContentGenerationError("...")
```

### Step 2: `BaseAnalyzer` 수정 - **JD 토큰 환산 후 누적**

**목표**: `GeminiAPIClient`가 반환한 **API 사용량**을 받아, **환산 규칙**에 따라 **JD 토큰**으로 변환한 뒤 `StorageManager`에 **작업(job)별로 누적**합니다.

**대상 파일**: `pdf_processor/analyzers/base.py`

**수정 지침**:

1. `_generate_with_quality_retry`는 `Tuple[str, int]`를 반환하되, `int`는 **API 사용량**입니다.
2. `upload_and_analyze`에서 **환산기(`BillingConverter`)**를 통해 **JD 토큰**으로 변환 → `StorageManager.record_token_usage`에 **JD 토큰**을 기록합니다.

```python
# In: pdf_processor/analyzers/base.py -> BaseAnalyzer class

def _generate_with_quality_retry(...) -> Tuple[str, int]:
    for attempt in range(1, attempts + 1):
        try:
            response, api_usage_tokens = self.api_client.generate_content(...)
            text = response.text
            # ... 품질 검사 ...
            return text, api_usage_tokens
        except Exception as e:
            # ...

def upload_and_analyze(...) -> str:
    try:
        content = [prompt] + uploaded_files
        response_text, api_usage_tokens = self._generate_with_quality_retry(content)

        # 1) API 사용량 → 2) JD 토큰 환산 → 3) 누적 기록
        try:
            sm = self._sm_cached()
            if sm and api_usage_tokens > 0:
                # 모델/캐시 여부 등 맥락을 함께 넘길 수 있도록 확장
                jd_tokens = BillingConverter.api_to_jd(
                    api_total=api_usage_tokens,
                    model=self.model_name,
                    usage_details=getattr(response, "usage_metadata", None),
                )
                if jd_tokens > 0:
                    sm.record_token_usage(self.session_id, jd_tokens)
        except Exception as e:
            logger.warning(f"Failed to record JD token usage for job {self.session_id}: {e}")

        return response_text
    finally:
        # ...
```

> **예시 환산기 인터페이스**(구현 위치 자유)
>
> ```python
> class BillingConverter:
>     @staticmethod
>     def api_to_jd(api_total: int, model: str, usage_details: Any = None) -> int:
>         """
>         내부 요율표에 따라 API 사용량을 JD 토큰으로 환산한다.
>         - 기본: total_token_count 기반
>         - 선택: cache/thoughts/tool 토큰 가중치 반영
>         - 반환: JD 토큰(정수)
>         """
>         # 예: 단순 예시(구체 요율은 ENV/DB에서 로드)
>         # return math.ceil(api_total / JD_RATE[model])
>         ...
> ```

### Step 3: `StorageManager` 기능 추가 - **JD 토큰** 기록

**목표**: `job_id`별 **JD 토큰 사용량**을 Redis에 안전하게(atomically) 누적·조회합니다.

**대상 파일**: `storage_manager.py`

**수정 지침(필드명도 ‘JD’로 명확화)**:

```python
# In: storage_manager.py -> StorageManager class

def record_token_usage(self, job_id: str, jd_tokens: int) -> None:
    """Atomically increment the JD token usage for a job."""
    if self.use_local_only or not self.redis_client or jd_tokens <= 0:
        return
    try:
        key = f"job:{job_id}:usage"
        self._with_retry(self.redis_client.hincrby, key, "jd_tokens_total", int(jd_tokens))
        self._with_retry(self.redis_client.expire, key, 2592000)  # 30-day TTL
    except Exception as e:
        logger.error(f"Failed to record JD token usage for job {job_id}: {e}")

def get_token_usage(self, job_id: str) -> int:
    """Retrieve the total JD token usage for a job."""
    if self.use_local_only or not self.redis_client:
        return 0
    try:
        key = f"job:{job_id}:usage"
        tokens = self._with_retry(self.redis_client.hget, key, "jd_tokens_total")
        return int(tokens) if tokens else 0
    except Exception:
        return 0
```

> *(선택적 고도화)* 실제 청구(외부)와 내부 과금(JD 토큰)의 **1:1 정합성**을 원할 경우, `cache_tokens_details` 등 세부 지표를 별도로 저장하고 환산기에 **할인 계수**를 반영하십시오.

### Step 4: Celery 작업 흐름 수정 - **JD 토큰 사후 차감**

**목표**: 작업이 성공적으로 완료된 후, 누적된 **JD 토큰**을 조회하여 사용자 잔액에서 차감합니다.

**대상 파일**: `tasks.py`

```python
# In: tasks.py -> run_analysis_task

# ... 성공 종료 지점 ...

try:
    metadata = storage_manager.get_job_metadata(job_id)
    user_id = metadata.get("user_id") if isinstance(metadata, dict) else None

    if user_id:
        jd_tokens_used = storage_manager.get_token_usage(job_id)
        if jd_tokens_used > 0:
            storage_manager.consume_user_tokens(user_id, jd_tokens_used)
            logger.info(f"Job {job_id} completed. Consumed {jd_tokens_used} JD tokens for user {user_id}.")
except Exception as e:
    logger.error(f"Failed to consume JD tokens for job {job_id}: {e}")

return result_payload
```

---

## Part 3: Preflight 및 UI/UX 개편 가이드

### 3.1. `preflight` 엔드포인트 간소화

**목표**: 토큰 **예측** 로직을 제거하고, 작업 규모(파일 수, 페이지 수, 청크 수) 정보만 반환합니다.

**대상 파일**: `server/routes/preflight.py`

* `_per_chunk_tokens`, `estimated_tokens`, `pct_per_chunk` 관련 로직 전부 삭제
* 응답 필드에서 `tokens_per_chunk`, `estimated_tokens`, `pct_per_chunk` 제거
* **남기는 필드**: `job_id`, `mode`, `files`, `total_chunks` 등 사실 정보만

### 3.2. 프론트엔드 UI 수정

**목표**: 예측 토큰 UI를 제거하고, 간단한 **작업 확인 모달**을 구현합니다.

**대상 파일**: `frontend/index.html`

* `preflight` 결과(파일/페이지/청크 수)만 모달로 표시
* 모달 ‘확인’ 클릭 시 `POST /jobs/{job_id}/start` 호출 → 실제 작업 시작
* 작업 종료 후 **소비된 JD 토큰**을 결과 화면에서 표시

### 3.3. 사용자 가이드 문서 업데이트

**목표**: 사용자에게 **JD 토큰 정책**을 명확히 안내합니다.

**대상 파일**: `frontend/guide.html` 등

> **FAQ: 토큰은 어떻게 계산되나요?**
> JokboDude의 **토큰(JD 토큰)**은 JokboDude의 **내부 과금 단위**입니다. 작업이 완료되면, 실제 Gemini API **사용량**(usage_metadata)을 **환산 규칙**에 따라 JD 토큰으로 변환하여 차감합니다.
> – **영향 요인**: 입력량(페이지·파일 수/텍스트 밀도), 선택 모델(내부 요율표), 출력 길이, (선택) 컨텍스트 캐싱 사용 여부 등
> – **사전 점검(선택)**: `countTokens`로 **입력 길이**만 확인하여 컨텍스트 초과를 방지할 수 있으나, **JD 토큰 산정에는 사용하지 않습니다.**

---

## 4. 최종 체크리스트

1. [ ] `GeminiAPIClient`가 `(response, api_usage_tokens)`를 반환하는가?
2. [ ] `BaseAnalyzer`가 `api_usage_tokens`를 **JD 토큰으로 환산**한 뒤 `storage_manager.record_token_usage`를 호출하는가?
3. [ ] `StorageManager`에 **JD 토큰 전용 필드**(`jd_tokens_total`)로 기록·조회가 구현되었는가?
4. [ ] Celery 태스크가 작업 성공 후 `consume_user_tokens(user_id, jd_tokens_used)`를 호출하는가?
5. [ ] `preflight` 엔드포인트에서 **예측 토큰** 관련 로직이 모두 제거되었는가?
6. [ ] 프론트엔드에서 **예측 토큰 UI**가 제거되고, **작업 확인 모달** 및 **결과 후 JD 토큰 표시**가 구현되었는가?
7. [ ] 사용자 가이드에 **“JD 토큰 = 내부 과금 단위, API 사용량을 환산해 차감”** 정책이 반영되었는가?
8. *(선택)* [ ] 모델·캐시·thinking 토큰 등 세부 사용량을 반영하는 **요율표/환산기(BillingConverter)**가 ENV/DB 기반으로 구성되었는가?
