"""Response handling and saving for API responses"""

import json
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional


class ResponseHandler:
    """Handles saving and managing API responses"""
    
    def __init__(self):
        """Initialize response handler"""
        self.debug_dir = Path("output/debug")
        self.debug_dir.mkdir(parents=True, exist_ok=True)
    
    def save_api_response(
        self,
        response_text: str,
        jokbo_filename: str,
        lesson_filename: Optional[str] = None,
        mode: str = "lesson-centric",
        finish_reason: Optional[str] = None,
        response_metadata: Optional[Dict] = None
    ):
        """Save API response to debug file
        
        Args:
            response_text: Response text from API
            jokbo_filename: Name of jokbo file
            lesson_filename: Optional name of lesson file
            mode: Processing mode
            finish_reason: Reason for finishing
            response_metadata: Additional metadata
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Create filename based on mode
        if mode == "jokbo-centric":
            if lesson_filename:
                filename = f"{timestamp}_jokbo-centric_{jokbo_filename}_{lesson_filename}_response.json"
            else:
                filename = f"{timestamp}_jokbo-centric_unknown.pdf_{jokbo_filename}_response.json"
        else:
            filename = f"gemini_response_{timestamp}_jokbo_{jokbo_filename}_{lesson_filename}.json"
        
        # Prepare debug data
        debug_data = {
            "timestamp": timestamp,
            "jokbo_file": jokbo_filename,
            "mode": mode,
            "response_text": response_text
        }
        
        if lesson_filename:
            debug_data["lesson_file"] = lesson_filename
        
        if finish_reason:
            debug_data["finish_reason"] = finish_reason
            
        if response_metadata:
            debug_data["metadata"] = response_metadata
        
        # Save to file
        debug_file = self.debug_dir / filename
        with open(debug_file, 'w', encoding='utf-8') as f:
            json.dump(debug_data, f, ensure_ascii=False, indent=2)
        
        print(f"  디버그 응답 저장됨: {debug_file}")
    
    def save_chunk_result(
        self,
        chunk_info: Dict[str, Any],
        result: Dict[str, Any],
        temp_dir: Path
    ) -> str:
        """Save chunk processing result
        
        Args:
            chunk_info: Information about the chunk
            result: Processing result
            temp_dir: Temporary directory for saving
            
        Returns:
            Path to saved file
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S%f")[:-3]
        
        # Create filename
        chunk_filename = f"chunk_{Path(chunk_info['file_path']).stem}_p{chunk_info['start_page']}-{chunk_info['end_page']}_{timestamp}.json"
        chunk_file = temp_dir / chunk_filename
        
        # Save data
        save_data = {
            "chunk_info": chunk_info,
            "result": result,
            "timestamp": timestamp
        }
        
        with open(chunk_file, 'w', encoding='utf-8') as f:
            json.dump(save_data, f, ensure_ascii=False, indent=2)
        
        return str(chunk_file)
    
    def save_lesson_result(
        self,
        lesson_idx: int,
        lesson_path: str,
        result: Dict[str, Any],
        temp_dir: Path
    ) -> str:
        """Save lesson processing result
        
        Args:
            lesson_idx: Index of lesson
            lesson_path: Path to lesson file
            result: Processing result
            temp_dir: Temporary directory
            
        Returns:
            Path to saved file
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S%f")[:-3]
        
        # Create filename
        lesson_filename = f"lesson_{lesson_idx:03d}_{Path(lesson_path).stem}_{timestamp}.json"
        lesson_file = temp_dir / lesson_filename
        
        # Save data
        save_data = {
            "lesson_index": lesson_idx,
            "lesson_path": lesson_path,
            "result": result,
            "timestamp": timestamp
        }
        
        with open(lesson_file, 'w', encoding='utf-8') as f:
            json.dump(save_data, f, ensure_ascii=False, indent=2)
        
        return str(lesson_file)