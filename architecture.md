# Architecture Documentation / 아키텍처 문서

## Overview / 개요

JokboDude is an intelligent PDF processing system that filters lecture materials based on exam questions (jokbo/족보) using Google Gemini AI API. The system analyzes relationships between lecture slides and past exam questions to generate filtered PDFs containing only the most relevant study materials.

족보듀드는 Google Gemini AI API를 사용하여 시험 문제(족보)를 기반으로 강의 자료를 필터링하는 지능형 PDF 처리 시스템입니다. 이 시스템은 강의 슬라이드와 기출 문제 간의 관계를 분석하여 가장 관련성 높은 학습 자료만을 포함한 필터링된 PDF를 생성합니다.

## System Architecture / 시스템 아키텍처

```mermaid
graph TB
    subgraph "User Interface"
        CLI[Command Line Interface<br/>main.py]
    end
    
    subgraph "Core Processing Engine"
        PP[PDF Processor<br/>pdf_processor.py]
        PC[PDF Creator<br/>pdf_creator.py]
        GEMINI[Google Gemini AI API]
    end
    
    subgraph "Supporting Components"
        CONFIG[Configuration<br/>config.py]
        CONST[Constants<br/>constants.py]
        VAL[Validators<br/>validators.py]
        HELPER[Processor Helpers<br/>pdf_processor_helpers.py]
        ERROR[Error Handler<br/>error_handler.py]
    end
    
    subgraph "Input/Output"
        JOKBO[(Jokbo PDFs<br/>족보/)]
        LESSON[(Lesson PDFs<br/>lesson/)]
        OUTPUT[(Filtered PDFs<br/>output/)]
        DEBUG[(Debug Logs<br/>output/debug/)]
        SESSION[(Session Data<br/>output/temp/sessions/)]
    end
    
    CLI --> PP
    CLI --> PC
    
    PP --> GEMINI
    PP --> HELPER
    PP --> VAL
    PP --> SESSION
    
    PC --> OUTPUT
    
    PP --> DEBUG
    
    CONFIG --> PP
    CONST --> PP
    ERROR --> PP
    ERROR --> PC
    
    JOKBO --> PP
    LESSON --> PP
    
    style GEMINI fill:#4285f4,color:#fff
    style PP fill:#ea4335,color:#fff
    style PC fill:#34a853,color:#fff
```

## Component Interaction Flow / 컴포넌트 상호작용 흐름

```mermaid
sequenceDiagram
    participant User
    participant CLI as main.py
    participant PP as PDFProcessor
    participant API as Gemini AI
    participant PC as PDFCreator
    participant FS as File System
    
    User->>CLI: Execute command
    CLI->>FS: Scan PDF directories
    FS-->>CLI: Return PDF file lists
    
    alt Lesson-Centric Mode
        CLI->>PP: process_lesson_with_all_jokbos()
        PP->>API: Upload lesson PDF
        loop For each jokbo
            PP->>API: Upload jokbo PDF
            PP->>API: Analyze relationships
            API-->>PP: Return analysis results
            PP->>API: Delete uploaded jokbo
        end
    else Jokbo-Centric Mode
        CLI->>PP: process_jokbo_with_all_lessons()
        PP->>API: Upload jokbo PDF
        loop For each lesson
            PP->>API: Upload lesson PDF
            PP->>API: Analyze relationships
            API-->>PP: Return analysis results
            PP->>API: Delete uploaded lesson
        end
    end
    
    PP->>FS: Save debug response
    PP-->>CLI: Return analysis results
    CLI->>PC: create_filtered_pdf()
    PC->>FS: Extract relevant pages
    PC->>FS: Generate explanation pages
    PC->>FS: Save filtered PDF
    PC-->>CLI: Confirm completion
    CLI-->>User: Display results
```

## Processing Modes / 처리 모드

### 1. Lesson-Centric Mode (Default) / 강의 중심 모드 (기본값)

```mermaid
graph LR
    subgraph "Input"
        L1[Lesson PDF]
        J1[Jokbo 1]
        J2[Jokbo 2]
        J3[Jokbo N]
    end
    
    subgraph "Analysis"
        A1[Find related<br/>exam questions<br/>for each slide]
    end
    
    subgraph "Output"
        O1[Filtered PDF:<br/>Slide → Questions → Explanations]
    end
    
    L1 --> A1
    J1 --> A1
    J2 --> A1
    J3 --> A1
    A1 --> O1
```

**Purpose**: Study specific lecture topics by seeing which exam questions relate to each slide.

**목적**: 각 슬라이드와 관련된 시험 문제를 확인하여 특정 강의 주제를 학습합니다.

### 2. Jokbo-Centric Mode / 족보 중심 모드

```mermaid
graph LR
    subgraph "Input"
        J1[Jokbo PDF]
        L1[Lesson 1]
        L2[Lesson 2]
        L3[Lesson N]
    end
    
    subgraph "Analysis"
        A1[Find source<br/>lecture slides<br/>for each question]
    end
    
    subgraph "Output"
        O1[Filtered PDF:<br/>Question → Slides → Explanations]
    end
    
    J1 --> A1
    L1 --> A1
    L2 --> A1
    L3 --> A1
    A1 --> O1
```

**Purpose**: Exam preparation by understanding which lecture slides are the source for each question.

**목적**: 각 문제의 출처가 되는 강의 슬라이드를 이해하여 시험을 준비합니다.

## Key Components / 주요 컴포넌트

### 1. Main Entry Point (main.py) / 메인 진입점

- **Command-line argument parsing** / 명령줄 인자 파싱
- **PDF file discovery** (filters out Zone.Identifier files) / PDF 파일 탐색 (Zone.Identifier 파일 제외)
- **Mode routing** (lesson-centric vs jokbo-centric) / 모드 라우팅 (강의 중심 vs 족보 중심)
- **Parallel processing orchestration** / 병렬 처리 조정
- **Session management** (cleanup, listing) / 세션 관리 (정리, 목록 표시)

### 2. PDF Processor (pdf_processor.py) / PDF 처리기

```mermaid
graph TB
    subgraph "PDFProcessor Class"
        UP[File Upload<br/>Management]
        CHUNK[PDF Chunking<br/>40 pages/chunk]
        CACHE[PDF Page Count<br/>Cache]
        SESSION[Session<br/>Management]
        PARALLEL[Parallel<br/>Processing]
        RETRY[Retry Logic<br/>Exponential Backoff]
    end
    
    UP --> CHUNK
    CHUNK --> PARALLEL
    PARALLEL --> RETRY
    SESSION --> CHUNK
```

**Key Features** / 주요 기능:
- **Automatic file cleanup** after processing / 처리 후 자동 파일 정리
- **Large PDF chunking** (configurable MAX_PAGES_PER_CHUNK) / 대용량 PDF 청킹 (MAX_PAGES_PER_CHUNK 설정 가능)
- **Thread-safe caching** for concurrent access / 동시 접근을 위한 스레드 안전 캐싱
- **Session-based processing** with unique IDs / 고유 ID를 사용한 세션 기반 처리
- **Debug response saving** to output/debug/ / output/debug/에 디버그 응답 저장

### 3. PDF Creator (pdf_creator.py) / PDF 생성기

```mermaid
graph TB
    subgraph "PDFCreator Class"
        EXTRACT[Page<br/>Extraction]
        CACHE[Thread-safe<br/>PDF Cache]
        EXPLAIN[Explanation<br/>Page Generation]
        MERGE[PDF<br/>Merging]
        CLEANUP[Temp File<br/>Cleanup]
    end
    
    EXTRACT --> CACHE
    CACHE --> EXPLAIN
    EXPLAIN --> MERGE
    MERGE --> CLEANUP
```

**Key Features** / 주요 기능:
- **Multi-page question extraction** with boundary detection / 경계 감지를 통한 다중 페이지 문제 추출
- **CJK font support** for Korean text / 한국어 텍스트를 위한 CJK 폰트 지원
- **Thread-safe PDF caching** with locks / 락을 사용한 스레드 안전 PDF 캐싱
- **Automatic continuation page inclusion** / 자동 연속 페이지 포함

### 4. Configuration (config.py) / 설정

- **Model selection**: Pro, Flash, Flash-lite / 모델 선택: Pro, Flash, Flash-lite
- **Generation parameters**: temperature, tokens, etc. / 생성 매개변수: 온도, 토큰 등
- **Safety settings** to prevent content blocking / 콘텐츠 차단 방지를 위한 안전 설정
- **API key management** via environment variables / 환경 변수를 통한 API 키 관리

### 5. Constants (constants.py) / 상수

- **Prompt templates** for different modes / 다양한 모드를 위한 프롬프트 템플릿
- **Relevance scoring criteria** (1-100 scale, 5-point increments) / 관련성 점수 기준 (1-100 척도, 5점 단위)
- **Output format specifications** / 출력 형식 사양
- **Processing thresholds** and limits / 처리 임계값 및 제한

## Data Flow / 데이터 흐름

```mermaid
graph TB
    subgraph "Input Stage"
        I1[Jokbo PDFs]
        I2[Lesson PDFs]
    end
    
    subgraph "Processing Stage"
        P1[File Upload to Gemini]
        P2[AI Analysis]
        P3[JSON Response Parsing]
        P4[Result Validation]
        P5[Connection Filtering]
    end
    
    subgraph "Output Stage"
        O1[Page Extraction]
        O2[Explanation Generation]
        O3[PDF Assembly]
        O4[Final PDF]
    end
    
    I1 --> P1
    I2 --> P1
    P1 --> P2
    P2 --> P3
    P3 --> P4
    P4 --> P5
    P5 --> O1
    O1 --> O2
    O2 --> O3
    O3 --> O4
```

## Relevance Scoring System / 관련성 점수 시스템

```mermaid
graph TB
    subgraph "Score Ranges (5-point increments)"
        S90[90-100: Core Exam Slides<br/>핵심 출제 슬라이드]
        S70[70-85: High Relevance<br/>높은 관련성]
        S50[50-65: Medium Relevance<br/>중간 관련성]
        S25[25-45: Low Relevance<br/>낮은 관련성]
        S5[5-20: Almost Unrelated<br/>거의 무관]
    end
    
    subgraph "Filtering"
        F1[Score >= 50<br/>Include]
        F2[Score < 50<br/>Exclude]
    end
    
    S90 --> F1
    S70 --> F1
    S50 --> F1
    S25 --> F2
    S5 --> F2
    
    style S90 fill:#4CAF50,color:#fff
    style S70 fill:#8BC34A,color:#fff
    style S50 fill:#FFC107,color:#000
    style S25 fill:#FF9800,color:#fff
    style S5 fill:#F44336,color:#fff
```

## Parallel Processing Architecture / 병렬 처리 아키텍처

```mermaid
graph TB
    subgraph "Main Process"
        M1[Main Thread]
        M2[Session ID: 20250801_123456_abc]
    end
    
    subgraph "Thread Pool"
        T1[Thread 1]
        T2[Thread 2]
        T3[Thread N]
    end
    
    subgraph "Shared Resources"
        S1[Shared Session ID]
        S2[Pre-uploaded Files]
        S3[Thread-safe PDF Cache]
    end
    
    M1 --> M2
    M2 --> S1
    
    T1 --> S1
    T2 --> S1
    T3 --> S1
    
    T1 --> S2
    T2 --> S2
    T3 --> S2
    
    T1 --> S3
    T2 --> S3
    T3 --> S3
```

**Benefits** / 장점:
- **3x faster processing** with default 3 workers / 기본 3개 워커로 3배 빠른 처리
- **Shared session management** / 공유 세션 관리
- **Reduced API calls** through file reuse / 파일 재사용을 통한 API 호출 감소
- **Thread-safe operations** / 스레드 안전 작업

## Error Handling and Recovery / 오류 처리 및 복구

```mermaid
graph TB
    subgraph "Error Types"
        E1[File Operation Errors]
        E2[API Errors]
        E3[Parsing Errors]
        E4[Validation Errors]
    end
    
    subgraph "Recovery Mechanisms"
        R1[Exponential Backoff<br/>Max 3 retries]
        R2[Session Recovery<br/>from chunks]
        R3[Graceful Degradation]
        R4[Debug Logging]
    end
    
    E1 --> R3
    E2 --> R1
    E3 --> R2
    E4 --> R4
    
    R1 --> R4
    R2 --> R4
    R3 --> R4
```

## Session Management / 세션 관리

```mermaid
graph TB
    subgraph "Session Lifecycle"
        CREATE[Session Creation<br/>Timestamp + Random ID]
        PROCESS[Processing<br/>Chunk Storage]
        COMPLETE[Completion<br/>Result Generation]
        CLEANUP[Cleanup<br/>After 7 days]
    end
    
    subgraph "Session Structure"
        DIR[sessions/20250801_123456_abc/]
        STATE[processing_state.json]
        CHUNKS[chunk_results/*.json]
    end
    
    CREATE --> PROCESS
    PROCESS --> COMPLETE
    COMPLETE --> CLEANUP
    
    DIR --> STATE
    DIR --> CHUNKS
```

## Key Design Decisions / 주요 설계 결정

### 1. Chunking Strategy / 청킹 전략
- **40-page chunks** for optimal API performance / 최적의 API 성능을 위한 40페이지 청크
- **Configurable** via MAX_PAGES_PER_CHUNK / MAX_PAGES_PER_CHUNK를 통해 설정 가능
- **Automatic merging** of results across chunks / 청크 간 결과 자동 병합

### 2. Thread Safety / 스레드 안전성
- **PDF cache** protected by threading.Lock / threading.Lock으로 보호되는 PDF 캐시
- **Single session ID** shared across threads / 스레드 간 공유되는 단일 세션 ID
- **Independent PDFProcessor** instances per thread / 스레드별 독립적인 PDFProcessor 인스턴스

### 3. Memory Management / 메모리 관리
- **Immediate cleanup** of uploaded files / 업로드된 파일의 즉시 정리
- **Cached PDF objects** to reduce I/O / I/O 감소를 위한 캐시된 PDF 객체
- **Temporary file cleanup** in destructors / 소멸자에서의 임시 파일 정리

### 4. Reliability / 신뢰성
- **Exponential backoff** for API failures / API 실패에 대한 지수 백오프
- **Session recovery** from partial results / 부분 결과로부터의 세션 복구
- **Comprehensive error logging** / 포괄적인 오류 로깅

## Utility Scripts / 유틸리티 스크립트

### cleanup_gemini_files.py
- **Lists** uploaded Gemini files / 업로드된 Gemini 파일 목록 표시
- **Manages** API quota usage / API 할당량 사용 관리
- **Interactive** deletion options / 대화형 삭제 옵션

### cleanup_sessions.py
- **Session management** interface / 세션 관리 인터페이스
- **Shows** size, age, and status / 크기, 나이, 상태 표시
- **Bulk or selective** cleanup / 대량 또는 선택적 정리

### recover_from_chunks.py
- **Recovers** interrupted processing / 중단된 처리 복구
- **Session-aware** recovery / 세션 인식 복구
- **Chunk file** reconstruction / 청크 파일 재구성

## Performance Optimization / 성능 최적화

```mermaid
graph TB
    subgraph "Optimization Strategies"
        O1[Model Selection<br/>Pro vs Flash vs Flash-lite]
        O2[Parallel Processing<br/>3 concurrent workers]
        O3[File Caching<br/>Reduce redundant I/O]
        O4[Chunk Processing<br/>Handle large PDFs]
        O5[Thinking Budget<br/>0-24576 control]
    end
    
    O1 --> |Cost/Speed| O5
    O2 --> |3x faster| O3
    O3 --> |Memory efficient| O4
```

## Future Enhancements / 향후 개선사항

1. **Context Caching** implementation for cost reduction / 비용 절감을 위한 컨텍스트 캐싱 구현
2. **Async support** for better concurrency / 더 나은 동시성을 위한 비동기 지원
3. **Web interface** for easier access / 쉬운 접근을 위한 웹 인터페이스
4. **Batch processing** improvements / 배치 처리 개선
5. **Advanced filtering** options / 고급 필터링 옵션

## Technical Requirements / 기술 요구사항

- **Python 3.8+**
- **Google Gemini API key**
- **PyMuPDF** for PDF manipulation / PDF 조작을 위한 PyMuPDF
- **ReportLab** for PDF generation / PDF 생성을 위한 ReportLab
- **Threading support** for parallel processing / 병렬 처리를 위한 스레딩 지원

## Security Considerations / 보안 고려사항

- **API key** stored in environment variables / 환경 변수에 저장된 API 키
- **Temporary files** cleaned up automatically / 자동으로 정리되는 임시 파일
- **No persistent storage** of sensitive data / 민감한 데이터의 지속적 저장 없음
- **Session isolation** for multi-user scenarios / 다중 사용자 시나리오를 위한 세션 격리