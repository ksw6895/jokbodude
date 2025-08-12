"""Jokbo-centric analysis functionality"""

from pathlib import Path
from typing import Dict, Any, List, Optional
import pymupdf as fitz
import json
from datetime import datetime
import threading

from prompt_builder import PromptBuilder
from validators import PDFValidator
from pdf_processor.api.gemini_client import GeminiClient
from pdf_processor.parsers.json_parser import JSONParser
from pdf_processor.parsers.result_merger import ResultMerger
from pdf_processor.utils.file_manager import FileManagerUtil
from pdf_processor.pdf.pdf_splitter import PDFSplitter
from processing_config import ProcessingConfig


class JokboAnalyzer:
    """Handles jokbo-centric PDF analysis"""
    
    def __init__(self, model, file_manager: FileManagerUtil):
        """Initialize jokbo analyzer
        
        Args:
            model: Gemini model instance
            file_manager: File manager for uploads
        """
        self.model = model
        self.file_manager = file_manager
        self.gemini_client = GeminiClient(model)
        self.json_parser = JSONParser()
        self.result_merger = ResultMerger()
        self.pdf_splitter = PDFSplitter()
    
    def analyze_lessons_for_jokbo(
        self, 
        lesson_paths: List[str], 
        jokbo_path: str
    ) -> Dict[str, Any]:
        """Analyze multiple lesson PDFs against one jokbo PDF
        
        Args:
            lesson_paths: List of lesson PDF paths
            jokbo_path: Path to jokbo PDF
            
        Returns:
            Analysis results dictionary
        """
        print("\n족보 중심 모드 - 각 족보 문제에 대한 관련 강의자료 분석")
        print(f"족보 파일: {Path(jokbo_path).name}")
        print(f"분석할 강의자료: {len(lesson_paths)}개")
        
        # Delete existing files
        print("  기존 업로드 파일 정리 중...")
        self.file_manager.delete_all_uploaded_files()
        
        # Upload jokbo once
        jokbo_file = self.file_manager.upload_pdf(
            jokbo_path, f"족보_{Path(jokbo_path).name}"
        )
        
        all_results = []
        
        # Process each lesson
        for idx, lesson_path in enumerate(lesson_paths):
            print(f"\n[{idx+1}/{len(lesson_paths)}] 분석 중: {Path(lesson_path).name}")
            
            try:
                result = self.analyze_single_lesson_with_jokbo_preloaded(
                    lesson_path, jokbo_file
                )
                
                if "error" not in result:
                    all_results.append(result)
                else:
                    print(f"    오류 발생: {result['error']}")
                    
            except Exception as e:
                print(f"    처리 중 오류: {str(e)}")
                continue
        
        # Clean up
        self.file_manager.delete_file_safe(jokbo_file)
        
        # Merge results
        if not all_results:
            return {"jokbo_pages": []}
        
        return self.result_merger.merge_jokbo_centric_results(all_results)
    
    def analyze_single_lesson_with_jokbo_preloaded(
        self,
        lesson_path: str,
        jokbo_file
    ) -> Dict[str, Any]:
        """Analyze one lesson against pre-uploaded jokbo
        
        Args:
            lesson_path: Path to lesson PDF
            jokbo_file: Pre-uploaded jokbo file object
            
        Returns:
            Analysis results
        """
        lesson_filename = Path(lesson_path).name
        
        # Check if lesson needs chunking
        lesson_chunks = self.pdf_splitter.split_pdf_for_analysis(lesson_path)
        
        if len(lesson_chunks) == 1:
            # Small file, process normally
            return self._analyze_single_chunk(
                lesson_path, jokbo_file, lesson_filename
            )
        
        # Large file, process in chunks
        print(f"  큰 파일 감지: {len(lesson_chunks)}개 청크로 분할")
        chunk_results = []
        
        for chunk_idx, (chunk_path, start_page, end_page) in enumerate(lesson_chunks):
            print(f"  청크 {chunk_idx+1}/{len(lesson_chunks)} 처리 중 "
                  f"(페이지 {start_page}-{end_page})")
            
            try:
                result = self._analyze_jokbo_with_lesson_chunk_preloaded(
                    jokbo_file, chunk_path, start_page, end_page,
                    lesson_filename, chunk_idx, len(lesson_chunks)
                )
                
                if "error" not in result:
                    chunk_results.append(result)
                    
            except Exception as e:
                print(f"    청크 처리 오류: {str(e)}")
                continue
            finally:
                # Clean up chunk file if it's temporary
                if chunk_path != lesson_path:
                    import os
                    try:
                        os.remove(chunk_path)
                    except:
                        pass
        
        # Merge chunk results
        if chunk_results:
            return self.result_merger.merge_jokbo_centric_results(chunk_results)
        else:
            return {"error": "All chunks failed to process"}
    
    def _analyze_single_chunk(
        self,
        lesson_path: str,
        jokbo_file,
        lesson_filename: str
    ) -> Dict[str, Any]:
        """Analyze a single lesson chunk against jokbo
        
        Args:
            lesson_path: Path to lesson/chunk PDF
            jokbo_file: Pre-uploaded jokbo file
            lesson_filename: Original lesson filename
            
        Returns:
            Analysis results
        """
        # Upload lesson
        lesson_file = self.file_manager.upload_pdf(
            lesson_path, f"강의자료_{lesson_filename}"
        )
        
        # Build prompt
        prompt = PromptBuilder.build_jokbo_centric_prompt(
            Path(jokbo_file.display_name).name.replace("족보_", ""),
            lesson_filename
        )
        
        # Generate content
        content = [prompt, jokbo_file, lesson_file]
        
        try:
            response = self.gemini_client.generate_content_with_retry(content)
            
            # Parse response
            result = self.json_parser.parse_response_json(
                response.text, "jokbo-centric"
            )
            
            return result
            
        except Exception as e:
            print(f"  분석 오류: {str(e)}")
            return {"error": str(e)}
        finally:
            # Clean up lesson file
            self.file_manager.delete_file_safe(lesson_file)
    
    def _analyze_jokbo_with_lesson_chunk_preloaded(
        self,
        jokbo_file,
        lesson_chunk_path: str,
        start_page: int,
        end_page: int,
        lesson_filename: str,
        chunk_idx: int,
        total_chunks: int
    ) -> Dict[str, Any]:
        """Analyze jokbo with a lesson chunk
        
        Args:
            jokbo_file: Pre-uploaded jokbo file
            lesson_chunk_path: Path to lesson chunk
            start_page: Start page of chunk
            end_page: End page of chunk
            lesson_filename: Original lesson filename
            chunk_idx: Index of current chunk
            total_chunks: Total number of chunks
            
        Returns:
            Analysis results with adjusted page numbers
        """
        # Upload chunk
        chunk_name = f"강의자료_{lesson_filename}_청크{chunk_idx+1}"
        lesson_chunk_file = self.file_manager.upload_pdf(
            lesson_chunk_path, chunk_name
        )
        
        # Build prompt with chunk info
        prompt = PromptBuilder.build_jokbo_centric_prompt(
            Path(jokbo_file.display_name).name.replace("족보_", ""),
            lesson_filename,
            chunk_info={
                "current_chunk": chunk_idx + 1,
                "total_chunks": total_chunks,
                "start_page": start_page,
                "end_page": end_page
            }
        )
        
        # Generate content
        content = [prompt, jokbo_file, lesson_chunk_file]
        
        try:
            response = self.gemini_client.generate_content_with_retry(content)
            result = self.json_parser.parse_response_json(
                response.text, "jokbo-centric"
            )
            
            # Adjust page numbers for chunk
            if "jokbo_pages" in result:
                for page in result["jokbo_pages"]:
                    for question in page.get("questions", []):
                        for connection in question.get("connections", []):
                            if "lesson_page" in connection:
                                # Adjust from chunk-relative to absolute
                                connection["lesson_page"] += (start_page - 1)
            
            return result
            
        except Exception as e:
            print(f"  청크 분석 오류: {str(e)}")
            return {"error": str(e)}
        finally:
            # Clean up chunk file
            self.file_manager.delete_file_safe(lesson_chunk_file)