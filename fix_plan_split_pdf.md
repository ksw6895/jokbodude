# PDF 분할 처리 개선 계획

## 문제 요약
- Gemini API에 너무 큰 PDF를 보내서 응답이 잘림
- 특히 큰 족보/강의자료 PDF를 통째로 보내는 것이 문제

## 해결 방안

### 1. PDF 분할 전략
- **중심 파일**: 그대로 유지 (족보 중심 모드에서는 족보 PDF)
- **매칭 파일**: 40페이지 단위로 분할하여 처리
  - 예: 120페이지 강의자료 → 3개 조각 (1-40, 41-80, 81-120)

### 2. 구현 방법
```python
# pdf_processor.py에 추가
def split_pdf_for_analysis(self, pdf_path: str, max_pages: int = 40):
    """PDF를 작은 조각으로 나누기"""
    pdf = fitz.open(pdf_path)
    total_pages = len(pdf)
    
    if total_pages <= max_pages:
        # 작으면 그대로 사용
        return [(pdf_path, 1, total_pages)]
    
    # 큰 경우 분할
    chunks = []
    for start in range(0, total_pages, max_pages):
        end = min(start + max_pages, total_pages)
        chunks.append((pdf_path, start + 1, end))
    
    pdf.close()
    return chunks

def analyze_single_lesson_with_jokbo(self, lesson_path: str, jokbo_path: str):
    """개선된 분석 - 큰 강의자료는 분할"""
    # 강의자료를 40페이지 단위로 분할
    lesson_chunks = self.split_pdf_for_analysis(lesson_path, max_pages=40)
    
    all_results = []
    for chunk_path, start_page, end_page in lesson_chunks:
        print(f"  분석 중: {Path(lesson_path).name} (페이지 {start_page}-{end_page})")
        
        # 족보 전체 + 강의자료 조각을 Gemini에 전송
        chunk_result = self._analyze_jokbo_with_lesson_chunk(
            jokbo_path, 
            chunk_path, 
            start_page, 
            end_page
        )
        
        if "error" not in chunk_result:
            all_results.append(chunk_result)
    
    # 모든 조각 결과를 병합
    return self._merge_chunk_results(all_results)
```

### 3. 업로드 최적화
```python
def _analyze_jokbo_with_lesson_chunk(self, jokbo_path, lesson_path, start_page, end_page):
    """족보 전체 + 강의자료 일부만 업로드"""
    # 족보는 전체 업로드
    jokbo_file = self.upload_pdf(jokbo_path)
    
    # 강의자료는 필요한 페이지만 추출하여 업로드
    temp_pdf = self.extract_pages(lesson_path, start_page, end_page)
    lesson_chunk_file = self.upload_pdf(temp_pdf)
    
    # Gemini 분석
    response = self.model.generate_content([prompt, jokbo_file, lesson_chunk_file])
    
    # 정리
    self.delete_file_safe(lesson_chunk_file)
    return self.parse_response(response)
```

### 4. 장점
- Gemini API 부담 감소 (한 번에 보내는 데이터 크기 제한)
- 응답 잘림 문제 해결
- `question_numbers_on_page` 같은 필드 누락 방지
- 최종 결과는 여전히 하나의 통합 PDF

### 5. 설정
```python
MAX_PAGES_PER_CHUNK = 40  # 환경 변수로 조정 가능
```

## 핵심
- **족보는 전체 + 강의자료는 40페이지씩 분할**하여 여러 번 API 호출
- 각 결과를 병합하여 하나의 족보 중심 PDF 생성
- API 응답 안정성 확보