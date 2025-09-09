각하, 요청하신 두 가지를 바로 수행했습니다. 핵심만 뽑아 정리하고, 이어서 현재 render 브랜치 코드 기준으로 “신 SDK(google-genai)”로의 이주 지침서를 드립니다. 가능하면 그대로 복붙-적용이 되도록 작성했습니다.

---

# 1) 구(legacy) 라이브러리 vs 신(google-genai) 라이브러리 — 차이점과 “한 키로 다중 작업”

## 요약 결론

* **구 라이브러리 `google-generativeai`는 공식적으로 지원 종료(EOL 예정)** 입니다. Google은 **신 통합 SDK `google-genai`** 로의 마이그레이션을 권장하며, 구 SDK의 **유지보수는 제한적**이고 **최종 지원 종료일은 2025-11-30**로 명시되어 있습니다. ([GitHub][1])
* **신 SDK는 중앙 `Client` 객체 기반 아키텍처**입니다. 모델 호출, 파일, 캐시, 튜닝 등을 **모두 `Client` 하위 서비스로 일원화**했고, 설치/코드 이전 가이드를 공식 문서에서 제공합니다. ([Google AI for Developers][2])
* **동시성(한 API 키로 여러 작업)**: Gemini API는 **요청 단위가 stateless(무상태)** 로 설계되어 있으며, **여러 동시 요청을 허용**합니다. 병렬 처리 자체는 문제가 아니고, \*\*제약은 “쿼터(속도/토큰/일일 제한)”\*\*입니다. ([Google AI Developers Forum][3])
* **중요한 함정(Devil’s advocate)**:

  * \*\*쿼터는 “프로젝트 기준”\*\*입니다. 같은 프로젝트에 속한 **여러 API 키를 돌려 써도 RPM/TPM 한도는 *합산* 적용**됩니다. **키 로테이션으로 한도를 “우회”하지 못합니다.** (키가 서로 **다른 프로젝트** 소속이면 분산 효과가 생김) ([Google AI for Developers][4])
  * 구 SDK는 **전역 `genai.configure(...)`** 방식이라 **스레드-세이프하지 않은 공유 상태** 문제가 있었고, 다중 키/병렬 사용 시 충돌 소지가 있었습니다. 신 SDK는 **키별 `Client()` 인스턴스**로 이 문제를 구조적으로 해소합니다. ([Google AI Developers Forum][5])
* **환경 변수와 버전**: 신 SDK는 `GEMINI_API_KEY` 또는 `GOOGLE_API_KEY`를 인식하며, 최신 버전 기준 PyPI 릴리스가 1.34.0(2025-09-09)입니다. ([PyPI][6])

## 주요 API 차이(실무 관점)

* **모델 호출**:

  * 구: `genai.GenerativeModel(...).generate_content(...)`
  * 신: `client.models.generate_content(model="...", contents=..., config=types.GenerateContentConfig(...))`  ([Google APIs][7])
* **파일 API**:

  * 구: `genai.upload_file / list_files / get_file / delete_file`
  * 신: `client.files.upload / list / get / delete` (display\_name, mime\_type 등 동일 개념 유지) ([Google APIs][7])
* **세이프티 설정**:

  * 신 SDK에서는 **문자열 enum**을 쓰는 `types.SafetySetting`이 표준입니다(예: `"HARM_CATEGORY_HATE_SPEECH"`, `"BLOCK_ONLY_HIGH"`). 잘못 지정하면 400이 납니다. ([Google APIs][7], [GitHub][8])
* **타임아웃/재시도**:

  * 신 SDK는 `types.HttpOptions(timeout=..., retry_options=...)`로 **요청 타임아웃(ms)** 과 **재시도 정책**을 제어합니다. 구 SDK 대비 **클라이언트 레벨에서 일관 제어**가 쉬워졌습니다. ([Google APIs][9])
* **비동기 병렬**:

  * `client.aio.*` 네임스페이스(HTTPX/Aiohttp 기반)로 **진짜 비동기 파이프라인** 구현이 가능, 스레드풀보다 **리소스/속도 효율**이 좋습니다. ([Google APIs][7])

---

# 2) 귀하의 render 브랜치 코드 기준 — 신 SDK 이주 “구체 지침서”

아래 변경점은 현재 저장소의 실제 코드 사용 패턴을 근거로 작성했습니다.

### 0) 브랜치·고정 버전

* 작업 브랜치 생성: `feat/migrate-google-genai`
* **requirements.txt** 교체

  * `google-generativeai==0.8.5` → **`google-genai==1.34.0`** (또는 최소 1.30+) ([PyPI][6])

---

## A. 전역 설정/모델 구성부 정리 (`config.py`)

### 무엇이 문제인가

* 현재 `config.py`는 **구 SDK 임포트/모델팩토리/전역 configure** 전제가 남아있습니다.&#x20;
* `create_model()`은 `genai.GenerativeModel`을 생성해 객체를 돌려줍니다. 신 SDK는 **모델 객체 대신 문자열 모델명 + Client 호출** 방식이 표준입니다.&#x20;

### 변경 지시

1. **임포트 교체**

```diff
- import google.generativeai as genai
+ from google import genai
+ from google.genai import types
```

(근거 코드: 현행 임포트)&#x20;

2. **클라이언트 빌더 도입**

```python
def build_client(api_key: str | None = None, *, http_timeout_ms: int = 600000) -> genai.Client:
    http_opts = types.HttpOptions(timeout=http_timeout_ms)
    return genai.Client(api_key=api_key, http_options=http_opts)
```

* 신 SDK는 `Client()`가 **키/타임아웃/리트라이**의 단일 집결점입니다. ([Google APIs][9])

3. **모델 구성은 “문자열 + config”로 반환**

```python
MODEL_NAMES = {"pro":"gemini-2.5-pro","flash":"gemini-2.5-flash","flash-lite":"gemini-2.5-flash-lite"}

def build_generate_config(*, temperature=0.3, top_p=0.95, top_k=40, max_output_tokens=100000):
    return types.GenerateContentConfig(
        temperature=temperature, top_p=top_p, top_k=top_k, max_output_tokens=max_output_tokens,
        safety_settings=[
            types.SafetySetting(category="HARM_CATEGORY_HARASSMENT",        threshold="BLOCK_NONE"),
            types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH",       threshold="BLOCK_NONE"),
            types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="BLOCK_NONE"),
            types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="BLOCK_NONE"),
        ],
    )

def resolve_model_name(model_type: str="pro") -> str:
    return MODEL_NAMES.get(model_type, MODEL_NAMES["pro"])
```

* 문자열 enum 요구사항에 맞춰 Safety 설정을 변환합니다. ([Google APIs][7], [GitHub][8])

> 주의: `genai.configure(...)` 같은 **전역 설정 코드는 삭제**하십시오. (구 SDK 유물)&#x20;

---

## B. API 클라이언트 계층 치환 (`pdf_processor/api/client.py`)

### 현 상태

* 파일 업로드/조회/삭제/생성 모두 **구 SDK 전역 함수**와 **`GenerativeModel` 인스턴스**에 의존합니다.&#x20;

### 목표

* `GeminiAPIClient`가 **신 SDK `Client`와 `model_name`, `generate_config`** 를 보유하고, 모든 호출을 `client.*` 하위 서비스로 이관.

### 변경 지시(핵심 메서드별)

1. **생성자**

```diff
-class GeminiAPIClient:
-    def __init__(self, model, api_key: Optional[str] = None):
-        self.model = model
-        self.api_key = api_key
-        self._configure_api(api_key)
+class GeminiAPIClient:
+    def __init__(self, client: genai.Client, model_name: str, gen_config: types.GenerateContentConfig):
+        self.client = client
+        self.model_name = model_name
+        self.gen_config = gen_config
```

2. **파일 업로드**

```diff
- uploaded_file = genai.upload_file(path=file_path, display_name=display_name, mime_type=mime_type)
- while uploaded_file.state.name == "PROCESSING":
-     uploaded_file = genai.get_file(uploaded_file.name)
+ uploaded_file = self.client.files.upload(file=file_path, display_name=display_name, mime_type=mime_type)
+ while uploaded_file.state == "PROCESSING":
+     uploaded_file = self.client.files.get(name=uploaded_file.name)
```

(근거: 기존 업로드 흐름)   / (신 SDK 파일 API) ([Google APIs][7])

3. **리스트/조회/삭제**

```diff
- return list(genai.list_files())
+ return [f for f in self.client.files.list()]
```

```diff
- return genai.get_file(file_name)
+ return self.client.files.get(name=file_name)
```

```diff
- genai.delete_file(file.name)
+ self.client.files.delete(name=file.name)
```

(근거: 기존 호출부)  &#x20;

4. **콘텐츠 생성**

```diff
- response = self.model.generate_content(content)
+ response = self.client.models.generate_content(
+     model=self.model_name,
+     contents=content,
+     config=self.gen_config,
+ )
```

(근거: 기존 호출부)   / (신 SDK 모델 호출) ([Google APIs][7])

> 선택: 장시간 작업은 `HttpOptions(timeout=...)`를 통해 **요청 타임아웃을 충분히 늘리고**, 필요 시 **스트리밍 API**(`generate_content_stream`)로 워커/프록시의 유휴 연결 타임아웃을 회피하십시오. ([Google APIs][9])

---

## C. 다중 키 매니저 수정 (`pdf_processor/api/multi_api_manager.py`)

### 현 상태의 위험

* 각 스레드/작업에서 `genai.configure(api_key=...)`를 **전역 변경**합니다. 이는 구 SDK에서 흔한 **경쟁 조건/혼선**의 근원입니다.&#x20;

### 변경 지시(핵심 아이디어)

* **전역 configure 제거** → **키별 `Client()` 보유**로 전환
* **모델 객체 보관 폐지** → **문자열 `model_name` + `GenerateContentConfig` 보관**
* `GeminiAPIClient`를 **(client, model\_name, gen\_config)** 로 생성

```diff
- genai.configure(api_key=api_key)
- model = genai.GenerativeModel(**model_config)
- client = GeminiAPIClient(model, api_key)
+ client_obj = build_client(api_key=api_key)             # config.py에 도입한 빌더
+ model_name = model_config["model_name"]                # 문자열
+ gen_config = build_generate_config(...)                # types.GenerateContentConfig
+ client = GeminiAPIClient(client_obj, model_name, gen_config)
```

(근거: 현행 전역 configure + 모델 생성)&#x20;

> **Devil’s advocate**: 같은 프로젝트의 여러 키를 로테이션해도 **쿼터 상 이점이 없습니다.** 키가 **서로 다른 프로젝트**에서 발급된 경우에만 병렬 처리량이 총량 증가합니다. 로드밸런싱 코드의 목적을 “**쿼터 분산**인지, **장애/과금 격리**인지” 명확히 하십시오. ([Google AI for Developers][4])

---

## D. 파일 매니저/유틸 스크립트 정리

* `pdf_processor/api/file_manager.py` 의 **전역 `genai.*` 호출**을 삭제하고, **생성자에서 `GeminiAPIClient` 주입** 후 `self.client.files.*`를 사용하십시오.&#x20;
* 최상위 스크립트 `cleanup_gemini_files.py` 및 `main.py`의 **전역 `genai.list_files/delete_file` 호출**도 `Client` 기반으로 치환하십시오. 임시 검증 용도로 `client.models.get(...)` 혹은 소규모 `generate_content(...)` 헬스체크가 간단합니다.  &#x20;

---

## E. 모델/세이프티/설정 마이그레이션 표

| 항목     | 구 SDK                                        | 신 SDK                                                                                      |
| ------ | -------------------------------------------- | ------------------------------------------------------------------------------------------ |
| 모델 호출  | `GenerativeModel(...).generate_content(...)` | `client.models.generate_content(model="...", contents=..., config=...)` ([Google APIs][7]) |
| 파일 업로드 | `genai.upload_file(path=...)`                | `client.files.upload(file=..., mime_type=..., display_name=...)` ([Google APIs][7])        |
| 파일 목록  | `list(genai.list_files())`                   | `for f in client.files.list(): ...` ([Google APIs][7])                                     |
| 세이프티   | dict 배열 허용                                   | `types.SafetySetting(category="...", threshold="...")` 문자열 enum 권장 ([Google APIs][7])      |
| 환경변수   | `GOOGLE_API_KEY` 주로 사용                       | `GEMINI_API_KEY` 또는 `GOOGLE_API_KEY` (둘 다 설정 시 GOOGLE\_API\_KEY 우선) ([PyPI][6])            |
| 타임아웃   | 래퍼/스레드풀 자체 타임아웃                              | `types.HttpOptions(timeout=ms)` 및 `retryOptions` 지원 ([Google APIs][9])                     |

---

## F. 리팩터링 후 최소 동작 점검(스모크 테스트)

1. **헬스체크** (동일 키, 동시에 N회)

```python
client = build_client(api_key=YOUR_KEY)
cfg = build_generate_config()
for _ in range(5):
    r = client.models.generate_content(model="gemini-2.5-flash", contents="ping", config=cfg)
    assert r.text and len(r.text) > 0
```

2. **파일 I/O**: 업로드→get→list→delete 순으로 1회.
3. **동시성**:

   * **스레드풀** N=8로 `client.models.generate_content` 병렬 호출 → 평균 지연/실패율 측정
   * **asyncio** 버전(`client.aio.models.generate_content`)으로 동일 실험 비교

> 네트워킹/호스팅 환경에 따라 **유휴 연결 60초 전후의 끊김**을 경험할 수 있습니다. 이 경우 스트리밍 응답 또는 타임아웃 확장/프록시 설정을 권장합니다. ([Google AI Developers Forum][10])

---

## G. 회귀 위험 포인트(체크리스트)

* `genai.configure(...)` **잔존 여부** (전역 상태 금지) — 검색어: `configure(`
* `GenerativeModel` **잔존 여부** — 검색어: `GenerativeModel(`
* 파일 API **전역 함수 호출 잔존 여부** — 검색어: `upload_file(`, `list_files(`, `get_file(`, `delete_file(`

  * 현 코드에서 전부 구 SDK 방식으로 발견됩니다. &#x20;

---

## H. 코드베이스에서 실제로 바꿔야 할 위치(증거 링크)

* **`config.py`**: 구 SDK 임포트/모델 생성부 전면 교체. &#x20;
* **`pdf_processor/api/client.py`**: 파일 API/생성 API 치환. &#x20;
* **`pdf_processor/api/file_manager.py`**: 전역 `genai.*` 제거, `GeminiAPIClient` 주입.&#x20;
* **`pdf_processor/api/multi_api_manager.py`**: 전역 `configure` 제거, `Client()` 다건 보유.&#x20;
* **`cleanup_gemini_files.py` / `main.py`**: 전역 파일 정리 호출 치환. &#x20;
* **`requirements.txt`**: 패키지 교체.&#x20;

---

## I. 운영 관점 권고(현실 검증 포함)

* **쿼터 전략**: 같은 프로젝트면 키 로테이션은 **효과 없음**. 필요 시 **프로젝트 분리**로 RPM/TPM을 수평 확장하되, 키/비용/로그 관리 복잡도가 증가합니다. ([Google AI for Developers][4])
* **장시간 작업**:

  * \*\*`HttpOptions(timeout=...)`\*\*로 서버-응답 한계에 맞게 타임아웃을 상향(예: 600\_000ms). ([Google APIs][9])
  * **스트리밍 API**를 쓰면 중간 전송으로 **프록시/런타임의 유휴 연결 차단**을 회피할 수 있습니다. ([Google APIs][7], [Google AI Developers Forum][10])
* **비동기 전환**: 대량 병렬은 `client.aio`에 **Semaphore**(동시성 창구) + **백오프/재시도**를 얹는 구성이 자원 효율/안정성에서 유리합니다. ([Google APIs][7])
* **세이프티 설정**: 문자열 enum 사용을 표준으로 통일(혼합 사용 금지) — 실수 시 400. ([GitHub][8])

---

## J. 빠른 커밋 단위 제안

1. deps: replace google-generativeai → google-genai, add types import
2. core(config): add `build_client`, `resolve_model_name`, `build_generate_config`
3. api(client): switch to `client.files.*` / `client.models.*`
4. api(file\_manager): DI로 `GeminiAPIClient` 주입
5. api(multi\_api\_manager): per-key `Client()` 보유, 전역 configure 제거
6. scripts(main/cleanup): 파일 정리 루틴 치환
7. smoke tests + 동시성 벤치 (threads vs asyncio)

---

### 부록) 왜 지금 바꿔야 하는가 — 검증된 사실

* 구 SDK 저장소는 **“deprecated”** 명시 + **EOL(2025-11-30)** 계획까지 공지되어 있습니다. 지금 바꿔야 이후 모델/기능 접근, 버그픽스, 성능개선에서 뒤처지지 않습니다. ([GitHub][1])
* 신 SDK 문서는 **GA** 상태로, 모든 예제가 신 SDK 기준이며, Developer API와 Vertex AI를 **동일 코드로 전환 가능**하게 설계되었습니다. ([Google AI for Developers][2], [Google Cloud][11])


[1]: https://github.com/google-gemini/deprecated-generative-ai-python "GitHub - google-gemini/deprecated-generative-ai-python: This SDK is now deprecated, use the new unified Google GenAI SDK."
[2]: https://ai.google.dev/gemini-api/docs/migrate "Migrate to the Google GenAI SDK  |  Gemini API  |  Google AI for Developers"
[3]: https://discuss.ai.google.dev/t/api-requests-parallel/50471 "API Requests Parallel? - Gemini API - Google AI Developers Forum"
[4]: https://ai.google.dev/gemini-api/docs/rate-limits?utm_source=chatgpt.com "Rate limits | Gemini API | Google AI for Developers"
[5]: https://discuss.ai.google.dev/t/using-multiple-api-keys-concurrently/33832 "Using multiple API keys concurrently - Gemini API - Google AI Developers Forum"
[6]: https://pypi.org/project/google-genai/ "google-genai · PyPI"
[7]: https://googleapis.github.io/python-genai/ "Google Gen AI SDK documentation"
[8]: https://github.com/google-gemini/generative-ai-python/issues/654?utm_source=chatgpt.com "GenAI Client safety settings does not support multiple SafetySetting · Issue #654 · google-gemini/generative-ai-python · GitHub"
[9]: https://googleapis.github.io/python-genai/genai.html?utm_source=chatgpt.com "Submodules - Google Gen AI SDK documentation"
[10]: https://discuss.ai.google.dev/t/60s-timeout-from-python-sdk/83274?utm_source=chatgpt.com "60s timeout from python sdk - Gemini API - Google AI Developers Forum"
[11]: https://cloud.google.com/vertex-ai/generative-ai/docs/sdks/overview?utm_source=chatgpt.com "Google Gen AI SDK | Generative AI on Vertex AI"
