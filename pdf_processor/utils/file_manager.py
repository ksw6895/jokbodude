"""File management utilities for PDF processing"""

from pathlib import Path
from typing import Optional, List
import google.generativeai as genai


class FileManagerUtil:
    """Manages file uploads and cleanup for Gemini API"""
    
    def __init__(self):
        """Initialize file manager"""
        self.uploaded_files = []
        self.debug_dir = Path("output/debug")
        self.debug_dir.mkdir(parents=True, exist_ok=True)
    
    def upload_pdf(self, pdf_path: str, display_name: Optional[str] = None):
        """Upload PDF file to Gemini API
        
        Args:
            pdf_path: Path to PDF file
            display_name: Optional display name for the file
            
        Returns:
            Uploaded file object from Gemini API
        """
        try:
            if display_name is None:
                display_name = Path(pdf_path).name
                
            print(f"파일 업로드 중: {display_name}")
            uploaded_file = genai.upload_file(pdf_path, display_name=display_name)
            
            # 파일 처리 대기
            import time
            while uploaded_file.state.name == "PROCESSING":
                time.sleep(1)
                uploaded_file = genai.get_file(uploaded_file.name)
            
            if uploaded_file.state.name == "FAILED":
                raise ValueError(f"파일 업로드 실패: {display_name}")
            
            self.uploaded_files.append(uploaded_file)
            print(f"파일 업로드 완료: {display_name}")
            return uploaded_file
            
        except Exception as e:
            from error_handler import ErrorHandler
            ErrorHandler.handle_file_operation_error(e, pdf_path, "upload")
            raise
    
    def list_uploaded_files(self):
        """List all uploaded files in Gemini"""
        for file in genai.list_files():
            print(f"  {file.display_name}, URI: {file.uri}")
    
    def delete_all_uploaded_files(self):
        """Delete all uploaded files from Gemini"""
        for file in self.uploaded_files:
            self.delete_file_safe(file)
        self.uploaded_files.clear()
    
    def delete_file_safe(self, file):
        """Safely delete a file from Gemini
        
        Args:
            file: File object to delete
        """
        try:
            genai.delete_file(file.name)
            print(f"  파일 삭제됨: {file.display_name}")
        except Exception as e:
            print(f"  파일 삭제 실패 (이미 삭제됨): {file.display_name}")
    
    def cleanup_except_center_file(self, center_file_display_name: str):
        """Clean up all files except the center file
        
        Args:
            center_file_display_name: Display name of file to keep
        """
        for file in self.uploaded_files:
            if file.display_name != center_file_display_name:
                self.delete_file_safe(file)
    
    def __del__(self):
        """Clean up uploaded files when object is destroyed"""
        for file in self.uploaded_files:
            self.delete_file_safe(file)