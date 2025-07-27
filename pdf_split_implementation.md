# PDF 분할 처리 구현 완료

## 해결된 문제
1. **Gemini API 응답 잘림 문제**: 큰 PDF를 보내면 응답이 잘려서 `question_numbers_on_page` 같은 필드가 누락되는 문제
2. **JSON 파싱 오류**: 응답이 잘리면서 JSON이 불완전하게 되어 파싱 실패

## 구현 내용

### 1. PDF 분할 함수 추가
```python
def split_pdf_for_analysis(self, pdf_path: str, max_pages: int = 40) -> List[Tuple[str, int, int]]
```
- PDF가 40페이지를 초과하면 자동으로 분할
- 환경변수 `MAX_PAGES_PER_CHUNK`로 조정 가능

### 2. 페이지 추출 함수 추가
```python
def extract_pdf_pages(self, pdf_path: str, start_page: int, end_page: int) -> str
```
- 지정된 페이지 범위만 추출하여 임시 PDF 파일 생성

### 3. 족보 중심 모드 개선
- `analyze_single_lesson_with_jokbo()` 함수가 이제 큰 강의자료를 자동으로 분할 처리
- 족보는 전체를 유지하고, 강의자료만 40페이지씩 분할
- 각 조각별로 분석 후 결과를 병합

### 4. 결과 병합 로직
- `_merge_jokbo_centric_results()` 함수 추가
- 여러 조각의 분석 결과를 하나로 통합
- 페이지 번호 자동 조정 (오프셋 적용)
- 중복 제거 및 relevance_score 기준 정렬

## 사용 방법

### 기본 사용 (40페이지 단위로 자동 분할)
```bash
python main.py --mode jokbo-centric
```

### 분할 크기 조정
```bash
export MAX_PAGES_PER_CHUNK=30
python main.py --mode jokbo-centric
```

## 장점
1. **API 안정성 향상**: 응답 잘림 문제 해결
2. **필드 누락 방지**: `question_numbers_on_page` 등 모든 필드가 정상적으로 반환됨
3. **메모리 효율성**: 한 번에 처리하는 데이터 크기 제한
4. **유연성**: 환경변수로 분할 크기 조정 가능

## 주의사항
- 분할 처리로 인해 전체 처리 시간은 약간 증가할 수 있음
- 하지만 안정성과 정확성이 크게 향상됨