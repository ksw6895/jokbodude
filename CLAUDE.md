# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a PDF processing system that filters lecture materials based on exam questions (족보/jokbo). It uses Google Gemini AI API to analyze the relationship between lecture slides and exam questions, generating filtered PDFs containing only relevant content.

## Key Development Commands

### Setup and Installation
```bash
# Create and activate virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env and add your Gemini API key: GEMINI_API_KEY=your_actual_api_key_here
```

### Running the Application
```bash
# Basic run (processes all files)
python main.py

# Parallel processing (faster)
python main.py --parallel

# Jokbo-centric mode
python main.py --mode jokbo-centric --parallel

# Single file processing
python main.py --single-lesson "lesson/specific_file.pdf"

# Custom directories
python main.py --jokbo-dir "custom_jokbo" --lesson-dir "custom_lesson" --output-dir "custom_output"

# Model selection for cost/speed optimization
python main.py --model flash                              # Faster, cheaper
python main.py --model flash-lite --thinking-budget 0    # Fastest, cheapest

# Multi-API mode for better reliability
python main.py --mode jokbo-centric --multi-api           # Use multiple API keys

# Chunk size optimization (for large PDFs or API limits)
export MAX_PAGES_PER_CHUNK=30                             # Override default 30 if needed
python main.py --parallel
```

### Testing and Debugging
```bash
# Run specific test files
python test_jokbo_centric_debug.py
python test_fix_verification.py

# Clean up Gemini uploaded files
python cleanup_gemini_files.py

# Check debug output
ls output/debug/
```

## High-Level Architecture

### Core Components and Entry Points

1. **main.py**: Main entry point that orchestrates the PDF processing workflow
   - Parses command-line arguments for mode selection (lesson-centric vs jokbo-centric)
   - Scans directories for PDF files (filters out Zone.Identifier files)
   - Routes to appropriate processing function based on mode
   - Manages parallel vs sequential processing

2. **pdf_processor.py**: Core AI analysis engine
   - Manages file uploads to Gemini API with automatic cleanup
   - Implements both sequential and parallel processing strategies
   - Handles PDF splitting for large files (configurable via MAX_PAGES_PER_CHUNK)
   - Thread-safe caching of PDF metadata
   - Exponential backoff retry logic for API stability
   - Saves debug responses to output/debug/ directory

3. **pdf_creator.py**: PDF generation and manipulation
   - Thread-safe PDF caching mechanism for concurrent access
   - Multi-page question extraction with automatic boundary detection
   - Creates formatted explanation pages with CJK font support
   - Manages temporary file cleanup

### Supporting Components

4. **config.py**: Gemini AI configuration
   - Model selection logic (Pro/Flash/Flash-lite)
   - Thinking budget configuration for cost optimization
   - Safety settings to prevent content blocking

5. **constants.py**: Centralized prompt templates
   - Separate prompts for lesson-centric and jokbo-centric modes
   - Relevance scoring criteria (1-11 scale)
   - JSON output format specifications

6. **validators.py**: Input validation and page number adjustment
   - PDF page count validation
   - Page number boundary checks with retry logic
   - Chunk-aware page number adjustment

7. **pdf_processor_helpers.py**: Analysis result processing
   - JSON parsing with error recovery
   - Result merging across multiple analyses
   - Connection filtering based on relevance scores

8. **error_handler.py**: Centralized error handling
   - File operation error handling
   - API error handling with context
   - User-friendly error messages

### Processing Flow

1. **Initialization**: Load environment variables, create output directories
2. **File Discovery**: Scan jokbo/ and lesson/ directories for PDFs
3. **Mode Selection**:
   - **Lesson-Centric**: Each lesson analyzed against all jokbos
   - **Jokbo-Centric**: Each jokbo analyzed against all lessons
4. **Analysis Phase**:
   - Upload files to Gemini (with chunking for large PDFs)
   - AI analyzes relationships and returns structured JSON
   - Parallel mode uses ThreadPoolExecutor with tqdm progress
5. **PDF Generation**:
   - Extract relevant pages from source PDFs
   - Generate explanation pages with AI insights
   - Combine into final filtered PDF
6. **Cleanup**: Delete uploaded files, close PDF handles

### Key Design Decisions

- **Chunking Strategy**: Large PDFs split into 30-page chunks (configurable)
- **Thread Safety**: PDF cache protected by threading.Lock
- **Session Management**: Single session ID shared across all threads in parallel processing
- **Retry Logic**: 3 attempts with exponential backoff for API calls
- **Debug Support**: All API responses saved with timestamps
- **Memory Management**: Explicit cleanup in destructors
- **Page Boundary Handling**: Automatic inclusion of continuation pages

## Operating Modes

### 1. Lesson-Centric Mode (Default)
- Analyzes each lesson against all jokbo files
- Output: `filtered_{lesson_name}_all_jokbos.pdf`
- Structure: Lecture slide → Related exam questions → AI explanations
- Best for: Studying specific lecture topics

### 2. Jokbo-Centric Mode
- Analyzes each jokbo against all lesson files
- Output: `jokbo_centric_{jokbo_name}_all_lessons.pdf`
- Structure: Exam question → Related lecture slides → AI explanations
- Command: `python main.py --mode jokbo-centric`
- Best for: Exam preparation, understanding question sources
- Features:
  - Relevance scoring (1-100, 5점 단위로 부여)
  - Top 2 connections per question (configurable via MAX_CONNECTIONS_PER_QUESTION)
  - Minimum score threshold filtering (default: 50점)

## 최근 개선사항 (Recent Improvements - 2025-08-02)

### 1. Multi-API 모드 버그 수정 및 개선
- **AttributeError 수정**: `merge_chunk_results` → `load_and_merge_chunk_results` 메서드 이름 수정
- **빈 응답 처리**: 응답 길이 0인 경우 즉시 실패로 처리하는 로직 추가
- **실패 청크 재시도**: Multi-API 모드에서 실패한 청크를 다른 API로 재시도하는 로직 구현
- **API 상태 관리 강화**: 
  - 연속 실패 횟수 추적 (consecutive_failures)
  - 3회 연속 실패 시 자동 쿨다운 (10분)
  - 빈 응답도 실패로 카운트
- **청크 크기 최적화 가이드**: 
  - 환경변수 MAX_PAGES_PER_CHUNK 설정 방법 문서화
  - 기본값 30 권장 (토큰 제한 완화)

## 이전 개선사항 (2025-08-01)

### 1. 관련성 점수 체계 개선 (1-11 → 1-100점)
- **문제**: 기존 1-11점 체계가 너무 단순하고, 동일한 그림이 아닌데도 11점을 받는 경우 발생
- **해결**: 100점 만점 체계로 변경, 5점 단위로만 점수 부여
- **새로운 점수 기준**:
  - **90-100점**: 핵심 출제 슬라이드
    - 100점: 슬라이드 내용이 그대로 문제로 출제
    - 95점: 동일한 그림/도표가 존재 ⭐
    - 90점: 이 슬라이드만 보면 문제를 100% 맞힐 수 있음 🎯
  - **70-85점**: 직접적으로 관련된 슬라이드
  - **50-65점**: 중간 정도 관련된 슬라이드
  - **25-45점**: 간접적으로 관련된 슬라이드
  - **5-20점**: 거의 무관한 슬라이드
- **장점**:
  - 더 세밀한 관련성 평가 가능
  - 5점 단위로 점수를 제한하여 일관성 향상
  - 최소 기준점을 50점으로 높여 더 관련성 높은 슬라이드만 선별

### 2. 세션 격리 개선 - 병렬 처리 시 단일 세션 사용
- **문제**: 병렬 처리 시 각 스레드가 새로운 세션을 생성하여 불필요한 세션 디렉토리 생성
- **해결**: PDFProcessor 생성자에 선택적 session_id 매개변수 추가
- **효과**: 
  - 이전: 메인 프로세스 1개 + 스레드별 세션 3개 = 총 4개 세션
  - 현재: 모든 스레드가 메인 프로세스의 세션 ID 공유 = 총 1개 세션
- **구현**:
  ```python
  # 메인 프로세서
  main_processor = PDFProcessor(model)
  
  # 스레드에서 세션 ID 공유
  thread_processor = PDFProcessor(model, session_id=main_processor.session_id)
  ```

## 이전 개선사항 (2025-07-28)

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
   - 각 문제별 관련 강의 슬라이드에 relevance_score 추가
   - 관련성 점수 기반으로 상위 2개 연결만 선택하여 표시
   - 최소 점수 기준 미만 연결은 자동 제외
   - PDF 출력에 관련성 점수 표시

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

## Important Technical Details

### PDF Processing Specifics
- Page numbers are 1-based (matching PDF viewer display)
- Multi-page questions detected via `question_numbers_on_page` array
- Last question on page automatically includes next page
- Thread-safe PDF caching prevents file access conflicts

### API Usage Optimization
- Files uploaded individually to minimize memory usage
- Automatic cleanup of uploaded files after processing
- Chunking for PDFs over 40 pages (configurable)
- Parallel processing pre-uploads shared files once

### Error Handling
- All API responses saved to output/debug/ for troubleshooting
- Exponential backoff retry (up to 3 attempts)
- Graceful degradation for individual file failures
- Comprehensive error messages with context
- Empty response detection and handling
- Failed chunk retry with different API keys

### Chunk Size Optimization
- Default chunk size: 30 pages (configurable via MAX_PAGES_PER_CHUNK)
- Recommended: 30 pages for better stability with token limits
- Monitor response failures to find optimal size for your use case

### Model Selection Strategy
- **Pro**: Best quality, use for critical analysis
- **Flash**: Good balance of speed/quality/cost
- **Flash-lite**: Maximum speed/minimum cost
- Thinking budget: 0 (disabled) to 24576 (maximum)

## Utility Scripts

### cleanup_gemini_files.py
- Lists and manages uploaded Gemini files
- Useful for quota management and cleanup
- Interactive selection or bulk deletion

### cleanup_sessions.py
- Interactive session management tool
- Shows session size, age, and status
- Selective or bulk deletion options
- Command: `python cleanup_sessions.py`

### recover_from_chunks.py
- Recovers interrupted PDF generation from chunk files
- Session-aware recovery support
- Commands:
  - `python recover_from_chunks.py --list-sessions`
  - `python recover_from_chunks.py --session SESSION_ID`

### Test Scripts
- `test_jokbo_centric_debug.py`: Tests jokbo-centric processing
- `test_fix_verification.py`: Verifies bug fixes
