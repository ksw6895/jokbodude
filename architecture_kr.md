# 아키텍처 문서

## 개요

족보듀드(JokboDude)는 Google Gemini AI API를 활용하여 시험 문제(족보)를 기반으로 강의 자료를 지능적으로 필터링하는 PDF 처리 시스템입니다. 본 시스템은 강의 슬라이드와 기출 문제 간의 연관성을 심층 분석하여, 학습에 가장 효과적인 자료만을 선별한 맞춤형 PDF를 생성합니다.

### 주요 특징
- **AI 기반 분석**: Google Gemini 2.5 모델을 활용한 정확한 연관성 분석
- **이중 처리 모드**: 강의 중심 및 족보 중심 분석 지원
- **병렬 처리**: 다중 스레드를 활용한 고속 처리
- **지능형 점수 시스템**: 100점 만점의 정밀한 관련성 평가

## 시스템 아키텍처

```mermaid
graph TB
    subgraph "사용자 인터페이스"
        CLI[명령줄 인터페이스<br/>main.py]
    end
    
    subgraph "핵심 처리 엔진"
        PP[PDF 처리기<br/>pdf_processor.py]
        PC[PDF 생성기<br/>pdf_creator.py]
        GEMINI[Google Gemini AI API]
    end
    
    subgraph "지원 컴포넌트"
        CONFIG[설정 관리<br/>config.py]
        CONST[상수 정의<br/>constants.py]
        VAL[검증기<br/>validators.py]
        HELPER[처리 도우미<br/>pdf_processor_helpers.py]
        ERROR[오류 처리기<br/>error_handler.py]
    end
    
    subgraph "입출력 시스템"
        JOKBO[(족보 PDF<br/>jokbo/)]
        LESSON[(강의자료 PDF<br/>lesson/)]
        OUTPUT[(필터링된 PDF<br/>output/)]
        DEBUG[(디버그 로그<br/>output/debug/)]
        SESSION[(세션 데이터<br/>output/temp/sessions/)]
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

## 컴포넌트 상호작용 흐름

```mermaid
sequenceDiagram
    participant 사용자
    participant CLI as main.py
    participant PP as PDF처리기
    participant API as Gemini AI
    participant PC as PDF생성기
    participant FS as 파일시스템
    
    사용자->>CLI: 명령 실행
    CLI->>FS: PDF 디렉토리 스캔
    FS-->>CLI: PDF 파일 목록 반환
    
    alt 강의 중심 모드
        CLI->>PP: process_lesson_with_all_jokbos()
        PP->>API: 강의 PDF 업로드
        loop 각 족보에 대해
            PP->>API: 족보 PDF 업로드
            PP->>API: 관계 분석 요청
            API-->>PP: 분석 결과 반환
            PP->>API: 업로드된 족보 삭제
        end
    else 족보 중심 모드
        CLI->>PP: process_jokbo_with_all_lessons()
        PP->>API: 족보 PDF 업로드
        loop 각 강의자료에 대해
            PP->>API: 강의 PDF 업로드
            PP->>API: 관계 분석 요청
            API-->>PP: 분석 결과 반환
            PP->>API: 업로드된 강의 삭제
        end
    end
    
    PP->>FS: 디버그 응답 저장
    PP-->>CLI: 분석 결과 반환
    CLI->>PC: create_filtered_pdf()
    PC->>FS: 관련 페이지 추출
    PC->>FS: 설명 페이지 생성
    PC->>FS: 필터링된 PDF 저장
    PC-->>CLI: 완료 확인
    CLI-->>사용자: 결과 표시
```

## 처리 모드 상세 설명

### 1. 강의 중심 모드 (기본값)

```mermaid
graph LR
    subgraph "입력"
        L1[강의 PDF]
        J1[족보 1]
        J2[족보 2]
        J3[족보 N]
    end
    
    subgraph "분석"
        A1[각 슬라이드별<br/>관련 시험문제<br/>탐색]
    end
    
    subgraph "출력"
        O1[필터링된 PDF:<br/>슬라이드 → 문제 → 해설]
    end
    
    L1 --> A1
    J1 --> A1
    J2 --> A1
    J3 --> A1
    A1 --> O1
```

**활용 목적**: 
- 특정 강의 주제를 깊이 있게 학습
- 각 슬라이드가 어떤 시험 문제로 출제되었는지 파악
- 강의 내용의 중요도 판단

**출력 구조**:
1. 강의 슬라이드 (원본)
2. 관련 족보 문제들
3. AI 생성 해설 및 오답 설명

### 2. 족보 중심 모드

```mermaid
graph LR
    subgraph "입력"
        J1[족보 PDF]
        L1[강의 1]
        L2[강의 2]
        L3[강의 N]
    end
    
    subgraph "분석"
        A1[각 문제별<br/>출처 강의슬라이드<br/>탐색]
    end
    
    subgraph "출력"
        O1[필터링된 PDF:<br/>문제 → 슬라이드 → 해설]
    end
    
    J1 --> A1
    L1 --> A1
    L2 --> A1
    L3 --> A1
    A1 --> O1
```

**활용 목적**:
- 시험 대비 집중 학습
- 각 문제의 출제 근거 파악
- 출제 빈도가 높은 슬라이드 식별

**출력 구조**:
1. 족보 문제 (원본)
2. 관련 강의 슬라이드들 (관련성 점수순)
3. AI 생성 해설 및 학습 포인트

## 주요 컴포넌트 상세 분석

### 1. 메인 진입점 (main.py)

```mermaid
graph TB
    subgraph "main.py 기능"
        ARG[명령줄 인자 파싱]
        SCAN[PDF 파일 스캔<br/>Zone.Identifier 제외]
        MODE[모드 선택<br/>강의/족보 중심]
        PARALLEL[병렬 처리 관리]
        SESSION[세션 관리<br/>정리/목록/복구]
    end
    
    ARG --> SCAN
    SCAN --> MODE
    MODE --> PARALLEL
    PARALLEL --> SESSION
```

**주요 기능**:
- **지능형 파일 탐색**: Zone.Identifier 등 시스템 파일 자동 제외
- **유연한 처리 모드**: 사용자 요구에 따른 분석 방식 선택
- **세션 기반 관리**: 처리 상태 추적 및 복구 지원
- **자동 정리 기능**: 오래된 세션 자동 삭제 (기본 7일)

### 2. PDF 처리기 (pdf_processor.py)

```mermaid
graph TB
    subgraph "PDFProcessor 클래스"
        UP[파일 업로드<br/>관리]
        CHUNK[PDF 청킹<br/>40페이지/청크]
        CACHE[페이지 수<br/>캐시]
        SESSION[세션<br/>관리]
        PARALLEL[병렬<br/>처리]
        RETRY[재시도 로직<br/>지수 백오프]
    end
    
    UP --> CHUNK
    CHUNK --> PARALLEL
    PARALLEL --> RETRY
    SESSION --> CHUNK
```

**핵심 기능**:
- **지능형 청킹**: 대용량 PDF를 40페이지 단위로 분할 처리
- **스레드 안전 캐싱**: 동시 접근 시에도 안전한 PDF 메타데이터 관리
- **세션 기반 처리**: 고유 ID로 각 처리 작업 추적
- **강력한 오류 처리**: 3회 재시도 및 지수 백오프
- **상세 디버그 로깅**: 모든 API 응답을 타임스탬프와 함께 저장

### 3. PDF 생성기 (pdf_creator.py)

```mermaid
graph TB
    subgraph "PDFCreator 클래스"
        EXTRACT[페이지<br/>추출]
        CACHE[스레드 안전<br/>PDF 캐시]
        EXPLAIN[해설 페이지<br/>생성]
        MERGE[PDF<br/>병합]
        CLEANUP[임시 파일<br/>정리]
    end
    
    EXTRACT --> CACHE
    CACHE --> EXPLAIN
    EXPLAIN --> MERGE
    MERGE --> CLEANUP
```

**핵심 기능**:
- **다중 페이지 문제 처리**: 페이지 경계를 넘는 문제 자동 감지
- **한글 완벽 지원**: CJK 폰트를 활용한 깔끔한 텍스트 렌더링
- **스레드 안전 설계**: 락을 활용한 동시성 제어
- **지능형 페이지 포함**: 마지막 문제의 연속 페이지 자동 포함

### 4. 설정 관리 (config.py)

**모델 선택 옵션**:
- **Pro**: 최고 품질, 복잡한 분석에 적합
- **Flash**: 속도와 품질의 균형
- **Flash-lite**: 최고 속도, 최저 비용

**주요 설정**:
- Temperature: 0.3 (일관된 결과를 위한 낮은 창의성)
- Max Output Tokens: 100,000 (대용량 분석 결과 지원)
- Response Type: JSON (구조화된 데이터 처리)

### 5. 상수 정의 (constants.py)

**프롬프트 템플릿 구조**:
- 공통 소개 및 주의사항
- 모드별 특화 작업 지시
- 출력 형식 명세
- 관련성 점수 기준

## 관련성 점수 시스템

```mermaid
graph TB
    subgraph "점수 범위 (5점 단위)"
        S90[90-100점<br/>핵심 출제 슬라이드<br/>거의 그대로 출제]
        S70[70-85점<br/>높은 관련성<br/>주요 내용 포함]
        S50[50-65점<br/>중간 관련성<br/>배경 지식 제공]
        S25[25-45점<br/>낮은 관련성<br/>간접적 연관]
        S5[5-20점<br/>거의 무관<br/>같은 과목 수준]
    end
    
    subgraph "필터링 규칙"
        F1[50점 이상<br/>포함]
        F2[50점 미만<br/>제외]
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

**점수 부여 원칙**:
1. **극히 엄격한 기준 적용**: 90점 이상은 정말 확실한 경우만
2. **5점 단위 부여**: 일관성 있는 평가
3. **50점 최소 기준**: 낮은 관련성은 자동 제외
4. **다중 평가 요소**: 텍스트, 그림, 개념 등 종합 고려

## 병렬 처리 아키텍처

```mermaid
graph TB
    subgraph "메인 프로세스"
        M1[메인 스레드]
        M2[세션 ID: 20250801_123456_abc]
    end
    
    subgraph "스레드 풀"
        T1[스레드 1]
        T2[스레드 2]
        T3[스레드 N]
    end
    
    subgraph "공유 자원"
        S1[공유 세션 ID]
        S2[사전 업로드 파일]
        S3[스레드 안전 PDF 캐시]
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

**성능 향상 효과**:
- **3배 빠른 처리**: 기본 3개 워커 사용
- **API 호출 최적화**: 파일 재사용으로 비용 절감
- **메모리 효율성**: 공유 캐시 활용
- **안정적인 동시성**: 락 기반 동기화

## 오류 처리 및 복구 메커니즘

```mermaid
graph TB
    subgraph "오류 유형"
        E1[파일 작업 오류]
        E2[API 오류]
        E3[파싱 오류]
        E4[검증 오류]
    end
    
    subgraph "복구 메커니즘"
        R1[지수 백오프<br/>최대 3회 재시도]
        R2[세션 복구<br/>청크 파일 활용]
        R3[우아한 성능 저하]
        R4[상세 디버그 로깅]
    end
    
    E1 --> R3
    E2 --> R1
    E3 --> R2
    E4 --> R4
    
    R1 --> R4
    R2 --> R4
    R3 --> R4
```

**복구 전략**:
1. **API 오류**: 2^n초 대기 후 재시도 (최대 3회)
2. **부분 실패**: 성공한 부분만으로 결과 생성
3. **세션 복구**: 중단된 작업을 청크 파일에서 복원
4. **상세 로깅**: 모든 오류를 타임스탬프와 함께 기록

## 세션 관리 시스템

```mermaid
graph TB
    subgraph "세션 생명주기"
        CREATE[세션 생성<br/>타임스탬프 + 랜덤 ID]
        PROCESS[처리 중<br/>청크 저장]
        COMPLETE[완료<br/>결과 생성]
        CLEANUP[정리<br/>7일 후 자동]
    end
    
    subgraph "세션 구조"
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

**세션 관리 명령**:
```bash
# 세션 목록 확인
python main.py --list-sessions

# 오래된 세션 정리
python main.py --cleanup-old 3  # 3일 이상된 세션 삭제

# 모든 세션 정리
python main.py --cleanup
```

## 주요 설계 결정 사항

### 1. 청킹 전략
- **40페이지 단위**: API 성능과 메모리 사용의 최적 균형
- **동적 조정 가능**: MAX_PAGES_PER_CHUNK 설정
- **자동 병합**: 청크별 결과를 하나로 통합

### 2. 스레드 안전성
- **PDF 캐시 보호**: threading.Lock으로 동시 접근 제어
- **세션 ID 공유**: 모든 스레드가 하나의 세션 사용
- **독립적 처리기**: 각 스레드별 PDFProcessor 인스턴스

### 3. 메모리 관리
- **즉시 정리**: 사용 완료된 파일 즉시 삭제
- **캐시 활용**: 반복적인 PDF 열기/닫기 방지
- **자동 정리**: 소멸자에서 임시 파일 제거

### 4. 안정성 확보
- **지수 백오프**: API 장애 시 점진적 재시도
- **부분 복구**: 일부 성공 시에도 결과 생성
- **포괄적 로깅**: 문제 추적을 위한 상세 기록

## 유틸리티 스크립트

### cleanup_gemini_files.py
**기능**:
- Gemini에 업로드된 파일 목록 조회
- API 할당량 관리
- 대화형 삭제 인터페이스

### cleanup_sessions.py
**기능**:
- 세션별 크기, 나이, 상태 표시
- 선택적 또는 일괄 삭제
- 공간 사용량 분석

### recover_from_chunks.py
**기능**:
- 중단된 처리 작업 복구
- 청크 파일 기반 재구성
- 세션별 복구 지원

## 성능 최적화 전략

```mermaid
graph TB
    subgraph "최적화 전략"
        O1[모델 선택<br/>Pro vs Flash vs Flash-lite]
        O2[병렬 처리<br/>3개 동시 워커]
        O3[파일 캐싱<br/>중복 I/O 감소]
        O4[청크 처리<br/>대용량 PDF 대응]
        O5[Thinking Budget<br/>0-24576 제어]
    end
    
    O1 --> |비용/속도| O5
    O2 --> |3배 빠름| O3
    O3 --> |메모리 효율| O4
```

**최적화 팁**:
1. **빠른 처리**: `--model flash-lite --thinking-budget 0`
2. **균형 잡힌 처리**: `--model flash --parallel`
3. **고품질 처리**: `--model pro`

## 사용 시나리오별 권장 설정

### 중간고사 대비
```bash
python main.py --mode jokbo-centric --parallel --model flash
```

### 특정 단원 학습
```bash
python main.py --single-lesson "lesson/특정단원.pdf" --model pro
```

### 대량 처리 (전체 학기)
```bash
python main.py --parallel --model flash-lite --thinking-budget 0
```

## 향후 개선 계획

1. **컨텍스트 캐싱**: 반복 분석 시 비용 절감
2. **비동기 처리**: 더 나은 동시성 지원
3. **웹 인터페이스**: 사용자 친화적 UI
4. **배치 처리 개선**: 대규모 파일 세트 최적화
5. **고급 필터링**: 사용자 정의 규칙 지원

## 기술 요구사항

- **Python 3.8** 이상
- **Google Gemini API 키**
- **필수 라이브러리**:
  - PyMuPDF: PDF 조작
  - ReportLab: PDF 생성
  - python-dotenv: 환경 변수 관리
  - tqdm: 진행률 표시 (선택사항)

## 보안 고려사항

1. **API 키 보호**: 환경 변수 사용 (.env 파일)
2. **임시 파일 관리**: 자동 정리로 데이터 유출 방지
3. **세션 격리**: 다중 사용자 환경 지원
4. **민감 정보 처리**: 메모리에만 보관, 디스크 저장 최소화

## 문제 해결 가이드

### 일반적인 오류와 해결책

1. **API 키 오류**
   - `.env` 파일에 `GEMINI_API_KEY` 설정 확인
   - API 키 유효성 검증

2. **메모리 부족**
   - 청크 크기 감소: `MAX_PAGES_PER_CHUNK = 20`
   - 병렬 워커 수 감소: `--workers 2`

3. **처리 중단**
   - 세션 복구: `python recover_from_chunks.py`
   - 디버그 로그 확인: `output/debug/`

4. **PDF 오류**
   - PDF 파일 무결성 확인
   - PyMuPDF 버전 업데이트

## 성능 벤치마크

**테스트 환경**: 
- 족보 5개 (각 20페이지)
- 강의자료 10개 (각 100페이지)

**처리 시간**:
- 순차 처리: ~15분
- 병렬 처리 (3 워커): ~5분
- Flash-lite + 병렬: ~3분

**API 비용 예상**:
- Pro 모델: ~$0.50/처리
- Flash 모델: ~$0.10/처리
- Flash-lite: ~$0.05/처리