"""Session management for PDF processing"""

import random
import string
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any
import json


class SessionManager:
    """Manages session IDs and directories for PDF processing"""
    
    def __init__(self, session_id: Optional[str] = None):
        """Initialize session manager
        
        Args:
            session_id: Existing session ID to use, or None to generate new one
        """
        if session_id:
            self.session_id = session_id
        else:
            self.session_id = self._generate_session_id()
            
        self.session_dir = Path("output/temp/sessions") / self.session_id
        self.chunk_results_dir = self.session_dir / "chunk_results"
        self.chunk_results_dir.mkdir(parents=True, exist_ok=True)
        
        # Only print for newly generated sessions
        if not session_id:
            print(f"세션 ID: {self.session_id}")
    
    def _generate_session_id(self) -> str:
        """Generate unique session ID with timestamp and random suffix
        
        Returns:
            Session ID string in format YYYYMMDD_HHMMSS_random
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        random_suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
        return f"{timestamp}_{random_suffix}"
    
    def save_processing_state(self, state_info: Dict[str, Any], state_file: Path):
        """Save processing state to file
        
        Args:
            state_info: State information to save
            state_file: Path to state file
        """
        try:
            with open(state_file, 'w', encoding='utf-8') as f:
                json.dump(state_info, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"상태 저장 실패: {e}")
    
    def load_processing_state(self, state_file: Path) -> Optional[Dict[str, Any]]:
        """Load processing state from file
        
        Args:
            state_file: Path to state file
            
        Returns:
            State information dictionary or None if failed
        """
        try:
            if state_file.exists():
                with open(state_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            print(f"상태 로드 실패: {e}")
        return None
    
    def cleanup_temp_files(self, temp_dir: Path):
        """Clean up temporary files in directory
        
        Args:
            temp_dir: Directory to clean up
        """
        try:
            import shutil
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
        except Exception as e:
            print(f"임시 파일 정리 실패: {e}")