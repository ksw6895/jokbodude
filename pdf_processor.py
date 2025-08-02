import json
from pathlib import Path
import pymupdf as fitz
from typing import List, Dict, Any, Tuple, TYPE_CHECKING, Optional
import google.generativeai as genai
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from multiprocessing import Process, Queue
import threading
from datetime import datetime
import os
import shutil
try:
    from tqdm import tqdm
    TQDM_AVAILABLE = True
except ImportError:
    TQDM_AVAILABLE = False
from config import configure_api
from constants import (
    COMMON_PROMPT_INTRO, COMMON_WARNINGS, RELEVANCE_CRITERIA,
    LESSON_CENTRIC_TASK, LESSON_CENTRIC_OUTPUT_FORMAT,
    JOKBO_CENTRIC_TASK, JOKBO_CENTRIC_OUTPUT_FORMAT,
    MAX_CONNECTIONS_PER_QUESTION, RELEVANCE_SCORE_THRESHOLD
)
from validators import PDFValidator
from pdf_processor_helpers import PDFProcessorHelpers
from error_handler import ErrorHandler
from prompt_builder import PromptBuilder
from file_manager import FileManager
from processing_config import ProcessingConfig
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
        # File manager for centralized file operations
        self.file_manager = FileManager()
        
        # 세션 식별자 시스템
        # Ensure API is configured
        configure_api()
        
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
            self.file_manager.delete_file_safe(file)
    
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
    
    def split_pdf_for_analysis(self, pdf_path: str, max_pages: int = ProcessingConfig.DEFAULT_CHUNK_SIZE) -> List[Tuple[str, int, int]]:
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
        return self.file_manager.list_uploaded_files()
    
    def delete_all_uploaded_files(self):
        """Delete all uploaded files from the account"""
        return self.file_manager.delete_all_uploaded_files()
    
    def delete_file_safe(self, file):
        """Safely delete a file with retry logic"""
        return self.file_manager.delete_file_safe(file)
    
    def cleanup_except_center_file(self, center_file_display_name: str):
        """중심 파일을 제외한 모든 파일 삭제"""
        return self.file_manager.cleanup_except_center_file(center_file_display_name)
    
    def generate_content_with_retry(self, content, max_retries=ProcessingConfig.MAX_RETRIES, backoff_factor=ProcessingConfig.BACKOFF_FACTOR):
        """Generate content with exponential backoff retry for parallel mode"""
        for attempt in range(max_retries):
            try:
                response = self.model.generate_content(content)
                
                # Check for empty response first
                if not response.text or len(response.text) == 0:
                    print(f"  경고: 빈 응답 받음 (시도 {attempt + 1}/{max_retries})")
                    if attempt < max_retries - 1:
                        wait_time = backoff_factor ** attempt
                        print(f"  {wait_time}초 후 재시도...")
                        time.sleep(wait_time)
                        continue
                    else:
                        # Last attempt, raise exception for empty response
                        raise ValueError("Empty response from API")
                
                # Check finish reason
                if response.candidates:
                    finish_reason = str(response.candidates[0].finish_reason)
                    if 'MAX_TOKENS' in finish_reason or finish_reason == '2':
                        print(f"  경고: 응답이 토큰 제한으로 잘림 (finish_reason: {finish_reason}, 길이: {len(response.text)})")
                        # Still return the response to salvage what we can
                        return response
                    elif 'SAFETY' in finish_reason or finish_reason == '3':
                        print(f"  경고: 안전성 문제로 응답 차단 (finish_reason: {finish_reason})")
                        if attempt < max_retries - 1:
                            wait_time = backoff_factor ** attempt
                            time.sleep(wait_time)
                            continue
                        return response
                
                # Check if response is complete JSON
                try:
                    json.loads(response.text)
                    return response
                except json.JSONDecodeError as json_error:
                    # If this is the last attempt, return the incomplete response
                    if attempt == max_retries - 1:
                        print(f"  경고: 불완전한 JSON 응답 (finish_reason: {finish_reason if 'finish_reason' in locals() else 'unknown'}, 길이: {len(response.text)})")
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
                import re
                
                # Find the jokbo_pages array start
                jokbo_pages_start = response_text.find('"jokbo_pages"')
                if jokbo_pages_start == -1:
                    return {"error": "No jokbo_pages found", "partial": True}
                
                # Extract complete page objects
                recovered_pages = []
                
                # Find all page objects using a more robust approach
                # Look for complete page structures
                page_starts = []
                for match in re.finditer(r'"jokbo_page"\s*:\s*(\d+)', response_text):
                    page_starts.append((match.start(), match.group(1)))
                
                for i, (start_pos, page_num) in enumerate(page_starts):
                    # Find the start of this page object
                    obj_start = response_text.rfind('{', 0, start_pos)
                    if obj_start == -1:
                        continue
                    
                    # Try to find the end of this page object
                    # Look for the next page start or the end of the array
                    if i < len(page_starts) - 1:
                        next_start = page_starts[i + 1][0]
                        search_end = response_text.rfind('{', 0, next_start)
                    else:
                        # For the last page, search to the end
                        search_end = len(response_text)
                    
                    # Try to extract this page object
                    brace_count = 0
                    bracket_count = 0
                    in_string = False
                    escape_next = False
                    obj_end = -1
                    
                    for j in range(obj_start, min(search_end, len(response_text))):
                        char = response_text[j]
                        
                        if escape_next:
                            escape_next = False
                            continue
                        
                        if char == '\\':
                            escape_next = True
                            continue
                        
                        if char == '"' and not escape_next:
                            in_string = not in_string
                            continue
                        
                        if not in_string:
                            if char == '{':
                                brace_count += 1
                            elif char == '}':
                                brace_count -= 1
                                if brace_count == 0:
                                    obj_end = j + 1
                                    break
                            elif char == '[':
                                bracket_count += 1
                            elif char == ']':
                                bracket_count -= 1
                    
                    if obj_end > obj_start:
                        try:
                            page_obj_str = response_text[obj_start:obj_end]
                            page_obj = json.loads(page_obj_str)
                            
                            # Validate that this is a complete page object
                            if "jokbo_page" in page_obj and "questions" in page_obj:
                                # Check if questions array is complete
                                questions = page_obj.get("questions", [])
                                valid_questions = []
                                
                                for q in questions:
                                    # A complete question should have at least these fields
                                    if all(key in q for key in ["question_number", "question_text", "answer"]):
                                        valid_questions.append(q)
                                
                                if valid_questions:
                                    page_obj["questions"] = valid_questions
                                    recovered_pages.append(page_obj)
                                    print(f"  페이지 {page_num} 복구 성공: {len(valid_questions)}개 문제")
                        except json.JSONDecodeError:
                            # This page object is incomplete
                            continue
                
                if recovered_pages:
                    result = {
                        "jokbo_pages": recovered_pages,
                        "partial": True,
                        "recovered_pages": len(recovered_pages),
                        "total_questions_recovered": sum(len(p.get("questions", [])) for p in recovered_pages)
                    }
                    print(f"  부분 파싱 성공! {len(recovered_pages)}개 페이지, 총 {result['total_questions_recovered']}개 문제 복구")
                    return result
                else:
                    return {"error": "No complete pages could be recovered", "partial": True}
                
            except Exception as e:
                print(f"  부분 파싱 실패: {str(e)}")
                import traceback
                traceback.print_exc()
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
    
    def save_api_response(self, response_text: str, jokbo_filename: str, lesson_filename: str = None, mode: str = "lesson-centric", finish_reason: str = None, response_metadata: Dict = None):
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
            "response_length": len(response_text),
            "finish_reason": finish_reason,
            "response_metadata": response_metadata or {}
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
    
    def parse_response_json(self, response_text: str, mode: str = "jokbo-centric") -> Dict[str, Any]:
        """Centralized JSON parsing with automatic fallback to partial parsing
        
        Args:
            response_text: The response text to parse
            mode: Processing mode - either "jokbo-centric" or "lesson-centric"
            
        Returns:
            Dict containing either parsed JSON or error with partial recovery attempt
        """
        try:
            # First try direct JSON parsing
            return json.loads(response_text)
        except json.JSONDecodeError as e:
            print(f"  JSON 파싱 오류: {str(e)}")
            print(f"  부분 파싱 시도 중...")
            
            # Attempt partial parsing
            partial_result = self.parse_partial_json(response_text, mode)
            
            # If partial parsing completely failed, raise the original error
            if partial_result.get("error") and not partial_result.get("partial"):
                raise ValueError(f"Complete JSON parsing failure: {partial_result['error']}")
            
            # Return the partial result (which may contain recovered data)
            return partial_result
    
    def analyze_single_jokbo_with_lesson(self, jokbo_path: str, lesson_path: str) -> Dict[str, Any]:
        """Analyze one jokbo PDF against one lesson PDF"""
        
        # Extract the actual filename
        jokbo_filename = Path(jokbo_path).name
        
        # Delete all uploaded files before starting
        print("  기존 업로드 파일 정리 중...")
        self.delete_all_uploaded_files()
        
        # 프롬프트 구성
        prompt = PromptBuilder.build_lesson_centric_prompt(jokbo_filename)
        
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
            result = self.parse_response_json(response.text, "lesson-centric")
            
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
        prompt = PromptBuilder.build_lesson_centric_prompt(jokbo_filename)
        
        # Prepare content with pre-uploaded lesson file
        content = [prompt, lesson_file, jokbo_file]
        
        print(f"  [{datetime.now().strftime('%H:%M:%S')}] Thread-{threading.current_thread().ident}: AI 분석 시작 - {jokbo_filename}")
        try:
            response = self.generate_content_with_retry(content)
        except ValueError as e:
            if "Empty response from API" in str(e):
                print(f"  오류: 빈 응답 받음 - {jokbo_filename}")
                # Delete jokbo file before returning
                self.delete_file_safe(jokbo_file)
                return {"error": "Empty response from API"}
            else:
                raise
        
        # Save API response for debugging
        self.save_api_response(response.text, jokbo_filename, lesson_file.display_name.replace("강의자료_", ""), "lesson-centric")
        
        # Delete jokbo file immediately after analysis  
        print(f"  [{datetime.now().strftime('%H:%M:%S')}] Thread-{threading.current_thread().ident}: 족보 파일 삭제 중 - {jokbo_filename}")
        if not self.delete_file_safe(jokbo_file):
            # 삭제 실패 시 중심 파일을 제외한 모든 파일 정리
            self.cleanup_except_center_file(lesson_file.display_name)
        
        try:
            result = self.parse_response_json(response.text, "lesson-centric")
            
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
        """Analyze multiple jokbo PDFs against one lesson PDF with chunking support"""
        
        # Check if lesson file needs chunking
        max_pages_per_chunk = ProcessingConfig.DEFAULT_CHUNK_SIZE
        lesson_chunks = self.split_pdf_for_analysis(lesson_path, max_pages=max_pages_per_chunk)
        
        if len(lesson_chunks) == 1:
            # Small lesson file, use original method
            return self._analyze_pdfs_for_lesson_original(jokbo_paths, lesson_path)
        
        # Large lesson file, process in chunks
        print(f"  큰 강의자료를 {len(lesson_chunks)}개 조각으로 분할하여 처리합니다.")
        print(f"  세션 ID: {self.session_id}")
        
        # Save processing state
        state = {
            'session_id': self.session_id,
            'mode': 'lesson-centric',
            'lesson_path': lesson_path,
            'jokbo_paths': jokbo_paths,
            'total_chunks': len(lesson_chunks),
            'processed_chunks': 0,
            'status': 'processing',
            'started_at': datetime.now().isoformat()
        }
        self.save_processing_state(state, self.session_dir / "processing_state.json")
        
        chunk_results_saved = []
        
        try:
            # Process each lesson chunk
            if TQDM_AVAILABLE:
                chunk_iterator = tqdm(enumerate(lesson_chunks), total=len(lesson_chunks), 
                                    desc="강의자료 청크 처리", unit="청크")
            else:
                chunk_iterator = enumerate(lesson_chunks)
            
            for idx, (chunk_path, start_page, end_page) in chunk_iterator:
                if not TQDM_AVAILABLE:
                    print(f"  분석 중: {Path(lesson_path).name} (페이지 {start_page}-{end_page}) [{idx+1}/{len(lesson_chunks)}]")
                
                # Create temporary PDF for this chunk
                temp_pdf = self.extract_pdf_pages(chunk_path, start_page, end_page)
                
                try:
                    # Analyze this chunk against all jokbo files
                    chunk_result = self._analyze_lesson_chunk_with_jokbos(
                        temp_pdf, jokbo_paths, start_page, end_page
                    )
                    
                    # Save chunk result
                    chunk_info = {
                        'lesson_filename': Path(lesson_path).name,
                        'start_page': start_page,
                        'end_page': end_page,
                        'total_pages': end_page - start_page + 1,
                        'chunk_index': idx
                    }
                    
                    saved_path = self.save_chunk_result(chunk_info, chunk_result, self.chunk_results_dir)
                    chunk_results_saved.append(saved_path)
                    
                    # Update state
                    state['processed_chunks'] = idx + 1
                    self.save_processing_state(state, self.session_dir / "processing_state.json")
                    
                finally:
                    # Clean up temp PDF
                    if temp_pdf.exists():
                        temp_pdf.unlink()
            
            # Load and merge all chunk results
            return self._merge_lesson_centric_chunk_results(chunk_results_saved)
            
        except Exception as e:
            print(f"  오류 발생: {str(e)}")
            state['status'] = 'failed'
            state['error'] = str(e)
            self.save_processing_state(state, self.session_dir / "processing_state.json")
            raise
        
        finally:
            # Update final state
            state['status'] = 'completed'
            state['completed_at'] = datetime.now().isoformat()
            self.save_processing_state(state, self.session_dir / "processing_state.json")
    
    def _analyze_pdfs_for_lesson_original(self, jokbo_paths: List[str], lesson_path: str) -> Dict[str, Any]:
        """Original method for small lesson files (no chunking)"""
        
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
        max_pages_per_chunk = ProcessingConfig.DEFAULT_CHUNK_SIZE
        lesson_chunks = self.split_pdf_for_analysis(lesson_path, max_pages=max_pages_per_chunk)
        
        if len(lesson_chunks) == 1:
            # Small file, process normally
            return self._analyze_single_lesson_with_jokbo_original(lesson_path, jokbo_path)
        
        # Large file, process in chunks with session management
        print(f"  큰 강의자료를 {len(lesson_chunks)}개 조각으로 분할하여 처리합니다.")
        print(f"  세션 ID: {self.session_id} (sequential mode)")
        
        # Save processing state
        state = {
            'session_id': self.session_id,
            'mode': 'jokbo-centric-sequential',
            'jokbo_path': jokbo_path,
            'lesson_path': lesson_path,
            'total_chunks': len(lesson_chunks),
            'processed_chunks': 0,
            'status': 'processing',
            'started_at': datetime.now().isoformat()
        }
        self.save_processing_state(state, self.session_dir / "processing_state.json")
        
        # Upload jokbo PDF once (전체 족보는 한 번만 업로드)
        jokbo_file = self.upload_pdf(jokbo_path, f"족보_{jokbo_filename}")
        
        temp_files = []
        chunk_results_saved = []
        
        try:
            for idx, (chunk_path, start_page, end_page) in enumerate(lesson_chunks):
                print(f"  분석 중: {lesson_filename} (페이지 {start_page}-{end_page}) [{idx+1}/{len(lesson_chunks)}]")
                
                # Extract pages to temporary file
                temp_pdf = self.extract_pdf_pages(chunk_path, start_page, end_page)
                temp_files.append(temp_pdf)
                
                # Get total pages for validation
                lesson_total_pages = self.get_pdf_page_count(lesson_path)
                
                # Analyze this chunk
                chunk_result = self._analyze_jokbo_with_lesson_chunk(
                    jokbo_file, temp_pdf, jokbo_filename, lesson_filename,
                    start_page, end_page, lesson_total_pages
                )
                
                # Save chunk result to file
                chunk_info = {
                    'lesson_path': lesson_path,
                    'lesson_filename': lesson_filename,
                    'start_page': start_page,
                    'end_page': end_page,
                    'total_pages': lesson_total_pages,
                    'chunk_index': idx
                }
                
                saved_path = self.save_chunk_result(chunk_info, chunk_result, self.chunk_results_dir)
                chunk_results_saved.append(saved_path)
                print(f"    청크 결과 저장: {Path(saved_path).name}")
                
                # Update processing state
                state['processed_chunks'] = idx + 1
                self.save_processing_state(state, self.session_dir / "processing_state.json")
            
            # Load and merge all chunk results
            print(f"  모든 청크 처리 완료. 결과 병합 중...")
            all_results = []
            for saved_path in chunk_results_saved:
                with open(saved_path, 'r', encoding='utf-8') as f:
                    chunk_data = json.load(f)
                    if "error" not in chunk_data['result']:
                        all_results.append(chunk_data['result'])
            
            # Merge results from all chunks
            merged_result = self._merge_jokbo_centric_results(all_results)
            
            # Update state to completed
            state['status'] = 'completed'
            state['completed_at'] = datetime.now().isoformat()
            self.save_processing_state(state, self.session_dir / "processing_state.json")
            
            # 오류 발생 시 중심 파일을 제외한 모든 파일 정리
            self.cleanup_except_center_file(jokbo_file.display_name)
            
            return merged_result
            
        except Exception as e:
            # Update state to failed
            state['status'] = 'failed'
            state['error'] = str(e)
            state['failed_at'] = datetime.now().isoformat()
            self.save_processing_state(state, self.session_dir / "processing_state.json")
            raise
            
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
        prompt = PromptBuilder.build_jokbo_centric_prompt(jokbo_filename, lesson_filename)
        
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
            result = self.parse_response_json(response.text, "jokbo-centric")
            return result
        except (json.JSONDecodeError, ValueError) as e:
            print(f"  Failed to parse response: {str(e)}")
            return {"error": "Failed to parse response"}
    
    def _analyze_jokbo_with_lesson_chunk(self, jokbo_file, lesson_chunk_path: str, 
                                       jokbo_filename: str, lesson_filename: str,
                                       start_page: int, end_page: int,
                                       lesson_total_pages: int = None) -> Dict[str, Any]:
        """Analyze jokbo with a chunk of lesson PDF with retry logic
        
        Args:
            jokbo_file: Pre-uploaded jokbo file object
            lesson_chunk_path: Path to lesson chunk PDF
            jokbo_filename: Original jokbo filename
            lesson_filename: Original lesson filename
            start_page: Start page of chunk in original PDF
            end_page: End page of chunk in original PDF
            lesson_total_pages: Total pages in the original lesson PDF (not chunk)
        """
        
        # Upload lesson chunk
        chunk_display_name = f"강의자료_{lesson_filename}_p{start_page}-{end_page}"
        lesson_chunk_file = self.upload_pdf(lesson_chunk_path, chunk_display_name)
        
        # Use provided total pages or get from chunk (for backward compatibility)
        if lesson_total_pages is None:
            # This should not happen in proper usage, but keep for safety
            print(f"  경고: lesson_total_pages가 제공되지 않아 청크 크기를 사용합니다.")
            with fitz.open(lesson_chunk_path) as pdf:
                total_pages = len(pdf)
        else:
            total_pages = lesson_total_pages
        
        max_retries = ProcessingConfig.MAX_RETRIES
        retry_count = 0
        
        while retry_count < max_retries:
            # Build prompt using helper
            prompt = PDFProcessorHelpers.build_chunk_prompt(
                jokbo_filename, lesson_filename, start_page, end_page,
                JOKBO_CENTRIC_TASK, COMMON_WARNINGS, 
                JOKBO_CENTRIC_OUTPUT_FORMAT.format(
                    jokbo_filename=jokbo_filename,
                    lesson_filename=lesson_filename
                )
            )
            
            # Prepare content for model
            content = [prompt, jokbo_file, lesson_chunk_file]
            
            response = self.model.generate_content(content)
            
            # Get finish reason and metadata
            finish_reason = None
            response_metadata = {}
            if response.candidates:
                finish_reason = str(response.candidates[0].finish_reason)
                response_metadata['finish_reason_raw'] = finish_reason
                if 'MAX_TOKENS' in finish_reason or finish_reason == '2':
                    print(f"  경고: 응답이 토큰 제한으로 잘림 (청크 p{start_page}-{end_page})")
            
            # Save API response for debugging
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            debug_filename = f"gemini_response_{timestamp}_chunk_p{start_page}-{end_page}_{lesson_filename}.json"
            self.save_api_response(response.text, jokbo_filename, lesson_filename, f"chunk_p{start_page}-{end_page}", 
                                 finish_reason=finish_reason, response_metadata=response_metadata)
        
            # Delete lesson chunk file immediately
            self.delete_file_safe(lesson_chunk_file)
            
            try:
                # Clean up common JSON errors from Gemini
                cleaned_text = PDFProcessorHelpers.clean_json_text(response.text)
                
                result = self.parse_response_json(cleaned_text, "jokbo-centric")
                
                # Validate and adjust page numbers
                retry_needed, invalid_questions = PDFProcessorHelpers.validate_question_pages(
                    result, start_page, end_page, total_pages,
                    PDFValidator.validate_and_adjust_page_number
                )
                
                if retry_needed and retry_count < max_retries - 1:
                    retry_count += 1
                    invalid_q_list = list(invalid_questions)
                    print(f"  재분석 시도 (남은 횟수: {max_retries - retry_count}) - 문제 {', '.join(invalid_q_list)}에서 잘못된 페이지 번호 감지")
                    
                    # Delete uploaded file before retry
                    self.delete_file_safe(lesson_chunk_file)
                    
                    # Re-upload for retry
                    lesson_chunk_file = self.upload_pdf(lesson_chunk_path, chunk_display_name)
                    continue
                
                # If we can't retry anymore, remove invalid questions
                if invalid_questions:
                    PDFProcessorHelpers.remove_invalid_questions(result, invalid_questions)
                
                return result
            except json.JSONDecodeError as e:
                print(f"  JSON 파싱 실패 (청크 p{start_page}-{end_page}): {str(e)}")
                
                # Check if response was truncated
                if 'MAX_TOKENS' in finish_reason or finish_reason == '2':
                    print(f"  응답이 토큰 제한으로 잘렸으므로 부분 파싱을 시도합니다.")
                
                # Try partial parsing
                partial_result = self.parse_partial_json(response.text, "jokbo-centric")
                
                # Check if we recovered anything
                if partial_result.get("jokbo_pages") or partial_result.get("partial"):
                    recovered_count = partial_result.get("total_questions_recovered", 0)
                    if recovered_count > 0:
                        print(f"  부분 파싱 성공! {recovered_count}개 문제 복구")
                        
                        # Validate and adjust page numbers for partial result
                        retry_needed, invalid_questions = PDFProcessorHelpers.validate_question_pages(
                            partial_result, start_page, end_page, total_pages,
                            PDFValidator.validate_and_adjust_page_number
                        )
                        if invalid_questions:
                            PDFProcessorHelpers.remove_invalid_questions(partial_result, invalid_questions)
                        
                        # Mark as partial but successful
                        partial_result["partial_recovery"] = True
                        partial_result["original_error"] = str(e)
                        return partial_result
                
                # If partial parsing also failed
                print(f"  부분 파싱도 실패했습니다. 디버그 파일로 저장합니다.")
                debug_file = self.debug_dir / f"failed_json_chunk_p{start_page}-{end_page}.txt"
                with open(debug_file, 'w', encoding='utf-8') as f:
                    f.write(f"Finish Reason: {finish_reason}\n")
                    f.write(f"Response Length: {len(response.text)}\n")
                    f.write(f"Parse Error: {str(e)}\n")
                    f.write(f"---Response Text---\n")
                    f.write(response.text)
                return {"error": "Failed to parse response", "finish_reason": finish_reason}
        
        # If all retries failed, return error
        print(f"  모든 재분석 시도 실패 (청크 p{start_page}-{end_page})")
        return {"error": f"Failed after {max_retries} attempts"}
    
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
    
    def _analyze_lesson_chunk_with_jokbos(self, lesson_chunk_path: str, jokbo_paths: List[str], 
                                          start_page: int, end_page: int) -> Dict[str, Any]:
        """Analyze a lesson chunk against all jokbo files (lesson-centric)
        
        Args:
            lesson_chunk_path: Path to the lesson chunk PDF
            jokbo_paths: List of jokbo file paths
            start_page: Start page in original lesson
            end_page: End page in original lesson
            
        Returns:
            Analysis results for this chunk
        """
        all_related_slides = {}
        total_questions = 0
        all_key_topics = set()
        
        # Upload lesson chunk once
        lesson_chunk_file = self.upload_pdf(lesson_chunk_path, 
                                          f"강의자료_chunk_p{start_page}-{end_page}")
        
        try:
            # Process each jokbo against this chunk
            if TQDM_AVAILABLE:
                jokbo_iterator = tqdm(jokbo_paths, desc="    족보 분석", leave=False)
            else:
                jokbo_iterator = jokbo_paths
                
            for jokbo_path in jokbo_iterator:
                jokbo_filename = Path(jokbo_path).name
                if not TQDM_AVAILABLE:
                    print(f"    족보 분석 중: {jokbo_filename}")
                
                # Upload jokbo file
                jokbo_file = self.upload_pdf(jokbo_path, f"족보_{jokbo_filename}")
                
                try:
                    # Build prompt
                    prompt = PromptBuilder.build_lesson_centric_prompt(jokbo_filename)
                    
                    # Prepare content
                    content = [prompt, lesson_chunk_file, jokbo_file]
                    
                    # Generate response with retry
                    response = self.generate_content_with_retry(content)
                    
                    # Save debug response
                    self.save_api_response(response.text, jokbo_filename, 
                                         f"chunk_p{start_page}-{end_page}", "lesson-centric-chunk")
                    
                    # Parse response
                    result = self.parse_response_json(response.text, "lesson-centric")
                    
                    if "error" not in result:
                        # Adjust page numbers for chunk
                        for slide in result.get("related_slides", []):
                            # Adjust lesson page to original page number
                            chunk_page = slide["lesson_page"]
                            original_page = chunk_page + start_page - 1
                            slide["lesson_page"] = original_page
                            
                            # Add to results
                            if original_page not in all_related_slides:
                                all_related_slides[original_page] = {
                                    "lesson_page": original_page,
                                    "related_jokbo_questions": [],
                                    "importance_score": slide.get("importance_score", 5),
                                    "key_concepts": set()
                                }
                            
                            # Merge questions
                            all_related_slides[original_page]["related_jokbo_questions"].extend(
                                slide.get("related_jokbo_questions", [])
                            )
                            
                            # Update importance score
                            all_related_slides[original_page]["importance_score"] = max(
                                all_related_slides[original_page]["importance_score"],
                                slide.get("importance_score", 5)
                            )
                            
                            # Add key concepts
                            all_related_slides[original_page]["key_concepts"].update(
                                slide.get("key_concepts", [])
                            )
                        
                        # Update summary
                        if "summary" in result:
                            total_questions += result["summary"].get("total_questions", 0)
                            all_key_topics.update(result["summary"].get("key_topics", []))
                    
                finally:
                    # Delete jokbo file
                    self.delete_file_safe(jokbo_file)
                    
        finally:
            # Delete lesson chunk file
            self.delete_file_safe(lesson_chunk_file)
        
        # Convert sets to lists
        final_slides = []
        for slide_data in all_related_slides.values():
            slide_data["key_concepts"] = list(slide_data["key_concepts"])
            final_slides.append(slide_data)
        
        return {
            "related_slides": final_slides,
            "summary": {
                "total_questions": total_questions,
                "key_topics": list(all_key_topics),
                "chunk_info": {
                    "start_page": start_page,
                    "end_page": end_page
                }
            }
        }
    
    def _merge_lesson_centric_chunk_results(self, chunk_result_paths: List[str]) -> Dict[str, Any]:
        """Merge results from multiple lesson chunks (lesson-centric mode)
        
        Args:
            chunk_result_paths: List of saved chunk result file paths
            
        Returns:
            Merged analysis results
        """
        all_related_slides = {}
        total_questions = 0
        all_key_topics = set()
        
        print(f"  병합 중: {len(chunk_result_paths)}개 청크 결과")
        
        # Load and merge each chunk result
        for chunk_path in chunk_result_paths:
            with open(chunk_path, 'r', encoding='utf-8') as f:
                chunk_data = json.load(f)
                
            if "error" in chunk_data['result']:
                continue
                
            result = chunk_data['result']
            
            # Merge slides
            for slide in result.get("related_slides", []):
                lesson_page = slide["lesson_page"]
                
                if lesson_page not in all_related_slides:
                    all_related_slides[lesson_page] = slide
                else:
                    # Merge with existing slide
                    existing = all_related_slides[lesson_page]
                    
                    # Extend questions
                    existing["related_jokbo_questions"].extend(
                        slide.get("related_jokbo_questions", [])
                    )
                    
                    # Update importance score
                    existing["importance_score"] = max(
                        existing["importance_score"],
                        slide.get("importance_score", 5)
                    )
                    
                    # Merge key concepts
                    existing_concepts = set(existing.get("key_concepts", []))
                    existing_concepts.update(slide.get("key_concepts", []))
                    existing["key_concepts"] = list(existing_concepts)
            
            # Update summary
            if "summary" in result:
                total_questions += result["summary"].get("total_questions", 0)
                all_key_topics.update(result["summary"].get("key_topics", []))
        
        # Remove duplicate questions per slide
        for slide_data in all_related_slides.values():
            questions = slide_data.get("related_jokbo_questions", [])
            # Remove duplicates while preserving order
            seen = set()
            unique_questions = []
            for q in questions:
                key = (q.get("jokbo_filename"), q.get("jokbo_page"), q.get("question_number"))
                if key not in seen:
                    seen.add(key)
                    unique_questions.append(q)
            slide_data["related_jokbo_questions"] = unique_questions
        
        # Convert to list and sort
        final_slides = list(all_related_slides.values())
        final_slides.sort(key=lambda x: x["lesson_page"])
        
        print(f"  병합 완료: {len(final_slides)}개 관련 슬라이드, {total_questions}개 문제")
        
        return {
            "related_slides": final_slides,
            "summary": {
                "total_related_slides": len(final_slides),
                "total_questions": total_questions,
                "key_topics": list(all_key_topics),
                "study_recommendations": "각 슬라이드별로 관련된 족보 문제들을 중점적으로 학습하세요."
            }
        }
    
    def analyze_lessons_for_jokbo(self, lesson_paths: List[str], jokbo_path: str) -> Dict[str, Any]:
        """Analyze multiple lesson PDFs against one jokbo PDF (jokbo-centric)"""
        
        # 세션 기반 처리 상태 저장
        print(f"  세션 ID: {self.session_id} (jokbo-centric sequential mode)")
        
        state = {
            'session_id': self.session_id,
            'mode': 'jokbo-centric-sequential-multi',
            'jokbo_path': jokbo_path,
            'lesson_paths': lesson_paths,
            'total_lessons': len(lesson_paths),
            'processed_lessons': 0,
            'status': 'processing',
            'started_at': datetime.now().isoformat()
        }
        self.save_processing_state(state, self.session_dir / "processing_state.json")
        
        all_jokbo_pages = {}
        all_connections = {}  # {question_id: [connections with scores]}
        total_related_slides = 0
        lesson_results_saved = []
        
        # Process each lesson file individually
        for idx, lesson_path in enumerate(lesson_paths):
            print(f"  분석 중: {Path(lesson_path).name} [{idx+1}/{len(lesson_paths)}]")
            result = self.analyze_single_lesson_with_jokbo(lesson_path, jokbo_path)
            
            if "error" in result:
                print(f"    오류 발생: {result['error']}")
                # 오류도 저장
                error_info = {
                    'lesson_path': lesson_path,
                    'error': result['error']
                }
                saved_path = self.save_lesson_result(idx, lesson_path, error_info, self.chunk_results_dir)
                lesson_results_saved.append(saved_path)
            else:
                # 성공한 결과 저장
                saved_path = self.save_lesson_result(idx, lesson_path, result, self.chunk_results_dir)
                lesson_results_saved.append(saved_path)
                print(f"    강의자료 결과 저장: {Path(saved_path).name}")
            
            # 처리 상태 업데이트
            state['processed_lessons'] = idx + 1
            self.save_processing_state(state, self.session_dir / "processing_state.json")
        
        # 저장된 결과들을 로드하여 병합
        print(f"  모든 강의자료 처리 완료. 결과 병합 중...")
        for saved_path in lesson_results_saved:
            with open(saved_path, 'r', encoding='utf-8') as f:
                lesson_data = json.load(f)
                
                if "error" in lesson_data:
                    continue
                
                result = lesson_data.get('result', lesson_data)
                
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
        
        # 최종 결과
        result = {
            "jokbo_pages": final_pages_list,
            "summary": {
                "total_jokbo_pages": len(final_pages_list),
                "total_questions": total_questions,
                "total_related_slides": filtered_total_slides,
                "study_recommendations": "각 족보 문제별로 가장 관련성이 높은 강의 슬라이드를 중점적으로 학습하세요."
            }
        }
        
        # 처리 상태를 완료로 업데이트
        state['status'] = 'completed'
        state['completed_at'] = datetime.now().isoformat()
        self.save_processing_state(state, self.session_dir / "processing_state.json")
        
        return result
    
    def analyze_single_lesson_with_jokbo_preloaded(self, lesson_path: str, jokbo_file) -> Dict[str, Any]:
        """Analyze one lesson PDF against pre-uploaded jokbo file (jokbo-centric) with chunk splitting"""
        
        # Extract the actual filename
        lesson_filename = Path(lesson_path).name
        jokbo_filename = jokbo_file.display_name.replace("족보_", "")
        
        # Split lesson PDF if it's too large
        max_pages_per_chunk = ProcessingConfig.DEFAULT_CHUNK_SIZE
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
            try:
                response = self.generate_content_with_retry(content)
            except ValueError as e:
                if "Empty response from API" in str(e):
                    print(f"  오류: 빈 응답 받음 - {lesson_filename}")
                    # Delete lesson file before returning
                    self.delete_file_safe(lesson_file)
                    return {"error": "Empty response from API"}
                else:
                    raise
            
            # Save API response for debugging
            self.save_api_response(response.text, jokbo_filename, lesson_filename, "jokbo-centric")
            
            # Delete lesson file immediately after analysis
            print(f"  [{datetime.now().strftime('%H:%M:%S')}] Thread-{threading.current_thread().ident}: 강의자료 파일 삭제 중 - {lesson_filename}")
            self.delete_file_safe(lesson_file)
            # Multi-API 모드에서는 다른 API의 파일을 정리하지 않음
            
            try:
                result = self.parse_response_json(response.text, "jokbo-centric")
                
                # Remove self-referencing slides (where jokbo page == lesson page)
                removed_count = PDFProcessorHelpers.remove_self_referencing_slides(result, jokbo_filename)
                
                print(f"  [{datetime.now().strftime('%H:%M:%S')}] Thread-{threading.current_thread().ident}: 분석 완료 - {lesson_filename}")
                return result
            except (json.JSONDecodeError, ValueError) as e:
                print(f"  Failed to parse response: {str(e)}")
                return {"error": "Failed to parse response"}
        
        # Large file, process in chunks
        print(f"  [{datetime.now().strftime('%H:%M:%S')}] Thread-{threading.current_thread().ident}: 큰 강의자료를 {len(lesson_chunks)}개 조각으로 분할하여 처리합니다.")
        
        # Get total pages of the original lesson PDF
        lesson_total_pages = self.get_pdf_page_count(lesson_path)
        
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
                    start_page, end_page, lesson_total_pages
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
        
        try:
            response = self.generate_content_with_retry(content)
        except ValueError as e:
            if "Empty response from API" in str(e):
                print(f"  오류: 빈 응답 받음 (청크 p{start_page}-{end_page})")
                # Delete the chunk file before returning
                self.delete_file_safe(lesson_chunk_file)
                return {"error": "Empty response from API", "chunk_info": {"start": start_page, "end": end_page}}, None
            else:
                raise
        
        # Get finish reason and metadata
        finish_reason = None
        response_metadata = {}
        if response.candidates:
            finish_reason = str(response.candidates[0].finish_reason)
            response_metadata['finish_reason_raw'] = finish_reason
        
        # Save API response for debugging
        self.save_api_response(response.text, jokbo_filename, lesson_filename, f"chunk_p{start_page}-{end_page}",
                             finish_reason=finish_reason, response_metadata=response_metadata)
        
        # Check if response was truncated
        if finish_reason and ('MAX_TOKENS' in finish_reason or finish_reason == '2'):
            print(f"  경고: 응답이 토큰 제한으로 잘림 (청크 p{start_page}-{end_page}, 길이: {len(response.text)})")
        
        # Comprehensive response validation (still check in case of other issues)
        if not response.text or len(response.text) == 0:
            print(f"  오류: 빈 응답 받음 (청크 p{start_page}-{end_page})")
            # Delete the chunk file before returning
            self.delete_file_safe(lesson_chunk_file)
            return {"error": "Empty response from API", "chunk_info": {"start": start_page, "end": end_page}}, None
        elif len(response.text) < ProcessingConfig.MIN_RESPONSE_LENGTH:
            print(f"  오류: 응답이 너무 짧음 (청크 p{start_page}-{end_page}, 길이: {len(response.text)})")
            self.delete_file_safe(lesson_chunk_file)
            return {"error": f"Response too short: {len(response.text)} chars", "chunk_info": {"start": start_page, "end": end_page}}, None
        elif not response.text.strip().startswith('{') and not response.text.strip().startswith('['):
            print(f"  오류: JSON 형식이 아닌 응답 (청크 p{start_page}-{end_page})")
            self.delete_file_safe(lesson_chunk_file)
            return {"error": "Response doesn't appear to be JSON", "chunk_info": {"start": start_page, "end": end_page}}, None
        
        try:
            # Clean up common JSON errors from Gemini
            cleaned_text = PDFProcessorHelpers.clean_json_text(response.text)
            result = self.parse_response_json(cleaned_text, "jokbo-centric")
            
            # Remove self-referencing slides (where jokbo page == lesson page)
            removed_count = PDFProcessorHelpers.remove_self_referencing_slides(result, jokbo_filename)
            
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
            
            # Try smart partial parsing - extract complete question objects
            partial_result = self.parse_partial_json(response.text, "jokbo-centric")
            
            # If we got some data from partial parsing
            if partial_result.get("jokbo_pages"):
                print(f"  부분 파싱으로 {len(partial_result['jokbo_pages'])}개 페이지 데이터 복구")
                
                # Apply validation to partial results
                retry_needed, invalid_questions = PDFProcessorHelpers.validate_question_pages(
                    partial_result, start_page, end_page, lesson_total_pages,
                    self.validate_and_adjust_page_number
                )
                if invalid_questions:
                    PDFProcessorHelpers.remove_invalid_questions(partial_result, invalid_questions)
                
                # Add finish_reason to result if truncated
                if finish_reason and ('MAX_TOKENS' in finish_reason or finish_reason == '2'):
                    partial_result['truncated'] = True
                    partial_result['finish_reason'] = finish_reason
                
                return partial_result, lesson_chunk_file
            else:
                # Complete parsing failure - save debug info
                debug_file = self.debug_dir / f"failed_json_chunk_p{start_page}-{end_page}.txt"
                with open(debug_file, 'w', encoding='utf-8') as f:
                    f.write(f"Finish reason: {finish_reason}\n")
                    f.write(f"Response length: {len(response.text)}\n")
                    f.write("="*50 + "\n")
                    f.write(response.text)
                
                # Delete the chunk file before returning
                self.delete_file_safe(lesson_chunk_file)
                
                # Return error with finish_reason info
                error_result = {"error": "Failed to parse response"}
                if finish_reason:
                    error_result["finish_reason"] = finish_reason
                return error_result, None
    
    def analyze_lessons_for_jokbo_parallel(self, lesson_paths: List[str], jokbo_path: str, max_workers: int = ProcessingConfig.DEFAULT_THREAD_WORKERS) -> Dict[str, Any]:
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
        max_pages_per_chunk = ProcessingConfig.DEFAULT_CHUNK_SIZE
        
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
    
    def analyze_pdfs_for_lesson_parallel(self, jokbo_paths: List[str], lesson_path: str, max_workers: int = ProcessingConfig.DEFAULT_THREAD_WORKERS) -> Dict[str, Any]:
        """Analyze multiple jokbo PDFs against one lesson PDF using parallel processing with chunking support"""
        
        print(f"  [{datetime.now().strftime('%H:%M:%S')}] 병렬 처리 시작")
        
        # Check if lesson file needs chunking
        max_pages_per_chunk = ProcessingConfig.DEFAULT_CHUNK_SIZE
        lesson_chunks = self.split_pdf_for_analysis(lesson_path, max_pages=max_pages_per_chunk)
        
        if len(lesson_chunks) == 1:
            # Small lesson file, use original parallel method
            return self._analyze_pdfs_for_lesson_parallel_original(jokbo_paths, lesson_path, max_workers)
        
        # Large lesson file, process chunks in parallel
        print(f"  큰 강의자료를 {len(lesson_chunks)}개 조각으로 분할하여 병렬 처리합니다.")
        print(f"  세션 ID: {self.session_id}")
        
        # Save processing state
        state = {
            'session_id': self.session_id,
            'mode': 'lesson-centric-parallel',
            'lesson_path': lesson_path,
            'jokbo_paths': jokbo_paths,
            'total_chunks': len(lesson_chunks),
            'processed_chunks': 0,
            'status': 'processing',
            'started_at': datetime.now().isoformat()
        }
        self.save_processing_state(state, self.session_dir / "processing_state.json")
        
        chunk_results_saved = []
        lock = threading.Lock()
        
        def process_chunk(chunk_info: Tuple[int, Tuple[str, int, int]]) -> str:
            """Process a single chunk in parallel"""
            idx, (chunk_path, start_page, end_page) = chunk_info
            thread_id = threading.current_thread().ident
            print(f"  [{datetime.now().strftime('%H:%M:%S')}] Thread-{thread_id}: 청크 처리 시작 - 페이지 {start_page}-{end_page}")
            
            # Create temporary PDF for this chunk
            temp_pdf = self.extract_pdf_pages(chunk_path, start_page, end_page)
            
            try:
                # Create thread processor with parent session
                thread_processor = PDFProcessor(self.model, session_id=self.session_id)
                
                # Analyze this chunk against all jokbo files
                chunk_result = thread_processor._analyze_lesson_chunk_with_jokbos(
                    temp_pdf, jokbo_paths, start_page, end_page
                )
                
                # Save chunk result
                chunk_info = {
                    'lesson_filename': Path(lesson_path).name,
                    'start_page': start_page,
                    'end_page': end_page,
                    'total_pages': end_page - start_page + 1,
                    'chunk_index': idx
                }
                
                saved_path = thread_processor.save_chunk_result(chunk_info, chunk_result, self.chunk_results_dir)
                
                # Update state
                with lock:
                    state['processed_chunks'] += 1
                    self.save_processing_state(state, self.session_dir / "processing_state.json")
                
                return saved_path
                
            finally:
                # Clean up temp PDF
                if temp_pdf.exists():
                    temp_pdf.unlink()
                # Clean up thread processor
                try:
                    thread_processor.__del__()
                except:
                    pass
        
        try:
            # Process chunks in parallel
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                if TQDM_AVAILABLE:
                    futures = list(tqdm(
                        executor.map(process_chunk, enumerate(lesson_chunks)),
                        total=len(lesson_chunks),
                        desc="강의자료 청크 병렬 처리"
                    ))
                else:
                    futures = list(executor.map(process_chunk, enumerate(lesson_chunks)))
                
                chunk_results_saved = futures
            
            # Load and merge all chunk results
            return self._merge_lesson_centric_chunk_results(chunk_results_saved)
            
        except Exception as e:
            print(f"  오류 발생: {str(e)}")
            state['status'] = 'failed'
            state['error'] = str(e)
            self.save_processing_state(state, self.session_dir / "processing_state.json")
            raise
        
        finally:
            # Update final state
            state['status'] = 'completed'
            state['completed_at'] = datetime.now().isoformat()
            self.save_processing_state(state, self.session_dir / "processing_state.json")
    
    def _analyze_pdfs_for_lesson_parallel_original(self, jokbo_paths: List[str], lesson_path: str, max_workers: int = ProcessingConfig.DEFAULT_THREAD_WORKERS) -> Dict[str, Any]:
        """Original parallel method for small lesson files (no chunking)"""
        
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
    
    def save_lesson_result(self, lesson_idx: int, lesson_path: str, result: Dict[str, Any], temp_dir: Path) -> str:
        """강의자료별 처리 결과를 임시 파일로 저장
        
        Args:
            lesson_idx: 강의자료 인덱스
            lesson_path: 강의자료 경로
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
        lesson_filename = Path(lesson_path).name
        filename = f"lesson_{lesson_idx:03d}_{lesson_filename}_{timestamp}.json"
        filepath = temp_dir / filename
        
        # 결과 저장
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump({
                'lesson_idx': lesson_idx,
                'lesson_path': lesson_path,
                'lesson_filename': lesson_filename,
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
    
    def analyze_lessons_for_jokbo_multi_api(self, lesson_paths: List[str], jokbo_path: str, api_keys: List[str], model_type: str = "pro", thinking_budget: Optional[int] = None) -> Dict[str, Any]:
        """Analyze multiple lesson PDFs against one jokbo PDF using multiple API keys (jokbo-centric)
        
        Each API key handles one chunk at a time with isolated context (jokbo + chunk only)
        
        Args:
            lesson_paths: List of lesson PDF file paths
            jokbo_path: Path to the jokbo PDF file
            api_keys: List of Gemini API keys to use
            model_type: Model type to use
            thinking_budget: Thinking budget for flash models
            
        Returns:
            Analysis results
        """
        from api_key_manager import APIKeyManager
        from config import create_model, configure_api
        import google.generativeai as genai
        from multiprocessing import Process, Queue
        import multiprocessing
        
        print(f"  [{datetime.now().strftime('%H:%M:%S')}] Multi-API 처리 시작 (족보 중심)")
        print(f"  사용 가능한 API 키: {len(api_keys)}개")
        
        # Initialize API Key Manager
        api_manager = APIKeyManager(api_keys, model_type, thinking_budget)
        
        # 임시 파일 디렉토리 설정
        temp_dir = Path("output/temp/chunk_results")
        state_file = Path("output/temp/processing_state.json")
        
        # In Multi-API mode, each API manages its own files
        # Do not delete all files as APIs cannot access files uploaded by other APIs
        print("  Multi-API 모드: 각 API가 자체 파일 관리")
        
        # Prepare all chunks from all lesson files
        all_chunks = []
        max_pages_per_chunk = ProcessingConfig.DEFAULT_CHUNK_SIZE
        
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
        
        print(f"  총 {len(all_chunks)}개 청크를 Multi-API로 처리합니다.")
        
        # Dictionary to store results by lesson file
        lesson_results = {}
        for lesson_path in lesson_paths:
            lesson_results[lesson_path] = []
        
        lock = threading.Lock()
        processed_chunks = 0
        failed_chunks = []
        
        
        # Distribute chunks evenly among APIs
        chunks_per_api = len(all_chunks) // len(api_keys)
        extra_chunks = len(all_chunks) % len(api_keys)
        
        api_assignments = []
        chunk_start = 0
        
        for i, api_key in enumerate(api_keys):
            # Distribute extra chunks to first APIs
            chunk_count = chunks_per_api + (1 if i < extra_chunks else 0)
            assigned_chunks = all_chunks[chunk_start:chunk_start + chunk_count]
            
            if assigned_chunks:  # Only add if there are chunks to process
                # Pass model configuration instead of model instance
                api_assignments.append((api_key, model_type, thinking_budget, assigned_chunks))
                print(f"  API #{i+1} (***{api_key[-4:]}): {len(assigned_chunks)}개 청크 할당")
            
            chunk_start += chunk_count
        
        print(f"  [{datetime.now().strftime('%H:%M:%S')}] {len(all_chunks)}개 청크를 {len(api_assignments)}개 API로 처리 시작")
        
        # Use multiprocessing instead of threading
        result_queue = Queue()
        processes = []
        
        # Start processes for each API
        for api_key, model_type, thinking_budget, assigned_chunks in api_assignments:
            p = Process(target=PDFProcessor.process_api_chunks_multiprocess,
                       args=(api_key, model_type, thinking_budget, assigned_chunks, 
                             jokbo_path, self.session_id, result_queue))
            p.start()
            processes.append(p)
        
        print(f"  [{datetime.now().strftime('%H:%M:%S')}] {len(processes)}개 프로세스 시작 완료 - Multi-API 처리 중...")
        
        # Collect results
        total_successful = 0
        total_failed = 0
        results_collected = 0
        
        if TQDM_AVAILABLE:
            pbar = tqdm(total=len(all_chunks), desc="청크 처리")
        
        # Wait for all processes to complete
        while results_collected < len(processes):
            try:
                successful, failed = result_queue.get(timeout=1)
                total_successful += successful
                total_failed += failed
                results_collected += 1
                
                if TQDM_AVAILABLE:
                    pbar.update(successful + failed)
                    
            except:
                # Check if any process is still alive
                alive_count = sum(1 for p in processes if p.is_alive())
                if alive_count == 0 and results_collected < len(processes):
                    # All processes died but we didn't get all results
                    print(f"  경고: 일부 프로세스가 비정상 종료됨")
                    break
        
        # Wait for all processes to finish
        for p in processes:
            p.join()
        
        if TQDM_AVAILABLE:
            pbar.close()
        
        print(f"  [{datetime.now().strftime('%H:%M:%S')}] 모든 청크 처리 완료")
        
        # Print API usage statistics
        print("\n  API 사용 통계:")
        api_status = api_manager.get_status()
        for api_name, status in api_status.items():
            state_msg = '사용 가능' if status['available'] else f"쿨다운 ({status['cooldown_remaining']})"
            print(f"    {api_name}: 사용 횟수={status['usage_count']}, 마지막 사용={status['last_used']}, 상태={state_msg}") 
        
        # 모든 청크 결과 파일 로드 및 병합
        print(f"  [{datetime.now().strftime('%H:%M:%S')}] 청크 결과 병합 중...")
        final_result = self.load_and_merge_chunk_results(self.chunk_results_dir)
        
        # Check for failed chunks and retry with different APIs
        failed_chunk_files = []
        for chunk_file in self.chunk_results_dir.glob("chunk_*.json"):
            with open(chunk_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if "error" in data["result"]:
                    # Check if it's an empty response error
                    if "Empty response from API" in data["result"].get("error", ""):
                        failed_chunk_files.append((chunk_file, data["chunk_info"]))
        
        # Retry failed chunks with different API
        if failed_chunk_files:
            print(f"\n  [{datetime.now().strftime('%H:%M:%S')}] {len(failed_chunk_files)}개 실패한 청크 재시도 중...")
            retry_queue = Queue()
            retry_chunks = [chunk_info for _, chunk_info in failed_chunk_files]
            
            # Get an available API for retry
            available_api = None
            for api_key in api_keys:
                if api_key not in [assignment[0] for assignment in api_assignments]:
                    available_api = api_key
                    break
            
            if not available_api:
                # Use round-robin to select API
                available_api = api_keys[len(retry_chunks) % len(api_keys)]
            
            # Retry with single API
            retry_process = Process(target=PDFProcessor.process_api_chunks_multiprocess,
                                  args=(available_api, model_type, thinking_budget, retry_chunks,
                                       jokbo_path, self.session_id, retry_queue))
            retry_process.start()
            
            # Wait for retry results
            retry_successful, retry_failed = retry_queue.get()
            retry_process.join()
            
            print(f"  재시도 결과: 성공 {retry_successful}, 실패 {retry_failed}")
            total_successful += retry_successful
            total_failed = retry_failed  # Update to final failed count
        
        # 최종 결과 병합
        print(f"  [{datetime.now().strftime('%H:%M:%S')}] 최종 청크 결과 병합 중...")
        final_result = self.load_and_merge_chunk_results(self.chunk_results_dir)
        
        # Apply final filtering and sorting
        if "all_connections" in final_result:
            all_connections = final_result["all_connections"]
            print(f"  병합 완료: {len(all_connections)}개 문제")
            
            # 최종 필터링 및 정렬
            print(f"  [{datetime.now().strftime('%H:%M:%S')}] 최종 필터링 및 문제 번호순 정렬 중...")
            final_result = self.apply_final_filtering_and_sorting(all_connections)
            
            # 디버그 정보 출력
            print(f"  [{datetime.now().strftime('%H:%M:%S')}] 최종 결과: {final_result['summary']['total_jokbo_pages']}개 페이지, "
                  f"{final_result['summary']['total_questions']}개 문제, {final_result['summary']['total_related_slides']}개 연결")
        
        if total_failed > 0:
            print(f"  경고: {total_failed}개 청크 최종 처리 실패")
            final_result["failed_chunks"] = total_failed
        
        return final_result
    
    @staticmethod
    def process_api_chunks_multiprocess(api_key: str, model_type: str, thinking_budget: Optional[int], 
                                       assigned_chunks: List[Dict[str, Any]], jokbo_path: str, 
                                       session_id: str, result_queue: Queue):
        """Process chunks in a separate process with its own genai instance
        
        This function runs in a separate process to avoid genai.configure conflicts
        """
        import google.generativeai as genai
        from config import create_model, configure_api
        
        import multiprocessing
        thread_id = multiprocessing.current_process().pid
        api_key_suffix = api_key[-4:] if api_key else "None"
        successful = 0
        failed = 0
        
        print(f"  [Process-{thread_id}] API ***{api_key_suffix}: {len(assigned_chunks)}개 청크 처리 시작")
        
        # Configure API for this process
        configure_api(api_key)
        
        # Create model for this process
        model = create_model(model_type, thinking_budget)
        
        # Create PDFProcessor for this process
        processor = PDFProcessor(model, session_id=session_id)
        
        # Upload jokbo file once for all chunks
        print(f"  [Process-{thread_id}] 족보 업로드 중...")
        try:
            jokbo_file = processor.upload_pdf(jokbo_path, f"족보_{Path(jokbo_path).name}_{api_key_suffix}")
            jokbo_filename = Path(jokbo_path).name
        except Exception as e:
            print(f"  [Process-{thread_id}] 족보 업로드 실패 - {str(e)}")
            # Save error for all chunks
            for chunk_info in assigned_chunks:
                error_result = {"error": f"족보 업로드 실패: {str(e)}", "chunk_info": chunk_info}
                processor.save_chunk_result(chunk_info, error_result, processor.chunk_results_dir)
            result_queue.put((0, len(assigned_chunks)))
            return
        
        # Process each assigned chunk
        for chunk_idx, chunk_info in enumerate(assigned_chunks):
            lesson_path = chunk_info['lesson_path']
            lesson_filename = chunk_info['lesson_filename']
            start_page = chunk_info['start_page']
            end_page = chunk_info['end_page']
            
            print(f"  [Process-{thread_id}] [{chunk_idx+1}/{len(assigned_chunks)}] {lesson_filename} (p{start_page}-{end_page}) 처리 중...")
            
            try:
                # Extract pages to temporary file if needed
                max_pages_per_chunk = ProcessingConfig.DEFAULT_CHUNK_SIZE
                if len(processor.split_pdf_for_analysis(lesson_path, max_pages=max_pages_per_chunk)) > 1:
                    temp_pdf = processor.extract_pdf_pages(chunk_info['chunk_path'], start_page, end_page)
                else:
                    temp_pdf = chunk_info['chunk_path']
                
                # Analyze this chunk
                result, uploaded_file = processor._analyze_jokbo_with_lesson_chunk_preloaded(
                    jokbo_file, temp_pdf,
                    jokbo_filename,
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
                
                # Delete only the lesson file (keep jokbo for next chunks)
                if uploaded_file:
                    print(f"  [Process-{thread_id}] 강의자료 파일 삭제 중...")
                    processor.delete_file_safe(uploaded_file)
                    if uploaded_file in processor.uploaded_files:
                        processor.uploaded_files.remove(uploaded_file)
                
                # Check if result has error (including empty response)
                if "error" in result:
                    print(f"  [Process-{thread_id}] 청크 처리 실패: {result.get('error', 'Unknown error')}")
                    # Save error result for potential retry
                    saved_path = processor.save_chunk_result(chunk_info, result, processor.chunk_results_dir)
                    failed += 1
                else:
                    # Save successful result
                    saved_path = processor.save_chunk_result(chunk_info, result, processor.chunk_results_dir)
                    print(f"  [Process-{thread_id}] 결과 저장 완료: {Path(saved_path).name}")
                    successful += 1
                
            except Exception as e:
                print(f"  [Process-{thread_id}] 오류 발생: {str(e)}")
                # Save error result
                error_result = {"error": str(e), "chunk_info": chunk_info}
                processor.save_chunk_result(chunk_info, error_result, processor.chunk_results_dir)
                failed += 1
        
        # Delete jokbo file after all chunks are processed
        print(f"  [Process-{thread_id}] 모든 청크 완료, 족보 파일 삭제 중...")
        if jokbo_file:
            processor.delete_file_safe(jokbo_file)
            if jokbo_file in processor.uploaded_files:
                processor.uploaded_files.remove(jokbo_file)
        
        # Clean up any remaining files
        try:
            processor.__del__()
        except:
            pass
        
        print(f"  [Process-{thread_id}] 완료 (성공: {successful}, 실패: {failed})")
        result_queue.put((successful, failed))