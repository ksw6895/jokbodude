"""
File management module for handling uploaded files in Gemini API.
Provides centralized file operations with proper cleanup and tracking.
"""

import time
from typing import List, Optional, Set, Any
from datetime import datetime
import google.generativeai as genai

from ..utils.logging import get_logger

logger = get_logger(__name__)


class FileManager:
    """Manages file uploads and cleanup for Gemini API.

    In multi-API mode, this manager should be scoped to a single API key.
    Pass a bound GeminiAPIClient so all list/delete operations use the
    correct credentials, avoiding cross-key 403s.
    """

    def __init__(self, api_client: Optional["GeminiAPIClient"] = None):
        """Initialize the file manager.

        Args:
            api_client: API client bound to the owning API key.
        """
        self.uploaded_files: List[Any] = []
        self._tracked_files: Set[str] = set()
        self.api_client = api_client
        
    def track_file(self, file: Any) -> None:
        """
        Track an uploaded file for cleanup.
        
        Args:
            file: File object to track
        """
        self.uploaded_files.append(file)
        self._tracked_files.add(file.name)
        logger.debug(f"Tracking file: {file.display_name}")
        
    def untrack_file(self, file: Any) -> None:
        """
        Remove a file from tracking.
        
        Args:
            file: File object to untrack
        """
        if file in self.uploaded_files:
            self.uploaded_files.remove(file)
        self._tracked_files.discard(file.name)
        logger.debug(f"Untracked file: {file.display_name}")
    
    def list_uploaded_files(self) -> List[Any]:
        """
        List all uploaded files in the account.
        
        Returns:
            List of uploaded files
        """
        try:
            # Use bound client when available so we stay in the correct key context
            if self.api_client is not None:
                files = self.api_client.list_files()
            else:
                files = list(genai.list_files())
            if self.api_client is not None:
                logger.info(f"Found {len(files)} uploaded files [key={self.api_client._key_tag()}]")
            else:
                logger.info(f"Found {len(files)} uploaded files")
            return files
        except Exception as e:
            if self.api_client is not None:
                logger.error(f"Failed to list files [key={self.api_client._key_tag()}]: {str(e)}")
            else:
                logger.error(f"Failed to list files: {str(e)}")
            return []
    
    def delete_file_safe(self, file: Any, max_retries: int = 3) -> bool:
        """
        Safely delete a file with retry logic.
        
        Args:
            file: File object to delete
            max_retries: Maximum number of retry attempts
            
        Returns:
            True if deletion successful, False otherwise
        """
        for attempt in range(max_retries):
            try:
                if self.api_client is not None:
                    # Delegate to client (already bound to correct key)
                    ok = self.api_client.delete_file(file, max_retries=1)
                    if ok:
                        logger.info(f"Deleted file: {file.display_name}")
                        self.untrack_file(file)
                        return True
                else:
                    genai.delete_file(file.name)
                    logger.info(f"Deleted file: {file.display_name}")
                    self.untrack_file(file)
                    return True
            except Exception as e:
                logger.warning(
                    f"Failed to delete {file.display_name} (attempt {attempt + 1}/{max_retries}): {str(e)}"
                )
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)  # Exponential backoff
        
        logger.error(f"Failed to delete file {file.display_name} after {max_retries} attempts")
        return False
    
    def delete_all_uploaded_files(self) -> int:
        """
        Delete all uploaded files from the account.
        
        Returns:
            Number of files deleted
        """
        # Only delete files that were tracked by this manager (uploaded by this client)
        all_files = self.list_uploaded_files()
        files = [f for f in all_files if f.name in self._tracked_files]
        if self.api_client is not None:
            logger.info(
                f"Cleanup scope: {len(files)} tracked / {len(all_files)} total [key={self.api_client._key_tag()}]"
            )
        if not files:
            logger.info("No files to delete")
            return 0
            
        deleted_count = 0
        logger.info(f"Deleting {len(files)} files...")
        
        for file in files:
            if self.delete_file_safe(file):
                deleted_count += 1
                
        logger.info(f"Deleted {deleted_count}/{len(files)} files")
        return deleted_count
    
    def cleanup_tracked_files(self) -> None:
        """Clean up all tracked files."""
        if not self.uploaded_files:
            return
            
        logger.info(f"Cleaning up {len(self.uploaded_files)} tracked files")
        for file in self.uploaded_files[:]:  # Copy list to avoid modification during iteration
            self.delete_file_safe(file)
            
        self.uploaded_files.clear()
        self._tracked_files.clear()
    
    def cleanup_except_center_file(self, center_file_display_name: str) -> None:
        """
        Delete all files except the center file.
        
        Args:
            center_file_display_name: Display name of the file to keep
        """
        try:
            # Only consider files uploaded by this FileManager instance
            files = [f for f in self.list_uploaded_files() if f.name in self._tracked_files]
            for file in files:
                if file.display_name != center_file_display_name:
                    self.delete_file_safe(file)
                else:
                    if self.api_client is not None:
                        logger.info(f"Keeping center file: {center_file_display_name} [key={self.api_client._key_tag()}]")
                    else:
                        logger.info(f"Keeping center file: {center_file_display_name}")
        except Exception as e:
            logger.error(f"Error during cleanup: {str(e)}")
    
    def find_file_by_display_name(self, display_name: str) -> Optional[Any]:
        """
        Find a file by its display name.
        
        Args:
            display_name: Display name to search for
            
        Returns:
            File object if found, None otherwise
        """
        files = self.list_uploaded_files()
        for file in files:
            if file.display_name == display_name:
                return file
        return None
    
    def get_tracked_file_count(self) -> int:
        """
        Get the number of tracked files.
        
        Returns:
            Number of tracked files
        """
        return len(self.uploaded_files)
    
    def __del__(self):
        """Clean up tracked files when object is destroyed."""
        if hasattr(self, 'uploaded_files') and self.uploaded_files:
            logger.warning("FileManager being destroyed with tracked files, attempting cleanup")
            self.cleanup_tracked_files()
