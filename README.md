# JokboDude - AI 기반 족보 학습 도우미 🤖📚

족보(기출문제)와 강의자료를 AI로 분석하여, 시험에 나올 가능성이 높은 강의 내용과 관련 문제만 추출해주는 스마트 학습 도구입니다.

## 주요 기능 ✨

- **AI 분석**: Google Gemini AI가 족보와 강의자료의 연관성을 자동 분석
- **스마트 필터링**: 시험에 나올 가능성이 높은 강의 슬라이드만 추출
- **문제 매칭**: 각 강의 내용에 해당하는 족보 문제를 자동으로 매칭
- **상세 해설**: AI가 생성한 문제 해설과 정답 제공
- **효율적 학습**: 중요한 내용만 집중적으로 학습 가능

## 시작하기 🚀

### 필요 사항

- Python 3.8 이상
- Google Gemini API 키 ([여기서 발급](https://makersuite.google.com/app/apikey))

### 설치 방법

1. 저장소 클론
```bash
git clone https://github.com/ksw6895/jokbodude.git
cd jokbodude
```

2. 가상환경 생성 및 활성화
```bash
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
```

3. 의존성 설치
```bash
pip install -r requirements.txt
```

4. 환경 설정
```bash
cp .env.example .env
```

5. `.env` 파일을 열어 Gemini API 키 입력
```
GEMINI_API_KEY=your_actual_api_key_here
```

## 사용법 📖

### 기본 사용법

```bash
python main.py
```

이 명령어는 `jokbo/` 폴더의 모든 족보와 `lesson/` 폴더의 모든 강의자료를 분석합니다.

### 특정 강의자료만 처리

```bash
python main.py --single-lesson "lesson/특정강의.pdf"
```

### 커스텀 디렉토리 사용

```bash
python main.py --jokbo-dir "내족보폴더" --lesson-dir "내강의폴더" --output-dir "결과폴더"
```

## 폴더 구조 📁

```
jokbodude/
├── jokbo/              # 족보 PDF 파일들을 넣는 곳
├── lesson/             # 강의자료 PDF 파일들을 넣는 곳
├── output/             # 분석 결과가 저장되는 곳
├── main.py             # 메인 실행 파일
├── config.py           # Gemini API 설정
├── pdf_processor.py    # PDF 분석 엔진
├── pdf_creator.py      # PDF 생성 엔진
└── requirements.txt    # Python 패키지 목록
```

## 출력 결과 📄

생성되는 PDF는 다음과 같은 구조로 되어 있습니다:

1. **관련 강의 슬라이드** (원본 그대로)
2. **매칭된 족보 문제** (문제 부분만 추출)
3. **AI 해설 페이지** (정답과 상세 설명)
4. **학습 요약 페이지** (전체 통계 및 권장사항)

파일명: `filtered_[강의자료명]_all_jokbos.pdf`

## 작동 원리 🔧

1. **개별 분석**: 각 족보를 강의자료와 1:1로 비교 분석
2. **결과 병합**: 모든 족보의 분석 결과를 하나로 통합
3. **PDF 생성**: 관련성 높은 내용만 추출하여 새 PDF 생성

자세한 아키텍처는 [architecture.md](architecture.md) 참조

## 라이선스 📜

MIT License

## 기여하기 🤝

버그 리포트, 기능 제안, 풀 리퀘스트 환영합니다!

## 문의 💬

이슈 탭을 통해 문의해주세요.