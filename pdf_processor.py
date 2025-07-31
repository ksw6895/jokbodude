import json
from pathlib import Path
import pymupdf as fitz
from typing import List, Dict, Any, Tuple, TYPE_CHECKING, Optional
import google.generativeai as genai
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
from datetime import datetime
import os
import shutil
try:
    from tqdm import tqdm
    TQDM_AVAILABLE = True
except ImportError:
    TQDM_AVAILABLE = False
from constants import (
    COMMON_PROMPT_INTRO, COMMON_WARNINGS, RELEVANCE_CRITERIA,
    LESSON_CENTRIC_TASK, LESSON_CENTRIC_OUTPUT_FORMAT,
    JOKBO_CENTRIC_TASK, JOKBO_CENTRIC_OUTPUT_FORMAT,
    MAX_CONNECTIONS_PER_QUESTION, RELEVANCE_SCORE_THRESHOLD
)
from validators import PDFValidator
from pdf_processor_helpers import PDFProcessorHelpers
from error_handler import ErrorHandler
import random
import string

if TYPE_CHECKING:
    from google.generativeai.types import file_types

class PDFProcessor:
    def __init__(self, model, session_id=None):
        self.model = model
        self.uploaded_files = []
        # Create debug directory if it doesn't exist
        self.debug_dir = Path("output/debug")
        self.debug_dir.mkdir(parents=True, exist_ok=True)
        # Cache for PDF page counts
        self.pdf_page_counts = {}
        
        # 세션 식별자 시스템
        if session_id:
            # 기존 세션 ID 사용
            self.session_id = session_id
        else:
            # 새 세션 ID 생성
            self.session_id = self._generate_session_id()
            
        self.session_dir = Path("output/temp/sessions") / self.session_id
        self.chunk_results_dir = self.session_dir / "chunk_results"
        self.chunk_results_dir.mkdir(parents=True, exist_ok=True)
        
        # 세션 ID 출력 (새로 생성된 경우에만)
        if not session_id:
            print(f"세션 ID: {self.session_id}")
    
    def _generate_session_id(self) -> str:
        """세션 ID 생성 (타임스탬프 + 랜덤 문자)"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        random_suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
        return f"{timestamp}_{random_suffix}"
    
    def __del__(self):
        """Clean up uploaded files when object is destroyed"""
        for file in self.uploaded_files:
            try:
                genai.delete_file(file.name)
                print(f"  Deleted uploaded file: {file.display_name}")
            except Exception as e:
                ErrorHandler.handle_file_error("delete", Path(file.display_name), e)
    
    def get_pdf_page_count(self, pdf_path: str) -> int:
        """Get total page count of a PDF file (cached)
        
        Args:
            pdf_path: Path to the PDF file
            
        Returns:
            Total number of pages in the PDF
        """
        if pdf_path not in self.pdf_page_counts:
            self.pdf_page_counts[pdf_path] = PDFValidator.get_pdf_page_count(pdf_path)
        return self.pdf_page_counts[pdf_path]
    
    def validate_and_adjust_page_number(self, page_num: int, start_page: int, end_page: int, 
                                      total_pages: int, chunk_path: str) -> Optional[int]:
        """Validate and adjust page number with retry logic (wrapper for validator)"""
        return PDFValidator.validate_and_adjust_page_number(
            page_num, start_page, end_page, total_pages, chunk_path
        )
    
    def split_pdf_for_analysis(self, pdf_path: str, max_pages: int = 40) -> List[Tuple[str, int, int]]:
        """Split PDF into smaller chunks for analysis
        
        Args:
            pdf_path: Path to the PDF file
            max_pages: Maximum pages per chunk (default: 40)
            
        Returns:
            List of tuples (pdf_path, start_page, end_page)
        """
        pdf_path = Path(pdf_path)
        with fitz.open(str(pdf_path)) as pdf:
            total_pages = len(pdf)
        
        if total_pages <= max_pages:
            # Small enough to process as one chunk
            return [(str(pdf_path), 1, total_pages)]
        
        # Split into chunks
        chunks = []
        for start in range(0, total_pages, max_pages):
            end = min(start + max_pages, total_pages)
            chunks.append((str(pdf_path), start + 1, end))
        
        print(f"  Split {pdf_path.name} into {len(chunks)} chunks ({total_pages} pages total)")
        return chunks
    
    def extract_pdf_pages(self, pdf_path: str, start_page: int, end_page: int) -> str:
        """Extract specific pages from PDF and save to temporary file
        
        Args:
            pdf_path: Path to source PDF
            start_page: First page to extract (1-based)
            end_page: Last page to extract (1-based)
            
        Returns:
            Path to temporary PDF file
        """
        import tempfile
        
        with fitz.open(str(pdf_path)) as src_pdf:
            # Create new PDF with selected pages
            output = fitz.open()
            
            # Convert to 0-based indexing
            for page_num in range(start_page - 1, end_page):
                if page_num < len(src_pdf):
                    output.insert_pdf(src_pdf, from_page=page_num, to_page=page_num)
            
            # Save to temporary file
            temp_file = tempfile.NamedTemporaryFile(suffix='.pdf', delete=False)
            output.save(temp_file.name)
            output.close()
            
            return temp_file.name
    
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
                    return False
    
    def cleanup_except_center_file(self, center_file_display_name: str):
        """중심 파일을 제외한 모든 파일 삭제"""
        files = self.list_uploaded_files()
        deleted_count = 0
        failed_count = 0
        
        for file in files:
            # 중심 파일이 아닌 경우만 삭제
            if file.display_name != center_file_display_name:
                try:
                    genai.delete_file(file.name)
                    deleted_count += 1
                    print(f"  Deleted non-center file: {file.display_name}")
                except Exception as e:
                    failed_count += 1
                    print(f"  Failed to delete non-center file {file.display_name}: {e}")
            else:
                print(f"  Keeping center file: {file.display_name}")
        
        if deleted_count > 0:
            print(f"  Cleaned up {deleted_count} files (kept center file)")
        if failed_count > 0:
            print(f"  Failed to clean up {failed_count} files")
        
        return deleted_count, failed_count
    
    def generate_content_with_retry(self, content, max_retries=3, backoff_factor=2):
        """Generate content with exponential backoff retry for parallel mode"""
        for attempt in range(max_retries):
            try:
                response = self.model.generate_content(content)
                
                # Check if response is complete JSON
                try:
                    json.loads(response.text)
                    return response
                except json.JSONDecodeError as json_error:
                    # If this is the last attempt, return the incomplete response
                    if attempt == max_retries - 1:
                        print(f"  경고: 불완전한 JSON 응답 (길이: {len(response.text)})")
                        return response
                    else:
                        print(f"  재시도 {attempt + 1}/{max_retries}: 불완전한 JSON 응답 감지 (길이: {len(response.text)})")
                        wait_time = backoff_factor ** attempt
                        time.sleep(wait_time)
                        continue
                        
            except Exception as e:
                if attempt == max_retries - 1:
                    raise
                wait_time = backoff_factor ** attempt
                print(f"  재시도 {attempt + 1}/{max_retries} after {wait_time}s: {str(e)[:50]}...")
                time.sleep(wait_time)
    
    def parse_partial_json(self, response_text: str, mode: str = "jokbo-centric") -> Dict[str, Any]:
        """Try to parse partial JSON response and salvage what's possible"""
        print(f"  부분 JSON 파싱 시도 중... (응답 길이: {len(response_text)})")
        
        if mode == "jokbo-centric":
            # Try to extract complete jokbo_pages entries
            try:
                # Find the last complete "questions" array closing
                # Look for pattern: }]}]} which typically ends a complete page entry
                import re
                
                # Find all complete page objects
                pages = []
                page_pattern = r'\{\s*"jokbo_page"\s*:\s*\d+\s*,\s*"questions"\s*:\s*\[.*?\]\s*\}'
                
                # Try to find the jokbo_pages array start
                jokbo_pages_start = response_text.find('"jokbo_pages"')
                if jokbo_pages_start == -1:
                    return {"error": "No jokbo_pages found", "partial": True}
                
                # Extract the content after jokbo_pages
                content_after_pages = response_text[jokbo_pages_start:]
                
                # Try progressive closing of brackets
                for i in range(len(content_after_pages), max(0, len(content_after_pages) - 10000), -100):
                    test_json = '{' + content_after_pages[:i]
                    
                    # Count open brackets and try to close them
                    open_braces = test_json.count('{') - test_json.count('}')
                    open_brackets = test_json.count('[') - test_json.count(']')
                    
                    # Add closing brackets
                    test_json += ']' * open_brackets + '}' * open_braces
                    
                    try:
                        parsed = json.loads(test_json)
                        if "jokbo_pages" in parsed and len(parsed["jokbo_pages"]) > 0:
                            print(f"  부분 파싱 성공! {len(parsed['jokbo_pages'])}개 페이지 복구")
                            parsed["partial"] = True
                            parsed["recovered_pages"] = len(parsed["jokbo_pages"])
                            return parsed
                    except json.JSONDecodeError:
                        continue
                
                return {"error": "Failed to parse even partially", "partial": True}
                
            except Exception as e:
                print(f"  부분 파싱 실패: {str(e)}")
                return {"error": str(e), "partial": True}
        
        elif mode == "lesson-centric":
            # Try to extract complete related_slides entries
            try:
                # Try to find the related_slides array start
                related_slides_start = response_text.find('"related_slides"')
                if related_slides_start == -1:
                    return {"error": "No related_slides found", "partial": True}
                
                # Extract the content after related_slides
                content_after_slides = response_text[related_slides_start:]
                
                # Try progressive closing of brackets
                for i in range(len(content_after_slides), max(0, len(content_after_slides) - 10000), -100):
                    test_json = '{' + content_after_slides[:i]
                    
                    # Count open brackets and try to close them
                    open_braces = test_json.count('{') - test_json.count('}')
                    open_brackets = test_json.count('[') - test_json.count(']')
                    
                    # Add closing brackets
                    test_json += ']' * open_brackets + '}' * open_braces
                    
                    try:
                        parsed = json.loads(test_json)
                        if "related_slides" in parsed and len(parsed["related_slides"]) > 0:
                            print(f"  부분 파싱 성공! {len(parsed['related_slides'])}개 슬라이드 복구")
                            parsed["partial"] = True
                            parsed["recovered_slides"] = len(parsed["related_slides"])
                            return parsed
                    except json.JSONDecodeError:
                        continue
                
                return {"error": "Failed to parse even partially", "partial": True}
                
            except Exception as e:
                print(f"  부분 파싱 실패: {str(e)}")
                return {"error": str(e), "partial": True}
        
        # For other modes, return error
        return {"error": f"Partial parsing not implemented for mode: {mode}", "partial": True}
    
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
        
        # 프롬프트 구성
        intro = COMMON_PROMPT_INTRO.format(
            first_file_desc="강의자료 PDF (참고용)",
            second_file_desc=f'족보 PDF "{jokbo_filename}" (분석 대상)'
        )
        
        output_format = LESSON_CENTRIC_OUTPUT_FORMAT.format(jokbo_filename=jokbo_filename)
        
        prompt = f"""{intro}

{LESSON_CENTRIC_TASK}
        
{COMMON_WARNINGS}
        
{RELEVANCE_CRITERIA}
        
{output_format}
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
        
        # 오류 발생 시 중심 파일을 제외한 모든 파일 정리
        self.cleanup_except_center_file(lesson_file.display_name)
        
        try:
            result = json.loads(response.text)
            
            # Get total pages in jokbo PDF
            jokbo_path = Path(jokbo_path) if isinstance(jokbo_path, str) else jokbo_path
            with fitz.open(str(jokbo_path)) as pdf:
                total_jokbo_pages = len(pdf)
            
            # Validate and filter results
            if "related_slides" in result:
                for slide in result["related_slides"]:
                    if "related_jokbo_questions" in slide:
                        slide["related_jokbo_questions"] = PDFValidator.filter_valid_questions(
                            slide["related_jokbo_questions"], total_jokbo_pages, jokbo_filename
                        )
            
            return result
        except json.JSONDecodeError as e:
            print(f"  JSON 파싱 실패: {str(e)}")
            # Try partial parsing for lesson-centric mode
            partial_result = self.parse_partial_json(response.text, "lesson-centric")
            if "error" not in partial_result or partial_result.get("related_slides"):
                print(f"  부분 파싱으로 일부 데이터 복구 성공")
                return partial_result
            return {"error": "Failed to parse response"}
    
    def analyze_single_jokbo_with_lesson_preloaded(self, jokbo_path: str, lesson_file) -> Dict[str, Any]:
        """Analyze one jokbo PDF against pre-uploaded lesson file"""
        
        # Extract the actual filename
        jokbo_filename = Path(jokbo_path).name
        
        # Upload only jokbo PDF
        print(f"  [{datetime.now().strftime('%H:%M:%S')}] Thread-{threading.current_thread().ident}: 족보 업로드 시작 - {jokbo_filename}")
        jokbo_file = self.upload_pdf(jokbo_path, f"족보_{jokbo_filename}")
        
        # 프롬프트 구성
        intro = COMMON_PROMPT_INTRO.format(
            first_file_desc="강의자료 PDF (참고용)",
            second_file_desc=f'족보 PDF "{jokbo_filename}" (분석 대상)'
        )
        
        output_format = LESSON_CENTRIC_OUTPUT_FORMAT.format(jokbo_filename=jokbo_filename)
        
        prompt = f"""{intro}

{LESSON_CENTRIC_TASK}
        
{COMMON_WARNINGS}
        
{RELEVANCE_CRITERIA}
        
{output_format}
        """
        
        # Prepare content with pre-uploaded lesson file
        content = [prompt, lesson_file, jokbo_file]
        
        print(f"  [{datetime.now().strftime('%H:%M:%S')}] Thread-{threading.current_thread().ident}: AI 분석 시작 - {jokbo_filename}")
        response = self.generate_content_with_retry(content)
        
        # Save API response for debugging
        self.save_api_response(response.text, jokbo_filename, lesson_file.display_name.replace("강의자료_", ""), "lesson-centric")
        
        # Delete jokbo file immediately after analysis  
        print(f"  [{datetime.now().strftime('%H:%M:%S')}] Thread-{threading.current_thread().ident}: 족보 파일 삭제 중 - {jokbo_filename}")
        if not self.delete_file_safe(jokbo_file):
            # 삭제 실패 시 중심 파일을 제외한 모든 파일 정리
            self.cleanup_except_center_file(lesson_file.display_name)
        
        try:
            result = json.loads(response.text)
            
            # Get total pages in jokbo PDF
            with fitz.open(str(jokbo_path)) as pdf:
                total_jokbo_pages = len(pdf)
            
            # Validate and filter results
            if "related_slides" in result:
                for slide in result["related_slides"]:
                    if "related_jokbo_questions" in slide:
                        slide["related_jokbo_questions"] = PDFValidator.filter_valid_questions(
                            slide["related_jokbo_questions"], total_jokbo_pages, jokbo_filename
                        )
            
            print(f"  [{datetime.now().strftime('%H:%M:%S')}] Thread-{threading.current_thread().ident}: 분석 완료 - {jokbo_filename}")
            return result
        except json.JSONDecodeError as e:
            print(f"  JSON 파싱 실패: {str(e)}")
            # Try partial parsing for lesson-centric mode
            partial_result = self.parse_partial_json(response.text, "lesson-centric")
            if "error" not in partial_result or partial_result.get("related_slides"):
                print(f"  부분 파싱으로 일부 데이터 복구 성공")
                return partial_result
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
        """Analyze one lesson PDF against one jokbo PDF (jokbo-centric) with splitting for large files"""
        
        # Extract the actual filename
        jokbo_filename = Path(jokbo_path).name
        lesson_filename = Path(lesson_path).name
        
        # Delete all uploaded files before starting
        print("  기존 업로드 파일 정리 중...")
        self.delete_all_uploaded_files()
        
        # Split lesson PDF if it's too large
        max_pages_per_chunk = int(os.environ.get('MAX_PAGES_PER_CHUNK', '40'))
        lesson_chunks = self.split_pdf_for_analysis(lesson_path, max_pages=max_pages_per_chunk)
        
        if len(lesson_chunks) == 1:
            # Small file, process normally
            return self._analyze_single_lesson_with_jokbo_original(lesson_path, jokbo_path)
        
        # Large file, process in chunks
        print(f"  큰 강의자료를 {len(lesson_chunks)}개 조각으로 분할하여 처리합니다.")
        
        # Upload jokbo PDF once (전체 족보는 한 번만 업로드)
        jokbo_file = self.upload_pdf(jokbo_path, f"족보_{jokbo_filename}")
        
        all_results = []
        temp_files = []
        
        try:
            for chunk_path, start_page, end_page in lesson_chunks:
                print(f"  분석 중: {lesson_filename} (페이지 {start_page}-{end_page})")
                
                # Extract pages to temporary file
                temp_pdf = self.extract_pdf_pages(chunk_path, start_page, end_page)
                temp_files.append(temp_pdf)
                
                # Analyze this chunk
                chunk_result = self._analyze_jokbo_with_lesson_chunk(
                    jokbo_file, temp_pdf, jokbo_filename, lesson_filename,
                    start_page, end_page
                )
                
                if "error" not in chunk_result:
                    all_results.append(chunk_result)
            
            # Merge results from all chunks
            merged_result = self._merge_jokbo_centric_results(all_results)
            
            # 오류 발생 시 중심 파일을 제외한 모든 파일 정리
            self.cleanup_except_center_file(jokbo_file.display_name)
            
            return merged_result
            
        finally:
            # Clean up temporary files
            for temp_file in temp_files:
                try:
                    os.unlink(temp_file)
                except (OSError, IOError) as e:
                    print(f"Failed to delete temp file: {e}")
    
    def _analyze_single_lesson_with_jokbo_original(self, lesson_path: str, jokbo_path: str) -> Dict[str, Any]:
        """Original implementation for small files"""
        
        # Extract the actual filename
        jokbo_filename = Path(jokbo_path).name
        lesson_filename = Path(lesson_path).name
        
        # 프롬프트 구성
        intro = f"""당신은 병리학 교수입니다. 하나의 족보(기출문제) PDF와 하나의 강의자료 PDF를 비교 분석합니다.

중요: 족보 파일명은 반드시 "{jokbo_filename}"을 그대로 사용하세요.
중요: 강의자료 파일명은 반드시 "{lesson_filename}"을 그대로 사용하세요."""
        
        output_format = JOKBO_CENTRIC_OUTPUT_FORMAT.format(
            jokbo_filename=jokbo_filename,
            lesson_filename=lesson_filename
        )
        
        prompt = f"""{intro}

{JOKBO_CENTRIC_TASK}
        
{COMMON_WARNINGS}
        
{output_format}
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
        
        # 오류 발생 시 중심 파일을 제외한 모든 파일 정리
        self.cleanup_except_center_file(jokbo_file.display_name)
        
        try:
            result = json.loads(response.text)
            return result
        except json.JSONDecodeError as e:
            print(f"  JSON 파싱 실패: {str(e)}")
            # Try partial parsing for jokbo-centric mode
            partial_result = self.parse_partial_json(response.text, "jokbo-centric")
            if "error" not in partial_result or partial_result.get("jokbo_pages"):
                print(f"  부분 파싱으로 일부 데이터 복구 성공")
                return partial_result
            return {"error": "Failed to parse response"}
    
    def _analyze_jokbo_with_lesson_chunk(self, jokbo_file, lesson_chunk_path: str, 
                                       jokbo_filename: str, lesson_filename: str,
                                       start_page: int, end_page: int) -> Dict[str, Any]:
        """Analyze jokbo with a chunk of lesson PDF"""
        
        # Upload lesson chunk
        chunk_display_name = f"강의자료_{lesson_filename}_p{start_page}-{end_page}"
        lesson_chunk_file = self.upload_pdf(lesson_chunk_path, chunk_display_name)
        
        # 프롬프트 구성 (페이지 범위 명시)
        intro = f"""당신은 병리학 교수입니다. 하나의 족보(기출문제) PDF와 하나의 강의자료 PDF의 일부분을 비교 분석합니다.

중요: 족보 파일명은 반드시 "{jokbo_filename}"을 그대로 사용하세요.
중요: 강의자료 파일명은 반드시 "{lesson_filename}"을 그대로 사용하세요.
중요: 현재 분석하는 강의자료는 전체의 {start_page}-{end_page} 페이지 부분입니다."""
        
        output_format = JOKBO_CENTRIC_OUTPUT_FORMAT.format(
            jokbo_filename=jokbo_filename,
            lesson_filename=lesson_filename
        )
        
        prompt = f"""{intro}

{JOKBO_CENTRIC_TASK}
        
{COMMON_WARNINGS}
        
{output_format}
        """
        
        # Prepare content for model
        content = [prompt, jokbo_file, lesson_chunk_file]
        
        response = self.model.generate_content(content)
        
        # Save API response for debugging
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        debug_filename = f"gemini_response_{timestamp}_chunk_p{start_page}-{end_page}_{lesson_filename}.json"
        self.save_api_response(response.text, jokbo_filename, lesson_filename, f"chunk_p{start_page}-{end_page}")
        
        # Delete lesson chunk file immediately
        self.delete_file_safe(lesson_chunk_file)
        
        try:
            # Clean up common JSON errors from Gemini
            cleaned_text = response.text
            # Fix incorrectly quoted keys like "4"번" -> "4번"
            import re
            cleaned_text = re.sub(r'"(\d+)"번"', r'"\1번"', cleaned_text)
            
            result = json.loads(cleaned_text)
            # Adjust page numbers to account for chunk offset
            # NOTE: Gemini sometimes returns absolute page numbers even for chunks
            if "jokbo_pages" in result:
                for page_info in result["jokbo_pages"]:
                    for question in page_info.get("questions", []):
                        for slide in question.get("related_lesson_slides", []):
                            if "lesson_page" in slide:
                                page_num = slide["lesson_page"]
                                # Only apply offset if the page number seems to be chunk-relative
                                # (i.e., it's within the chunk's page count)
                                chunk_page_count = end_page - start_page + 1
                                if page_num <= chunk_page_count:
                                    # This looks like a chunk-relative page number
                                    slide["lesson_page"] = page_num + (start_page - 1)
                                    print(f"DEBUG: Adjusted chunk-relative page {page_num} to absolute page {slide['lesson_page']} for chunk p{start_page}-{end_page}")
                                else:
                                    # This is already an absolute page number
                                    print(f"DEBUG: Page {page_num} appears to be absolute (exceeds chunk size {chunk_page_count}), keeping as-is for chunk p{start_page}-{end_page}")
            return result
        except json.JSONDecodeError as e:
            print(f"  JSON 파싱 실패 (청크 p{start_page}-{end_page}): {str(e)}")
            # Try partial parsing
            partial_result = self.parse_partial_json(cleaned_text, "jokbo-centric")
            if "error" not in partial_result or partial_result.get("jokbo_pages"):
                print(f"  부분 파싱으로 청크 데이터 일부 복구")
                # Still need to adjust page numbers for chunk offset
                if "jokbo_pages" in partial_result:
                    for page_info in partial_result["jokbo_pages"]:
                        for question in page_info.get("questions", []):
                            for slide in question.get("related_lesson_slides", []):
                                if "lesson_page" in slide:
                                    page_num = slide["lesson_page"]
                                    chunk_page_count = end_page - start_page + 1
                                    if page_num <= chunk_page_count:
                                        slide["lesson_page"] = page_num + (start_page - 1)
                return partial_result
            else:
                debug_file = self.debug_dir / f"failed_json_chunk_p{start_page}-{end_page}.txt"
                with open(debug_file, 'w', encoding='utf-8') as f:
                    f.write(response.text)
                return {"error": "Failed to parse response"}
    
    def _merge_jokbo_centric_results(self, results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Merge results from multiple chunks for jokbo-centric mode"""
        
        if not results:
            return {"error": "No valid results to merge"}
        
        print(f"Merging {len(results)} chunk results...")
        
        # Start with the first result as base
        merged = results[0].copy()
        
        # Log initial state
        initial_pages = len(merged.get("jokbo_pages", []))
        initial_questions = sum(len(p.get("questions", [])) for p in merged.get("jokbo_pages", []))
        print(f"Initial result has {initial_pages} pages with {initial_questions} questions")
        
        # Merge additional results
        for chunk_idx, result in enumerate(results[1:], 1):
            print(f"\nMerging chunk {chunk_idx}...")
            if "jokbo_pages" in result:
                for page_info in result["jokbo_pages"]:
                    page_num = page_info["jokbo_page"]
                    
                    # Find matching page in merged result
                    merged_page = None
                    for mp in merged.get("jokbo_pages", []):
                        if mp["jokbo_page"] == page_num:
                            merged_page = mp
                            break
                    
                    if merged_page:
                        # Merge questions for this page
                        for question in page_info.get("questions", []):
                            q_num = question.get("question_number")
                            
                            # Find matching question
                            merged_question = None
                            for mq in merged_page.get("questions", []):
                                if mq.get("question_number") == q_num:
                                    merged_question = mq
                                    break
                            
                            if merged_question:
                                # Update fields that might have been empty in first chunk
                                if not merged_question.get("question_numbers_on_page") and question.get("question_numbers_on_page"):
                                    merged_question["question_numbers_on_page"] = question["question_numbers_on_page"]
                                
                                # Merge related_lesson_slides
                                existing_slides = {s.get("lesson_page"): s for s in merged_question.get("related_lesson_slides", [])}
                                
                                for slide in question.get("related_lesson_slides", []):
                                    slide_page = slide.get("lesson_page")
                                    if slide_page not in existing_slides:
                                        merged_question["related_lesson_slides"].append(slide)
                                    else:
                                        # Update if new score is higher
                                        if slide.get("relevance_score", 0) > existing_slides[slide_page].get("relevance_score", 0):
                                            existing_slides[slide_page].update(slide)
                            else:
                                # Add new question that wasn't in the merged result
                                print(f"  Adding new question {q_num} to page {page_num}")
                                merged_page["questions"].append(question)
                    else:
                        # Add new page that wasn't in the merged result
                        print(f"  Adding new page {page_num} with {len(page_info.get('questions', []))} questions")
                        merged.setdefault("jokbo_pages", []).append(page_info)
        
        # Re-sort and filter slides by relevance score
        if "jokbo_pages" in merged:
            for page_info in merged["jokbo_pages"]:
                for question in page_info.get("questions", []):
                    slides = question.get("related_lesson_slides", [])
                    # Sort by relevance score (descending)
                    slides.sort(key=lambda x: x.get("relevance_score", 0), reverse=True)
                    # Keep only top connections and those above threshold
                    filtered_slides = []
                    for slide in slides[:MAX_CONNECTIONS_PER_QUESTION]:
                        if slide.get("relevance_score", 0) >= RELEVANCE_SCORE_THRESHOLD:
                            filtered_slides.append(slide)
                    question["related_lesson_slides"] = filtered_slides
        
        # Log final state
        final_pages = len(merged.get("jokbo_pages", []))
        final_questions = sum(len(p.get("questions", [])) for p in merged.get("jokbo_pages", []))
        print(f"\nMerge complete: {final_pages} pages with {final_questions} questions (was {initial_pages} pages with {initial_questions} questions)")
        
        return merged
    
    def analyze_lessons_for_jokbo(self, lesson_paths: List[str], jokbo_path: str) -> Dict[str, Any]:
        """Analyze multiple lesson PDFs against one jokbo PDF (jokbo-centric)"""
        
        all_jokbo_pages = {}
        all_connections = {}  # {question_id: [connections with scores]}
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
                    question_id = f"{jokbo_page}_{question['question_number']}"
                    
                    # 문제가 처음 발견된 경우
                    if question_id not in all_connections:
                        all_connections[question_id] = {
                            "question_data": {
                                "jokbo_page": jokbo_page,
                                "question_number": question["question_number"],
                                "question_text": question["question_text"],
                                "answer": question["answer"],
                                "explanation": question["explanation"],
                                "wrong_answer_explanations": question.get("wrong_answer_explanations", {}),
                                "question_numbers_on_page": question.get("question_numbers_on_page", [])
                            },
                            "connections": []
                        }
                    
                    # 관련 슬라이드 연결 추가 (점수 포함)
                    for slide in question.get("related_lesson_slides", []):
                        all_connections[question_id]["connections"].append(slide)
        
        # 각 문제에 대해 상위 2개 연결만 선택
        final_pages = {}
        total_questions = 0
        filtered_total_slides = 0
        
        for question_id, data in all_connections.items():
            question_data = data["question_data"]
            connections = data["connections"]
            
            # relevance_score로 정렬하고 상위 2개만 선택
            sorted_connections = sorted(
                connections,
                key=lambda x: x.get("relevance_score", 0),
                reverse=True
            )[:MAX_CONNECTIONS_PER_QUESTION]
            
            # 최소 점수 기준을 충족하는 연결만 유지
            filtered_connections = [
                conn for conn in sorted_connections
                if conn.get("relevance_score", 0) >= RELEVANCE_SCORE_THRESHOLD
            ]
            
            if filtered_connections:  # 관련 슬라이드가 있는 경우만 포함
                jokbo_page = question_data["jokbo_page"]
                if jokbo_page not in final_pages:
                    final_pages[jokbo_page] = {
                        "jokbo_page": jokbo_page,
                        "questions": []
                    }
                
                question_entry = question_data.copy()
                question_entry["related_lesson_slides"] = filtered_connections
                final_pages[jokbo_page]["questions"].append(question_entry)
                
                total_questions += 1
                filtered_total_slides += len(filtered_connections)
        
        # Convert dict to list and sort by page number
        final_pages_list = list(final_pages.values())
        final_pages_list.sort(key=lambda x: x["jokbo_page"])
        
        return {
            "jokbo_pages": final_pages_list,
            "summary": {
                "total_jokbo_pages": len(final_pages_list),
                "total_questions": total_questions,
                "total_related_slides": filtered_total_slides,
                "study_recommendations": "각 족보 문제별로 가장 관련성이 높은 강의 슬라이드를 중점적으로 학습하세요."
            }
        }
    
    def analyze_single_lesson_with_jokbo_preloaded(self, lesson_path: str, jokbo_file) -> Dict[str, Any]:
        """Analyze one lesson PDF against pre-uploaded jokbo file (jokbo-centric) with chunk splitting"""
        
        # Extract the actual filename
        lesson_filename = Path(lesson_path).name
        jokbo_filename = jokbo_file.display_name.replace("족보_", "")
        
        # Split lesson PDF if it's too large
        max_pages_per_chunk = int(os.environ.get('MAX_PAGES_PER_CHUNK', '40'))
        lesson_chunks = self.split_pdf_for_analysis(lesson_path, max_pages=max_pages_per_chunk)
        
        if len(lesson_chunks) == 1:
            # Small file, process normally
            print(f"  [{datetime.now().strftime('%H:%M:%S')}] Thread-{threading.current_thread().ident}: 강의자료 업로드 시작 - {lesson_filename}")
            lesson_file = self.upload_pdf(lesson_path, f"강의자료_{lesson_filename}")
            
            # 프롬프트 구성
            intro = f"""당신은 병리학 교수입니다. 하나의 족보(기출문제) PDF와 하나의 강의자료 PDF를 비교 분석합니다.

중요: 족보 파일명은 반드시 "{jokbo_filename}"을 그대로 사용하세요.
중요: 강의자료 파일명은 반드시 "{lesson_filename}"을 그대로 사용하세요."""
            
            output_format = JOKBO_CENTRIC_OUTPUT_FORMAT.format(
                jokbo_filename=jokbo_filename,
                lesson_filename=lesson_filename
            )
            
            prompt = f"""{intro}

{JOKBO_CENTRIC_TASK}
            
{COMMON_WARNINGS}
            
{output_format}
            """
            
            # Prepare content with pre-uploaded jokbo file
            content = [prompt, jokbo_file, lesson_file]
            
            print(f"  [{datetime.now().strftime('%H:%M:%S')}] Thread-{threading.current_thread().ident}: AI 분석 시작 - {lesson_filename}")
            response = self.generate_content_with_retry(content)
            
            # Save API response for debugging
            self.save_api_response(response.text, jokbo_filename, lesson_filename, "jokbo-centric")
            
            # Delete lesson file immediately after analysis
            print(f"  [{datetime.now().strftime('%H:%M:%S')}] Thread-{threading.current_thread().ident}: 강의자료 파일 삭제 중 - {lesson_filename}")
            if not self.delete_file_safe(lesson_file):
                # 삭제 실패 시 중심 파일을 제외한 모든 파일 정리
                self.cleanup_except_center_file(jokbo_file.display_name)
            
            try:
                result = json.loads(response.text)
                print(f"  [{datetime.now().strftime('%H:%M:%S')}] Thread-{threading.current_thread().ident}: 분석 완료 - {lesson_filename}")
                return result
            except json.JSONDecodeError as e:
                print(f"  JSON 파싱 실패: {str(e)}")
                # Try partial parsing for jokbo-centric mode
                partial_result = self.parse_partial_json(response.text, "jokbo-centric")
                if "error" not in partial_result or partial_result.get("jokbo_pages"):
                    print(f"  부분 파싱으로 일부 데이터 복구 성공")
                    return partial_result
                return {"error": "Failed to parse response"}
        
        # Large file, process in chunks
        print(f"  [{datetime.now().strftime('%H:%M:%S')}] Thread-{threading.current_thread().ident}: 큰 강의자료를 {len(lesson_chunks)}개 조각으로 분할하여 처리합니다.")
        
        all_results = []
        temp_files = []
        
        try:
            for chunk_path, start_page, end_page in lesson_chunks:
                print(f"  [{datetime.now().strftime('%H:%M:%S')}] Thread-{threading.current_thread().ident}: 분석 중: {lesson_filename} (페이지 {start_page}-{end_page})")
                
                # Extract pages to temporary file
                temp_pdf = self.extract_pdf_pages(chunk_path, start_page, end_page)
                temp_files.append(temp_pdf)
                
                # Analyze this chunk
                chunk_result, uploaded_file = self._analyze_jokbo_with_lesson_chunk_preloaded(
                    jokbo_file, temp_pdf, jokbo_filename, lesson_filename,
                    start_page, end_page
                )
                
                # Don't need to track uploaded_file here since it's in the same thread
                # and will be deleted by this thread
                self.delete_file_safe(uploaded_file)
                
                if "error" not in chunk_result:
                    all_results.append(chunk_result)
            
            # Merge results from all chunks
            merged_result = self._merge_jokbo_centric_results(all_results)
            
            print(f"  [{datetime.now().strftime('%H:%M:%S')}] Thread-{threading.current_thread().ident}: 분석 완료 - {lesson_filename}")
            return merged_result
            
        finally:
            # Clean up temporary files
            for temp_file in temp_files:
                try:
                    os.unlink(temp_file)
                except (OSError, IOError) as e:
                    print(f"Failed to delete temp file: {e}")
    
    def _analyze_jokbo_with_lesson_chunk_preloaded(self, jokbo_file, lesson_chunk_path: str, 
                                                   jokbo_filename: str, lesson_filename: str,
                                                   start_page: int, end_page: int,
                                                   lesson_total_pages: int,
                                                   max_retries: int = 3) -> Tuple[Dict[str, Any], Any]:
        """Analyze pre-uploaded jokbo with a chunk of lesson PDF in parallel mode
        
        Args:
            jokbo_file: Pre-uploaded jokbo file object
            lesson_chunk_path: Path to lesson chunk PDF
            jokbo_filename: Original jokbo filename
            lesson_filename: Original lesson filename  
            start_page: Start page of chunk
            end_page: End page of chunk
            lesson_total_pages: Total pages in the lesson PDF
            max_retries: Maximum retries for invalid page numbers
            
        Returns:
            Tuple of (result_dict, uploaded_file_to_delete)
        """
        
        # Upload lesson chunk
        chunk_display_name = f"강의자료_{lesson_filename}_p{start_page}-{end_page}"
        lesson_chunk_file = self.upload_pdf(lesson_chunk_path, chunk_display_name)
        
        # Build prompt using helper
        output_format = JOKBO_CENTRIC_OUTPUT_FORMAT.format(
            jokbo_filename=jokbo_filename,
            lesson_filename=lesson_filename
        )
        
        prompt = PDFProcessorHelpers.build_chunk_prompt(
            jokbo_filename, lesson_filename, start_page, end_page,
            JOKBO_CENTRIC_TASK, COMMON_WARNINGS, output_format
        )
        
        # Prepare content for model
        content = [prompt, jokbo_file, lesson_chunk_file]
        
        response = self.generate_content_with_retry(content)
        
        # Save API response for debugging
        self.save_api_response(response.text, jokbo_filename, lesson_filename, f"chunk_p{start_page}-{end_page}")
        
        try:
            # Clean up common JSON errors from Gemini
            cleaned_text = PDFProcessorHelpers.clean_json_text(response.text)
            result = json.loads(cleaned_text)
            
            # Process page numbers with validation and potential retry
            retry_needed, invalid_questions = PDFProcessorHelpers.validate_question_pages(
                result, start_page, end_page, lesson_total_pages, 
                self.validate_and_adjust_page_number
            )
            
            # If retry is needed and we have retries left
            if retry_needed and max_retries > 0:
                print(f"  재분석 시도 (남은 횟수: {max_retries}) - 문제 {', '.join(invalid_questions)}에서 잘못된 페이지 번호 감지")
                self.delete_file_safe(lesson_chunk_file)
                time.sleep(2)  # Wait before retry
                return self._analyze_jokbo_with_lesson_chunk_preloaded(
                    jokbo_file, lesson_chunk_path, jokbo_filename, lesson_filename,
                    start_page, end_page, lesson_total_pages, max_retries - 1
                )
            
            # If still have invalid pages after all retries, remove those questions
            if retry_needed:
                PDFProcessorHelpers.remove_invalid_questions(result, invalid_questions)
            
            return result, lesson_chunk_file
        except json.JSONDecodeError as e:
            print(f"  JSON 파싱 실패 (청크 p{start_page}-{end_page}): {str(e)}")
            # Try partial parsing
            partial_result = self.parse_partial_json(cleaned_text, "jokbo-centric")
            if "error" not in partial_result or partial_result.get("jokbo_pages"):
                print(f"  부분 파싱으로 청크 데이터 일부 복구")
                # Apply validation to partial results
                retry_needed, invalid_questions = PDFProcessorHelpers.validate_question_pages(
                    partial_result, start_page, end_page, lesson_total_pages,
                    self.validate_and_adjust_page_number
                )
                if invalid_questions:
                    PDFProcessorHelpers.remove_invalid_questions(partial_result, invalid_questions)
                
                return partial_result, lesson_chunk_file
            else:
                debug_file = self.debug_dir / f"failed_json_chunk_p{start_page}-{end_page}.txt"
                with open(debug_file, 'w', encoding='utf-8') as f:
                    f.write(response.text)
                # Delete the chunk file before returning
                self.delete_file_safe(lesson_chunk_file)
                return {"error": "Failed to parse response"}, None
    
    def analyze_lessons_for_jokbo_parallel(self, lesson_paths: List[str], jokbo_path: str, max_workers: int = 3) -> Dict[str, Any]:
        """Analyze multiple lesson PDFs against one jokbo PDF using parallel processing (jokbo-centric)"""
        
        print(f"  [{datetime.now().strftime('%H:%M:%S')}] 병렬 처리 시작 (족보 중심)")
        
        # 임시 파일 디렉토리 설정
        temp_dir = Path("output/temp/chunk_results")
        state_file = Path("output/temp/processing_state.json")
        
        # 이전 처리 상태 확인
        previous_state = self.load_processing_state(state_file)
        if previous_state:
            print(f"  이전 처리 상태 발견: {previous_state.get('processed_chunks', 0)}개 청크 완료")
            # TODO: 재시작 로직 구현
        
        # Delete all uploaded files before starting
        print("  기존 업로드 파일 정리 중...")
        self.delete_all_uploaded_files()
        
        print(f"  [{datetime.now().strftime('%H:%M:%S')}] 족보 업로드 중...")
        # Pre-upload jokbo file once
        jokbo_file = self.upload_pdf(jokbo_path, f"족보_{Path(jokbo_path).name}")
        
        lock = threading.Lock()
        processed_chunks = 0
        
        # Prepare all chunks from all lesson files
        all_chunks = []
        max_pages_per_chunk = int(os.environ.get('MAX_PAGES_PER_CHUNK', '40'))
        
        print(f"  강의자료 청크 준비 중...")
        for lesson_path in lesson_paths:
            lesson_total_pages = self.get_pdf_page_count(lesson_path)
            lesson_chunks = self.split_pdf_for_analysis(lesson_path, max_pages=max_pages_per_chunk)
            for chunk_path, start_page, end_page in lesson_chunks:
                all_chunks.append({
                    'lesson_path': lesson_path,
                    'lesson_filename': Path(lesson_path).name,
                    'chunk_path': chunk_path,
                    'start_page': start_page,
                    'end_page': end_page,
                    'total_pages': lesson_total_pages
                })
        
        print(f"  총 {len(all_chunks)}개 청크를 병렬 처리합니다.")
        
        # Dictionary to store results by lesson file
        lesson_results = {}
        for lesson_path in lesson_paths:
            lesson_results[lesson_path] = []
        
        def process_single_chunk(chunk_info: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
            """Process a single chunk in a thread"""
            thread_id = threading.current_thread().ident
            lesson_path = chunk_info['lesson_path']
            lesson_filename = chunk_info['lesson_filename']
            start_page = chunk_info['start_page']
            end_page = chunk_info['end_page']
            
            print(f"  [{datetime.now().strftime('%H:%M:%S')}] Thread-{thread_id}: 처리 시작 - {lesson_filename} (p{start_page}-{end_page})")
            
            # Create a new PDFProcessor instance for this thread with parent session ID
            thread_processor = PDFProcessor(self.model, session_id=self.session_id)
            
            try:
                # Extract pages to temporary file if needed
                if len(self.split_pdf_for_analysis(lesson_path, max_pages=max_pages_per_chunk)) > 1:
                    temp_pdf = thread_processor.extract_pdf_pages(chunk_info['chunk_path'], start_page, end_page)
                else:
                    temp_pdf = chunk_info['chunk_path']
                
                # Analyze this chunk
                result, uploaded_file = thread_processor._analyze_jokbo_with_lesson_chunk_preloaded(
                    jokbo_file, temp_pdf, 
                    jokbo_file.display_name.replace("족보_", ""),
                    lesson_filename,
                    start_page, end_page,
                    chunk_info['total_pages']
                )
                
                # Clean up temp file if created
                if temp_pdf != chunk_info['chunk_path']:
                    try:
                        os.unlink(temp_pdf)
                    except:
                        pass
                
                # 결과를 임시 파일로 저장
                saved_path = thread_processor.save_chunk_result(chunk_info, result, thread_processor.chunk_results_dir)
                print(f"    [{datetime.now().strftime('%H:%M:%S')}] Thread-{thread_id}: 결과 저장 - {Path(saved_path).name}")
                
                # 성공 시 처리 상태 업데이트
                with lock:
                    nonlocal processed_chunks
                    processed_chunks += 1
                
                # Note: uploaded_file is already deleted in _analyze_jokbo_with_lesson_chunk_preloaded
                # 메모리에서는 결과를 반환하지 않음 (파일로 저장했으므로)
                return (lesson_path, saved_path, None)
            except Exception as e:
                print(f"    [{datetime.now().strftime('%H:%M:%S')}] Thread-{thread_id}: 오류 발생 - {str(e)}")
                # 오류도 파일로 저장
                error_result = {"error": str(e), "chunk_info": chunk_info}
                saved_path = thread_processor.save_chunk_result(chunk_info, error_result, thread_processor.chunk_results_dir)
                return (lesson_path, saved_path, None)
            finally:
                # Ensure cleanup happens
                try:
                    thread_processor.__del__()
                except (OSError, IOError) as e:
                    print(f"Failed to delete temp file: {e}")
        
        # Process all chunks in parallel
        print(f"  [{datetime.now().strftime('%H:%M:%S')}] {len(all_chunks)}개 청크 병렬 처리 시작 (max_workers={max_workers})")
        
        # List to store uploaded files for cleanup
        uploaded_files_to_delete = []
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all chunk tasks at once
            future_to_chunk = {
                executor.submit(process_single_chunk, chunk_info): chunk_info
                for chunk_info in all_chunks
            }
            
            print(f"  [{datetime.now().strftime('%H:%M:%S')}] 모든 작업 제출 완료 - 동시 처리 중...")
            
            # Process completed tasks with progress bar
            if TQDM_AVAILABLE:
                pbar = tqdm(total=len(future_to_chunk), desc="청크 처리")
            
            for future in as_completed(future_to_chunk):
                chunk_info = future_to_chunk[future]
                try:
                    lesson_path, saved_path, uploaded_file = future.result()
                    
                    # 처리 상태 저장 (중간 저장)
                    if processed_chunks % 10 == 0:  # 10개마다 상태 저장
                        state_info = {
                            "jokbo_path": jokbo_path,
                            "total_chunks": len(all_chunks),
                            "processed_chunks": processed_chunks,
                            "timestamp": datetime.now().isoformat()
                        }
                        self.save_processing_state(state_info, state_file)
                
                except Exception as e:
                    lesson_filename = chunk_info['lesson_filename']
                    print(f"    [{datetime.now().strftime('%H:%M:%S')}] 처리 중 오류: {lesson_filename} - {str(e)}")
                finally:
                    if TQDM_AVAILABLE:
                        pbar.update(1)
            
            if TQDM_AVAILABLE:
                pbar.close()
        
        # 최종 처리 상태 저장
        final_state = {
            "jokbo_path": jokbo_path,
            "total_chunks": len(all_chunks),
            "processed_chunks": processed_chunks,
            "status": "completed",
            "timestamp": datetime.now().isoformat(),
            "session_id": self.session_id
        }
        self.save_processing_state(final_state, state_file)
        
        # 파일 기반 병합
        print(f"  [{datetime.now().strftime('%H:%M:%S')}] 임시 파일에서 청크 결과 병합 중...")
        merge_result = self.load_and_merge_chunk_results(self.chunk_results_dir)
        
        if "error" in merge_result:
            print(f"  병합 오류: {merge_result['error']}")
            return {"error": merge_result['error']}
        
        all_connections = merge_result["all_connections"]
        print(f"  병합 완료: {len(all_connections)}개 문제")
        
        # 최종 필터링 및 정렬
        print(f"  [{datetime.now().strftime('%H:%M:%S')}] 최종 필터링 및 문제 번호순 정렬 중...")
        final_result = self.apply_final_filtering_and_sorting(all_connections)
        
        # 디버그 정보 출력
        print(f"  [{datetime.now().strftime('%H:%M:%S')}] 최종 결과: {final_result['summary']['total_jokbo_pages']}개 페이지, {final_result['summary']['total_questions']}개 문제, {final_result['summary']['total_related_slides']}개 연결")
        if final_result['summary']['total_questions'] == 0:
            print(f"  경고: 필터링 후 관련 강의 슬라이드가 없습니다. (THRESHOLD={RELEVANCE_SCORE_THRESHOLD})")
        
        # 모든 업로드된 청크 파일 삭제 (이미 삭제되었을 수 있으므로 무시)
        print(f"  [{datetime.now().strftime('%H:%M:%S')}] 업로드된 청크 파일 삭제 중...")
        # 청크 파일들은 이미 각 스레드에서 삭제되었으므로 이 단계는 건너뜀
        
        # 족보 파일 삭제 (중심 파일이므로 모든 처리가 끝난 후 한 번만 삭제)
        try:
            self.delete_file_safe(jokbo_file)
        except Exception as e:
            print(f"  족보 파일 삭제 실패: {str(e)}")
            # 중심 파일 제외 정리 시도
            self.cleanup_except_center_file(jokbo_file.display_name)
        
        # 결과 크기 확인
        import sys
        result_size = sys.getsizeof(final_result["jokbo_pages"])
        print(f"  [{datetime.now().strftime('%H:%M:%S')}] 결과 데이터 크기: {result_size / 1024 / 1024:.2f} MB")
        
        return final_result
    
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
            
            # Create a new PDFProcessor instance for this thread with parent session ID
            thread_processor = PDFProcessor(self.model, session_id=self.session_id)
            
            try:
                result = thread_processor.analyze_single_jokbo_with_lesson_preloaded(jokbo_path, lesson_file)
                return result
            except Exception as e:
                print(f"    [{datetime.now().strftime('%H:%M:%S')}] Thread-{thread_id}: 오류 발생 - {str(e)}")
                return {"error": str(e)}
            finally:
                # Ensure cleanup happens
                try:
                    thread_processor.__del__()
                except (OSError, IOError) as e:
                    print(f"Failed to delete temp file: {e}")
        
        # Process jokbo files in parallel
        print(f"  [{datetime.now().strftime('%H:%M:%S')}] {len(jokbo_paths)}개 족보 파일 병렬 처리 시작 (max_workers={max_workers})")
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all tasks at once
            future_to_jokbo = {
                executor.submit(process_single_jokbo, jokbo_path): jokbo_path 
                for jokbo_path in jokbo_paths
            }
            
            print(f"  [{datetime.now().strftime('%H:%M:%S')}] 모든 작업 제출 완료 - 동시 처리 중...")
            
            # Process completed tasks with progress bar
            if TQDM_AVAILABLE:
                pbar = tqdm(total=len(future_to_jokbo), desc="족보 파일 처리")
            
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
                finally:
                    if TQDM_AVAILABLE:
                        pbar.update(1)
            
            if TQDM_AVAILABLE:
                pbar.close()
        
        # Convert sets to lists and prepare final result
        final_slides = []
        for slide_data in all_related_slides.values():
            slide_data["key_concepts"] = list(slide_data["key_concepts"])
            final_slides.append(slide_data)
        
        # Sort by lesson page number
        final_slides.sort(key=lambda x: x["lesson_page"])
        
        # 강의자료 파일 삭제 (중심 파일이므로 모든 처리가 끝난 후 한 번만 삭제)
        try:
            self.delete_file_safe(lesson_file)
        except Exception as e:
            print(f"  강의자료 파일 삭제 실패: {str(e)}")
            # 중심 파일 제외 정리 시도
            self.cleanup_except_center_file(lesson_file.display_name)
        
        return {
            "related_slides": final_slides,
            "summary": {
                "total_related_slides": len(final_slides),
                "total_questions": total_questions,
                "key_topics": list(all_key_topics),
                "study_recommendations": "각 슬라이드별로 관련된 족보 문제들을 중점적으로 학습하세요."
            }
        }
    def save_chunk_result(self, chunk_info: Dict[str, Any], result: Dict[str, Any], temp_dir: Path) -> str:
        """청크 처리 결과를 임시 파일로 저장
        
        Args:
            chunk_info: 청크 정보 (lesson_filename, start_page, end_page 등)
            result: 처리 결과
            temp_dir: 임시 파일 저장 디렉토리
            
        Returns:
            저장된 파일 경로
        """
        # 디렉토리가 Path 객체가 아닌 경우 변환
        if not isinstance(temp_dir, Path):
            temp_dir = Path(temp_dir)
        
        # 임시 디렉토리 생성
        temp_dir.mkdir(parents=True, exist_ok=True)
        
        # 파일명 생성
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"chunk_{chunk_info['lesson_filename']}_p{chunk_info['start_page']}-{chunk_info['end_page']}_{timestamp}.json"
        filepath = temp_dir / filename
        
        # 결과 저장
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump({
                'chunk_info': chunk_info,
                'result': result,
                'timestamp': timestamp
            }, f, ensure_ascii=False, indent=2)
        
        return str(filepath)
    
    def load_and_merge_chunk_results(self, temp_dir: Path) -> Dict[str, Any]:
        """임시 파일들을 순차적으로 읽어 병합
        
        Args:
            temp_dir: 임시 파일이 저장된 디렉토리
            
        Returns:
            병합된 결과
        """
        if not temp_dir.exists():
            return {"error": "임시 디렉토리가 존재하지 않습니다"}
        
        # 모든 청크 파일 읽기
        chunk_files = sorted(temp_dir.glob("chunk_*.json"))
        if not chunk_files:
            return {"error": "처리된 청크 파일이 없습니다"}
        
        print(f"  {len(chunk_files)}개 청크 파일 병합 중...")
        
        all_jokbo_pages = {}
        all_connections = {}
        
        # 각 파일을 순차적으로 읽어 병합
        for idx, chunk_file in enumerate(chunk_files):
            print(f"  [{idx+1}/{len(chunk_files)}] {chunk_file.name} 병합 중...")
            
            with open(chunk_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                chunk_result = data['result']
            
            # 에러가 있는 청크는 건너뛰기
            if "error" in chunk_result:
                print(f"    오류 청크 건너뛰기: {chunk_result['error']}")
                continue
            
            # 결과 병합
            for page_info in chunk_result.get("jokbo_pages", []):
                jokbo_page = page_info["jokbo_page"]
                if jokbo_page not in all_jokbo_pages:
                    all_jokbo_pages[jokbo_page] = {
                        "jokbo_page": jokbo_page,
                        "questions": []
                    }
                
                # 각 문제 처리
                for question in page_info.get("questions", []):
                    question_id = f"{jokbo_page}_{question['question_number']}"
                    
                    if question_id not in all_connections:
                        all_connections[question_id] = {
                            "question_data": {
                                "jokbo_page": jokbo_page,
                                "question_number": question["question_number"],
                                "question_text": question["question_text"],
                                "answer": question["answer"],
                                "explanation": question["explanation"],
                                "wrong_answer_explanations": question.get("wrong_answer_explanations", {}),
                                "question_numbers_on_page": question.get("question_numbers_on_page", [])
                            },
                            "connections": []
                        }
                    
                    # 관련 슬라이드 추가
                    for slide in question.get("related_lesson_slides", []):
                        all_connections[question_id]["connections"].append(slide)
        
        return {
            "all_connections": all_connections,
            "total_chunks": len(chunk_files),
            "processed_chunks": len(chunk_files)
        }
    
    def apply_final_filtering_and_sorting(self, all_connections: Dict[str, Any]) -> Dict[str, Any]:
        """최종 필터링 (상위 2개, 5점 이상) 및 문제 번호순 정렬
        
        Args:
            all_connections: 모든 문제와 연결 정보
            
        Returns:
            필터링 및 정렬된 최종 결과
        """
        # 상위 2개 연결 선택 및 점수 필터링
        filtered_questions = []
        
        for question_id, data in all_connections.items():
            question_data = data["question_data"]
            connections = data["connections"]
            
            # relevance_score로 정렬하고 상위 2개만 선택
            sorted_connections = sorted(
                connections,
                key=lambda x: x.get("relevance_score", 0),
                reverse=True
            )[:MAX_CONNECTIONS_PER_QUESTION]
            
            # 최소 점수 기준을 충족하는 연결만 유지
            filtered_connections = [
                conn for conn in sorted_connections
                if conn.get("relevance_score", 0) >= RELEVANCE_SCORE_THRESHOLD
            ]
            
            if filtered_connections:
                question_entry = question_data.copy()
                question_entry["related_lesson_slides"] = filtered_connections
                filtered_questions.append(question_entry)
        
        # 문제 번호로 정렬 (숫자로 변환하여 정렬)
        filtered_questions.sort(
            key=lambda q: int(q.get("question_number", "0")) 
            if q.get("question_number", "0").isdigit() else 0
        )
        
        # 정렬된 문제들을 페이지별로 재구성
        sorted_pages = {}
        for question in filtered_questions:
            jokbo_page = question.get("jokbo_page", 0)
            if jokbo_page not in sorted_pages:
                sorted_pages[jokbo_page] = {
                    "jokbo_page": jokbo_page,
                    "questions": []
                }
            sorted_pages[jokbo_page]["questions"].append(question)
        
        # 페이지 번호로 정렬
        final_pages_list = list(sorted_pages.values())
        final_pages_list.sort(key=lambda x: x["jokbo_page"])
        
        # 통계 계산
        total_questions = len(filtered_questions)
        total_slides = sum(len(q.get("related_lesson_slides", [])) for q in filtered_questions)
        
        return {
            "jokbo_pages": final_pages_list,
            "summary": {
                "total_jokbo_pages": len(final_pages_list),
                "total_questions": total_questions,
                "total_related_slides": total_slides,
                "study_recommendations": "각 족보 문제별로 가장 관련성이 높은 강의 슬라이드를 중점적으로 학습하세요."
            }
        }
    
    def save_processing_state(self, state_info: Dict[str, Any], state_file: Path):
        """처리 상태 저장
        
        Args:
            state_info: 저장할 상태 정보
            state_file: 상태 파일 경로
        """
        state_file.parent.mkdir(parents=True, exist_ok=True)
        with open(state_file, 'w', encoding='utf-8') as f:
            json.dump(state_info, f, ensure_ascii=False, indent=2)
    
    def load_processing_state(self, state_file: Path) -> Optional[Dict[str, Any]]:
        """이전 처리 상태 복원
        
        Args:
            state_file: 상태 파일 경로
            
        Returns:
            상태 정보 또는 None
        """
        if state_file.exists():
            with open(state_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        return None
    
    def cleanup_temp_files(self, temp_dir: Path):
        """임시 파일 정리
        
        Args:
            temp_dir: 임시 파일 디렉토리
        """
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
            print(f"  임시 파일 정리 완료: {temp_dir}")