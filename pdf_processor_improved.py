"""
개선된 PDF 처리기 - 파일 관리 기능 강화
"""

import json
from pathlib import Path
import pymupdf as fitz
from typing import List, Dict, Any, Tuple, TYPE_CHECKING
import google.generativeai as genai
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
from datetime import datetime
import atexit

if TYPE_CHECKING:
    from google.generativeai.types import file_types

class ImprovedPDFProcessor:
    # 클래스 변수로 모든 업로드된 파일 추적
    _all_uploaded_files = []
    _cleanup_registered = False
    
    def __init__(self, model):
        self.model = model
        self.uploaded_files = []
        
        # 프로그램 종료 시 정리 함수 등록 (한 번만)
        if not ImprovedPDFProcessor._cleanup_registered:
            atexit.register(ImprovedPDFProcessor.cleanup_all_files)
            ImprovedPDFProcessor._cleanup_registered = True
    
    @classmethod
    def cleanup_all_files(cls):
        """프로그램 종료 시 모든 업로드된 파일 정리"""
        if cls._all_uploaded_files:
            print(f"\n[정리] {len(cls._all_uploaded_files)}개의 업로드된 파일을 삭제합니다...")
            for file in cls._all_uploaded_files:
                try:
                    genai.delete_file(file.name)
                    print(f"  ✓ 삭제됨: {file.display_name}")
                except Exception as e:
                    print(f"  ✗ 삭제 실패: {file.display_name} - {e}")
            cls._all_uploaded_files.clear()
    
    def upload_pdf_with_retry(self, pdf_path: str, display_name: str = None, max_retries: int = 3):
        """재시도 로직이 포함된 PDF 업로드"""
        if display_name is None:
            display_name = Path(pdf_path).name
        
        for attempt in range(max_retries):
            try:
                # 이미 업로드된 동일한 파일이 있는지 확인
                existing_files = list(genai.list_files())
                for file in existing_files:
                    if file.display_name == display_name and file.state.name == "ACTIVE":
                        print(f"  [{datetime.now().strftime('%H:%M:%S')}] 기존 파일 재사용: {display_name}")
                        return file
                
                # 새로 업로드
                uploaded_file = genai.upload_file(
                    path=pdf_path,
                    display_name=display_name,
                    mime_type="application/pdf"
                )
                
                # 처리 대기
                wait_time = 0
                while uploaded_file.state.name == "PROCESSING" and wait_time < 30:
                    time.sleep(2)
                    wait_time += 2
                    uploaded_file = genai.get_file(uploaded_file.name)
                
                if uploaded_file.state.name == "FAILED":
                    raise ValueError(f"파일 처리 실패: {display_name}")
                
                print(f"  [{datetime.now().strftime('%H:%M:%S')}] 업로드 성공: {display_name}")
                
                # 전역 리스트에 추가
                ImprovedPDFProcessor._all_uploaded_files.append(uploaded_file)
                self.uploaded_files.append(uploaded_file)
                
                return uploaded_file
                
            except Exception as e:
                print(f"  업로드 시도 {attempt + 1}/{max_retries} 실패: {e}")
                if attempt < max_retries - 1:
                    time.sleep(3)  # 재시도 전 대기
                else:
                    raise
    
    def clear_old_files(self, hours: int = 24):
        """오래된 파일 자동 정리"""
        try:
            from datetime import datetime, timedelta
            
            current_time = datetime.now()
            cutoff_time = current_time - timedelta(hours=hours)
            
            files = list(genai.list_files())
            deleted_count = 0
            
            for file in files:
                # 파일 생성 시간 확인 (API에서 제공하는 경우)
                if hasattr(file, 'create_time'):
                    # 오래된 파일 삭제
                    try:
                        genai.delete_file(file.name)
                        deleted_count += 1
                    except:
                        pass
            
            if deleted_count > 0:
                print(f"  {deleted_count}개의 오래된 파일을 정리했습니다.")
                
        except Exception as e:
            print(f"  파일 정리 중 오류: {e}")
    
    # 기존 analyze 메서드들은 upload_pdf 대신 upload_pdf_with_retry 사용하도록 수정
    # ... (나머지 코드는 upload_pdf를 upload_pdf_with_retry로 변경)