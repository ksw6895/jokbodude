# CLAUDE.md

이 파일은 Claude Code (claude.ai/code)가 이 프로젝트를 이해하고 작업할 수 있도록 돕는 가이드입니다.

## 프로젝트 개요 (Project Overview)

족보(기출문제)를 기반으로 강의자료를 필터링하는 PDF 처리 시스템입니다. Google Gemini AI API를 사용하여 강의 슬라이드와 시험 문제 간의 연관성을 분석하고, 관련 있는 강의 내용과 문제만을 포함한 필터링된 PDF를 생성합니다.

## 개발 명령어 (Development Commands)

### 설치 및 실행 (Setup and Running)
```bash
# Create virtual environment (if not exists)
python -m venv venv

# Activate virtual environment
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run the main application (processes all files)
python main.py

# Run with specific files
python main.py --single-lesson "lesson/specific_file.pdf"

# Run with custom directories
python main.py --jokbo-dir "custom_jokbo" --lesson-dir "custom_lesson" --output-dir "custom_output"

# Run with parallel processing (faster)
python main.py --parallel

# Run in jokbo-centric mode (족보 중심 모드)
python main.py --mode jokbo-centric

# Run in jokbo-centric mode with parallel processing
python main.py --mode jokbo-centric --parallel

# Run with Gemini 2.5 Flash model (faster, cheaper)
python main.py --model flash

# Run with Flash-lite model, no thinking (fastest, cheapest)
python main.py --model flash-lite --thinking-budget 0

# Run with Flash model, moderate thinking budget
python main.py --model flash --thinking-budget 8192
```

### 환경 설정 (Environment Configuration)
1. Copy `.env.example` to `.env`
2. Add your Gemini API key: `GEMINI_API_KEY=your_actual_api_key_here`

## 아키텍처 (Architecture)

### 핵심 컴포넌트 (Core Components)

1. **main.py**: PDF 처리 워크플로우를 조정하는 진입점
   - Finds PDF files in jokbo and lesson directories
   - Processes each lesson file against all jokbo files
   - Manages output directory and file naming

2. **config.py**: Gemini AI 설정
   - Loads API key from environment
   - Configures model with JSON response format
   - Sets safety settings to avoid content blocking

3. **pdf_processor.py**: AI 분석 처리
   - Uploads PDFs to Gemini API (one jokbo at a time with the lesson)
   - `analyze_single_jokbo_with_lesson()`: Analyzes one jokbo-lesson pair
   - `analyze_single_jokbo_with_lesson_preloaded()`: Analyzes with pre-uploaded lesson file
   - `analyze_pdfs_for_lesson()`: Processes multiple jokbo files sequentially
   - `analyze_pdfs_for_lesson_parallel()`: True parallel processing with pre-uploaded lesson
   - Returns structured JSON with slide-to-question mappings
   - Manages file cleanup on destruction
   - Improved prompts for more accurate slide matching
   - Includes wrong answer explanations in analysis

4. **pdf_creator.py**: 필터링된 출력 PDF 생성
   - Extracts relevant pages from original PDFs
   - `extract_jokbo_question()`: Extracts full pages from jokbo PDFs (supports multi-page questions)
   - Combines lecture slides with full jokbo question pages
   - Adds Gemini-generated explanations with wrong answer analysis
   - Uses PyMuPDF for PDF manipulation
   - Caches opened PDFs for performance

### 데이터 흐름 (Data Flow)

1. User runs `main.py` with optional arguments
2. System scans directories for PDF files (ignoring Zone.Identifier files)
3. For each lesson PDF:
   - Each jokbo PDF is uploaded to Gemini API one at a time (1 lesson + 1 jokbo)
   - AI analyzes relationships and returns JSON mapping for each pair
   - Results from all jokbo files are merged
   - System creates a single output PDF with filtered content
   - Original lecture slides are preserved, followed by:
     - Cropped question portions from jokbo PDFs (with images preserved)
     - Gemini-generated explanations and answers for each question

### 주요 의존성 (Key Dependencies)

- **google-generativeai**: Gemini AI API client
- **PyMuPDF (fitz)**: PDF reading and manipulation
- **reportlab**: PDF creation (though primarily using PyMuPDF)
- **python-dotenv**: Environment variable management

### 출력 구조 (Output Structure)

Filtered PDFs are saved as: `filtered_{lesson_name}_all_jokbos.pdf`

Each output PDF contains:
- Original lecture slides that have related exam questions
- For each related question:
  - Full jokbo question page(s) from the PDF (preserving images and choices)
  - Gemini-generated explanation page with:
    - Correct answer
    - Detailed explanation
    - Wrong answer explanations (why each option is incorrect)
    - Relevance to lecture content
- Summary page with overall statistics and study recommendations
- Organized by lecture page order with related questions following each slide

## 작동 모드 (Operating Modes)

### 1. 강의자료 중심 모드 (Lesson-Centric - 기본값)
- 각 강의자료를 중심으로 모든 족보와 비교
- 출력: `filtered_{강의자료명}_all_jokbos.pdf`
- 구조: 강의 슬라이드 → 관련 족보 문제 → 해설

### 2. 족보 중심 모드 (Jokbo-Centric)
- 각 족보를 중심으로 모든 강의자료와 비교
- 출력: `jokbo_centric_{족보명}_all_lessons.pdf`
- 구조: 족보 페이지 → 관련 강의 슬라이드 → 해설
- 사용법: `python main.py --mode jokbo-centric`

## 최근 개선사항 (Recent Improvements - 2025-07-28)

### 1. 병렬 처리 모드 대규모 개선
- **Critical Bug Fix**: 족보 중심 병렬 모드에서 `all_connections` 미정의 버그 수정
- **스레드 안전성**: PDF 캐시에 threading.Lock 추가로 동시 접근 문제 해결
- **API 안정성**: 지수 백오프 재시도 로직 추가 (최대 3회, 2^n초 대기)
- **진행률 표시**: tqdm 통합으로 실시간 처리 상태 시각화
- **메모리 관리**: 스레드별 리소스 정리로 메모리 누수 방지
- **파일 관리**: 중심 파일 삭제 조정으로 경쟁 상태 방지

### 2. PDF 객체 일관성 버그 수정
- **문제**: 족보 중심 모드에서 마지막 문제가 있는 페이지의 다음 페이지가 포함되지 않는 버그
- **원인**: `create_jokbo_centric_pdf`와 `extract_jokbo_question`에서 서로 다른 PDF 객체 사용
- **해결**: 캐시된 PDF 메커니즘을 일관되게 사용하도록 수정
- **영향**: 이제 페이지 경계의 문제들이 올바르게 다음 페이지까지 포함됨

### 3. 사용법 문서 개선
- **README.md**: 명령어 옵션을 표 형식으로 정리하여 가독성 향상
- **시나리오별 사용법**: 중간고사, 기말고사 등 상황별 최적 설정 추가
- **고급 사용법**: Thinking Budget 설정 등 세부 옵션 설명 추가
- **병렬 모드 개선사항**: 안정성 및 성능 향상 내용 추가

## 이전 개선사항 (2025-07-27)

### 1. Gemini 모델 선택 기능 추가
- **3가지 모델 지원**: Pro, Flash, Flash-lite
- **Thinking Budget 설정**: Flash/Flash-lite 모델에서 thinking budget 제어 가능
  - 0: Thinking 비활성화 (최고 속도/최저 비용)
  - 1-24576: 수동 budget 설정
  - -1: 자동 budget 설정
- **비용 최적화**: Flash-lite는 Pro 대비 훨씬 저렴 ($0.10/1M input vs 더 높은 가격)
- **사용 예시**:
  ```bash
  python main.py --model flash                              # Flash 모델 사용
  python main.py --model flash-lite --thinking-budget 0     # 최고 속도/최저 비용
  python main.py --model flash --thinking-budget 8192       # 중간 thinking budget
  ```

### 2. 다중 페이지 문제 처리 개선
- **문제 페이지 인식 개선**: 문제의 첫 부분이 나타나는 페이지를 정확히 인식
- **페이지내 문제 번호 목록**: 각 페이지에 있는 모든 문제 번호를 추적
- **자동 다음 페이지 포함**: 페이지의 마지막 문제인 경우 자동으로 다음 페이지 포함
- **새로운 JSON 필드**:
  - `question_numbers_on_page`: 해당 페이지의 모든 문제 번호 배열
  - `is_last_question_on_page`: 페이지의 마지막 문제 여부
- **하드코딩 방식**: 복잡한 판단 로직 없이 단순하게 처리

### 3. 이전 개선사항들

1. **PyMuPDF Story API 오류 수정**
   - Story.draw() 메서드 오류 해결
   - Story 클래스 대신 insert_textbox() 사용으로 안정성 향상
   - PyMuPDF 버전 호환성 문제 해결
   - 한글 텍스트 렌더링을 위한 CJK 폰트 사용

2. **족보 중심 모드 개선**
   - 각 문제별 관련 강의 슬라이드에 relevance_score (1-11) 추가
   - 특수 점수 11점: 족보와 강의자료에 동일한 그림/도표가 있는 경우 ⭐
   - 관련성 점수 기반으로 상위 2개 연결만 선택하여 표시
   - 최소 점수 기준(5점) 미만 연결은 자동 제외
   - PDF 출력에 관련성 점수 표시 (11점은 특별 표시)

3. **코드 구조 개선**
   - constants.py 파일 추가하여 프롬프트 상수화
   - 중복 코드 제거 및 유지보수성 향상
   - 파일 업로드/삭제 최적화로 API 사용량 절감
   - 오류 발생 시 중심 파일 제외한 자동 정리 기능

### 4. 이전 개선사항 (2025-07-26)

1. **파일 업로드 관리 개선**
   - 처리 전 기존 업로드 파일 자동 삭제
   - 족보/강의자료 개별 업로드 및 즉시 삭제
   - 메모리 효율성 및 API 사용량 최적화

2. **문제 번호 인식 정확도 향상**
   - 실제 PDF에 표시된 문제 번호 사용
   - 페이지 내 순서가 아닌 실제 번호 추출
   - 페이지 번호 정확성 검증 강화

3. **족보 중심 모드 추가**
   - 족보를 기준으로 관련 강의자료 매칭
   - 시험 준비에 최적화된 학습 자료 생성
   - 병렬 처리 지원으로 빠른 분석

### 5. 이전 개선사항 (2025-07-24)

1. **Enhanced Prompt for Better Accuracy**
   - More strict criteria for slide relevance
   - Focus on "directly related" content only
   - Higher importance score thresholds (8-10 for direct relevance)

2. **Wrong Answer Explanations**
   - Added `wrong_answer_explanations` field in JSON response
   - Each choice explained why it's incorrect
   - Helps students understand common mistakes

3. **Multi-Page Question Support**
   - Added `jokbo_end_page` field for questions spanning multiple pages
   - Automatically extracts all pages for a single question
   - Preserves complete question context

4. **True Parallel Processing**
   - Added `--parallel` flag for faster processing
   - Pre-uploads lesson file once before parallel processing
   - Each thread has independent PDFProcessor instance
   - Uses ThreadPoolExecutor with configurable workers (default: 3)
   - Real concurrent processing with timestamp logging
   - Significant speed improvement for multiple jokbo files

5. **Debug Support**
   - Gemini API responses saved to `output/debug/`
   - JSON format with timestamp, filenames, response text
   - Useful for troubleshooting parsing errors

6. **Improved Prompts**
   - Strict exclusion of lecture material embedded questions
   - Accurate page number enforcement
   - Better question number recognition

7. **Future Considerations**
   - Context Caching implementation for cost reduction
   - Upgrading to latest google-genai SDK
   - Async support for even better performance

## 유틸리티 도구 (Utility Tools)

### cleanup_gemini_files.py
- Lists all files uploaded to Gemini API
- Selective or bulk deletion of uploaded files
- Useful for managing API quota and cleaning up after errors