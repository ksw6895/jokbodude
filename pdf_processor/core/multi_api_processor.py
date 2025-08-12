"""Multi-API processor for handling multiple Gemini API keys"""

from typing import Dict, Any, List, Optional
from pathlib import Path
import time
from datetime import datetime, timedelta
from multiprocessing import Process, Queue
import json

from pdf_processor.core.processor import PDFProcessor
from processing_config import ProcessingConfig
from config import create_model
from api_key_manager import APIKeyManager


class MultiAPIProcessor:
    """Handles processing with multiple API keys for better reliability"""
    
    def __init__(self):
        """Initialize multi-API processor"""
        self.api_manager = APIKeyManager()
    
    def analyze_lessons_for_jokbo_multi_api(
        self,
        lesson_paths: List[str],
        jokbo_path: str,
        api_keys: List[str],
        model_type: str = "pro",
        thinking_budget: Optional[int] = None
    ) -> Dict[str, Any]:
        """Analyze lessons using multiple API keys
        
        Args:
            lesson_paths: List of lesson PDF paths
            jokbo_path: Path to jokbo PDF
            api_keys: List of API keys to use
            model_type: Model type to use
            thinking_budget: Thinking budget for model
            
        Returns:
            Merged analysis results
        """
        print("\n=== Multi-API 모드 시작 ===")
        print(f"사용 가능한 API 키: {len(api_keys)}개")
        print(f"분석할 강의자료: {len(lesson_paths)}개")
        
        # Initialize API manager
        self.api_manager.initialize_keys(api_keys)
        
        # Create main processor for file management
        # Configure API key first, then create model
        import google.generativeai as genai
        genai.configure(api_key=api_keys[0])
        main_model = create_model(model_type, thinking_budget)
        main_processor = PDFProcessor(main_model)
        
        # Upload jokbo once
        print("\n족보 파일 업로드 중...")
        main_processor.delete_all_uploaded_files()
        jokbo_file = main_processor.upload_pdf(
            jokbo_path, f"족보_{Path(jokbo_path).name}"
        )
        
        # Process lessons with API rotation
        all_results = []
        failed_lessons = []
        
        for idx, lesson_path in enumerate(lesson_paths):
            print(f"\n[{idx+1}/{len(lesson_paths)}] 처리 중: {Path(lesson_path).name}")
            
            # Try with available API keys
            success = False
            result = None
            
            for attempt in range(min(3, len(api_keys))):
                api_key = self.api_manager.get_next_api()
                
                if not api_key:
                    print("  사용 가능한 API 키가 없습니다. 대기 중...")
                    time.sleep(60)
                    api_key = self.api_manager.get_next_api()
                    if not api_key:
                        break
                
                try:
                    # Create processor with current API key
                    # Configure API key first, then create model
                    genai.configure(api_key=api_key)
                    model = create_model(model_type, thinking_budget)
                    processor = PDFProcessor(model, session_id=main_processor.session_id)
                    
                    # Analyze lesson
                    result = processor.analyze_single_lesson_with_jokbo_preloaded(
                        lesson_path, jokbo_file
                    )
                    
                    if result and "error" not in result:
                        all_results.append(result)
                        self.api_manager.mark_success(api_key)
                        success = True
                        print(f"  성공 (API #{self._get_api_index(api_key, api_keys)})")
                        break
                    else:
                        self.api_manager.mark_failure(api_key)
                        print(f"  실패 (API #{self._get_api_index(api_key, api_keys)}): {result.get('error', 'Unknown error')}")
                        
                except Exception as e:
                    self.api_manager.mark_failure(api_key)
                    print(f"  오류 (API #{self._get_api_index(api_key, api_keys)}): {str(e)}")
            
            if not success:
                failed_lessons.append(lesson_path)
                print(f"  최종 실패: {Path(lesson_path).name}")
        
        # Clean up
        main_processor.delete_file_safe(jokbo_file)
        
        # Report results
        print(f"\n=== Multi-API 처리 완료 ===")
        print(f"성공: {len(all_results)}/{len(lesson_paths)}")
        if failed_lessons:
            print(f"실패한 파일들:")
            for lesson in failed_lessons:
                print(f"  - {Path(lesson).name}")
        
        # Merge results
        if not all_results:
            return {"jokbo_pages": []}
        
        # Use result merger from main processor
        merged = main_processor._merge_jokbo_centric_results(all_results)
        
        # Apply final filtering
        all_connections = self._convert_to_connections_dict(merged)
        final_result = main_processor.apply_final_filtering_and_sorting(all_connections)
        
        return final_result
    
    def _get_api_index(self, api_key: str, api_keys: List[str]) -> int:
        """Get API key index for display
        
        Args:
            api_key: API key to find
            api_keys: List of all API keys
            
        Returns:
            1-based index of API key
        """
        try:
            return api_keys.index(api_key) + 1
        except ValueError:
            return 0
    
    def _convert_to_connections_dict(self, merged_result: Dict[str, Any]) -> Dict[str, Any]:
        """Convert merged result to connections dictionary
        
        Args:
            merged_result: Merged results
            
        Returns:
            Connections dictionary
        """
        all_connections = {}
        
        for page in merged_result.get("jokbo_pages", []):
            for question in page.get("questions", []):
                q_num = str(question.get("question_number"))
                
                all_connections[q_num] = {
                    "question_number": question.get("question_number"),
                    "question_text": question.get("question_text"),
                    "answer": question.get("answer"),
                    "jokbo_page": page.get("jokbo_page"),
                    "jokbo_end_page": page.get("jokbo_end_page"),
                    "question_numbers_on_page": page.get("question_numbers_on_page", []),
                    "is_last_question_on_page": question.get("is_last_question_on_page", False),
                    "connections": question.get("connections", [])
                }
        
        return all_connections


def process_api_chunks_multiprocess(
    api_key: str,
    model_type: str,
    thinking_budget: Optional[int],
    session_id: str,
    jokbo_display_name: str,
    lesson_chunks: List[tuple],
    result_queue: Queue
):
    """Process chunks with a specific API key in separate process
    
    Args:
        api_key: API key to use
        model_type: Model type
        thinking_budget: Thinking budget
        session_id: Session ID
        jokbo_display_name: Jokbo display name
        lesson_chunks: List of lesson chunks to process
        result_queue: Queue for results
    """
    try:
        import google.generativeai as genai
        
        # Configure API
        genai.configure(api_key=api_key)
        
        # Configure API key first, then create model and processor
        genai.configure(api_key=api_key)
        model = create_model(model_type, thinking_budget)
        processor = PDFProcessor(model, session_id=session_id)
        
        # Re-upload jokbo in this process
        jokbo_files = [f for f in genai.list_files() if f.display_name == jokbo_display_name]
        if not jokbo_files:
            result_queue.put({"error": f"Jokbo file not found: {jokbo_display_name}"})
            return
        
        jokbo_file = jokbo_files[0]
        
        # Process assigned chunks
        for chunk_path, start_page, end_page, lesson_filename, chunk_idx, total_chunks in lesson_chunks:
            try:
                result = processor._analyze_jokbo_with_lesson_chunk_preloaded(
                    jokbo_file, chunk_path, start_page, end_page,
                    lesson_filename, chunk_idx, total_chunks
                )
                
                if result and "error" not in result:
                    # Save chunk result
                    chunk_info = {
                        "file_path": chunk_path,
                        "start_page": start_page,
                        "end_page": end_page,
                        "lesson_filename": lesson_filename
                    }
                    
                    saved_path = processor.save_chunk_result(
                        chunk_info, result, processor.chunk_results_dir
                    )
                    
                    result_queue.put({
                        "success": True,
                        "chunk_info": chunk_info,
                        "saved_path": saved_path
                    })
                else:
                    result_queue.put({
                        "error": result.get("error", "Unknown error"),
                        "chunk_info": {
                            "file_path": chunk_path,
                            "start_page": start_page,
                            "end_page": end_page
                        }
                    })
                    
            except Exception as e:
                result_queue.put({
                    "error": str(e),
                    "chunk_info": {
                        "file_path": chunk_path,
                        "start_page": start_page,
                        "end_page": end_page
                    }
                })
                
    except Exception as e:
        result_queue.put({"error": f"Process error: {str(e)}"})