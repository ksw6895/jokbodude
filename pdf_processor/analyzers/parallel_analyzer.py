"""Parallel processing functionality for PDF analysis"""

from pathlib import Path
from typing import Dict, Any, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import threading

from pdf_processor.analyzers.jokbo_analyzer import JokboAnalyzer
from pdf_processor.analyzers.lesson_analyzer import LessonAnalyzer
from pdf_processor.parsers.result_merger import ResultMerger
from pdf_processor.utils.file_manager import FileManagerUtil
from pdf_processor.utils.session_manager import SessionManager
from processing_config import ProcessingConfig

try:
    from tqdm import tqdm
    TQDM_AVAILABLE = True
except ImportError:
    TQDM_AVAILABLE = False


class ParallelAnalyzer:
    """Handles parallel processing of PDF analysis tasks"""
    
    def __init__(self, model, session_id: Optional[str] = None):
        """Initialize parallel analyzer
        
        Args:
            model: Gemini model instance
            session_id: Optional session ID for sharing across threads
        """
        self.model = model
        self.session_manager = SessionManager(session_id)
        self.result_merger = ResultMerger()
    
    def analyze_lessons_for_jokbo_parallel(
        self,
        lesson_paths: List[str],
        jokbo_path: str,
        max_workers: int = ProcessingConfig.DEFAULT_THREAD_WORKERS
    ) -> Dict[str, Any]:
        """Analyze lessons in parallel for jokbo-centric mode
        
        Args:
            lesson_paths: List of lesson PDF paths
            jokbo_path: Path to jokbo PDF
            max_workers: Maximum number of parallel workers
            
        Returns:
            Merged analysis results
        """
        print("\n병렬 처리 모드 - 족보 중심 분석")
        print(f"족보 파일: {Path(jokbo_path).name}")
        print(f"분석할 강의자료: {len(lesson_paths)}개")
        print(f"병렬 작업자 수: {max_workers}")
        
        # Main file manager for jokbo upload
        main_file_manager = FileManagerUtil()
        
        # Upload jokbo once
        print("\n족보 파일 업로드 중...")
        main_file_manager.delete_all_uploaded_files()
        jokbo_file = main_file_manager.upload_pdf(
            jokbo_path, f"족보_{Path(jokbo_path).name}"
        )
        
        # Process lessons in parallel
        all_results = []
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all tasks
            futures = {}
            
            for idx, lesson_path in enumerate(lesson_paths):
                future = executor.submit(
                    self._analyze_lesson_worker,
                    lesson_path,
                    jokbo_file,
                    idx,
                    len(lesson_paths)
                )
                futures[future] = lesson_path
            
            # Process results with progress bar
            if TQDM_AVAILABLE:
                progress_bar = tqdm(
                    total=len(lesson_paths),
                    desc="강의자료 분석 진행률"
                )
            
            for future in as_completed(futures):
                lesson_path = futures[future]
                
                try:
                    result = future.result()
                    if result and "error" not in result:
                        all_results.append(result)
                        
                        # Save intermediate result
                        self._save_lesson_result(
                            lesson_path, result
                        )
                except Exception as e:
                    print(f"\n오류 - {Path(lesson_path).name}: {str(e)}")
                
                if TQDM_AVAILABLE:
                    progress_bar.update(1)
            
            if TQDM_AVAILABLE:
                progress_bar.close()
        
        # Clean up jokbo file
        main_file_manager.delete_file_safe(jokbo_file)
        
        # Merge all results
        if not all_results:
            return {"jokbo_pages": []}
        
        merged_result = self.result_merger.merge_jokbo_centric_results(all_results)
        
        # Apply final filtering
        all_connections = self._convert_to_connections_dict(merged_result)
        final_result = self.result_merger.apply_final_filtering_and_sorting(all_connections)
        
        print(f"\n병렬 처리 완료!")
        return final_result
    
    def analyze_pdfs_for_lesson_parallel(
        self,
        jokbo_paths: List[str],
        lesson_path: str,
        max_workers: int = ProcessingConfig.DEFAULT_THREAD_WORKERS
    ) -> Dict[str, Any]:
        """Analyze jokbos in parallel for lesson-centric mode
        
        Args:
            jokbo_paths: List of jokbo PDF paths
            lesson_path: Path to lesson PDF
            max_workers: Maximum number of parallel workers
            
        Returns:
            Merged analysis results
        """
        print("\n병렬 처리 모드 - 강의자료 중심 분석")
        print(f"강의자료: {Path(lesson_path).name}")
        print(f"분석할 족보: {len(jokbo_paths)}개")
        print(f"병렬 작업자 수: {max_workers}")
        
        # Main file manager for lesson upload
        main_file_manager = FileManagerUtil()
        
        # Upload lesson once
        print("\n강의자료 파일 업로드 중...")
        main_file_manager.delete_all_uploaded_files()
        lesson_file = main_file_manager.upload_pdf(
            lesson_path, f"강의자료_{Path(lesson_path).name}"
        )
        
        # Process jokbos in parallel
        all_related_slides = {}
        total_questions = 0
        all_key_topics = set()
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all tasks
            futures = {}
            
            for idx, jokbo_path in enumerate(jokbo_paths):
                future = executor.submit(
                    self._analyze_jokbo_worker,
                    jokbo_path,
                    lesson_file,
                    idx,
                    len(jokbo_paths)
                )
                futures[future] = jokbo_path
            
            # Process results with progress bar
            if TQDM_AVAILABLE:
                progress_bar = tqdm(
                    total=len(jokbo_paths),
                    desc="족보 분석 진행률"
                )
            
            for future in as_completed(futures):
                jokbo_path = futures[future]
                
                try:
                    result = future.result()
                    if result and "error" not in result:
                        # Merge results
                        self._merge_lesson_centric_results(
                            result,
                            all_related_slides,
                            all_key_topics
                        )
                        
                        if "summary" in result:
                            total_questions += result["summary"].get("total_questions", 0)
                            
                except Exception as e:
                    print(f"\n오류 - {Path(jokbo_path).name}: {str(e)}")
                
                if TQDM_AVAILABLE:
                    progress_bar.update(1)
            
            if TQDM_AVAILABLE:
                progress_bar.close()
        
        # Clean up lesson file
        main_file_manager.delete_file_safe(lesson_file)
        
        # Prepare final result
        final_slides = []
        for slide_data in all_related_slides.values():
            slide_data["key_concepts"] = list(slide_data.get("key_concepts", set()))
            final_slides.append(slide_data)
        
        final_slides.sort(key=lambda x: x["lesson_page"])
        
        print(f"\n병렬 처리 완료!")
        
        return {
            "related_slides": final_slides,
            "summary": {
                "total_related_slides": len(final_slides),
                "total_questions": total_questions,
                "key_topics": list(all_key_topics),
                "study_recommendations": "각 슬라이드별로 관련된 족보 문제들을 중점적으로 학습하세요."
            }
        }
    
    def _analyze_lesson_worker(
        self,
        lesson_path: str,
        jokbo_file,
        idx: int,
        total: int
    ) -> Dict[str, Any]:
        """Worker function for analyzing a single lesson
        
        Args:
            lesson_path: Path to lesson PDF
            jokbo_file: Pre-uploaded jokbo file
            idx: Index of current task
            total: Total number of tasks
            
        Returns:
            Analysis results
        """
        print(f"\n[{idx+1}/{total}] 시작: {Path(lesson_path).name} "
              f"(Thread-{threading.current_thread().ident})")
        
        # Create thread-local analyzer with shared session
        thread_file_manager = FileManagerUtil()
        thread_analyzer = JokboAnalyzer(
            self.model,
            thread_file_manager
        )
        
        try:
            result = thread_analyzer.analyze_single_lesson_with_jokbo_preloaded(
                lesson_path,
                jokbo_file
            )
            
            print(f"[{idx+1}/{total}] 완료: {Path(lesson_path).name}")
            return result
            
        except Exception as e:
            print(f"[{idx+1}/{total}] 오류: {Path(lesson_path).name} - {str(e)}")
            return {"error": str(e)}
    
    def _analyze_jokbo_worker(
        self,
        jokbo_path: str,
        lesson_file,
        idx: int,
        total: int
    ) -> Dict[str, Any]:
        """Worker function for analyzing a single jokbo
        
        Args:
            jokbo_path: Path to jokbo PDF
            lesson_file: Pre-uploaded lesson file
            idx: Index of current task
            total: Total number of tasks
            
        Returns:
            Analysis results
        """
        print(f"\n[{idx+1}/{total}] 시작: {Path(jokbo_path).name} "
              f"(Thread-{threading.current_thread().ident})")
        
        # Create thread-local analyzer
        thread_file_manager = FileManagerUtil()
        thread_analyzer = LessonAnalyzer(
            self.model,
            thread_file_manager
        )
        
        try:
            result = thread_analyzer.analyze_single_jokbo_with_lesson_preloaded(
                jokbo_path,
                lesson_file
            )
            
            print(f"[{idx+1}/{total}] 완료: {Path(jokbo_path).name}")
            return result
            
        except Exception as e:
            print(f"[{idx+1}/{total}] 오류: {Path(jokbo_path).name} - {str(e)}")
            return {"error": str(e)}
    
    def _save_lesson_result(self, lesson_path: str, result: Dict[str, Any]):
        """Save intermediate lesson result to session directory
        
        Args:
            lesson_path: Path to lesson file
            result: Analysis result
        """
        from pdf_processor.api.response_handler import ResponseHandler
        handler = ResponseHandler()
        
        lesson_idx = hash(lesson_path) % 1000  # Simple index
        handler.save_lesson_result(
            lesson_idx,
            lesson_path,
            result,
            self.session_manager.chunk_results_dir
        )
    
    def _convert_to_connections_dict(self, merged_result: Dict[str, Any]) -> Dict[str, Any]:
        """Convert merged result to connections dictionary format
        
        Args:
            merged_result: Merged jokbo-centric results
            
        Returns:
            Dictionary keyed by question number
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
    
    def _merge_lesson_centric_results(
        self,
        result: Dict[str, Any],
        all_related_slides: Dict,
        all_key_topics: set
    ):
        """Merge lesson-centric results
        
        Args:
            result: Single analysis result
            all_related_slides: Accumulated slides
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
            
            all_related_slides[lesson_page]["related_jokbo_questions"].extend(
                slide.get("related_jokbo_questions", [])
            )
            
            all_related_slides[lesson_page]["importance_score"] = max(
                all_related_slides[lesson_page]["importance_score"],
                slide.get("importance_score", 5)
            )
            
            all_related_slides[lesson_page]["key_concepts"].update(
                slide.get("key_concepts", [])
            )
        
        if "summary" in result:
            all_key_topics.update(result["summary"].get("key_topics", []))