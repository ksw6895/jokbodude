"""
Centralized file management for PDF processing system
"""
import time
from typing import List, Optional, Any
import google.generativeai as genai
from processing_config import ProcessingConfig


class FileManager:
    """Centralized file management with consistent error handling"""
    
    def __init__(self):
        self.uploaded_files = []
    
    def delete_file_safe(self, file: Any, max_retries: int = ProcessingConfig.MAX_RETRIES) -> bool:
        """Delete a single file with retry logic
        
        Args:
            file: The file object to delete
            max_retries: Maximum number of retry attempts
            
        Returns:
            True if deletion successful, False otherwise
        """
        for attempt in range(max_retries):
            try:
                genai.delete_file(file.name)
                print(f"  Deleted uploaded file: {file.display_name}")
                return True
            except Exception as e:
                error_str = str(e)
                # 403 errors indicate file was uploaded by different API key - don't retry
                if "403" in error_str or "permission" in error_str.lower():
                    print(f"  오류: delete 실패 - 예상치 못한 오류: {file.display_name}")
                    print(f"    세부사항: {error_str}")
                    return False
                elif attempt < max_retries - 1:
                    wait_time = 2 ** attempt  # Exponential backoff
                    print(f"  재시도 {attempt + 1}/{max_retries} after {wait_time}s: {error_str}")
                    time.sleep(wait_time)
                else:
                    print(f"  Failed to delete file {file.display_name} after {max_retries} attempts: {e}")
                    return False
        return False
    
    def cleanup_except_center_file(self, center_file_display_name: str) -> tuple:
        """Clean up all uploaded files except the center file
        
        Args:
            center_file_display_name: Display name of the file to keep
            
        Returns:
            Tuple of (deleted_count, failed_count)
        """
        files = self.list_uploaded_files()
        deleted_count = 0
        failed_count = 0
        
        for file in files:
            # Skip the center file
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
    
    def cleanup_analysis_files(self, files_to_delete: List[Any], 
                             center_file: Optional[Any] = None) -> tuple:
        """Clean up uploaded files with optional center file preservation
        
        Args:
            files_to_delete: List of file objects to delete
            center_file: Optional center file to preserve
            
        Returns:
            Tuple of (deleted_count, failed_count)
        """
        deleted_count = 0
        failed_count = 0
        
        for file in files_to_delete:
            # Skip center file if specified
            if center_file and file.display_name == center_file.display_name:
                print(f"  Keeping center file: {file.display_name}")
                continue
            
            if self.delete_file_safe(file):
                deleted_count += 1
            else:
                failed_count += 1
        
        return deleted_count, failed_count
    
    def delete_all_uploaded_files(self) -> tuple:
        """Delete all uploaded files
        
        Returns:
            Tuple of (deleted_count, failed_count)
        """
        files = self.list_uploaded_files()
        
        if not files:
            print("  No uploaded files to delete")
            return 0, 0
        
        deleted_count = 0
        failed_count = 0
        
        print(f"  Found {len(files)} files to delete")
        for file in files:
            if self.delete_file_safe(file):
                deleted_count += 1
            else:
                failed_count += 1
        
        print(f"  Deleted {deleted_count} files")
        if failed_count > 0:
            print(f"  Failed to delete {failed_count} files")
            
        return deleted_count, failed_count
    
    def list_uploaded_files(self) -> List[Any]:
        """List all uploaded files
        
        Returns:
            List of file objects
        """
        try:
            return list(genai.list_files())
        except Exception as e:
            print(f"  Error listing files: {e}")
            return []
    
    def track_uploaded_file(self, file: Any):
        """Track an uploaded file for later cleanup
        
        Args:
            file: The uploaded file object
        """
        self.uploaded_files.append(file)