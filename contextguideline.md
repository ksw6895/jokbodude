## 🤖 AI 컨텍스트 충돌 해결을 위한 종합 가이드라인 (최종 수정본)

### 1\. 개요

#### 1.1. 문제 정의: AI의 '자기 참조' 오류

AI가 **족보 PDF**와 **강의자료 PDF**를 구분하지 못하고, 족보 문제를 족보 파일 자체에서 찾는 오류가 발생합니다. 이 버그는 다음과 같은 구체적인 증상으로 나타납니다.

  * **명백한 자기 참조:** AI가 `lesson_filename`에 족보 파일명을 기재합니다.
  * **모호한 자기 참조 (사용자 지적):** AI가 `lesson_page`를 `jokbo_page`와 동일하게 설정하면서(**AND**), `relevance_score`를 90점 이상으로 비정상적으로 높게 책정합니다. (스스로를 참조했기 때문에 "완벽히 일치"한다고 판단)

#### 1.2. 핵심 해결 전략: 예방 및 정밀 탐지

잘못된 `OR` 필터링은 정상 데이터(예: 진짜 90점짜리 매칭)를 훼손합니다. 우리는 버그의 증상(Symptom)을 명확히 정의하고, 이 증상에 **정확히 일치하는(AND) 데이터만** 정밀하게 제거하는 다층 방어 전략을 사용해야 합니다.

  * **[Layer 1] 예방 (Prompt Engineering):** AI가 애초에 두 파일을 혼동하지 않도록 프롬프트에 파일의 "신원(Identity)"을 명확히 주입합니다. (가장 근본적인 해결책)
  * **[Layer 2] 탐지 (Parser Validation):** 예방에 실패하여 오염된 데이터(자기 참조 슬라이드)가 생성되더라도, 파서(Parser) 단계에서 **'AND' 조건**으로 이를 정밀하게 탐지하고 해당 슬라이드만 필터링합니다.

-----

### 2\. [Layer 1] 예방: 프롬프트 명확화 (The Primary Fix)

**목표:** AI가 `족보_A.pdf`와 `강의자료_B.pdf`를 단순한 "두 개의 파일"이 아닌, 명확한 파일명을 가진 "별개의 개체"로 인지하게 만듭니다.

#### 2.1. 방향성: 파일명 컨텍스트 주입

AI에게 전달되는 모든 프롬프트에, 현재 분석 세션에서 사용되는 족보와 강의자료의 `display_name`을 명시적으로 주입합니다.

  * 현재 코드(`analyzers/base.py`의 `upload_and_analyze`)는 이미 `f"족보_{filename}"` 형식으로 `display_name`을 설정하고 있으므로, 이 이름을 프롬프트 템플릿에 그대로 활용합니다.

#### 2.2. 실행 계획

1.  **`constants.py` 수정 (템플릿 준비):**

      * `COMMON_PROMPT_INTRO`와 `COMMON_WARNINGS`의 프롬프트 템플릿에 `format()` 메소드로 주입될 플레이스홀더(`{jokbo_display_name}`, `{lesson_display_name}`)를 추가합니다.
      * **목적:** AI가 지시문을 읽는 순간부터 두 파일의 역할을 명확히 인지시킵니다.

    *예시 (COMMON\_PROMPT\_INTRO):*

    ```python
    COMMON_PROMPT_INTRO = """당신은 해당 과목의 교수입니다. 다음 두 개의 PDF가 업로드되어 있습니다:
    - 족보 PDF (파일명: {jokbo_display_name})
    - 강의자료 PDF (파일명: {lesson_display_name})
    ...
    ⚠️ 매우 중요: 문제는 오직 {jokbo_display_name} 파일에서만 추출하세요!"""
    ```

    *예시 (COMMON\_WARNINGS):*

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

### 3\. [Layer 2] 탐지: 파서 레벨 방어 로직 (The Safety Net)

**목표:** Layer 1의 프롬프트 지시에도 불구하고 AI가 '자기 참조' 버그를 일으킨 경우, "페이지 충돌"과 "점수 인플레이션"이 **동시에 발생**하는 명백한 증상을 정밀 탐지하여 '오염된 슬라이드'만 제거합니다.

#### 3.1. 방향성: 데이터 정제(Sanitization) 단계에서 휴리스틱 필터링

AI 응답을 처음 파싱하고 정제하는 \*\*`pdf_processor/parsers/response_parser.py`\*\*에 방어 로직을 위치시킵니다. 오염된 데이터가 시스템 내부로 유입되는 것을 원천 차단하는 것이 가장 안전합니다.

#### 3.2. 실행 계획: 강화된 휴리스틱 (AND 조건 적용)

  * **파일:** `pdf_processor/parsers/response_parser.py`

  * **함수:** `_sanitize_parsed_response`

  * **위치:** `jokbo-centric` 모드를 처리하는 로직 내부, `related_lesson_slides`를 순회(loop)하는 지점 (`for s in slides:` 내부).

  * **로직:**

    1.  현재 `question` 객체에서 `jokbo_page` 번호(`page_no`)를 가져옵니다.
    2.  `slide` 객체에서 `lesson_page`(`lp`)와 `relevance_score`(`sc`)를 가져옵니다.
    3.  **수정된 충돌 탐지 휴리스틱 (Heuristic) 적용:**

    <!-- end list -->

    ```python
    # ... (lp, sc 변수 추출 후) ...

    # --- ⭐️ 강화된 방어 로직 ⭐️ ---

    # 휴리스틱: 페이지 번호가 겹치는가? (AI가 '10페이지'를 혼동함)
    is_page_collision = (lp == page_no)

    if is_page_collision:
        # 이는 '페이지 번호가 같음'에서 비롯된 명백한 자기 참조의 증상임.
        # 이 경우에만 해당 슬라이드를 버그로 간주하고 필터링합니다.
        logger.warning(
            f"Discarding slide (Self-Reference Detected): Q={qnum}, JokboPage={page_no}, "
            f"LessonPage={lp}, Score={sc}."
        )
        continue  # 이 '오염된' 슬라이드만 버리고 다음 슬라이드로 넘어감
        
    # --- ⭐️ 로직 종료 ⭐️ ---

    # (기존 로직 계속)
    lf = re.sub(...) 
    ...
    best_by_key[key] = cand
    ```

#### 3.3. 기대 효과

  * **족보 문제 누락 방지:** `continue`는 `related_lesson_slides` 배열에 해당 슬라이드가 추가되는 것만 막습니다. 족보 문제 자체(`question`)는 `cleaned_questions`에 정상적으로 포함되며, 최종 PDF에 **문제는 절대 누락되지 않습니다.**
  * **정밀한 버그 수정:** 이 로직은 사용자가 지적한 핵심 증상(페이지 충돌)으로 인한 '자기 참조' 오류만 정밀하게 필터링합니다.
  * **정확한 결과물:** 최종 PDF의 해설 페이지에는 잘못된 강의자료가 삽입되는 대신, "관련 슬라이드: 없음" (또는 올바르게 연결된 다른 슬라이드)이 표시됩니다.

-----

### 4\. 실행 체크리스트

| 단계 | 파일 | 함수 (또는 상수) | 실행 내용 |
| :--- | :--- | :--- | :--- |
| Layer 1 | `constants.py` | `COMMON_PROMPT_INTRO`, `COMMON_WARNINGS` | `{jokbo_display_name}`, `{lesson_display_name}` 플레이스홀더 추가 |
| Layer 1 | `pdf_processor/analyzers/jokbo_centric.py` | `build_prompt` | `constants` 템플릿에 `.format()`으로 `display_name` 변수 주입 |
| Layer 1 | `pdf_processor/analyzers/jokbo_centric.py` | `analyze` | `build_prompt` 호출 시 두 파일명 모두 전달 |
| Layer 1 | `pdf_processor/analyzers/lesson_centric.py` | `build_prompt`, `analyze` | `jokbo_centric.py`와 동일하게 수정 |
| **Layer 2** | **`pdf_processor/parsers/response_parser.py`** | **`_sanitize_parsed_response`** | `jokbo-centric` 모드 로직 내, `related_lesson_slides` 순회 시 **`lp == page_no`** 충돌 탐지 및 `continue` 필터링 로직 추가 |
| Test | 테스트 환경 | - | 페이지 번호가 겹쳤을 때(자기 참조) 필터가 정상 동작하는지 확인 |
| Verify | 테스트 결과 | - | (수정 전) 버그 재현 -> (수정 후) 페이지 충돌 슬라이드만 정밀하게 필터링되고 족보 문제는 정상 출력됨을 확인 |

