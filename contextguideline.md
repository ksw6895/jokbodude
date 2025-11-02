
# AI 컨텍스트 충돌 (Context Collision) 해결을 위한 종합 가이드라인

## 1\. 개요 (Executive Summary)

### 1.1. 문제 정의 (The "Page 10" Bug)

현재 시스템은 AI(Gemini)가 `족보 PDF`와 `강의자료 PDF`라는 두 가지 컨텍스트를 동시에 분석할 때, 두 파일의 **페이지 번호가 겹치는 경우(예: 족보 10페이지 vs 강의자료 10페이지)** 이를 명확히 구분하지 못하는 문제가 있습니다.

이로 인해 "족보 10페이지의 문제"에 대한 근거를 "강의자료 10페이지"에서 찾는 \*\*'컨텍스트 충돌(Context Collision)'\*\*이 발생하며, 이는 최종 PDF에 완전히 잘못된 강의자료 슬라이드가 연결되는 치명적인 버그로 이어집니다.

### 1.2. 근본 원인

이 문제의 근본 원인은 \*\*'모호성(Ambiguity)'\*\*입니다. AI 모델은 두 PDF를 별개의 파일이 아닌, 하나의 거대한 텍스트 컨텍스트로 인식합니다. 프롬프트에서 "10페이지"라는 참조가 등장했을 때, AI는 이것이 `족보_A.pdf`의 10페이지인지 `강의자료_B.pdf`의 10페이지인지 구분할 명확한 기준이 없습니다.

### 1.3. 핵심 해결 전략: 명시성(Explicitness)과 다층 방어(Defense in Depth)

단일 해결책에 의존하는 것은 위험합니다. 이 문제는 AI의 확률적 특성과 데이터의 모호성이 결합된 문제이므로, 여러 단계의 방어 로직을 구축해야 합니다.

1.  **[예방] 프롬프트 명확화 (Layer 1: Prevention):** AI가 애초에 두 파일을 혼동하지 않도록, 프롬프트 엔지니어링을 통해 파일의 "신원(Identity)"을 명확히 주입합니다. (가장 중요)
2.  **[탐지] 파서 레벨 검증 (Layer 2: Detection):** AI가 (불가피하게) 실수를 하더라도, 응답(JSON)을 파싱하는 단계에서 이 "오염된 데이터"를 식별하고 자동으로 필터링합니다.

-----

## 2\. [Layer 1] 예방: 프롬프트 명확화 (The Primary Fix)

**목표:** AI가 `족보_A.pdf`와 `강의자료_B.pdf`를 단순한 "두 개의 파일"이 아닌, 명확한 파일명을 가진 "별개의 개체"로 인지하게 만듭니다.

### 2.1. 방향성: 파일명 컨텍스트 주입

AI에게 전달되는 모든 프롬프트에, 현재 분석 세션에서 사용되는 **`족보`와 `강의자료`의 `display_name`을 명시적으로 주입**합니다.

현재 코드(`analyzers/base.py`의 `upload_and_analyze`)는 이미 `f"족보_{filename}"` 형식으로 `display_name`을 설정하고 있으므로, 이 이름을 프롬프트 템플릿에 그대로 활용합니다.

### 2.2. 실행 계획

1.  **`constants.py` 수정 (템플릿 준비):**

      * `COMMON_PROMPT_INTRO`와 `COMMON_WARNINGS`의 프롬프트 템플릿에 `format()` 메소드로 주입될 플레이스홀더(`{jokbo_display_name}`, `{lesson_display_name}`)를 추가합니다.
      * **목적:** AI가 지시문을 읽는 순간부터 두 파일의 역할을 명확히 인지시킵니다.
      * **예시 (`COMMON_PROMPT_INTRO`):**
        ```python
        COMMON_PROMPT_INTRO = """당신은 해당 과목의 교수입니다. 다음 두 개의 PDF가 업로드되어 있습니다:
        - 족보 PDF (파일명: {jokbo_display_name})
        - 강의자료 PDF (파일명: {lesson_display_name})
        ...
        ⚠️ 매우 중요: 문제는 오직 {jokbo_display_name} 파일에서만 추출하세요!"""
        ```
      * **예시 (`COMMON_WARNINGS`):**
        ```python
        COMMON_WARNINGS = """...
        - jokbo_page는 반드시 **{jokbo_display_name}** 파일의 페이지 번호를 정확히 기입하세요
        - lesson_page는 반드시 **{lesson_display_name}** 파일의 페이지 번호를 정확히 기입하세요
        ...
        - 예: {lesson_display_name} PDF의 3번째 페이지는 lesson_page=3
        """
        ```

2.  **`analyzers/*.py` 수정 (템플릿 주입):**

      * `jokbo_centric.py`와 `lesson_centric.py`의 `build_prompt` 함수 시그니처를 수정하여, 두 파일명(`jokbo_filename`, `lesson_filename`)을 모두 인자로 받습니다.
      * `build_prompt` 함수 내부에서 `jokbo_display_name = f"족보_{jokbo_filename}"` 등으로 변수를 생성한 뒤, `constants.py`에서 가져온 템플릿에 `.format()`을 적용하여 최종 프롬프트를 완성합니다.
      * `analyze` 메소드는 `build_prompt`를 호출할 때 이 두 파일명을 전달해야 합니다.

-----

## 3\. [Layer 2] 탐지: 파서 레벨 방어 로직 (The Safety Net)

**목표:** Layer 1의 프롬프트 지시에도 불구하고 AI가 실수를 저질러, `jokbo_page == lesson_page`인 데이터를 생성했을 경우, 이 **"오염된 슬라이드"만** 제거합니다. 족보 문제 자체는 절대 누락되어서는 안 됩니다.

### 3.1. 방향성: 데이터 정제(Sanitization) 단계에서 필터링

이 방어 로직은 `pdf_creator.py`(PDF 생성기)가 아닌, AI 응답을 처음 파싱하고 정제하는 \*\*`pdf_processor/parsers/response_parser.py`\*\*에 위치해야 합니다. 오염된 데이터가 시스템 내부로 유입되는 것을 원천 차단하는 것이 가장 안전합니다.

### 3.2. 실행 계획

1.  **`pdf_processor/parsers/response_parser.py` 수정:**
      * **함수:** `_sanitize_parsed_response`
      * **위치:** `jokbo-centric` 모드를 처리하는 로직 내부, `related_lesson_slides`를 순회(loop)하는 지점.
      * **로직:**
        1.  현재 처리 중인 `question` 객체에서 `jokbo_page` 번호(`page_no`)를 가져옵니다.
        2.  `related_lesson_slides` 배열을 순회하면서 각 `slide`의 `lesson_page`(`lp`)와 `lesson_filename`(`lf_raw`)을 가져옵니다.
        3.  **충돌 탐지 휴리스틱 (Heuristic) 적용:**
              * `if lp == page_no:` (족보 페이지와 강의 페이지 번호가 같은가?)
              * **만약 같다면,** 이 슬라이드는 AI의 혼동으로 인한 오염 데이터일 확률이 99%입니다. (두 PDF의 정확히 같은 페이지가 우연히 연관될 1%의 확률보다, AI가 모호한 "10페이지" 참조를 잘못 연결할 99%의 버그 확률을 막는 것이 더 중요합니다.)
              * **권장 조치:** `continue`를 실행하여 이 특정 슬라이드(`slide`)만 `best_by_key` 딕셔너리에 추가하는 것을 건너뜁니다.
        4.  **(선택적 강화):** 더 확실한 탐지를 위해, `lp == page_no`이면서 `lf_raw`가 `jokbo_filename`을 포함하는지(AI가 파일명까지 혼동했는지) 이중으로 확인할 수 있습니다. 하지만 페이지 번호만 비교하는 것으로도 대부분의 오류를 잡을 수 있습니다.

### 3.3. 기대 효과

  * **족보 문제 누락 방지:** 이 로직은 `question` 객체를 버리는 것이 아니라, `question` 객체 내부의 `related_lesson_slides` 배열에서 \*오염된 원소(슬라이드)\*만 제거합니다.
  * **정확한 결과물:** 최종 PDF에는 족보 문제가 정상적으로 포함됩니다. 해설 페이지에는 잘못된 강의자료 10페이지가 삽입되는 대신, "관련 슬라이드: 없음" (또는 올바르게 연결된 다른 슬라이드)이 표시됩니다. 이는 사용자에게 잘못된 정보를 제공하는 것보다 훨씬 안전합니다.

-----

## 4\. 실행 체크리스트

| 단계 | 파일 | 함수 (또는 상수) | 실행 내용 |
| :--- | :--- | :--- | :--- |
| **Layer 1** | `constants.py` | `COMMON_PROMPT_INTRO` | `{jokbo_display_name}`, `{lesson_display_name}` 플레이스홀더 추가 |
| **Layer 1** | `constants.py` | `COMMON_WARNINGS` | `{jokbo_display_name}`, `{lesson_display_name}` 플레이스홀더 추가 |
| **Layer 1** | `pdf_processor/analyzers/jokbo_centric.py` | `build_prompt` | `jokbo_filename`과 `lesson_filename`을 인자로 받도록 수정 |
| **Layer 1** | `pdf_processor/analyzers/jokbo_centric.py` | `build_prompt` | `constants` 템플릿에 `.format()`으로 `display_name` 변수 주입 |
| **Layer 1** | `pdf_processor/analyzers/jokbo_centric.py` | `analyze` | `build_prompt` 호출 시 두 파일명 모두 전달 |
| **Layer 1** | `pdf_processor/analyzers/lesson_centric.py` | `build_prompt` | `jokbo_centric.py`와 동일하게 수정 |
| **Layer 1** | `pdf_processor/analyzers/lesson_centric.py` | `analyze` | `jokbo_centric.py`와 동일하게 수정 |
| **Layer 2** | `pdf_processor/parsers/response_parser.py` | `_sanitize_parsed_response` | `jokbo-centric` 모드 로직 내, `related_lesson_slides` 순회 시 `lp == page_no` 충돌 탐지 및 `continue` 필터링 로직 추가 |
| **Test** | 테스트 환경 | - | 페이지 번호가 겹치는 족보/강의자료 세트로 테스트 실행 |
| **Verify** | 테스트 결과 | - | (수정 전) 버그 재현 -\> (수정 후) 잘못된 슬라이드가 필터링되고 족보 문제는 정상 출력됨을 확인 |
