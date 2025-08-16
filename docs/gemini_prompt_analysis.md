# Gemini 프롬프트 정밀 점검 보고서

본 문서는 현재 저장소에서 Gemini API에 전달되는 프롬프트를 수집·정리하고, 실제로 문제를 야기할 수 있는 위험 요소와 개선안을 제시합니다. 분석 범위에는 두 분석 모드(lesson-centric, jokbo-centric)에서 조합되는 공통/전용 프롬프트 템플릿과 해당 프롬프트가 사용되는 맥락(파일 첨부, 파서 제약)이 포함됩니다.

## 프롬프트 개요

- 공통 구조: `content = [prompt] + uploaded_files`
  - 업로드 파일은 Gemini의 File API로 업로드된 PDF 객체입니다.
  - lesson-centric: `[prompt, 강의자료, 족보]`
  - jokbo-centric: `[prompt, 족보, 강의자료]`
- 시스템 인스트럭션은 사용하지 않으며, 모든 지시는 `prompt` 문자열 하나에 포함됩니다.
- 모델 설정: `response_mime_type=application/json`을 지정하여 JSON 출력을 요구합니다.

## 실제 사용 프롬프트 템플릿

두 모드 모두 아래 공통 섹션을 포함한 뒤, 각 모드별 작업/출력 포맷 섹션을 결합하여 사용합니다.

### 공통 섹션

다음 상수들이 프롬프트 최상단과 중간에 포함됩니다.

1) COMMON_PROMPT_INTRO (현재 코드 그대로, 중괄호 placeholder가 남아 있음)

```
당신은 병리학 교수입니다. 두 개의 PDF 파일을 받았습니다:
- 첫 번째 파일: {first_file_desc}
- 두 번째 파일: {second_file_desc}

⚠️ 매우 중요: 문제는 오직 족보 PDF에서만 추출하세요!
강의자료에 있는 예제 문제는 절대 추출하지 마세요!
```

2) COMMON_WARNINGS (페이지 번호 규칙, 문제형 슬라이드 제외, JSON 주의사항 등 대규모 제약 포함)

3) RELEVANCE_CRITERIA (연관성 점수 기준 및 5점 단위·특수 110점 규칙)

위 2)~3)은 본 저장소의 `constants.py`에 정의된 긴 지시문 그대로 포함됩니다.

### Lesson-Centric 프롬프트

조합 순서 (요약):

```
COMMON_PROMPT_INTRO

족보 파일: {jokbo_filename}

LESSON_CENTRIC_TASK

COMMON_WARNINGS

RELEVANCE_CRITERIA

LESSON_CENTRIC_OUTPUT_FORMAT
```

- 첨부 파일: `[prompt, 강의자료_{lesson_filename}, 족보_{jokbo_filename}]`

### Jokbo-Centric 프롬프트

조합 순서 (요약):

```
COMMON_PROMPT_INTRO

강의자료 파일: {lesson_filename}

JOKBO_CENTRIC_TASK

COMMON_WARNINGS

RELEVANCE_CRITERIA

JOKBO_CENTRIC_OUTPUT_FORMAT
```

- 첨부 파일: `[prompt, 족보_{jokbo_filename}, 강의자료_{lesson_filename}]`

참고: 두 모드의 OUTPUT_FORMAT에는 JSON 샘플 블록이 포함되어 있는데, 주석(`// ...`)과 범위 표기(예: `5-110`) 등 순수 JSON 스키마가 아닌 주석·설명 요소가 다수 포함되어 있습니다.

## 잠재적 문제와 실제 영향

아래 항목은 단순한 취향이나 사소한 스타일 이슈가 아니라, 실제 장애·오탐·코스트 증가로 이어질 수 있는 문제들입니다.

1) 미치환 placeholder 노출로 인한 프롬프트 혼란
- 문제: `COMMON_PROMPT_INTRO`에 `{first_file_desc}`, `{second_file_desc}`가 그대로 남아 있음. 각 모드에서 별도로 `강의자료 파일: ...`, `족보 파일: ...`를 추가하고 있지만, 미치환 placeholder가 혼재되어 모델의 역할·입력 인식에 혼란을 유발할 수 있음.
- 영향: 모델이 어떤 첨부가 “첫 번째/두 번째”인지, 어떤 파일이 중심인지 혼동 → 지시 위반, 잘못된 매핑, 불안정한 응답.
- 개선: 모드별로 intro를 `.format()`으로 정확히 치환하거나, placeholder를 제거하고 모드별 명시 문장만 남길 것.

2) 출력 포맷 블록에 “JSON 주석/설명” 포함
- 문제: `LESSON_CENTRIC_OUTPUT_FORMAT`, `JOKBO_CENTRIC_OUTPUT_FORMAT`에 `//` 주석, 범위 설명(예: `5-110`)이 JSON 예시 내에 포함됨.
- 영향: 모델이 예시를 그대로 모방해 주석 포함 JSON을 생성할 위험 큼. 현재 파서는 주석 제거 로직이 없음(따옴표/트레일링 콤마/NaN만 보정). → 파싱 실패 빈도 증가, 부분 복구 루틴 활성화로 품질·속도 저하.
- 개선: 출력 예시는 “순수 JSON”만 제공하고, 제약 설명은 JSON 블록 밖에 문장으로 분리. 또한 `response_mime_type=application/json`과 모순되지 않게 지시.

3) 과도하고 모순될 수 있는 점수 규칙(5점 단위 + 특수 110점)
- 문제: 극단적으로 엄격한 분포(대부분 25–65점, 90점 이상 거의 금지, 110점은 특수)와 5점 단위 강제 규칙.
- 영향: 모델이 조건을 맞추기 위해 추측/임의 보정을 할 가능성 증가. 실제로 `67`처럼 규칙을 벗어난 값이 종종 생성될 수 있고, 파서도 5점 단위로 강제 보정하지 않음(단순 int 변환·클램프만 수행).
- 개선: 프롬프트에 “허용 점수 집합”을 명시(예: `[5,10,...,100,110] 외 값 금지`), 잘못된 값은 가장 가까운 허용값으로 자동 보정하라고 지시. 병행하여 파서에서도 5점 그리드에 스냅하는 후처리 추가 권장.

4) 청크 페이지 규칙의 인지 부하와 충돌 가능성
- 문제: “청크의 첫 페이지는 항상 1부터” 규칙을 매우 강조. 모델은 청크 기준으로 페이지를 적되, 코드는 후처리로 오프셋을 적용(모드별로 구현되어 있음). 규칙 자체가 장황하고 예외가 많음.
- 영향: 모델이 규칙을 일부 놓치거나 인쇄 페이지 번호를 섞어 적을 위험. 페이지 불일치로 후처리 교정 불가 사례가 생길 수 있음.
- 개선: 프롬프트 상단에 “현재 첨부된 PDF에서 보이는 물리적 페이지 1부터”만 짧게 재진술하고, 예시는 단 하나의 일관된 샘플만 제공. 나머지는 코드에서 신뢰성 있게 보정.

5) `question_numbers_on_page`의 강제 채움 요구로 인한 환각 유도
- 문제: “빈 배열 금지 + 반드시 페이지 모든 문제 번호 나열” 요구.
- 영향: 모델이 확신이 없을 때도 값을 추정(환각)해 채울 위험. 이미지/스캔 PDF에서 번호 인식은 특히 취약.
- 개선: “불명확하면 빈 배열 허용 또는 '번호없음' 표준 토큰 사용”으로 완화하고, 해당 필드의 신뢰도/추정 플래그를 별도 제공하도록 요구. 가능하면 이 필드는 후단 OCR/룰로 보강.

6) 보기 개수(1~4 vs 1~5)의 불일치 가능성
- 문제: 설명 텍스트에서는 1~5번을 언급하지만, 예시 JSON은 1~4번만 제공.
- 영향: 모델이 예시를 과적합해 5지선다에서도 4개만 내보낼 가능성.
- 개선: “보기 개수는 실제 문제에 맞춰 1~N(최대 5)”로 명시하고, 예시도 1~5로 통일하거나 동적 예시로 수정.

7) 첨부 파일 순서와 intro의 “첫 번째/두 번째” 서술의 엇갈림
- 문제: 첨부 순서(lesson-centric은 [강의, 족보], jokbo-centric은 [족보, 강의])와 intro의 “첫 번째/두 번째 파일” 개념이 연결되지 않음.
- 영향: 모델이 어느 파일을 중심으로 분석해야 하는지 헷갈릴 수 있음.
- 개선: intro에서 “분석 중심 파일”과 “참고 파일”을 명시적으로 지칭하고, 첨부 순서도 문장으로 고정 선언.

8) 모델 구성과 프롬프트의 불일치 가능성(지원 토큰 수·안전 설정)
- 문제: `max_output_tokens=100000`은 모델·엔드포인트에 따라 비현실적인 값. 안전 필터는 전부 BLOCK_NONE.
- 영향: 엔드포인트 업스트림 오류/무시, 또는 드물게 안전 블록 시 비정상 재시도 증가.
- 개선: 실제 허용치로 설정(예: 프로/플래시 기준 권장값), 안전 설정은 최소한의 기본값 유지. 프롬프트와 무관하나 응답 안정성에 직접 영향하므로 함께 조정 권장.

## 개선안(프롬프트 중심, 실행 가능 제안)

1) 공통 Intro의 placeholder 제거/치환
- 방안 A(치환):
  - lesson-centric: `COMMON_PROMPT_INTRO.format(first_file_desc="강의자료(분석 중심)", second_file_desc=f"족보: {jokbo_filename}")`
  - jokbo-centric: `COMMON_PROMPT_INTRO.format(first_file_desc=f"족보: {jokbo_filename}", second_file_desc=f"강의자료: {lesson_filename}")`
- 방안 B(제거): placeholder를 아예 삭제하고, 모드별로 아래 문장을 intro 바로 뒤에 배치
  - lesson-centric: `첫 번째 첨부: 강의자료(분석 중심), 두 번째 첨부: 족보(참고)`
  - jokbo-centric: `첫 번째 첨부: 족보(분석 중심), 두 번째 첨부: 강의자료(참고)`

2) 출력 예시의 “순수 JSON화”
- 예시 블록에서 모든 `// 주석`, `,  // ...` 식 설명, 범위 표기(예: `5-110`) 제거.
- 블록 외부에 “규칙 설명”을 명문화(예: “relevance_score는 [5,10,...,100,110] 중 하나만 허용”).
- `response_mime_type=application/json`과 일관성 확보.

3) 점수 규칙의 실패 안전 장치
- 프롬프트: “허용 값 외 생성 금지 + 잘못 생성 시 가장 가까운 허용값으로 즉시 보정”을 명시.
- 파서: 스코어를 5점 그리드로 스냅(5 단위 반올림)하고 110 상한 예외만 허용.

4) 페이지 규칙 단순화 + 예시 통일
- 프롬프트 상단 한 줄: “항상 현재 첨부 PDF의 물리적 페이지(첫 장=1)만 사용”.
- 예시는 하나만 제공하고, 인쇄 페이지 번호 무시는 간단히 덧붙임.

5) `question_numbers_on_page` 완화
- 프롬프트: “불명확하면 빈 배열 허용 또는 '번호없음' 가능”으로 변경.
- 파서: 빈 배열을 그대로 허용하고, 추정치 표시 필드(예: `question_numbers_confidence`) 도입 고려.

6) 보기 개수 불일치 해소
- 프롬프트/예시 모두 “보기 개수는 실제 문제에 맞춤(최대 5)”로 통일. 예시 JSON을 1~5로 갱신.

7) 첨부 순서/역할 고정 선언
- intro 직후에 “첨부 순서와 역할”을 명시(예: “이 프롬프트에서 첫 번째 파일이 분석 중심 파일입니다”).

8) 모델 설정 정합성
- `max_output_tokens`를 실제 허용치로 하향 조정(예: 8k~16k 수준, 모델별 가이드 참조).
- 안전 설정은 기본값 또는 최소 완화로 복귀(응답 차단 증가 시 재조정).

## 제안된 프롬프트 스켈레톤(개선 적용 예시)

Lesson-Centric 예시(요약):

```
당신은 병리학 교수입니다. 이 작업에서 첫 번째 첨부는 강의자료(분석 중심), 두 번째 첨부는 족보(참고)입니다.
항상 현재 첨부 PDF의 물리적 페이지(첫 장=1)만 사용하세요. 인쇄 페이지 번호는 무시합니다.

작업:
- (간결 버전) 족보의 모든 문제를 분석하고, 강의자료 페이지별로 관련 문제를 그룹화합니다.
- (생략)

규칙(요약):
- 문제형 슬라이드는 제외.
- relevance_score는 [5,10,...,100,110] 중 하나만 허용. 허용 외 값이 생성되면 가장 가까운 값으로 보정하세요.
- question_numbers_on_page는 불명확하면 빈 배열 허용.

출력(JSON만):
{
  "related_slides": [
    {
      "lesson_page": 1,
      "related_jokbo_questions": [
        {
          "jokbo_filename": "{jokbo_filename}",
          "jokbo_page": 3,
          "question_number": "13",
          "question_text": "...",
          "answer": "...",
          "explanation": "...",
          "wrong_answer_explanations": {
            "1번": "...",
            "2번": "...",
            "3번": "...",
            "4번": "...",
            "5번": "..."
          },
          "relevance_score": 65,
          "relevance_reason": "...",
          "question_numbers_on_page": []
        }
      ],
      "importance_score": 50,
      "key_concepts": ["..."]
    }
  ],
  "summary": {"total_related_slides": 1, "total_questions": 1, "key_topics": ["..."], "study_recommendations": "..."}
}
```

Jokbo-Centric 예시(요약): 동일한 원칙으로 간결화 및 JSON 순수화.

## 빠른 적용 체크리스트

- [ ] COMMON_PROMPT_INTRO placeholder 제거/치환
- [ ] OUTPUT_FORMAT 예시의 주석/설명 제거(순수 JSON 유지)
- [ ] 허용 점수 집합 명시 + 파서 스냅 보정 추가
- [ ] 페이지 규칙 한 줄 요약으로 단순화 + 예시 통일
- [ ] question_numbers_on_page 완화(빈 배열 허용)
- [ ] 보기 개수 1~5 일관화
- [ ] 첨부 순서/역할 고정 문장 추가
- [ ] model 토큰/안전 설정 현실화

## 참고: 관련 코드 위치

- 프롬프트 구성:
  - `pdf_processor/analyzers/lesson_centric.py::build_prompt`
  - `pdf_processor/analyzers/jokbo_centric.py::build_prompt`
  - `constants.py` (COMMON_PROMPT_INTRO, COMMON_WARNINGS, RELEVANCE_CRITERIA, *_TASK, *_OUTPUT_FORMAT)
- 요청 전송/첨부:
  - `pdf_processor/analyzers/base.py::upload_and_analyze` (`content = [prompt] + uploaded_files`)
- 모델/설정:
  - `config.py::create_model` (generation_config, safety_settings)
- 파싱/복구:
  - `pdf_processor/parsers/response_parser.py` (코드펜스 제거, 경미한 수리, 부분 복구)

---

요청하시면 위 개선안을 반영하는 구체 코드 패치(PR 스코프)를 함께 준비할 수 있습니다. 특히 OUTPUT_FORMAT 순수화와 score 스냅 보정은 파싱 안정성 향상에 즉각적인 효과가 있습니다.

