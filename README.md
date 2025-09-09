# JokboDude - AI 기반 족보 학습 도우미 🤖📚


[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)


족보(기출문제)와 강의자료를 AI로 분석하여, 시험에 나올 가능성이 높은 강의 내용과 관련 문제만 추출해주는 스마트 학습 도구입니다.

## 🎯 이런 고민을 하는 학생들을 위한 프로그램입니다

> "교수님이 올려주신 강의자료가 300페이지인데... 이걸 다 봐야 하나요?"  
> "족보는 있는데 어떤 부분을 중점적으로 공부해야 할지 모르겠어요"  
> "시험 전날인데 뭐부터 봐야 할지..."

### 💡 JokboDude가 해결해드립니다!

1. **AI가 족보와 강의자료를 비교 분석**
   - 족보에 나온 문제들과 관련된 강의 슬라이드만 추출
   - 300페이지 → 50페이지로 압축 (예시)

2. **시험에 나올 내용만 골라서 정리**
   - 실제 기출문제와 연관된 슬라이드만 선별
   - 각 문제에 대한 AI 해설 제공

3. **효율적인 시험 준비**
   - 중요도가 높은 순서로 정리
   - 문제와 관련 강의 내용을 한 번에 확인

### 📚 사용 예시

**Before**: 
- 강의자료 PDF 300페이지 📚
- 족보 PDF 50페이지 📄
- "어디서부터 봐야 하지...?" 😵

**After**:
- 핵심 내용만 담은 PDF 1개! ✨
- 족보 문제 + 관련 강의 슬라이드 + AI 해설
- "이것만 보면 되는구나!" 😊

## 주요 기능 ✨

- **AI 분석**: Google Gemini AI가 족보와 강의자료의 연관성을 자동 분석
- **스마트 필터링**: 시험에 나올 가능성이 높은 강의 슬라이드만 추출
- **문제 매칭**: 각 강의 내용에 해당하는 족보 문제를 자동으로 매칭
- **상세 해설**: AI가 생성한 문제 해설과 정답, 오답 설명 제공
- **듀얼 모드**: 강의자료 중심 또는 족보 중심 학습 모드 지원
- **병렬 처리**: 다중 파일 빠른 처리 지원 (개선된 안정성과 성능)
- **대용량 파일 지원**: 자동 청크 분할로 크기 제한 없이 처리 가능 🆕
- **Multi-API 모드**: 여러 Gemini API 키를 사용한 병렬 처리로 성능 향상
- **효율적 학습**: 중요한 내용만 집중적으로 학습 가능
- **모델 선택**: Gemini 2.5 Pro/Flash/Flash-lite 중 선택 가능
- **비용 최적화**: Flash-lite 모델로 더 빠르고 저렴하게 처리


## 라이선스 📝

이 프로젝트는 **GNU Affero General Public License v3.0 (AGPLv3)** 라이선스를 따릅니다.

이 라이선스의 핵심 조건은 다음과 같습니다:
- 이 소프트웨어를 수정하여 네트워크를 통해 서비스 형태로 제공할 경우, **수정한 버전의 전체 소스 코드를 사용자에게 공개해야 합니다.**
- 이 소프트웨어의 소스 코드를 사용한 다른 파생 저작물 역시 **동일한 AGPLv3 라이선스로 배포**해야 합니다.

이는 JokboDude의 소스 코드가 상업적인 비공개 서비스로 재사용되는 것을 방지하고, 모든 개선 사항이 커뮤니티에 다시 공유되도록 보장하기 위함입니다. 라이선스 전문은 [여기](https://www.gnu.org/licenses/agpl-3.0.html)에서 확인하실 수 있습니다.


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
# 단일 API 키 사용
GEMINI_API_KEY=your_actual_api_key_here

# 또는 Multi-API 모드용 다중 키 설정 (선택사항)
GEMINI_API_KEYS=api_key_1,api_key_2,api_key_3
```

## 사용법 📖

### 🚀 빠른 시작 (3단계)

1. **PDF 파일 준비**
   - `jokbo/` 폴더에 족보 PDF 넣기
   - `lesson/` 폴더에 강의자료 PDF 넣기

2. **실행**
   ```bash
   python main.py
   ```

3. **결과 확인**
   - `output/` 폴더에서 생성된 PDF 확인!

### 📋 명령어 옵션

#### 기본 실행
```bash
python main.py
```
모든 족보와 강의자료를 분석합니다.

#### 🎯 분석 모드

**--mode lesson-centric (기본값)**
- **설명**: 각 강의자료에서 족보와 관련된 부분만 추출합니다
- **결과물**: 강의 슬라이드 → 관련 족보 문제 → AI 해설 순서로 구성
- **추천 대상**: 강의 내용을 중심으로 공부하고 싶은 학생
- **특징**: 대용량 강의자료 자동 청크 분할 처리 지원 🆕
- **예시**: `python main.py` 또는 `python main.py --mode lesson-centric`

**--mode jokbo-centric**
- **설명**: 각 족보 문제에 대해 관련된 강의 슬라이드를 찾아줍니다
- **결과물**: 족보 문제 → 관련 강의 슬라이드 → AI 해설 순서로 구성
- **추천 대상**: 시험 직전, 문제 위주로 공부하고 싶은 학생
- **예시**: `python main.py --mode jokbo-centric`

#### 🤖 AI 모델 선택
| 옵션 | 설명 | 특징 | 추천 사용 케이스 |
|------|------|------|-----------------|
| `--model pro` | Gemini 2.5 Pro 모델 사용 | 최고 품질, 정확한 분석 | 중요한 시험 준비 |
| `--model flash` (기본값) | Gemini 2.5 Flash 모델 사용 | 2배 빠른 속도, 50% 저렴 | 일반적인 사용 |
| `--model flash-lite` | Gemini 2.5 Flash-lite 모델 사용 | 4배 빠른 속도, 90% 저렴 | 대량 처리, 비용 절감 |
| `--thinking-budget N` | Flash/Flash-lite 추론 깊이 조절 | 0=비활성화, -1=자동 | 속도/품질 조절 |

#### ⚡ 성능 옵션

**--parallel**
- **설명**: 여러 파일을 동시에 처리하여 속도를 향상시킵니다
- **특징**: 
  - tqdm 진행률 표시 자동 활성화
  - Lesson-centric 모드에서 청크 병렬 처리 지원 🆕
- **예시**: `python main.py --parallel`

**--multi-api** 🆕
- **설명**: 여러 API 키를 사용하여 병렬 처리 (족보 중심 모드 전용)
- **특징**: 각 API가 독립적으로 작업하여 컨텍스트 감소 및 성능 향상
- **예시**: `python main.py --mode jokbo-centric --multi-api`
- **요구사항**: `.env` 파일에 `GEMINI_API_KEYS` 설정 필요

**--single-lesson FILE**
- **설명**: 모든 강의자료 대신 특정 파일 하나만 처리합니다
- **사용 시기**: 특정 과목이나 주차만 빠르게 분석하고 싶을 때
- **예시**: `python main.py --single-lesson "lesson/병리학_3주차.pdf"`

#### 📁 파일 경로 설정

**--jokbo-dir DIR**
- **설명**: 족보 PDF 파일들이 있는 폴더 경로 지정
- **기본값**: `jokbo`
- **예시**: `python main.py --jokbo-dir "D:/내문서/2024족보"`

**--lesson-dir DIR**  
- **설명**: 강의자료 PDF 파일들이 있는 폴더 경로 지정
- **기본값**: `lesson`
- **예시**: `python main.py --lesson-dir "C:/강의자료/병리학"`

**--output-dir DIR**
- **설명**: 분석 결과 PDF가 저장될 폴더 경로 지정
- **기본값**: `output`
- **예시**: `python main.py --output-dir "./시험준비자료"`

### 🎨 고급 사용법

#### 1. 시나리오별 최적 설정

**📚 중간고사 대비 (강의자료 전체 검토)**
```bash
python main.py --parallel --model flash
```

**📝 기말고사 직전 (족보 위주 학습)**
```bash
python main.py --mode jokbo-centric --model flash-lite --thinking-budget 0
```

**🚀 대량 처리 (Multi-API 모드)**
```bash
# .env에 GEMINI_API_KEYS=key1,key2,key3 설정 후
python main.py --mode jokbo-centric --multi-api --model flash
```

**🎯 특정 과목만 집중 공부**
```bash
python main.py --single-lesson "lesson/병리학_3주차.pdf" --model pro
```

#### 2. Thinking Budget 설정 (Flash/Flash-lite 전용)
```bash
# Thinking 비활성화 (최고 속도)
python main.py --model flash-lite --thinking-budget 0

# 자동 설정 (모델이 알아서 조절)
python main.py --model flash --thinking-budget -1

# 수동 설정 (1-24576)
python main.py --model flash --thinking-budget 8192
```

### 💡 사용 팁

1. **처음 사용시**: 기본 설정으로 시작해보세요
2. **비용이 걱정되면**: Flash-lite 모델 사용
3. **속도가 중요하면**: `--parallel` 옵션 추가
4. **품질이 중요하면**: Pro 모델 유지

### ⚠️ 주의사항

- PDF 파일명에 특수문자가 있으면 오류가 발생할 수 있습니다
- 대용량 PDF(100페이지 이상)는 처리 시간이 오래 걸릴 수 있습니다
- API 사용량 제한에 주의하세요

## 폴더 구조 📁

```
jokbodude/
├── jokbo/              # 족보 PDF 파일들을 넣는 곳
├── lesson/             # 강의자료 PDF 파일들을 넣는 곳
├── output/             # 분석 결과가 저장되는 곳
│   └── debug/          # Gemini API 응답 저장 (디버깅용)
├── main.py             # 메인 실행 파일
├── config.py           # Gemini API 설정
├── pdf_processor.py    # PDF 분석 엔진
├── pdf_creator.py      # PDF 생성 엔진
├── constants.py        # 프롬프트 및 설정 상수
├── cleanup_gemini_files.py  # Gemini 업로드 파일 정리 도구
└── requirements.txt    # Python 패키지 목록
```

## 출력 결과 📄

생성되는 PDF는 다음과 같은 구조로 되어 있습니다:

### 📖 PDF 구성 예시

```
[페이지 1] 강의 슬라이드 - "염증의 정의와 분류"
[페이지 2] 족보 문제 - 2023년 중간고사 3번 문제 (전체 페이지)
[페이지 3] AI 해설 - 정답: 2번, 해설: 급성 염증의 특징은...
[페이지 4] 강의 슬라이드 - "염증 매개체"
[페이지 5] 족보 문제 - 2022년 기말고사 7번 문제
[페이지 6] AI 해설 - 정답: 4번, 해설: 히스타민의 역할은...
...
[마지막] 학습 요약 - 총 12개 슬라이드, 15개 문제 매칭
```

1. **관련 강의 슬라이드** (원본 그대로)
2. **매칭된 족보 문제** (해당 페이지 전체 - 이미지, 선택지 포함)
3. **AI 해설 페이지** (정답과 상세 설명)
4. **학습 요약 페이지** (전체 통계 및 권장사항)

파일명: `filtered_[강의자료명]_all_jokbos.pdf`

## 작동 원리 🔧

1. **개별 분석**: 각 족보를 강의자료와 1:1로 비교 분석
2. **결과 병합**: 모든 족보의 분석 결과를 하나로 통합
3. **PDF 생성**: 관련성 높은 내용만 추출하여 새 PDF 생성

자세한 아키텍처는 [architecture.md](architecture.md) 참조

## 💰 비용 안내

- **오픈소스 무료 프로그램**입니다!
- 단, Google Gemini API 사용료는 별도 (무료 크레딧 제공)
- 학생들을 위한 팁: Gemini API는 처음 가입 시 무료 크레딧을 제공합니다

### 📊 모델별 비용 비교
- **Gemini 2.5 Pro**: 고품질 분석 (표준 가격)
- **Gemini 2.5 Flash**: 약 50% 저렴, 속도 2배
- **Gemini 2.5 Flash-lite**: 약 90% 저렴, 속도 4배 (`--thinking-budget 0` 사용 시)

## 🤔 자주 묻는 질문

**Q: 어떤 과목에 사용할 수 있나요?**  
A: PDF 형태의 강의자료와 족보가 있다면 모든 과목에 사용 가능합니다!

**Q: AI가 얼마나 정확한가요?**  
A: Google의 최신 AI 모델을 사용하여 높은 정확도를 보입니다. 다만, 최종 검토는 직접 하시는 것을 권장합니다.

**Q: 족보가 없어도 사용할 수 있나요?**  
A: 족보가 있어야 관련 내용을 추출할 수 있습니다. 선배들에게 족보를 구해보세요!

**Q: 어떤 모델을 사용해야 하나요?**  
A: 처음에는 기본 Pro 모델로 시작하고, 비용이 부담되면 Flash나 Flash-lite를 사용해보세요. 품질 차이가 크지 않다면 Flash-lite를 추천합니다.

**Q: 여러 페이지에 걸친 문제가 잘려서 나옵니다**  
A: 최신 버전에서는 페이지 마지막 문제를 자동으로 감지하여 다음 페이지까지 포함합니다!

**Q: 처리 중 중단됐는데 처음부터 다시 해야 하나요?**  
A: 아니요! `python recover_from_chunks.py --list-sessions`로 세션을 찾아 복원할 수 있습니다.

**Q: 강의자료가 너무 커서 처리가 안됩니다**  
A: 최신 버전에서는 대용량 강의자료를 자동으로 청크로 분할하여 처리합니다! 환경변수 `MAX_PAGES_PER_CHUNK`로 청크 크기 조절도 가능합니다.

## 🔧 유틸리티 도구

### cleanup_sessions.py - 세션 관리
```bash
# 대화형 모드
python cleanup_sessions.py

# N일 이상 된 세션 삭제
python cleanup_sessions.py --days 7

# 모든 세션 삭제
python cleanup_sessions.py --all
```

### recover_from_chunks.py - PDF 복원
```bash
# 복원 가능한 세션 목록
python recover_from_chunks.py --list-sessions

# 특정 세션 복원
python recover_from_chunks.py --session 20250801_123456_abc123

# 호환성 모드 (기존 방식)
python recover_from_chunks.py
```

### cleanup_gemini_files.py - Gemini 파일 관리
```bash
# 업로드된 파일 목록 확인 및 삭제
python cleanup_gemini_files.py
```

## 🆕 최근 개선사항 (2025-08-02)

### Lesson-Centric 모드 대규모 개선
- **대용량 파일 지원**: 강의자료를 자동으로 청크(40페이지)로 분할하여 처리
- **병렬 청크 처리**: 청크별 병렬 처리로 성능 대폭 향상
- **세션 관리**: 처리 상태 추적 및 중단된 작업 복구 기능
- **진행률 표시**: tqdm을 통한 청크 및 족보 처리 진행률 실시간 표시
- **스마트 결과 병합**: 중복 제거 및 중요도 점수 기반 병합

## 📝 이전 개선사항 (2025-08-01)

### Multi-API 모드 추가
- **다중 API 키 지원**: 여러 Gemini API 키를 동시에 사용하여 처리 속도 향상
- **컨텍스트 격리**: 각 API가 족보 1개 + 레슨 청크 1개만 처리하여 성능 최적화
- **자동 폴백**: Rate limit 발생 시 다른 API로 자동 전환
- **실시간 모니터링**: API별 사용 통계 및 상태 표시

## 📝 이전 개선사항 (2025-07-28)

### 병렬 처리 모드 안정성 대폭 개선
- **스레드 안전성**: PDF 캐시 접근에 Lock 추가로 동시성 문제 해결
- **자동 재시도**: API 호출 실패 시 지수 백오프로 자동 재시도
- **진행률 표시**: tqdm을 통한 실시간 처리 진행률 표시
- **메모리 관리**: 스레드별 리소스 정리로 메모리 누수 방지
- **오류 복구**: 개별 파일 처리 실패가 전체 프로세스에 영향 없음

### 다중 페이지 문제 처리 개선
- 페이지 경계에 걸친 문제 자동 감지 및 포함
- 마지막 문제의 경우 자동으로 다음 페이지 포함

## 🎓 개발 스토리

의대생의 시험 준비 고민에서 시작된 프로젝트입니다. 방대한 강의자료와 족보를 효율적으로 연결하여, 학습 시간을 단축하고 핵심 내용에 집중할 수 있도록 만들었습니다.

## 기여하기 🤝

버그 리포트, 기능 제안, 풀 리퀘스트 환영합니다!

## 문의 💬

이슈 탭을 통해 문의해주세요.
