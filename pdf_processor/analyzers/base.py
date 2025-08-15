"""
Base analyzer class for PDF analysis strategies.
Provides common functionality for different analysis modes.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path
import json
from datetime import datetime

from ..api.client import GeminiAPIClient
from ..api.file_manager import FileManager
from ..pdf.operations import PDFOperations
from ..parsers.response_parser import ResponseParser
from ..parsers.result_merger import ResultMerger
from ..utils.logging import get_logger
from ..utils.exceptions import PDFProcessorError

logger = get_logger(__name__)


class BaseAnalyzer(ABC):
    """Abstract base class for PDF analyzers."""
    
    def __init__(self, api_client: GeminiAPIClient, file_manager: FileManager, 
                 session_id: str, debug_dir: Path):
        """
        Initialize the analyzer.
        
        Args:
            api_client: Gemini API client instance
            file_manager: File manager instance
            session_id: Session identifier
            debug_dir: Directory for debug outputs
        """
        self.api_client = api_client
        self.file_manager = file_manager
        self.session_id = session_id
        self.debug_dir = debug_dir
        self.debug_dir.mkdir(parents=True, exist_ok=True)
        
    @abstractmethod
    def get_mode(self) -> str:
        """Get the analyzer mode name."""
        pass
    
    @abstractmethod
    def build_prompt(self, *args, **kwargs) -> str:
        """Build the analysis prompt."""
        pass
    
    @abstractmethod
    def analyze(self, *args, **kwargs) -> Dict[str, Any]:
        """Perform the analysis."""
        pass
    
    def save_debug_response(self, response_text: str, *file_identifiers: str) -> None:
        """
        Save API response for debugging.
        
        Args:
            response_text: The API response text
            file_identifiers: File names or identifiers for the debug filename
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        mode = self.get_mode()
        
        # Create filename from identifiers
        identifiers_str = "_".join(str(f).replace("/", "_").replace("\\", "_") 
                                  for f in file_identifiers)
        filename = f"{timestamp}_{mode}_{identifiers_str}_response.json"
        filepath = self.debug_dir / filename
        
        debug_data = {
            "timestamp": timestamp,
            "mode": mode,
            "session_id": self.session_id,
            "files": list(file_identifiers),
            "response": response_text,
            "response_length": len(response_text)
        }
        
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(debug_data, f, ensure_ascii=False, indent=2)
            logger.debug(f"Debug response saved to {filepath}")
        except Exception as e:
            logger.error(f"Failed to save debug response: {str(e)}")
    
    def process_with_chunks(self, pdf_path: str, analysis_func: callable, 
                          max_pages: int = 40) -> Dict[str, Any]:
        """
        Process a PDF in chunks if necessary.
        
        Args:
            pdf_path: Path to the PDF
            analysis_func: Function to call for analysis
            max_pages: Maximum pages per chunk
            
        Returns:
            Merged analysis results
        """
        chunks = PDFOperations.split_pdf_for_chunks(pdf_path, max_pages)
        
        if len(chunks) == 1:
            # Single chunk, process normally
            return analysis_func(pdf_path)
        
        # Process multiple chunks
        logger.info(f"Processing {len(chunks)} chunks for {Path(pdf_path).name}")
        chunk_results = []
        
        for i, (path, start_page, end_page) in enumerate(chunks):
            logger.info(f"Processing chunk {i+1}/{len(chunks)}: pages {start_page}-{end_page}")
            
            # Extract chunk to temporary file
            chunk_path = PDFOperations.extract_pages(path, start_page, end_page)
            
            try:
                # Analyze chunk
                result = analysis_func(chunk_path, chunk_info=(start_page, end_page))
                chunk_results.append(result)
            finally:
                # Clean up temporary file
                Path(chunk_path).unlink(missing_ok=True)
        
        # Merge results
        return ResultMerger.merge_chunk_results(chunk_results, self.get_mode())
    
    def upload_and_analyze(self, files_to_upload: List[Tuple[str, str]], 
                          prompt: str) -> str:
        """
        Upload files and perform analysis.
        
        Args:
            files_to_upload: List of (file_path, display_name) tuples
            prompt: Analysis prompt
            
        Returns:
            API response text
        """
        uploaded_files = []
        
        try:
            # Upload all files
            for file_path, display_name in files_to_upload:
                uploaded_file = self.api_client.upload_file(file_path, display_name)
                uploaded_files.append(uploaded_file)
                self.file_manager.track_file(uploaded_file)
            
            # Prepare content
            content = [prompt] + uploaded_files
            
            # Generate response
            response = self.api_client.generate_content(content)
            return response.text
            
        finally:
            # Clean up uploaded files
            for file in uploaded_files:
                self.file_manager.delete_file_safe(file)
    
    def parse_and_validate_response(self, response_text: str) -> Dict[str, Any]:
        """
        Parse and validate API response.
        
        Args:
            response_text: Raw API response text
            
        Returns:
            Parsed response dictionary
        """
        # Parse response
        result = ResponseParser.parse_response(response_text, self.get_mode())
        
        # Validate structure
        if not ResponseParser.validate_response_structure(result, self.get_mode()):
            raise PDFProcessorError(f"Invalid response structure for {self.get_mode()} mode")
        
        return result
    
    def filter_connections(self, connections: List[Dict[str, Any]], 
                         min_score: int = 70, max_connections: int = 2) -> List[Dict[str, Any]]:
        """
        Filter connections by relevance score.
        
        Args:
            connections: List of connections
            min_score: Minimum score threshold
            max_connections: Maximum connections to keep
            
        Returns:
            Filtered connections
        """
        return ResultMerger.filter_connections_by_score(
            connections, min_score, max_connections
        )
