import json
from pathlib import Path
import pymupdf as fitz
from typing import List, Dict, Any, Tuple, TYPE_CHECKING
import google.generativeai as genai
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
from datetime import datetime

if TYPE_CHECKING:
    from google.generativeai.types import file_types

class PDFProcessor:
    def __init__(self, model):
        self.model = model
        self.uploaded_files = []
    
    def __del__(self):
        """Clean up uploaded files when object is destroyed"""
        for file in self.uploaded_files:
            try:
                genai.delete_file(file.name)
                print(f"  Deleted uploaded file: {file.display_name}")
            except Exception as e:
                print(f"  Failed to delete file {file.display_name}: {e}")
    
    def upload_pdf(self, pdf_path: str, display_name: str = None):
        """Upload PDF file to Gemini API"""
        if display_name is None:
            display_name = Path(pdf_path).name
            
        uploaded_file = genai.upload_file(
            path=pdf_path,
            display_name=display_name,
            mime_type="application/pdf"
        )
        
        # Wait for file to be processed
        while uploaded_file.state.name == "PROCESSING":
            print(f"  [{datetime.now().strftime('%H:%M:%S')}] Processing {display_name}...")
            time.sleep(2)
            uploaded_file = genai.get_file(uploaded_file.name)
        
        if uploaded_file.state.name == "FAILED":
            raise ValueError(f"File processing failed: {display_name}")
            
        print(f"  [{datetime.now().strftime('%H:%M:%S')}] Uploaded: {display_name}")
        self.uploaded_files.append(uploaded_file)
        return uploaded_file
    
    def analyze_single_jokbo_with_lesson(self, jokbo_path: str, lesson_path: str) -> Dict[str, Any]:
        """Analyze one jokbo PDF against one lesson PDF"""
        
        # Extract the actual filename
        jokbo_filename = Path(jokbo_path).name
        
        prompt = f"""당신은 병리학 교수입니다. 하나의 족보(기출문제) PDF와 하나의 강의자료 PDF를 비교 분석합니다.

        중요: 족보 파일명은 반드시 "{jokbo_filename}"을 그대로 사용하세요. 파일명을 변경하거나 수정하지 마세요.

        작업:
        1. 족보 PDF의 모든 문제를 분석하세요
        2. 각 족보 문제와 직접적으로 관련된 강의자료 페이지를 찾으세요
        3. 강의자료의 각 페이지별로 관련된 족보 문제들을 그룹화하세요
        4. 각 문제의 정답과 함께 모든 선택지가 왜 오답인지도 설명하세요
        5. 문제가 여러 페이지에 걸쳐있으면 jokbo_end_page에 끝 페이지 번호를 표시하세요
        
        판단 기준 (엄격하게 적용):
        - 족보 문제가 직접적으로 다루는 개념이 해당 강의 슬라이드에 명시되어 있는가?
        - 해당 슬라이드가 실제로 "출제 슬라이드"일 가능성이 높은가?
        - 문제의 정답을 찾기 위해 반드시 필요한 핵심 정보가 포함되어 있는가?
        - 단순히 관련 주제가 아닌, 문제 해결에 직접적으로 필요한 내용인가?
        
        주의사항:
        - 너무 포괄적이거나 일반적인 연관성은 제외하세요
        - 문제와 직접적인 연관이 없는 배경 설명 슬라이드는 제외하세요
        - importance_score는 직접적 연관성이 높을수록 높게 (8-10), 간접적이면 낮게 (1-5) 책정하세요
        
        출력 형식:
        {{
            "related_slides": [
                {{
                    "lesson_page": 페이지번호,
                    "related_jokbo_questions": [
                        {{
                            "jokbo_filename": "{jokbo_filename}",
                            "jokbo_page": 족보페이지번호,
                            "jokbo_end_page": 족보끝페이지번호,  // 문제가 여러 페이지에 걸쳐있을 경우
                            "question_number": 문제번호,
                            "question_text": "문제 내용",
                            "answer": "정답",
                            "explanation": "해설",
                            "wrong_answer_explanations": {{
                                "1번": "왜 1번이 오답인지 설명",
                                "2번": "왜 2번이 오답인지 설명",
                                "3번": "왜 3번이 오답인지 설명",
                                "4번": "왜 4번이 오답인지 설명"
                            }},
                            "relevance_reason": "관련성 이유"
                        }}
                    ],
                    "importance_score": 1-10,
                    "key_concepts": ["핵심개념1", "핵심개념2"]
                }}
            ],
            "summary": {{
                "total_related_slides": 관련된슬라이드수,
                "total_questions": 총관련문제수,
                "key_topics": ["주요주제1", "주요주제2"],
                "study_recommendations": "학습 권장사항"
            }}
        }}
        """
        
        # Upload lesson PDF
        lesson_file = self.upload_pdf(lesson_path, f"강의자료_{Path(lesson_path).name}")
        
        # Upload jokbo PDF
        jokbo_file = self.upload_pdf(jokbo_path, f"족보_{Path(jokbo_path).name}")
        
        # Prepare content for model
        content = [prompt, lesson_file, jokbo_file]
        
        response = self.model.generate_content(content)
        
        try:
            result = json.loads(response.text)
            
            # Force correct filename in all questions
            if "related_slides" in result:
                for slide in result["related_slides"]:
                    if "related_jokbo_questions" in slide:
                        for question in slide["related_jokbo_questions"]:
                            question["jokbo_filename"] = jokbo_filename
            
            return result
        except json.JSONDecodeError:
            print(f"Failed to parse JSON response: {response.text}")
            return {"error": "Failed to parse response"}
    
    def analyze_single_jokbo_with_lesson_preloaded(self, jokbo_path: str, lesson_file) -> Dict[str, Any]:
        """Analyze one jokbo PDF against pre-uploaded lesson file"""
        
        # Extract the actual filename
        jokbo_filename = Path(jokbo_path).name
        
        # Upload only jokbo PDF
        print(f"  [{datetime.now().strftime('%H:%M:%S')}] Thread-{threading.current_thread().ident}: 족보 업로드 시작 - {jokbo_filename}")
        jokbo_file = self.upload_pdf(jokbo_path, f"족보_{jokbo_filename}")
        
        prompt = f"""당신은 병리학 교수입니다. 하나의 족보(기출문제) PDF와 하나의 강의자료 PDF를 비교 분석합니다.

        중요: 족보 파일명은 반드시 "{jokbo_filename}"을 그대로 사용하세요. 파일명을 변경하거나 수정하지 마세요.

        작업:
        1. 족보 PDF의 모든 문제를 분석하세요
        2. 각 족보 문제와 직접적으로 관련된 강의자료 페이지를 찾으세요
        3. 강의자료의 각 페이지별로 관련된 족보 문제들을 그룹화하세요
        4. 각 문제의 정답과 함께 모든 선택지가 왜 오답인지도 설명하세요
        5. 문제가 여러 페이지에 걸쳐있으면 jokbo_end_page에 끝 페이지 번호를 표시하세요
        
        판단 기준 (엄격하게 적용):
        - 족보 문제가 직접적으로 다루는 개념이 해당 강의 슬라이드에 명시되어 있는가?
        - 해당 슬라이드가 실제로 "출제 슬라이드"일 가능성이 높은가?
        - 문제의 정답을 찾기 위해 반드시 필요한 핵심 정보가 포함되어 있는가?
        - 단순히 관련 주제가 아닌, 문제 해결에 직접적으로 필요한 내용인가?
        
        주의사항:
        - 너무 포괄적이거나 일반적인 연관성은 제외하세요
        - 문제와 직접적인 연관이 없는 배경 설명 슬라이드는 제외하세요
        - importance_score는 직접적 연관성이 높을수록 높게 (8-10), 간접적이면 낮게 (1-5) 책정하세요
        
        출력 형식:
        {{
            "related_slides": [
                {{
                    "lesson_page": 페이지번호,
                    "related_jokbo_questions": [
                        {{
                            "jokbo_filename": "{jokbo_filename}",
                            "jokbo_page": 족보페이지번호,
                            "jokbo_end_page": 족보끝페이지번호,  // 문제가 여러 페이지에 걸쳐있을 경우
                            "question_number": 문제번호,
                            "question_text": "문제 내용",
                            "answer": "정답",
                            "explanation": "해설",
                            "wrong_answer_explanations": {{
                                "1번": "왜 1번이 오답인지 설명",
                                "2번": "왜 2번이 오답인지 설명",
                                "3번": "왜 3번이 오답인지 설명",
                                "4번": "왜 4번이 오답인지 설명"
                            }},
                            "relevance_reason": "관련성 이유"
                        }}
                    ],
                    "importance_score": 1-10,
                    "key_concepts": ["핵심개념1", "핵심개념2"]
                }}
            ],
            "summary": {{
                "total_related_slides": 관련된슬라이드수,
                "total_questions": 총관련문제수,
                "key_topics": ["주요주제1", "주요주제2"],
                "study_recommendations": "학습 권장사항"
            }}
        }}
        """
        
        # Prepare content with pre-uploaded lesson file
        content = [prompt, lesson_file, jokbo_file]
        
        print(f"  [{datetime.now().strftime('%H:%M:%S')}] Thread-{threading.current_thread().ident}: AI 분석 시작 - {jokbo_filename}")
        response = self.model.generate_content(content)
        
        try:
            result = json.loads(response.text)
            
            # Force correct filename in all questions
            if "related_slides" in result:
                for slide in result["related_slides"]:
                    if "related_jokbo_questions" in slide:
                        for question in slide["related_jokbo_questions"]:
                            question["jokbo_filename"] = jokbo_filename
            
            print(f"  [{datetime.now().strftime('%H:%M:%S')}] Thread-{threading.current_thread().ident}: 분석 완료 - {jokbo_filename}")
            return result
        except json.JSONDecodeError:
            print(f"Failed to parse JSON response: {response.text}")
            return {"error": "Failed to parse response"}
    
    def analyze_pdfs_for_lesson(self, jokbo_paths: List[str], lesson_path: str) -> Dict[str, Any]:
        """Analyze multiple jokbo PDFs against one lesson PDF by processing each jokbo individually"""
        
        all_related_slides = {}
        total_questions = 0
        all_key_topics = set()
        
        # Process each jokbo file individually
        for jokbo_path in jokbo_paths:
            print(f"  분석 중: {Path(jokbo_path).name}")
            result = self.analyze_single_jokbo_with_lesson(jokbo_path, lesson_path)
            
            if "error" in result:
                print(f"    오류 발생: {result['error']}")
                continue
            
            # Merge results
            for slide in result.get("related_slides", []):
                lesson_page = slide["lesson_page"]
                if lesson_page not in all_related_slides:
                    all_related_slides[lesson_page] = {
                        "lesson_page": lesson_page,
                        "related_jokbo_questions": [],
                        "importance_score": slide.get("importance_score", 5),
                        "key_concepts": set()
                    }
                
                # Add questions from this jokbo
                all_related_slides[lesson_page]["related_jokbo_questions"].extend(
                    slide.get("related_jokbo_questions", [])
                )
                
                # Update importance score (take maximum)
                all_related_slides[lesson_page]["importance_score"] = max(
                    all_related_slides[lesson_page]["importance_score"],
                    slide.get("importance_score", 5)
                )
                
                # Add key concepts
                all_related_slides[lesson_page]["key_concepts"].update(
                    slide.get("key_concepts", [])
                )
            
            # Update summary data
            if "summary" in result:
                total_questions += result["summary"].get("total_questions", 0)
                all_key_topics.update(result["summary"].get("key_topics", []))
        
        # Convert sets to lists and prepare final result
        final_slides = []
        for slide_data in all_related_slides.values():
            slide_data["key_concepts"] = list(slide_data["key_concepts"])
            final_slides.append(slide_data)
        
        # Sort by lesson page number
        final_slides.sort(key=lambda x: x["lesson_page"])
        
        return {
            "related_slides": final_slides,
            "summary": {
                "total_related_slides": len(final_slides),
                "total_questions": total_questions,
                "key_topics": list(all_key_topics),
                "study_recommendations": "각 슬라이드별로 관련된 족보 문제들을 중점적으로 학습하세요."
            }
        }
    
    def analyze_pdfs_for_lesson_parallel(self, jokbo_paths: List[str], lesson_path: str, max_workers: int = 3) -> Dict[str, Any]:
        """Analyze multiple jokbo PDFs against one lesson PDF using parallel processing"""
        
        print(f"  [{datetime.now().strftime('%H:%M:%S')}] 병렬 처리 시작 - 강의자료 업로드 중...")
        
        # Pre-upload lesson file once
        lesson_file = self.upload_pdf(lesson_path, f"강의자료_{Path(lesson_path).name}")
        
        all_related_slides = {}
        total_questions = 0
        all_key_topics = set()
        lock = threading.Lock()
        
        def process_single_jokbo(jokbo_path: str) -> Dict[str, Any]:
            """Process a single jokbo in a thread with independent PDFProcessor"""
            thread_id = threading.current_thread().ident
            print(f"  [{datetime.now().strftime('%H:%M:%S')}] Thread-{thread_id}: 처리 시작 - {Path(jokbo_path).name}")
            
            # Create a new PDFProcessor instance for this thread
            thread_processor = PDFProcessor(self.model)
            
            try:
                result = thread_processor.analyze_single_jokbo_with_lesson_preloaded(jokbo_path, lesson_file)
                return result
            except Exception as e:
                print(f"    [{datetime.now().strftime('%H:%M:%S')}] Thread-{thread_id}: 오류 발생 - {str(e)}")
                return {"error": str(e)}
        
        # Process jokbo files in parallel
        print(f"  [{datetime.now().strftime('%H:%M:%S')}] {len(jokbo_paths)}개 족보 파일 병렬 처리 시작 (max_workers={max_workers})")
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all tasks at once
            future_to_jokbo = {
                executor.submit(process_single_jokbo, jokbo_path): jokbo_path 
                for jokbo_path in jokbo_paths
            }
            
            print(f"  [{datetime.now().strftime('%H:%M:%S')}] 모든 작업 제출 완료 - 동시 처리 중...")
            
            # Process completed tasks
            for future in as_completed(future_to_jokbo):
                jokbo_path = future_to_jokbo[future]
                try:
                    result = future.result()
                    
                    if "error" in result:
                        print(f"    [{datetime.now().strftime('%H:%M:%S')}] 오류 발생: {result['error']}")
                        continue
                    
                    # Merge results (thread-safe)
                    with lock:
                        for slide in result.get("related_slides", []):
                            lesson_page = slide["lesson_page"]
                            if lesson_page not in all_related_slides:
                                all_related_slides[lesson_page] = {
                                    "lesson_page": lesson_page,
                                    "related_jokbo_questions": [],
                                    "importance_score": slide.get("importance_score", 5),
                                    "key_concepts": set()
                                }
                            
                            # Add questions from this jokbo
                            all_related_slides[lesson_page]["related_jokbo_questions"].extend(
                                slide.get("related_jokbo_questions", [])
                            )
                            
                            # Update importance score (take maximum)
                            all_related_slides[lesson_page]["importance_score"] = max(
                                all_related_slides[lesson_page]["importance_score"],
                                slide.get("importance_score", 5)
                            )
                            
                            # Add key concepts
                            all_related_slides[lesson_page]["key_concepts"].update(
                                slide.get("key_concepts", [])
                            )
                        
                        # Update summary data
                        if "summary" in result:
                            total_questions += result["summary"].get("total_questions", 0)
                            all_key_topics.update(result["summary"].get("key_topics", []))
                
                except Exception as e:
                    print(f"    [{datetime.now().strftime('%H:%M:%S')}] 처리 중 오류: {Path(jokbo_path).name} - {str(e)}")
        
        # Convert sets to lists and prepare final result
        final_slides = []
        for slide_data in all_related_slides.values():
            slide_data["key_concepts"] = list(slide_data["key_concepts"])
            final_slides.append(slide_data)
        
        # Sort by lesson page number
        final_slides.sort(key=lambda x: x["lesson_page"])
        
        return {
            "related_slides": final_slides,
            "summary": {
                "total_related_slides": len(final_slides),
                "total_questions": total_questions,
                "key_topics": list(all_key_topics),
                "study_recommendations": "각 슬라이드별로 관련된 족보 문제들을 중점적으로 학습하세요."
            }
        }