# 시스템 구조 설명서

## 🎯 시스템 개요

이 시스템은 의대 강의 자료(PDF)와 족보(시험 기출문제) PDF를 비교 분석하여, 시험과 관련된 강의 슬라이드만 추출해주는 프로그램입니다. Google의 AI (Gemini)를 활용하여 내용을 분석합니다.

### 🆕 두 가지 처리 모드

1. **강의자료 중심 모드** (--mode lesson)
   - 각 강의자료에서 족보와 관련된 부분만 추출
   - 여러 강의에 중복된 문제는 가장 적합한 곳에만 배치

2. **족보 중심 모드** (--mode jokbo)
   - 각 족보 문제별로 가장 관련성 높은 강의자료 찾기
   - 문제 → 최적 슬라이드 → 해설 순서로 정리

## 📊 작동 흐름

### 1. 전체 시스템 구조

```mermaid
graph TD
    A[📁 입력 파일들] --> B[🚀 main.py<br/>프로그램 시작]
    B --> C[🔍 파일 검색<br/>lesson/ & jokbo/]
    C --> D[📤 pdf_processor.py<br/>AI 분석 준비]
    D --> E[🤖 Gemini AI API<br/>내용 분석]
    E --> F[📊 분석 결과<br/>JSON 형식]
    F --> G[📝 pdf_creator.py<br/>PDF 생성]
    G --> H[📕 최종 결과물<br/>output/filtered_*.pdf]
    
    style E fill:#ffd700,stroke:#ff6347,stroke-width:3px
    style H fill:#90EE90,stroke:#228B22,stroke-width:2px
```

### 2. 상세 처리 과정

#### 강의자료 중심 모드 (--mode lesson)

```mermaid
sequenceDiagram
    participant 사용자
    participant 메인프로그램 as main.py
    participant AI분석기 as pdf_processor.py
    participant Gemini as 🤖 Gemini AI
    participant PDF생성기 as pdf_creator.py
    
    사용자->>메인프로그램: --mode lesson
    메인프로그램->>메인프로그램: 📁 PDF 파일 검색
    
    loop 각 강의 파일마다
        메인프로그램->>AI분석기: 강의 PDF 전달
        
        loop 각 족보 파일마다
            AI분석기->>Gemini: 📤 1개 강의 + 1개 족보 업로드
            Note over Gemini: 🧠 AI가 1:1 분석<br/>중요도 점수 부여
            Gemini->>AI분석기: 📊 분석 결과 반환
        end
        
        AI분석기->>메인프로그램: 모든 분석 결과 통합
    end
    
    메인프로그램->>메인프로그램: 🔄 중복 문제 최적화
    Note over 메인프로그램: 여러 강의에 나타난 문제는<br/>중요도 기반으로 최적 배치
    
    loop 각 강의마다
        메인프로그램->>PDF생성기: 최적화된 내용 전달
        PDF생성기->>사용자: 📕 강의별 PDF 생성
    end
```

#### 족보 중심 모드 (--mode jokbo)

```mermaid
sequenceDiagram
    participant 사용자
    participant 메인프로그램 as main.py
    participant AI분석기 as pdf_processor.py
    participant Gemini as 🤖 Gemini AI
    participant PDF생성기 as pdf_creator.py
    
    사용자->>메인프로그램: --mode jokbo
    메인프로그램->>메인프로그램: 📁 PDF 파일 검색
    
    loop 각 족보 파일마다
        메인프로그램->>AI분석기: 족보 PDF 전달
        AI분석기->>Gemini: 📤 족보 한 번 업로드
        
        loop 각 강의 파일마다
            AI분석기->>Gemini: 📤 강의 파일 추가
            Note over Gemini: 🧠 모든 문제 분석<br/>각 문제별 중요도 판단
            Gemini->>AI분석기: 📊 분석 결과 반환
        end
        
        AI분석기->>AI분석기: 🎯 문제별 최적 매칭
        Note over AI분석기: 각 문제마다 가장<br/>중요도 높은 강의 선택
        
        AI분석기->>메인프로그램: 최적화된 결과
        메인프로그램->>PDF생성기: 족보 중심 내용 전달
        PDF생성기->>사용자: 📕 족보별 PDF 생성
    end
```

### 3. 단계별 설명

1. **파일 준비** 📁
   - `lesson/` 폴더: 강의 자료 PDF 파일들
   - `jokbo/` 폴더: 족보(기출문제) PDF 파일들

2. **AI 분석** 🤖
   - 한 번에 1개 강의 + 1개 족보만 1:1 분석 (정확도 향상)
   - Gemini AI가 슬라이드와 문제의 연관성 분석
   - 중요도 점수(1-11점) 부여
     - 11점: 그림/도표 완전 일치 (출제 가능성 최고)
     - 8-10점: 직접적 연관성
     - 1-7점: 간접적 연관성

3. **결과 생성** 📊
   - 관련된 강의 슬라이드만 추출
   - 해당 족보 문제 페이지 포함
   - AI가 작성한 상세 해설 추가
   - `output/` 폴더에 저장

## 🔧 주요 구성 요소

### 핵심 파일들과 역할

```mermaid
graph LR
    subgraph 입력
        A1[📚 lesson/<br/>강의 PDF]
        A2[📝 jokbo/<br/>족보 PDF]
    end
    
    subgraph 처리 엔진
        B1[🚀 main.py<br/>전체 관리]
        B2[🔧 config.py<br/>설정 관리]
        B3[🤖 pdf_processor.py<br/>AI 분석]
        B4[📝 pdf_creator.py<br/>PDF 생성]
    end
    
    subgraph 외부 서비스
        C1[☁️ Gemini AI API<br/>구글 AI 서비스]
    end
    
    subgraph 출력
        D1[📕 output/<br/>필터링된 PDF]
    end
    
    A1 --> B1
    A2 --> B1
    B1 --> B3
    B2 --> B3
    B3 <--> C1
    B3 --> B4
    B4 --> D1
    
    style C1 fill:#ffd700,stroke:#ff6347,stroke-width:3px
```

1. **main.py** 🚀 - 프로그램 실행 담당
   - PDF 파일 찾기
   - 처리 과정 관리
   - 진행 상황 표시

2. **pdf_processor.py** 🤖 - AI 분석 담당
   - PDF를 Gemini API로 전송
   - 슬라이드-문제 매칭 분석
   - 결과 데이터 정리
   - 모드별 처리:
     - `analyze_pdfs_for_lesson()`: 강의자료 중심 분석
     - `analyze_jokbo_with_all_lessons()`: 족보 중심 분석

3. **pdf_creator.py** 📝 - PDF 생성 담당
   - 관련 슬라이드 추출
   - 족보 문제 페이지 추출
   - 해설 페이지 생성
   - 최종 PDF 제작
   - 모드별 처리:
     - `create_filtered_pdf()`: 강의자료 중심 PDF
     - `create_jokbo_centered_pdf()`: 족보 중심 PDF

4. **config.py** 🔧 - 설정 관리
   - Gemini API 설정
   - 환경 변수 관리

## 📁 결과물 구조

### 강의자료 중심 모드

생성 파일: `filtered_강의명_optimized.pdf`

```
📄 강의자료 중심 PDF
├── 📑 관련 강의 슬라이드 1
├── 📑 연관된 족보 문제 페이지
├── 📑 AI 해설 (정답 + 오답 설명)
├── 📑 관련 강의 슬라이드 2
├── 📑 연관된 족보 문제 페이지
├── 📑 AI 해설
└── 📑 요약 통계 페이지
```

### 족보 중심 모드

생성 파일: `jokbo_centered_족보명.pdf`

```
📄 족보 중심 PDF
├── 📑 족보 문제 1번 페이지
├── 📑 최적 매칭 강의 슬라이드
├── 📑 AI 상세 분석 (중요도, 정답, 해설)
├── 📑 족보 문제 2번 페이지
├── 📑 최적 매칭 강의 슬라이드
├── 📑 AI 상세 분석
└── 📑 족보 분석 요약
```

## ⚡ 병렬 처리 기능

`--parallel` 옵션 사용 시 더 빠른 처리:
- 여러 족보 파일을 동시에 분석
- 강의 파일은 한 번만 업로드
- 처리 시간 대폭 단축
- 강의자료 중심 모드에서만 지원

## 🎯 모드별 활용 예시

### 강의자료 중심 모드
- **상황**: "이 강의에서 시험에 나올 부분만 보고 싶어요"
- **사용**: `python main.py --mode lesson`
- **결과**: 각 강의별로 관련 족보 문제만 정리

### 족보 중심 모드
- **상황**: "이 족보 문제들이 어떤 강의에서 나왔는지 알고 싶어요"
- **사용**: `python main.py --mode jokbo`
- **결과**: 각 문제별로 가장 관련성 높은 강의 슬라이드 매칭

## 🎨 주요 특징

1. **정확한 매칭**
   - 1:1 문제-슬라이드 매핑
   - 이미지 포함 문제 우선 매칭 (중요도 11점)
   - 직접 관련된 내용만 추출
   - 중복 문제 자동 최적화

2. **상세한 해설**
   - 정답과 정답 이유
   - 각 오답이 틀린 이유 설명
   - 강의 내용과의 연관성 설명

3. **다중 페이지 지원**
   - 여러 페이지에 걸친 문제도 완전히 추출
   - 이미지와 표 등 모든 내용 보존

## 💡 활용 팁

1. **폴더 구조 준비**
   ```
   pathology/
   ├── jokbo/     (족보 PDF 넣기)
   ├── lesson/    (강의 PDF 넣기)
   └── output/    (결과물 생성됨)
   ```

2. **효과적인 사용법**
   - 같은 과목의 족보와 강의를 매칭
   - 병렬 처리로 시간 단축 (`--parallel`)
   - 특정 강의만 처리 (`--single-lesson`)
   - 모드 선택:
     - `--mode lesson`: 강의별로 최적화된 PDF
     - `--mode jokbo`: 족보별로 최적 강의 매칭

3. **결과물 활용**
   - 시험 직전 핵심 내용만 복습
   - 오답 설명으로 실수 방지
   - 중요도 점수로 우선순위 파악