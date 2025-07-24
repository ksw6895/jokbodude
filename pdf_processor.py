import json
from pathlib import Path
import pymupdf as fitz
from typing import List, Dict, Any, Tuple, TYPE_CHECKING
import google.generativeai as genai
import time

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
            print(f"  Processing {display_name}...")
            time.sleep(2)
            uploaded_file = genai.get_file(uploaded_file.name)
        
        if uploaded_file.state.name == "FAILED":
            raise ValueError(f"File processing failed: {display_name}")
            
        print(f"  Uploaded: {display_name}")
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
        2. 각 족보 문제와 관련된 강의자료 페이지를 찾으세요
        3. 강의자료의 각 페이지별로 관련된 족보 문제들을 그룹화하세요
        
        판단 기준:
        - 족보 문제에서 다루는 주제가 강의자료에 나오는가?
        - 족보 문제의 정답을 이해하는데 해당 강의자료가 도움이 되는가?
        - 시험에 나올 가능성이 높은 내용인가?
        
        출력 형식:
        {{
            "related_slides": [
                {{
                    "lesson_page": 페이지번호,
                    "related_jokbo_questions": [
                        {{
                            "jokbo_filename": "{jokbo_filename}",
                            "jokbo_page": 족보페이지번호,
                            "question_number": 문제번호,
                            "question_text": "문제 내용",
                            "answer": "정답",
                            "explanation": "해설",
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