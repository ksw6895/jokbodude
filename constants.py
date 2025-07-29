"""
Constants for JokboDude - 프롬프트 및 설정 상수
"""

# 공통 프롬프트 부분
COMMON_PROMPT_INTRO = """당신은 병리학 교수입니다. 두 개의 PDF 파일을 받았습니다:
- 첫 번째 파일: {first_file_desc}
- 두 번째 파일: {second_file_desc}

⚠️ 매우 중요: 문제는 오직 족보 PDF에서만 추출하세요!
강의자료에 있는 예제 문제는 절대 추출하지 마세요!"""

COMMON_WARNINGS = """**매우 중요한 주의사항**:
- question_number는 반드시 족보 PDF에 표시된 실제 문제 번호를 사용하세요 (예: 21번, 42번 등)
- 족보 페이지 내에서의 순서(1번째, 2번째)가 아닌, 실제 문제 번호를 확인하세요
- 만약 문제 번호가 명확하지 않으면 "번호없음"이라고 표시하세요
- jokbo_page는 반드시 **문제의 첫 부분이 나타나는** PDF 페이지 번호를 정확히 기입하세요

**페이지 번호 작성 규칙**:
- lesson_page는 반드시 강의자료 PDF 파일의 실제 페이지 번호를 사용하세요
- PDF 뷰어에 표시되는 페이지 번호를 그대로 사용하세요 (1페이지부터 시작)
- 페이지 번호는 0부터 시작하는 인덱스가 아닙니다
- 예: PDF 뷰어에서 "55/100"으로 표시되면 lesson_page는 55입니다
- jokbo_page도 동일한 규칙을 따릅니다 (PDF 뷰어에 표시되는 실제 페이지 번호)

**question_numbers_on_page 필드 작성 필수**:
- 각 jokbo_page에 있는 모든 문제 번호를 question_numbers_on_page 배열에 순서대로 나열하세요
- 예시: 한 페이지에 13번, 14번, 15번 문제가 있다면 → "question_numbers_on_page": ["13", "14", "15"]
- 빈 배열 []을 반환하지 마세요. 반드시 해당 페이지의 모든 문제 번호를 포함해야 합니다
- 페이지를 꼼꼼히 확인하여 모든 문제 번호를 찾아 배열에 포함시키세요

**JSON 형식 주의사항**:
- wrong_answer_explanations의 키는 반드시 큰따옴표로 묶어야 합니다
- 예시: "1번", "2번", "3번", "4번", "5번" (O) / "1"번", "2"번" (X)
- JSON 문법을 정확히 지켜주세요

**절대적 주의사항 - 강의자료 내 문제 제외**:
- 강의자료 PDF 내에 포함된 예제 문제나 연습 문제는 절대 추출하지 마세요
- 오직 족보 PDF 파일에 있는 문제만을 대상으로 분석하세요
- jokbo_page는 반드시 족보 PDF의 페이지 번호여야 하며, 강의자료의 페이지 번호를 사용하면 안 됩니다
- 강의자료는 오직 참고 자료로만 사용하고, 문제는 족보에서만 추출하세요"""

RELEVANCE_CRITERIA = """판단 기준 (엄격하게 적용):
- 족보 문제가 직접적으로 다루는 개념이 해당 강의 슬라이드에 명시되어 있는가?
- 해당 슬라이드가 실제로 "출제 슬라이드"일 가능성이 높은가?
- 문제의 정답을 찾기 위해 반드시 필요한 핵심 정보가 포함되어 있는가?
- 단순히 관련 주제가 아닌, 문제 해결에 직접적으로 필요한 내용인가?

점수 부여 기준:
- **특수 점수 11점**: 족보 문제에 사용된 그림, 도표, 다이어그램이 강의 슬라이드에 동일하게 존재하는 경우
- 직접적 연관성이 높은 경우: 8-10점
- 중간 정도의 연관성: 5-7점
- 간접적인 연관성: 1-4점

주의사항:
- 동일한 그림/도표가 있는 경우 반드시 11점을 부여하세요
- 너무 포괄적이거나 일반적인 연관성은 제외하세요
- 문제와 직접적인 연관이 없는 배경 설명 슬라이드는 제외하세요"""

# 강의 중심 모드 프롬프트
LESSON_CENTRIC_TASK = """작업:
1. 족보 PDF의 모든 문제를 분석하세요
2. 각 족보 문제와 직접적으로 관련된 강의자료 페이지를 찾으세요
3. 강의자료의 각 페이지별로 관련된 족보 문제들을 그룹화하세요
4. 각 문제의 정답과 함께 모든 선택지가 왜 오답인지도 설명하세요
5. 문제가 여러 페이지에 걸쳐있으면 jokbo_end_page에 끝 페이지 번호를 표시하세요"""

LESSON_CENTRIC_OUTPUT_FORMAT = """출력 형식:
{{
    "related_slides": [
        {{
            "lesson_page": 페이지번호,
            "related_jokbo_questions": [
                {{
                    "jokbo_filename": "{jokbo_filename}",
                    "jokbo_page": 족보페이지번호,
                    "jokbo_end_page": 족보끝페이지번호,  // 문제가 여러 페이지에 걸쳐있을 경우
                    "question_number": 문제번호,
                    "question_numbers_on_page": ["13", "14", "15"],  // 해당 페이지의 모든 문제 번호
                    "question_text": "문제 내용",
                    "answer": "정답",
                    "explanation": "해설",
                    "wrong_answer_explanations": {{
                        "1번": "왜 1번이 오답인지 설명",
                        "2번": "왜 2번이 오답인지 설명",
                        "3번": "왜 3번이 오답인지 설명",
                        "4번": "왜 4번이 오답인지 설명"
                    }},
                    "relevance_reason": "관련성 이유"
                }}
            ],
            "importance_score": 1-10,
            "key_concepts": ["핵심개념1", "핵심개념2"]
        }}
    ],
    "summary": {{
        "total_related_slides": 관련된슬라이드수,
        "total_questions": 총관련문제수,
        "key_topics": ["주요주제1", "주요주제2"],
        "study_recommendations": "학습 권장사항"
    }}
}}"""

# 족보 중심 모드 프롬프트
JOKBO_CENTRIC_TASK = """작업 (족보 중심 분석):
1. 족보 PDF의 모든 문제를 페이지 순서대로 분석하세요
2. 각 족보 문제와 관련된 강의자료 슬라이드를 찾으세요
3. 족보의 각 페이지별로 관련된 강의 슬라이드들을 그룹화하세요
4. 각 강의 슬라이드와의 관련성에 대해 relevance_score를 부여하세요:
   - 동일한 그림/도표가 있는 경우: 11점 (특수 점수)
   - 직접적으로 관련된 경우: 8-10점
   - 중간 정도 관련된 경우: 5-7점
   - 간접적으로 관련된 경우: 1-4점"""

JOKBO_CENTRIC_OUTPUT_FORMAT = """출력 형식:
{{
    "jokbo_pages": [
        {{
            "jokbo_page": 족보페이지번호,  // PDF 뷰어에 표시되는 실제 페이지 번호 (1부터 시작)
            "questions": [
                {{
                    "question_number": 문제번호,
                    "question_numbers_on_page": ["13", "14", "15"],  // 해당 페이지의 모든 문제 번호
                    "question_text": "문제 내용",
                    "answer": "정답",
                    "explanation": "해설",
                    "wrong_answer_explanations": {{
                        "1번": "왜 1번이 오답인지 설명",
                        "2번": "왜 2번이 오답인지 설명",
                        "3번": "왜 3번이 오답인지 설명",
                        "4번": "왜 4번이 오답인지 설명"
                    }},
                    "related_lesson_slides": [
                        {{
                            "lesson_filename": "{lesson_filename}",
                            "lesson_page": 강의페이지번호,  // PDF 뷰어에 표시되는 실제 페이지 번호 (1부터 시작)
                            "relevance_reason": "관련성 이유",
                            "relevance_score": 1-11
                        }}
                    ]
                }}
            ]
        }}
    ],
    "summary": {{
        "total_jokbo_pages": 총족보페이지수,
        "total_questions": 총문제수,
        "total_related_slides": 관련된강의슬라이드수
    }}
}}"""

# 기타 설정
MAX_CONNECTIONS_PER_QUESTION = 2  # 족보 중심 모드에서 문제당 최대 연결 수
RELEVANCE_SCORE_THRESHOLD = 5     # 관련성 점수 최소 기준