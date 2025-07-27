# PDF Processing System Architecture (시스템 아키텍처)

## 시스템 개요 (System Overview)

```mermaid
graph TD
    subgraph "📥 입력 (Input)"
        A1["📚 강의자료 PDFs<br/>(Lesson Materials)"]
        A2["📋 족보 PDFs<br/>(Past Exams)"]
    end
    
    subgraph "⚙️ 처리 과정 (Processing)"
        B["🎯 main.py<br/>진입점"] 
        C["🔍 PDF 파일 검색"]
        D["🔄 처리 모드 선택<br/>(강의/족보 중심)"]
        E["🤖 pdf_processor.py<br/>AI 분석 엔진"]
        F["☁️ Gemini API<br/>gemini-2.5-pro"]
        G["📊 분석 및 매칭<br/>문제 ↔ 슬라이드"]
        H["🔀 결과 병합"]
        I["📝 pdf_creator.py<br/>PDF 생성기"]
    end
    
    subgraph "📤 출력 (Output)"
        J["✅ 필터링된 PDFs<br/>(학습 자료)"]
        K["🐛 디버그 로그<br/>(API 응답)"]
    end
    
    subgraph "🔧 설정 (Configuration)"
        L["⚙️ config.py"]
        M["🔐 .env<br/>(API 키)"]
    end
    
    A1 --> B
    A2 --> B
    B --> C
    C --> D
    D --> E
    E --> F
    F --> G
    G --> H
    H --> I
    I --> J
    E --> K
    
    M --> L
    L --> E
    
    style A1 fill:#e1f5fe
    style A2 fill:#e1f5fe
    style B fill:#fff3e0
    style E fill:#f3e5f5
    style F fill:#e8f5e9
    style I fill:#f3e5f5
    style J fill:#c8e6c9
    style K fill:#ffecb3
```

## 상세 데이터 흐름 (Detailed Data Flow)

```mermaid
sequenceDiagram
    autonumber
    participant User as 👤 User
    participant Main as 🎯 main.py
    participant Processor as 🤖 pdf_processor.py
    participant Gemini as ☁️ Gemini API
    participant Creator as 📝 pdf_creator.py
    participant Output as 📄 Output PDF
    participant Debug as 🐛 Debug Logs

    User->>+Main: python main.py [--mode] [--parallel]
    Main->>Main: 📂 Find lesson & jokbo PDFs
    Main->>Main: 🧹 Clean up existing uploads
    
    rect rgb(240, 248, 255)
        Note over Main,Output: 강의자료 중심 모드 (기본값)
        loop 각 강의자료에 대해
            Main->>+Processor: analyze_pdfs_for_lesson()
            Processor->>Processor: 🗑️ 기존 업로드 파일 삭제
            Processor->>Gemini: 📤 강의자료 업로드
            
            loop 각 족보에 대해
                Processor->>Gemini: 📤 족보 업로드
                Processor->>Gemini: 🤔 연관성 분석
                Gemini-->>Processor: 📊 JSON 분석 결과 반환
                Processor->>Debug: 💾 API 응답 저장
                Processor->>Gemini: 🗑️ 족보 파일 삭제
                Processor->>Processor: 📁 결과 누적
            end
            
            Processor->>Processor: 🔀 모든 결과 병합
            Processor-->>-Main: 병합된 분석 결과 반환
            
            Main->>+Creator: create_filtered_pdf()
            Creator->>Creator: 📑 강의 슬라이드 추출
            
            loop 각 관련 문제에 대해
                Creator->>Creator: 📋 족보 전체 페이지 추출
                Creator->>Creator: 💡 해설 페이지 생성
            end
            
            Creator->>Creator: 📊 요약 페이지 추가
            Creator->>-Output: 💾 필터링된 PDF 저장
        end
    end

    Output-->>User: ✅ 필터링된 PDF가 output/ 폴더에 준비됨
```

## 컴포넌트 구조 (Component Architecture)

```mermaid
graph TB
    subgraph "📥 입력 파일 (Input Files)"
        A1["📚 lesson/<br/>강의자료 PDFs"]
        A2["📋 jokbo/<br/>족보 PDFs"]
    end
    
    subgraph "⚙️ 핵심 컴포넌트 (Core Components)"
        B1["🎯 main.py<br/>━━━━━━━━━━━━<br/>• 전체 조정<br/>• 모드 선택<br/>• 진행 추적"]
        B2["⚙️ config.py<br/>━━━━━━━━━━━━<br/>• API 설정<br/>• 모델: gemini-2.5-pro<br/>• Temperature: 0.3"]
        B3["🤖 pdf_processor.py<br/>━━━━━━━━━━━━<br/>• 업로드 관리<br/>• AI 분석<br/>• 결과 병합<br/>• 디버그 로깅"]
        B4["📝 pdf_creator.py<br/>━━━━━━━━━━━━<br/>• PDF 조작<br/>• 페이지 추출<br/>• 텍스트박스 해설<br/>• CJK 폰트 지원"]
    end
    
    subgraph "☁️ 외부 서비스 (External Services)"
        C1["🌟 Gemini API<br/>━━━━━━━━━━━━<br/>• gemini-2.5-pro<br/>• JSON 응답<br/>• 100K 토큰"]
    end
    
    subgraph "📤 출력 (Output)"
        D1["✅ output/<br/>필터링된 PDFs"]
        D2["🐛 output/debug/<br/>API 응답"]
    end
    
    subgraph "🔧 유틸리티 (Utilities)"
        E1["🧹 cleanup_gemini_files.py<br/>━━━━━━━━━━━━<br/>• 업로드 파일 조회<br/>• 선택적 삭제<br/>• 할당량 관리"]
    end
    
    A1 -.->|읽기| B1
    A2 -.->|읽기| B1
    B1 ==>|처리| B3
    B2 -->|설정| B3
    B3 ==>|업로드 & 분석| C1
    C1 ==>|JSON| B3
    B3 ==>|결과| B4
    B3 -.->|디버그| D2
    B4 ==>|생성| D1
    C1 <-.->|관리| E1
    
    style A1 fill:#e3f2fd,stroke:#1976d2
    style A2 fill:#e3f2fd,stroke:#1976d2
    style B1 fill:#fff8e1,stroke:#f57c00
    style B2 fill:#f3e5f5,stroke:#7b1fa2
    style B3 fill:#fce4ec,stroke:#c2185b
    style B4 fill:#fce4ec,stroke:#c2185b
    style C1 fill:#e8f5e9,stroke:#388e3c
    style D1 fill:#c8e6c9,stroke:#2e7d32
    style D2 fill:#fff3e0,stroke:#f57c00
    style E1 fill:#e0f2f1,stroke:#00796b
```

## PDF 생성 프로세스 (PDF Creation Process)

```mermaid
flowchart TD
    Start(["🚀 PDF 생성 시작"]) --> Mode{"📋 처리 모드?"}
    
    Mode -->|"강의자료 중심"| LC["📚 강의자료 PDF 열기"]
    Mode -->|"족보 중심"| JC["📋 족보 PDF 열기"]
    
    %% 강의자료 중심 흐름
    LC --> LC1{"📑 각 관련<br/>슬라이드에 대해"}
    LC1 --> LC2["📄 강의 슬라이드 삽입"]
    LC2 --> LC3{"❓ 관련 문제<br/>있음?"}
    LC3 -->|"예"| LC4["📋 족보 페이지 추출"]
    LC3 -->|"아니오"| LC1
    LC4 --> LC5["💡 텍스트박스 해설<br/>• 정답<br/>• 오답 설명<br/>• 관련성"]
    LC5 --> LC1
    
    %% 족보 중심 흐름
    JC --> JC1{"📋 각 족보<br/>페이지에 대해"}
    JC1 --> JC2["📄 족보 페이지 삽입"]
    JC2 --> JC3{"📚 관련 슬라이드<br/>있음?"}
    JC3 -->|"예"| JC4["📑 강의 슬라이드 추출"]
    JC3 -->|"아니오"| JC1
    JC4 --> JC5["💡 텍스트박스 해설<br/>• 관련 슬라이드 목록<br/>• 정답 & 해설"]
    JC5 --> JC1
    
    %% 공통 끝
    LC1 -->|"완료"| Summary["📊 요약 페이지 추가<br/>• 통계<br/>• 학습 권장사항"]
    JC1 -->|"완료"| Summary
    Summary --> Save["💾 출력 PDF 저장"]
    Save --> End(["✅ 완료"])
    
    style Start fill:#e8f5e9,stroke:#4caf50
    style End fill:#e8f5e9,stroke:#4caf50
    style Mode fill:#fff3e0,stroke:#ff9800
    style LC fill:#e3f2fd,stroke:#2196f3
    style JC fill:#fce4ec,stroke:#e91e63
    style Summary fill:#f3e5f5,stroke:#9c27b0
    style Save fill:#e0f2f1,stroke:#009688
```

## Gemini API 설정 (Configuration)

### 모델 설정 (Model Settings)

```python
GENERATION_CONFIG = {
    "temperature": 0.3,          # Low temperature for consistent results
    "top_p": 0.95,              # Nucleus sampling parameter
    "top_k": 40,                # Top-k sampling parameter
    "max_output_tokens": 100000, # Maximum output tokens (very high)
    "response_mime_type": "application/json"  # Force JSON response
}

# Available Models:
- gemini-2.5-pro (default) - Highest quality
- gemini-2.5-flash - Faster, cheaper
- gemini-2.5-flash-lite - Fastest, cheapest

# Thinking Budget (Flash/Flash-lite only):
- 0: Disable thinking (fastest)
- 1-24576: Manual budget
- -1: Automatic (model decides)
```

### 안전 설정 (Safety Settings)

모든 안전 카테고리를 `BLOCK_NONE`으로 설정하여 콘텐츠 차단 방지:
- HARM_CATEGORY_HARASSMENT
- HARM_CATEGORY_HATE_SPEECH
- HARM_CATEGORY_SEXUALLY_EXPLICIT
- HARM_CATEGORY_DANGEROUS_CONTENT

### API 사용 패턴 (Usage Pattern)

1. **Upload Pattern**: One lesson PDF + One jokbo PDF at a time
2. **Request Frequency**: Sequential processing (one jokbo at a time)
3. **File Management**: 
   - Clean up all existing uploads before starting
   - Upload files as needed
   - Delete immediately after analysis
   - Retry logic for failed deletions
4. **Error Handling**: Retry logic for file processing states
5. **Debug Support**: All API responses saved to output/debug/ for troubleshooting

### 토큰 제한 및 제약사항 (Token Limits)

- **Max Output Tokens**: 100,000 tokens (configured)
- **Input Size**: Limited by PDF file upload size
- **Processing Time**: 2-second polling interval for file upload status
- **Concurrent Uploads**: Not used - sequential processing only

### 응답 형식 (Response Format)

#### 강의자료 중심 모드 응답 (Lesson-Centric)
```json
{
  "related_slides": [{
    "lesson_page": number,
    "related_jokbo_questions": [{
      "jokbo_filename": string,
      "jokbo_page": number,
      "jokbo_end_page": number,  // For multi-page questions
      "question_number": number,
      "question_text": string,
      "answer": string,
      "explanation": string,
      "wrong_answer_explanations": {
        "1번": "Why option 1 is wrong",
        "2번": "Why option 2 is wrong",
        "3번": "Why option 3 is wrong",
        "4번": "Why option 4 is wrong"
      },
      "relevance_reason": string
    }],
    "importance_score": 1-10,
    "key_concepts": [string]
  }],
  "summary": {
    "total_related_slides": number,
    "total_questions": number,
    "key_topics": [string],
    "study_recommendations": string
  }
}
```

#### 족보 중심 모드 응답 (Jokbo-Centric)
```json
{
  "jokbo_pages": [{
    "jokbo_page": number,
    "questions": [{
      "question_number": number,
      "question_text": string,
      "answer": string,
      "explanation": string,
      "wrong_answer_explanations": {
        "1번": "...",
        "2번": "...",
        "3번": "...",
        "4번": "..."
      },
      "related_lesson_slides": [{
        "lesson_filename": string,
        "lesson_page": number,
        "relevance_reason": string
      }]
    }]
  }],
  "summary": {
    "total_jokbo_pages": number,
    "total_questions": number,
    "total_related_slides": number,
    "study_recommendations": string
  }
}
```

## Operating Modes (작동 모드)

### 1. Lesson-Centric Mode (강의자료 중심 - 기본값)
- 각 강의자료를 기준으로 모든 족보와 비교
- 출력: `filtered_{강의자료명}_all_jokbos.pdf`
- 용도: 특정 강의의 중요 내용 파악

### 2. Jokbo-Centric Mode (족보 중심)
- 각 족보를 기준으로 모든 강의자료와 비교
- 출력: `jokbo_centric_{족보명}_all_lessons.pdf`
- 용도: 시험 직전 족보 위주 학습
- 구조: 족보 페이지 → 관련 강의 슬라이드들 → AI 해설

### 3. Parallel Processing (병렬 처리)
- ThreadPoolExecutor 사용 (기본 3 workers)
- Pre-upload 방식으로 공통 파일 재사용
- 각 스레드별 독립적인 PDFProcessor 인스턴스

## 주요 기능 (Key Features)

### 1. 스마트 파일 업로드 관리
- 처리 전 모든 업로드 파일 삭제
- 메모리 효율을 위한 순차적 업로드/삭제
- 실패 시 자동 재시도 로직

### 2. 디버그 지원
- 모든 Gemini API 응답을 `output/debug/`에 저장
- 타임스탬프, 파일명, 원본 응답, 파싱 상태 포함
- 문제 해결에 필수적

### 3. 프롬프트 엔지니어링
- 강의자료 내 문제 엄격 제외
- 정확한 페이지/문제 번호 강제
- 일관성을 위한 파일명 보존

### 4. 여러 페이지 문제 지원
- 여러 페이지에 걸친 문제 처리
- 적절한 추출을 위해 `jokbo_end_page` 필드 사용

### 5. 오답 해설 기능
- 각 선택지가 오답인 이유 상세 설명
- 학생들의 일반적인 실수 이해 도움

## Recent Updates (최근 업데이트)

### 2025-07-28
1. **PDF 객체 일관성 버그 수정**
   - `create_jokbo_centric_pdf`에서 캐시된 PDF 메커니즘 사용
   - 페이지 경계 문제 해결 (마지막 문제의 다음 페이지 포함)
   - 디버그 로깅 추가로 페이지 포함 로직 추적 가능

2. **문서 개선**
   - README.md 사용법을 표 형식으로 재구성
   - 시나리오별 최적 설정 추가
   - 명령어 옵션 가독성 향상

### 2025-07-27
1. **Gemini 모델 선택 기능**
   - Pro, Flash, Flash-lite 모델 지원
   - Thinking Budget 설정 옵션 추가
   - 비용/속도 최적화 가능

2. **PyMuPDF Story API 오류 수정**
   - Story.draw() 메서드 TypeError 해결
   - Story 클래스 대신 insert_textbox() 사용
   - PyMuPDF 버전 호환성 문제 해결
   - CJK 폰트로 한글 텍스트 렌더링 개선

### 2025-07-26
1. **파일 업로드 관리 개선**
   - 자동 클린업 기능 추가
   - 메모리 효율성 향상
   
2. **디버깅 기능 강화**
   - API 응답 자동 저장
   - JSON 파싱 검증
   
3. **프롬프트 개선**
   - 강의자료 내 문제 제외 명시
   - 문제 번호 정확성 강화

## Data Flow Comparison (데이터 흐름 비교)

### Lesson-Centric Flow
```
1. For each lesson PDF:
   a. Clean up existing uploads
   b. Upload lesson file
   c. For each jokbo:
      - Upload jokbo
      - Analyze relationship
      - Save debug log
      - Delete jokbo
   d. Merge results
   e. Generate filtered PDF
```

### Jokbo-Centric Flow
```
1. For each jokbo PDF:
   a. Clean up existing uploads
   b. Upload jokbo file
   c. For each lesson:
      - Upload lesson
      - Analyze relationship
      - Save debug log
      - Delete lesson
   d. Merge results
   e. Generate jokbo-centric PDF
```

## Utility Tools (유틸리티)

### cleanup_gemini_files.py
- **목적**: Gemini API 업로드 파일 관리 도구
- **기능**:
  - 업로드된 모든 파일 목록 조회
  - 파일별 상세 정보 표시 (크기, 상태, 생성시간)
  - 선택적 삭제 또는 전체 삭제
  - 대화형 인터페이스
- **사용 시나리오**:
  - 프로그램 오류로 인한 잔여 파일 정리
  - API 할당량 관리
  - 디버깅 후 클린업
```