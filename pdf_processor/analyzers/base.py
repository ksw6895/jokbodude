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
from ..utils.exceptions import PDFProcessorError, ContentGenerationError

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
        Upload files and perform analysis with quality-aware retry.
        
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

            # Prepare content and attempt quality-aware generation
            content = [prompt] + uploaded_files
            return self._generate_with_quality_retry(content)

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

    # --------------------
    # Internal helpers
    # --------------------
    def _is_empty_result(self, data: Dict[str, Any], mode: str) -> bool:
        """Check if the parsed result is semantically empty for the given mode."""
        try:
            if mode == "jokbo-centric":
                pages = data.get("jokbo_pages") or []
                if not isinstance(pages, list) or not pages:
                    return True
                total_q = 0
                for p in pages:
                    total_q += len((p or {}).get("questions", []) or [])
                return total_q == 0
            else:
                slides = data.get("related_slides") or []
                if not isinstance(slides, list) or not slides:
                    return True
                total_q = 0
                for s in slides:
                    total_q += len((s or {}).get("related_jokbo_questions", []) or [])
                return total_q == 0
        except Exception:
            # If anything is off, don't incorrectly treat as empty
            return False

    def _generate_with_quality_retry(self, content: List[Any], retries: int = 1) -> str:
        """
        Generate content and retry if output looks suspicious per parser heuristics.
        Retries are performed with the same API key and same uploaded files.
        """
        last_error: Exception | None = None
        mode = self.get_mode()
        attempts = max(1, retries + 1)
        for attempt in range(1, attempts + 1):
            try:
                response = self.api_client.generate_content(content)
                text = response.text
                try:
                    parsed = ResponseParser.parse_response(text, mode)
                    # Treat empty results as valid (no matches) rather than fatal
                    if self._is_empty_result(parsed, mode):
                        logger.warning(
                            f"Empty {mode} result detected; treating as valid with no matches"
                        )
                        try:
                            # Return normalized JSON to ensure downstream parser consistency
                            return json.dumps(parsed, ensure_ascii=False)
                        except Exception:
                            # Fallback to raw text if serialization fails
                            return text
                    if ResponseParser.is_result_suspicious(parsed, mode):
                        logger.warning(f"Suspicious {mode} result detected (attempt {attempt}/{attempts}); retrying...")
                        if attempt < attempts:
                            continue
                        else:
                            raise ContentGenerationError("Suspicious content after retries")
                except Exception as pe:
                    # Parsing failed or suspicious; if more attempts, continue
                    last_error = pe
                    logger.warning(f"Parsing/quality check failed: {pe}")
                    if attempt < attempts:
                        continue
                    raise
                # Looks good
                return text
            except Exception as e:
                last_error = e
                logger.error(f"Generation failed on attempt {attempt}/{attempts}: {e}")
                if attempt < attempts:
                    continue
                raise ContentGenerationError(str(e))
        # Should not reach here
        if last_error:
            raise last_error
        raise ContentGenerationError("Unknown generation error")
