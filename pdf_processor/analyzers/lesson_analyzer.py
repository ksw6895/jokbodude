"""Lesson-centric analysis functionality"""

from pathlib import Path
from typing import Dict, Any, List
import pymupdf as fitz
import json
from datetime import datetime
import threading

from prompt_builder import PromptBuilder
from validators import PDFValidator
from pdf_processor.api.gemini_client import GeminiClient
from pdf_processor.parsers.json_parser import JSONParser
from pdf_processor.utils.file_manager import FileManagerUtil


class LessonAnalyzer:
    """Handles lesson-centric PDF analysis"""
    
    def __init__(self, model, file_manager: FileManagerUtil):
        """Initialize lesson analyzer
        
        Args:
            model: Gemini model instance
            file_manager: File manager for uploads
        """
        self.model = model
        self.file_manager = file_manager
        self.gemini_client = GeminiClient(model)
        self.json_parser = JSONParser()
    
    def analyze_pdfs_for_lesson(
        self,
        jokbo_paths: List[str],
        lesson_path: str
    ) -> Dict[str, Any]:
        """Analyze multiple jokbo PDFs against one lesson PDF
        
        Args:
            jokbo_paths: List of jokbo PDF paths
            lesson_path: Path to lesson PDF
            
        Returns:
            Merged analysis results
        """
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
            self._merge_lesson_results(
                result, all_related_slides, all_key_topics
            )
            
            # Update summary data
            if "summary" in result:
                total_questions += result["summary"].get("total_questions", 0)
                all_key_topics.update(result["summary"].get("key_topics", []))
        
        # Convert sets to lists and prepare final result
        final_slides = self._prepare_final_slides(all_related_slides)
        
        return {
            "related_slides": final_slides,
            "summary": {
                "total_related_slides": len(final_slides),
                "total_questions": total_questions,
                "key_topics": list(all_key_topics),
                "study_recommendations": "각 슬라이드별로 관련된 족보 문제들을 중점적으로 학습하세요."
            }
        }
    
    def analyze_single_jokbo_with_lesson(
        self,
        jokbo_path: str,
        lesson_path: str
    ) -> Dict[str, Any]:
        """Analyze one jokbo PDF against one lesson PDF
        
        Args:
            jokbo_path: Path to jokbo PDF
            lesson_path: Path to lesson PDF
            
        Returns:
            Analysis results
        """
        jokbo_filename = Path(jokbo_path).name
        
        # Delete all uploaded files before starting
        print("  기존 업로드 파일 정리 중...")
        self.file_manager.delete_all_uploaded_files()
        
        # Build prompt
        prompt = PromptBuilder.build_lesson_centric_prompt(jokbo_filename)
        
        # Upload files
        lesson_file = self.file_manager.upload_pdf(
            lesson_path, f"강의자료_{Path(lesson_path).name}"
        )
        jokbo_file = self.file_manager.upload_pdf(
            jokbo_path, f"족보_{jokbo_filename}"
        )
        
        # Prepare content for model
        content = [prompt, lesson_file, jokbo_file]
        
        try:
            response = self.gemini_client.generate_content_with_retry(content)
            
            # Parse response
            result = self.json_parser.parse_response_json(
                response.text, "lesson-centric"
            )
            
            # Get total pages in jokbo PDF for validation
            with fitz.open(str(jokbo_path)) as pdf:
                total_jokbo_pages = len(pdf)
            
            # Validate and filter results
            if "related_slides" in result:
                for slide in result["related_slides"]:
                    if "related_jokbo_questions" in slide:
                        slide["related_jokbo_questions"] = PDFValidator.filter_valid_questions(
                            slide["related_jokbo_questions"],
                            total_jokbo_pages,
                            jokbo_filename
                        )
            
            return result
            
        except Exception as e:
            print(f"  분석 오류: {str(e)}")
            # Try partial parsing
            partial_result = self.json_parser.parse_partial_json(
                response.text if 'response' in locals() else "",
                "lesson-centric"
            )
            if "error" not in partial_result or partial_result.get("related_slides"):
                print(f"  부분 파싱으로 일부 데이터 복구 성공")
                return partial_result
            return {"error": str(e)}
        finally:
            # Clean up files
            self.file_manager.delete_file_safe(jokbo_file)
            self.file_manager.cleanup_except_center_file(lesson_file.display_name)
    
    def analyze_single_jokbo_with_lesson_preloaded(
        self,
        jokbo_path: str,
        lesson_file
    ) -> Dict[str, Any]:
        """Analyze one jokbo against pre-uploaded lesson file
        
        Args:
            jokbo_path: Path to jokbo PDF
            lesson_file: Pre-uploaded lesson file object
            
        Returns:
            Analysis results
        """
        jokbo_filename = Path(jokbo_path).name
        
        # Upload only jokbo PDF
        print(f"  [{datetime.now().strftime('%H:%M:%S')}] "
              f"Thread-{threading.current_thread().ident}: "
              f"족보 업로드 시작 - {jokbo_filename}")
        
        jokbo_file = self.file_manager.upload_pdf(
            jokbo_path, f"족보_{jokbo_filename}"
        )
        
        # Build prompt
        prompt = PromptBuilder.build_lesson_centric_prompt(jokbo_filename)
        
        # Prepare content with pre-uploaded lesson file
        content = [prompt, lesson_file, jokbo_file]
        
        print(f"  [{datetime.now().strftime('%H:%M:%S')}] "
              f"Thread-{threading.current_thread().ident}: "
              f"AI 분석 시작 - {jokbo_filename}")
        
        try:
            response = self.gemini_client.generate_content_with_retry(content)
            result = self.json_parser.parse_response_json(
                response.text, "lesson-centric"
            )
            
            # Get total pages for validation
            with fitz.open(str(jokbo_path)) as pdf:
                total_jokbo_pages = len(pdf)
            
            # Validate results
            if "related_slides" in result:
                for slide in result["related_slides"]:
                    if "related_jokbo_questions" in slide:
                        slide["related_jokbo_questions"] = PDFValidator.filter_valid_questions(
                            slide["related_jokbo_questions"],
                            total_jokbo_pages,
                            jokbo_filename
                        )
            
            print(f"  [{datetime.now().strftime('%H:%M:%S')}] "
                  f"Thread-{threading.current_thread().ident}: "
                  f"분석 완료 - {jokbo_filename}")
            
            return result
            
        except Exception as e:
            print(f"  JSON 파싱 실패: {str(e)}")
            return {"error": str(e)}
        finally:
            # Clean up jokbo file
            print(f"  [{datetime.now().strftime('%H:%M:%S')}] "
                  f"Thread-{threading.current_thread().ident}: "
                  f"족보 파일 삭제 중 - {jokbo_filename}")
            
            if not self.file_manager.delete_file_safe(jokbo_file):
                self.file_manager.cleanup_except_center_file(lesson_file.display_name)
    
    def _merge_lesson_results(
        self,
        result: Dict[str, Any],
        all_related_slides: Dict,
        all_key_topics: set
    ):
        """Merge lesson analysis results
        
        Args:
            result: Single analysis result
            all_related_slides: Accumulated slides dictionary
            all_key_topics: Set of key topics
        """
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
    
    def _prepare_final_slides(self, all_related_slides: Dict) -> List[Dict]:
        """Prepare final slides list from merged results
        
        Args:
            all_related_slides: Dictionary of slides by page number
            
        Returns:
            Sorted list of slide dictionaries
        """
        final_slides = []
        
        for slide_data in all_related_slides.values():
            slide_data["key_concepts"] = list(slide_data["key_concepts"])
            final_slides.append(slide_data)
        
        # Sort by lesson page number
        final_slides.sort(key=lambda x: x["lesson_page"])
        
        return final_slides