"""
Enhanced Multi-API processor with chunk redistribution support

This module provides improved multi-API processing with intelligent chunk
redistribution when APIs fail, ensuring no chunks are lost.
"""

from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import json

from pdf_processor.core.processor import PDFProcessor
from pdf_processor.utils.chunk_distributor import ChunkDistributor, ChunkTask
from pdf_processor.utils.failed_chunk_retrier import FailedChunkRetrier
from pdf_processor.pdf.pdf_splitter import PDFSplitter
from pdf_processor.parsers.result_merger import ResultMerger
from processing_config import ProcessingConfig
from config import create_model
from api_key_manager import APIKeyManager


class EnhancedMultiAPIProcessor:
    """Enhanced multi-API processor with chunk redistribution"""
    
    def __init__(self, api_keys: List[str], model_type: str = "pro", thinking_budget: Optional[int] = None):
        """
        Initialize enhanced multi-API processor
        
        Args:
            api_keys: List of API keys
            model_type: Model type to use
            thinking_budget: Thinking budget for flash models
        """
        # Initialize API manager with configuration
        self.api_manager = APIKeyManager(api_keys, model_type, thinking_budget)
        self.model_type = model_type
        self.thinking_budget = thinking_budget
        
        # Initialize components
        self.chunk_distributor = ChunkDistributor(self.api_manager)
        self.failed_retrier = FailedChunkRetrier(self.api_manager, self.chunk_distributor)
        self.pdf_splitter = PDFSplitter()
        self.result_merger = ResultMerger()
        
        # Track API-specific processors and uploads
        self.api_processors: Dict[str, PDFProcessor] = {}
        self.api_uploads: Dict[str, Dict[str, Any]] = {}
        self.lock = threading.Lock()
        
        # Main session for shared resources
        self.main_session_id = None
        self.jokbo_path = None  # Store jokbo path instead of file
        self.api_jokbo_files: Dict[str, Any] = {}  # Each API will have its own jokbo upload
    
    def analyze_lessons_for_jokbo_with_redistribution(
        self,
        lesson_paths: List[str],
        jokbo_path: str,
        max_workers: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Analyze lessons with chunk redistribution on failure
        
        Args:
            lesson_paths: List of lesson PDF paths
            jokbo_path: Path to jokbo PDF
            max_workers: Maximum concurrent workers
            
        Returns:
            Merged analysis results
        """
        print("\n=== Enhanced Multi-API 모드 시작 ===")
        print(f"사용 가능한 API 키: {len(self.api_manager.api_keys)}개")
        print(f"분석할 강의자료: {len(lesson_paths)}개")
        
        # Auto-set max_workers to number of API keys if not specified
        if max_workers is None:
            max_workers = len(self.api_manager.api_keys)
        
        print(f"동시 작업자: {max_workers}개 (API 키 개수와 동일)")
        
        # Store jokbo path for later use by each API
        self.jokbo_path = jokbo_path
        
        # Just create a session ID (no processor needed)
        from datetime import datetime
        import random
        import string
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        random_suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
        self.main_session_id = f"{timestamp}_{random_suffix}"
        print(f"세션 ID: {self.main_session_id}")
        
        # Get API keys to use
        api_keys_to_use = list(self.api_manager.api_keys)[:max_workers]
        
        print("\n모든 강의자료를 작업 큐에 추가 중...")
        
        # Build work queue with all lessons and chunks
        work_queue = []
        total_chunks = 0
        
        for lesson_path in lesson_paths:
            lesson_name = Path(lesson_path).name
            lesson_chunks = self.pdf_splitter.split_pdf_for_analysis(lesson_path)
            
            for chunk_idx, chunk_info in enumerate(lesson_chunks):
                work_item = {
                    'lesson_path': lesson_path,
                    'lesson_name': lesson_name,
                    'chunk_info': chunk_info,
                    'chunk_idx': chunk_idx,
                    'total_chunks': len(lesson_chunks),
                    'is_single': len(lesson_chunks) == 1
                }
                work_queue.append(work_item)
                total_chunks += 1
        
        print(f"총 {total_chunks}개 작업 항목 생성 완료")
        print(f"\n병렬 처리 시작 (워커: {max_workers}개)...")
        
        # Process all work items in parallel with pre-assigned API keys
        all_results = self._process_work_queue_parallel(
            work_queue, 
            max_workers,
            api_keys_to_use
        )
        
        # Clean up uploads
        self._cleanup_all_uploads()
        
        # Report final statistics
        self._report_statistics()
        
        # Merge all results by lesson first, then combine
        lesson_results = {}
        for result in all_results:
            if result and 'lesson_path' in result:
                lesson_path = result['lesson_path']
                if lesson_path not in lesson_results:
                    lesson_results[lesson_path] = []
                lesson_results[lesson_path].append(result['data'])
        
        # Merge chunks for each lesson
        merged_lessons = []
        for lesson_path, chunks in lesson_results.items():
            if len(chunks) > 1:
                merged = self.result_merger.merge_jokbo_centric_results(chunks)
            else:
                merged = chunks[0] if chunks else {"jokbo_pages": []}
            merged_lessons.append(merged)
        
        # Merge all lessons
        if not merged_lessons:
            return {"jokbo_pages": []}
        
        final_merged = self.result_merger.merge_jokbo_centric_results(merged_lessons)
        
        # Apply final filtering (create temporary processor for this)
        import google.generativeai as genai
        genai.configure(api_key=api_keys_to_use[0])
        temp_model = create_model(self.model_type, self.thinking_budget)
        temp_processor = PDFProcessor(temp_model, session_id=self.main_session_id)
        
        all_connections = self._convert_to_connections_dict(final_merged)
        final_result = temp_processor.apply_final_filtering_and_sorting(all_connections)
        
        return final_result
    
    def _process_work_queue_parallel(
        self,
        work_queue: List[Dict[str, Any]],
        max_workers: int,
        api_keys_to_use: List[str]
    ) -> List[Dict[str, Any]]:
        """
        Process all work items in parallel using multiple APIs
        
        Args:
            work_queue: List of work items to process
            max_workers: Number of concurrent workers
            
        Returns:
            List of successful results with metadata
        """
        from queue import Queue
        import queue
        
        # Create thread-safe work queue
        work_items = Queue()
        for item in work_queue:
            work_items.put(item)
        
        # Results collection
        results = []
        results_lock = threading.Lock()
        
        # Progress tracking
        completed = 0
        failed = 0
        progress_lock = threading.Lock()
        
        def worker_thread(worker_id: int, assigned_api_key: str):
            """Worker thread that processes items from queue with its own API"""
            nonlocal completed, failed
            
            # Each worker configures its own API key
            import google.generativeai as genai
            genai.configure(api_key=assigned_api_key)
            
            # Create this worker's own processor
            model = create_model(self.model_type, self.thinking_budget)
            processor = PDFProcessor(model, session_id=self.main_session_id)
            
            # Upload jokbo for this worker
            print(f"  워커 #{worker_id} 족보 업로드 중 (API ...{assigned_api_key[-4:]})...")
            jokbo_file = processor.upload_pdf(
                self.jokbo_path, f"족보_{Path(self.jokbo_path).name}"
            )
            print(f"  워커 #{worker_id} 준비 완료")
            
            while True:
                try:
                    # Get work item (non-blocking with timeout)
                    try:
                        work_item = work_items.get(timeout=2)
                    except queue.Empty:
                        # No more work
                        break
                    
                    # Process with this worker's processor and jokbo
                    result = self._process_work_item_with_processor(
                        work_item, processor, jokbo_file, worker_id, assigned_api_key
                    )
                    
                    if result:
                        with results_lock:
                            results.append({
                                'lesson_path': work_item['lesson_path'],
                                'chunk_idx': work_item['chunk_idx'],
                                'data': result
                            })
                        with progress_lock:
                            completed += 1
                    else:
                        with progress_lock:
                            failed += 1
                    
                    # Update progress
                    with progress_lock:
                        total = len(work_queue)
                        progress_pct = (completed + failed) / total * 100
                        print(f"  📊 진행률: {completed + failed}/{total} "
                              f"({progress_pct:.1f}%) - "
                              f"성공: {completed}, 실패: {failed}, "
                              f"워커 #{worker_id}")
                    
                    work_items.task_done()
                    
                except Exception as e:
                    print(f"  ⚠️ Worker #{worker_id} exception: {e}")
                    with progress_lock:
                        failed += 1
        
        # Start worker threads with assigned API keys
        print(f"\n각 워커가 독립적으로 API 설정 및 족보 업로드...")
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = []
            for i, api_key in enumerate(api_keys_to_use):
                future = executor.submit(worker_thread, i + 1, api_key)
                futures.append(future)
            
            # Wait for all workers to complete
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    print(f"  ⚠️ Worker thread failed: {e}")
        
        print(f"\n✅ 병렬 처리 완료: 성공 {completed}개, 실패 {failed}개")
        return results
    
    def _process_work_item_with_processor(
        self,
        work_item: Dict[str, Any],
        processor: PDFProcessor,
        jokbo_file: Any,
        worker_id: int,
        api_key: str
    ) -> Optional[Dict[str, Any]]:
        """
        Process a work item with the worker's own processor and jokbo
        
        Args:
            work_item: Work item containing lesson and chunk info
            processor: This worker's PDFProcessor
            jokbo_file: This worker's uploaded jokbo file
            worker_id: Worker ID for logging
            api_key: This worker's API key
            
        Returns:
            Processing result or None
        """
        try:
            if work_item['is_single']:
                # Single file processing
                result = processor.analyze_single_lesson_with_jokbo_preloaded(
                    work_item['lesson_path'], jokbo_file
                )
            else:
                # Chunk processing
                chunk_path, start_page, end_page = work_item['chunk_info']
                
                # Upload chunk
                chunk_id = f"{Path(work_item['lesson_path']).stem}_chunk{work_item['chunk_idx']}"
                chunk_file = processor.upload_pdf(chunk_path, chunk_id)
                
                try:
                    # Analyze chunk
                    result = processor._analyze_jokbo_with_lesson_chunk_preloaded(
                        jokbo_file, chunk_path,
                        start_page, end_page,
                        work_item['lesson_name'],
                        work_item['chunk_idx'],
                        work_item['total_chunks']
                    )
                finally:
                    # Clean up chunk
                    processor.delete_file_safe(chunk_file)
            
            if result and "error" not in result:
                self.api_manager.mark_success(api_key)
                
                # Save chunk result to session folder
                if not work_item['is_single'] and hasattr(processor, 'save_chunk_result'):
                    chunk_info = {
                        'lesson_filename': work_item['lesson_name'],
                        'chunk_idx': work_item['chunk_idx'],
                        'total_chunks': work_item['total_chunks'],
                        'start_page': work_item['chunk_info'][1],
                        'end_page': work_item['chunk_info'][2]
                    }
                    temp_dir = Path(f"output/temp/sessions/{self.main_session_id}/chunk_results")
                    temp_dir.mkdir(parents=True, exist_ok=True)
                    processor.save_chunk_result(chunk_info, result, str(temp_dir))
                
                return result
            else:
                error = result.get("error", "Unknown error") if result else "No result"
                is_empty = len(str(result)) == 0 if result else True
                self.api_manager.mark_failure(api_key, is_empty_response=is_empty)
                print(f"    ❌ 실패 워커#{worker_id} ({work_item['lesson_name']}): {error}")
                return None
                
        except Exception as e:
            self.api_manager.mark_failure(api_key)
            print(f"    ❌ 오류 워커#{worker_id} ({work_item['lesson_name']}): {str(e)}")
            return None
    
    
    def _track_upload(self, api_key: str, upload_id: str, file_ref: Any):
        """
        Track file upload for an API
        
        🔴 IMPORTANT: API FILE ISOLATION
        - api_uploads[api_key_1] = Files uploaded by API #1 (only API #1 can access)
        - api_uploads[api_key_2] = Files uploaded by API #2 (only API #2 can access)
        - Cross-API file access will cause 403 Forbidden errors!
        """
        with self.lock:
            if api_key not in self.api_uploads:
                self.api_uploads[api_key] = {}
            self.api_uploads[api_key][upload_id] = file_ref
    
    def _untrack_upload(self, api_key: str, upload_id: str):
        """Remove upload tracking"""
        with self.lock:
            if api_key in self.api_uploads and upload_id in self.api_uploads[api_key]:
                del self.api_uploads[api_key][upload_id]
    
    def _cleanup_all_uploads(self):
        """Clean up all tracked uploads"""
        # Cleanup is now handled by each worker's processor destructor
        print("\n정리 완료 (각 워커가 자체 정리)")
        self.api_uploads.clear()
    
    def _report_statistics(self):
        """Report processing statistics"""
        print("\n=== 처리 통계 ===")
        
        # Chunk distributor stats
        progress = self.chunk_distributor.get_progress()
        print(f"청크 처리:")
        print(f"  - 총 청크: {progress['total']}")
        print(f"  - 완료: {progress['completed']}")
        print(f"  - 실패: {progress['failed']}")
        print(f"  - 재시도: {progress['retried']}")
        print(f"  - 재분배: {progress['redistributed']}")
        
        # Retry stats
        retry_stats = self.failed_retrier.get_retry_statistics()
        if retry_stats['total_retry_attempts'] > 0:
            print(f"\n재시도 통계:")
            print(f"  - 재시도된 청크: {retry_stats['total_retried_chunks']}")
            print(f"  - 총 재시도 횟수: {retry_stats['total_retry_attempts']}")
            print(f"  - 다중 재시도 청크: {retry_stats['chunks_with_multiple_retries']}")
            
            if retry_stats['failure_types']:
                print(f"  - 실패 유형:")
                for failure_type, count in retry_stats['failure_types'].items():
                    print(f"    • {failure_type}: {count}")
        
        # API stats
        api_status = self.api_manager.get_status()
        print(f"\nAPI 상태:")
        for api_id, status in api_status.items():
            print(f"  - API {api_id}:")
            print(f"    • 사용 횟수: {status['usage_count']}")
            print(f"    • 연속 실패: {status['consecutive_failures']}")
            print(f"    • 총 실패: {status['total_failures']}")
            if status['cooldown_remaining']:
                print(f"    • 쿨다운 남음: {status['cooldown_remaining']}")
    
    def _convert_to_connections_dict(self, merged_result: Dict[str, Any]) -> Dict[str, Any]:
        """Convert merged result to connections dictionary"""
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