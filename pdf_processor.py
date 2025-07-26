import json
from pathlib import Path
import pymupdf as fitz
from typing import List, Dict, Any, Tuple, TYPE_CHECKING
import google.generativeai as genai
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
from datetime import datetime
import os

if TYPE_CHECKING:
    from google.generativeai.types import file_types

class PDFProcessor:
    def __init__(self, model):
        self.model = model
        self.uploaded_files = []
        # Create debug directory if it doesn't exist
        self.debug_dir = Path("output/debug")
        self.debug_dir.mkdir(parents=True, exist_ok=True)
    
    def __del__(self):
        """Clean up uploaded files when object is destroyed"""
        for file in self.uploaded_files:
            try:
                genai.delete_file(file.name)
                print(f"  Deleted uploaded file: {file.display_name}")
            except Exception as e:
                print(f"  Failed to delete file {file.display_name}: {e}")
    
    def list_uploaded_files(self):
        """List all uploaded files in the account"""
        try:
            files = list(genai.list_files())
            return files
        except Exception as e:
            print(f"  Failed to list files: {e}")
            return []
    
    def delete_all_uploaded_files(self):
        """Delete all uploaded files from the account"""
        files = self.list_uploaded_files()
        deleted_count = 0
        failed_count = 0
        
        for file in files:
            try:
                genai.delete_file(file.name)
                deleted_count += 1
                print(f"  Deleted file: {file.display_name}")
            except Exception as e:
                failed_count += 1
                print(f"  Failed to delete file {file.display_name}: {e}")
        
        if deleted_count > 0:
            print(f"  Total deleted: {deleted_count} files")
        if failed_count > 0:
            print(f"  Total failed: {failed_count} files")
        
        return deleted_count, failed_count
    
    def delete_file_safe(self, file):
        """Safely delete a file with retry logic"""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                genai.delete_file(file.name)
                print(f"  Deleted file: {file.display_name}")
                return True
            except Exception as e:
                if attempt < max_retries - 1:
                    print(f"  Retry {attempt + 1}/{max_retries}: Failed to delete {file.display_name}")
                    time.sleep(2)
                else:
                    print(f"  Failed to delete file {file.display_name} after {max_retries} attempts: {e}")
                    # Try listing files and deleting jokbo files
                    files = self.list_uploaded_files()
                    for f in files:
                        if f.display_name.startswith("족보_"):
                            try:
                                genai.delete_file(f.name)
                                print(f"  Cleanup: Deleted {f.display_name}")
                            except:
                                pass
                    return False
    
    def save_api_response(self, response_text: str, jokbo_filename: str, lesson_filename: str = None, mode: str = "lesson-centric"):
        """Save Gemini API response to a file for debugging"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        if mode == "lesson-centric":
            filename = f"gemini_response_{timestamp}_{Path(lesson_filename).stem if lesson_filename else 'unknown'}_{Path(jokbo_filename).stem}.json"
        else:
            filename = f"gemini_response_{timestamp}_jokbo_{Path(jokbo_filename).stem}_{Path(lesson_filename).stem if lesson_filename else 'all'}.json"
        
        filepath = self.debug_dir / filename
        
        debug_data = {
            "timestamp": timestamp,
            "mode": mode,
            "jokbo_file": jokbo_filename,
            "lesson_file": lesson_filename,
            "response_text": response_text,
            "response_length": len(response_text)
        }
        
        try:
            # Try to parse as JSON to check if it's valid
            parsed = json.loads(response_text)
            debug_data["parsed_successfully"] = True
            debug_data["parsed_data"] = parsed
        except json.JSONDecodeError as e:
            debug_data["parsed_successfully"] = False
            debug_data["parse_error"] = str(e)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(debug_data, f, ensure_ascii=False, indent=2)
        
        print(f"  Debug: API response saved to {filepath}")
    
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
        
        # Delete all uploaded files before starting
        print("  기존 업로드 파일 정리 중...")
        self.delete_all_uploaded_files()
        
        prompt = f"""당신은 병리학 교수입니다. 하나의 족보(기출문제) PDF와 하나의 강의자료 PDF를 비교 분석합니다.

        중요: 족보 파일명은 반드시 "{jokbo_filename}"을 그대로 사용하세요. 파일명을 변경하거나 수정하지 마세요.

        작업:
        1. 족보 PDF의 모든 문제를 분석하세요
        2. 각 족보 문제와 직접적으로 관련된 강의자료 페이지를 찾으세요
        3. 강의자료의 각 페이지별로 관련된 족보 문제들을 그룹화하세요
        4. 각 문제의 정답과 함께 모든 선택지가 왜 오답인지도 설명하세요
        5. 문제가 여러 페이지에 걸쳐있으면 jokbo_end_page에 끝 페이지 번호를 표시하세요
        
        **매우 중요한 주의사항**:
        - question_number는 반드시 족보 PDF에 표시된 실제 문제 번호를 사용하세요 (예: 21번, 42번 등)
        - 족보 페이지 내에서의 순서(1번째, 2번째)가 아닌, 실제 문제 번호를 확인하세요
        - 만약 문제 번호가 명확하지 않으면 "번호없음"이라고 표시하세요
        - jokbo_page는 반드시 해당 문제가 실제로 있는 PDF의 페이지 번호를 정확히 기입하세요
        
        **절대적 주의사항 - 강의자료 내 문제 제외**:
        - 강의자료 PDF 내에 포함된 예제 문제나 연습 문제는 절대 추출하지 마세요
        - 오직 족보 PDF 파일("{jokbo_filename}")에 있는 문제만을 대상으로 분석하세요
        - jokbo_page는 반드시 족보 PDF의 페이지 번호여야 하며, 강의자료의 페이지 번호를 사용하면 안 됩니다
        - 강의자료는 오직 참고 자료로만 사용하고, 문제는 족보에서만 추출하세요
        
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
        
        # Save API response for debugging
        self.save_api_response(response.text, jokbo_filename, Path(lesson_path).name, "lesson-centric")
        
        # Delete jokbo file immediately after analysis
        self.delete_file_safe(jokbo_file)
        
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
        
        **매우 중요한 주의사항**:
        - question_number는 반드시 족보 PDF에 표시된 실제 문제 번호를 사용하세요 (예: 21번, 42번 등)
        - 족보 페이지 내에서의 순서(1번째, 2번째)가 아닌, 실제 문제 번호를 확인하세요
        - 만약 문제 번호가 명확하지 않으면 "번호없음"이라고 표시하세요
        - jokbo_page는 반드시 해당 문제가 실제로 있는 PDF의 페이지 번호를 정확히 기입하세요
        
        **절대적 주의사항 - 강의자료 내 문제 제외**:
        - 강의자료 PDF 내에 포함된 예제 문제나 연습 문제는 절대 추출하지 마세요
        - 오직 족보 PDF 파일("{jokbo_filename}")에 있는 문제만을 대상으로 분석하세요
        - jokbo_page는 반드시 족보 PDF의 페이지 번호여야 하며, 강의자료의 페이지 번호를 사용하면 안 됩니다
        - 강의자료는 오직 참고 자료로만 사용하고, 문제는 족보에서만 추출하세요
        
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
        
        # Save API response for debugging
        self.save_api_response(response.text, jokbo_filename, lesson_file.display_name.replace("강의자료_", ""), "lesson-centric")
        
        # Delete jokbo file immediately after analysis
        print(f"  [{datetime.now().strftime('%H:%M:%S')}] Thread-{threading.current_thread().ident}: 족보 파일 삭제 중 - {jokbo_filename}")
        self.delete_file_safe(jokbo_file)
        
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
    
    def analyze_single_lesson_with_jokbo(self, lesson_path: str, jokbo_path: str) -> Dict[str, Any]:
        """Analyze one lesson PDF against one jokbo PDF (jokbo-centric)"""
        
        # Extract the actual filename
        jokbo_filename = Path(jokbo_path).name
        lesson_filename = Path(lesson_path).name
        
        # Delete all uploaded files before starting
        print("  기존 업로드 파일 정리 중...")
        self.delete_all_uploaded_files()
        
        prompt = f"""당신은 병리학 교수입니다. 하나의 족보(기출문제) PDF와 하나의 강의자료 PDF를 비교 분석합니다.

        중요: 족보 파일명은 반드시 "{jokbo_filename}"을 그대로 사용하세요.
        중요: 강의자료 파일명은 반드시 "{lesson_filename}"을 그대로 사용하세요.

        작업 (족보 중심 분석):
        1. 족보 PDF의 모든 문제를 페이지 순서대로 분석하세요
        2. 각 족보 문제와 관련된 강의자료 슬라이드를 찾으세요
        3. 족보의 각 페이지별로 관련된 강의 슬라이드들을 그룹화하세요
        
        **매우 중요한 주의사항**:
        - question_number는 반드시 족보 PDF에 표시된 실제 문제 번호를 사용하세요
        - jokbo_page는 반드시 해당 문제가 실제로 있는 PDF의 페이지 번호를 정확히 기입하세요
        
        **절대적 주의사항 - 강의자료 내 문제 제외**:
        - 강의자료 PDF 내에 포함된 예제 문제나 연습 문제는 절대 분석하지 마세요
        - 오직 족보 PDF 파일("{jokbo_filename}")에 있는 문제만을 대상으로 분석하세요
        - 강의자료는 오직 참고 자료로만 사용하고, 문제는 족보에서만 추출하세요
        
        출력 형식:
        {{
            "jokbo_pages": [
                {{
                    "jokbo_page": 족보페이지번호,
                    "questions": [
                        {{
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
                            "related_lesson_slides": [
                                {{
                                    "lesson_filename": "{lesson_filename}",
                                    "lesson_page": 강의페이지번호,
                                    "relevance_reason": "관련성 이유"
                                }}
                            ]
                        }}
                    ]
                }}
            ],
            "summary": {{
                "total_jokbo_pages": 총족보페이지수,
                "total_questions": 총문제수,
                "total_related_slides": 관련된강의슬라이드수
            }}
        }}
        """
        
        # Upload jokbo PDF first
        jokbo_file = self.upload_pdf(jokbo_path, f"족보_{jokbo_filename}")
        
        # Upload lesson PDF
        lesson_file = self.upload_pdf(lesson_path, f"강의자료_{lesson_filename}")
        
        # Prepare content for model
        content = [prompt, jokbo_file, lesson_file]
        
        response = self.model.generate_content(content)
        
        # Save API response for debugging
        self.save_api_response(response.text, jokbo_filename, lesson_filename, "jokbo-centric")
        
        # Delete lesson file immediately after analysis
        self.delete_file_safe(lesson_file)
        
        try:
            result = json.loads(response.text)
            return result
        except json.JSONDecodeError:
            print(f"Failed to parse JSON response: {response.text}")
            return {"error": "Failed to parse response"}
    
    def analyze_lessons_for_jokbo(self, lesson_paths: List[str], jokbo_path: str) -> Dict[str, Any]:
        """Analyze multiple lesson PDFs against one jokbo PDF (jokbo-centric)"""
        
        all_jokbo_pages = {}
        total_related_slides = 0
        
        # Process each lesson file individually
        for lesson_path in lesson_paths:
            print(f"  분석 중: {Path(lesson_path).name}")
            result = self.analyze_single_lesson_with_jokbo(lesson_path, jokbo_path)
            
            if "error" in result:
                print(f"    오류 발생: {result['error']}")
                continue
            
            # Merge results
            for page_info in result.get("jokbo_pages", []):
                jokbo_page = page_info["jokbo_page"]
                if jokbo_page not in all_jokbo_pages:
                    all_jokbo_pages[jokbo_page] = {
                        "jokbo_page": jokbo_page,
                        "questions": []
                    }
                
                # Process each question on this page
                for question in page_info.get("questions", []):
                    # Find if this question already exists
                    existing_question = None
                    for q in all_jokbo_pages[jokbo_page]["questions"]:
                        if q["question_number"] == question["question_number"]:
                            existing_question = q
                            break
                    
                    if existing_question:
                        # Add new related slides to existing question
                        existing_question["related_lesson_slides"].extend(
                            question.get("related_lesson_slides", [])
                        )
                    else:
                        # Add new question
                        all_jokbo_pages[jokbo_page]["questions"].append(question)
                        total_related_slides += len(question.get("related_lesson_slides", []))
        
        # Convert dict to list and sort by page number
        final_pages = list(all_jokbo_pages.values())
        final_pages.sort(key=lambda x: x["jokbo_page"])
        
        # Count total questions
        total_questions = sum(len(page["questions"]) for page in final_pages)
        
        return {
            "jokbo_pages": final_pages,
            "summary": {
                "total_jokbo_pages": len(final_pages),
                "total_questions": total_questions,
                "total_related_slides": total_related_slides,
                "study_recommendations": "각 족보 문제별로 관련된 강의 슬라이드를 중점적으로 학습하세요."
            }
        }
    
    def analyze_single_lesson_with_jokbo_preloaded(self, lesson_path: str, jokbo_file) -> Dict[str, Any]:
        """Analyze one lesson PDF against pre-uploaded jokbo file (jokbo-centric)"""
        
        # Extract the actual filename
        lesson_filename = Path(lesson_path).name
        jokbo_filename = jokbo_file.display_name.replace("족보_", "")
        
        # Upload only lesson PDF
        print(f"  [{datetime.now().strftime('%H:%M:%S')}] Thread-{threading.current_thread().ident}: 강의자료 업로드 시작 - {lesson_filename}")
        lesson_file = self.upload_pdf(lesson_path, f"강의자료_{lesson_filename}")
        
        prompt = f"""당신은 병리학 교수입니다. 하나의 족보(기출문제) PDF와 하나의 강의자료 PDF를 비교 분석합니다.

        중요: 족보 파일명은 반드시 "{jokbo_filename}"을 그대로 사용하세요.
        중요: 강의자료 파일명은 반드시 "{lesson_filename}"을 그대로 사용하세요.

        작업 (족보 중심 분석):
        1. 족보 PDF의 모든 문제를 페이지 순서대로 분석하세요
        2. 각 족보 문제와 관련된 강의자료 슬라이드를 찾으세요
        3. 족보의 각 페이지별로 관련된 강의 슬라이드들을 그룹화하세요
        
        **매우 중요한 주의사항**:
        - question_number는 반드시 족보 PDF에 표시된 실제 문제 번호를 사용하세요
        - jokbo_page는 반드시 해당 문제가 실제로 있는 PDF의 페이지 번호를 정확히 기입하세요
        
        **절대적 주의사항 - 강의자료 내 문제 제외**:
        - 강의자료 PDF 내에 포함된 예제 문제나 연습 문제는 절대 분석하지 마세요
        - 오직 족보 PDF 파일("{jokbo_filename}")에 있는 문제만을 대상으로 분석하세요
        - 강의자료는 오직 참고 자료로만 사용하고, 문제는 족보에서만 추출하세요
        
        출력 형식:
        {{
            "jokbo_pages": [
                {{
                    "jokbo_page": 족보페이지번호,
                    "questions": [
                        {{
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
                            "related_lesson_slides": [
                                {{
                                    "lesson_filename": "{lesson_filename}",
                                    "lesson_page": 강의페이지번호,
                                    "relevance_reason": "관련성 이유"
                                }}
                            ]
                        }}
                    ]
                }}
            ],
            "summary": {{
                "total_jokbo_pages": 총족보페이지수,
                "total_questions": 총문제수,
                "total_related_slides": 관련된강의슬라이드수
            }}
        }}
        """
        
        # Prepare content with pre-uploaded jokbo file
        content = [prompt, jokbo_file, lesson_file]
        
        print(f"  [{datetime.now().strftime('%H:%M:%S')}] Thread-{threading.current_thread().ident}: AI 분석 시작 - {lesson_filename}")
        response = self.model.generate_content(content)
        
        # Save API response for debugging
        self.save_api_response(response.text, jokbo_filename, lesson_filename, "jokbo-centric")
        
        # Delete lesson file immediately after analysis
        print(f"  [{datetime.now().strftime('%H:%M:%S')}] Thread-{threading.current_thread().ident}: 강의자료 파일 삭제 중 - {lesson_filename}")
        self.delete_file_safe(lesson_file)
        
        try:
            result = json.loads(response.text)
            print(f"  [{datetime.now().strftime('%H:%M:%S')}] Thread-{threading.current_thread().ident}: 분석 완료 - {lesson_filename}")
            return result
        except json.JSONDecodeError:
            print(f"Failed to parse JSON response: {response.text}")
            return {"error": "Failed to parse response"}
    
    def analyze_lessons_for_jokbo_parallel(self, lesson_paths: List[str], jokbo_path: str, max_workers: int = 3) -> Dict[str, Any]:
        """Analyze multiple lesson PDFs against one jokbo PDF using parallel processing (jokbo-centric)"""
        
        print(f"  [{datetime.now().strftime('%H:%M:%S')}] 병렬 처리 시작 (족보 중심)")
        
        # Delete all uploaded files before starting
        print("  기존 업로드 파일 정리 중...")
        self.delete_all_uploaded_files()
        
        print(f"  [{datetime.now().strftime('%H:%M:%S')}] 족보 업로드 중...")
        # Pre-upload jokbo file once
        jokbo_file = self.upload_pdf(jokbo_path, f"족보_{Path(jokbo_path).name}")
        
        all_jokbo_pages = {}
        total_related_slides = 0
        lock = threading.Lock()
        
        def process_single_lesson(lesson_path: str) -> Dict[str, Any]:
            """Process a single lesson in a thread with independent PDFProcessor"""
            thread_id = threading.current_thread().ident
            print(f"  [{datetime.now().strftime('%H:%M:%S')}] Thread-{thread_id}: 처리 시작 - {Path(lesson_path).name}")
            
            # Create a new PDFProcessor instance for this thread
            thread_processor = PDFProcessor(self.model)
            
            try:
                result = thread_processor.analyze_single_lesson_with_jokbo_preloaded(lesson_path, jokbo_file)
                return result
            except Exception as e:
                print(f"    [{datetime.now().strftime('%H:%M:%S')}] Thread-{thread_id}: 오류 발생 - {str(e)}")
                return {"error": str(e)}
        
        # Process lesson files in parallel
        print(f"  [{datetime.now().strftime('%H:%M:%S')}] {len(lesson_paths)}개 강의자료 파일 병렬 처리 시작 (max_workers={max_workers})")
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all tasks at once
            future_to_lesson = {
                executor.submit(process_single_lesson, lesson_path): lesson_path 
                for lesson_path in lesson_paths
            }
            
            print(f"  [{datetime.now().strftime('%H:%M:%S')}] 모든 작업 제출 완료 - 동시 처리 중...")
            
            # Process completed tasks
            for future in as_completed(future_to_lesson):
                lesson_path = future_to_lesson[future]
                try:
                    result = future.result()
                    
                    if "error" in result:
                        print(f"    [{datetime.now().strftime('%H:%M:%S')}] 오류 발생: {result['error']}")
                        continue
                    
                    # Merge results (thread-safe)
                    with lock:
                        for page_info in result.get("jokbo_pages", []):
                            jokbo_page = page_info["jokbo_page"]
                            if jokbo_page not in all_jokbo_pages:
                                all_jokbo_pages[jokbo_page] = {
                                    "jokbo_page": jokbo_page,
                                    "questions": []
                                }
                            
                            # Process each question on this page
                            for question in page_info.get("questions", []):
                                # Find if this question already exists
                                existing_question = None
                                for q in all_jokbo_pages[jokbo_page]["questions"]:
                                    if q["question_number"] == question["question_number"]:
                                        existing_question = q
                                        break
                                
                                if existing_question:
                                    # Add new related slides to existing question
                                    existing_question["related_lesson_slides"].extend(
                                        question.get("related_lesson_slides", [])
                                    )
                                else:
                                    # Add new question
                                    all_jokbo_pages[jokbo_page]["questions"].append(question)
                                    total_related_slides += len(question.get("related_lesson_slides", []))
                
                except Exception as e:
                    print(f"    [{datetime.now().strftime('%H:%M:%S')}] 처리 중 오류: {Path(lesson_path).name} - {str(e)}")
        
        # Convert dict to list and sort by page number
        final_pages = list(all_jokbo_pages.values())
        final_pages.sort(key=lambda x: x["jokbo_page"])
        
        # Count total questions
        total_questions = sum(len(page["questions"]) for page in final_pages)
        
        return {
            "jokbo_pages": final_pages,
            "summary": {
                "total_jokbo_pages": len(final_pages),
                "total_questions": total_questions,
                "total_related_slides": total_related_slides,
                "study_recommendations": "각 족보 문제별로 관련된 강의 슬라이드를 중점적으로 학습하세요."
            }
        }
    
    def analyze_pdfs_for_lesson_parallel(self, jokbo_paths: List[str], lesson_path: str, max_workers: int = 3) -> Dict[str, Any]:
        """Analyze multiple jokbo PDFs against one lesson PDF using parallel processing"""
        
        print(f"  [{datetime.now().strftime('%H:%M:%S')}] 병렬 처리 시작")
        
        # Delete all uploaded files before starting
        print("  기존 업로드 파일 정리 중...")
        self.delete_all_uploaded_files()
        
        print(f"  [{datetime.now().strftime('%H:%M:%S')}] 강의자료 업로드 중...")
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