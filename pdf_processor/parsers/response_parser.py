"""
JSON response parser with error recovery.
Handles parsing of Gemini API responses with automatic partial recovery.
"""

import json
import re
from typing import Dict, Any, List, Optional

from ..utils.logging import get_logger
from ..utils.exceptions import JSONParsingError

logger = get_logger(__name__)


class ResponseParser:
    """Parses and validates API responses with error recovery."""
    
    @staticmethod
    def parse_response(response_text: str, mode: str = "jokbo-centric") -> Dict[str, Any]:
        """
        Parse JSON response with automatic fallback to partial parsing.
        
        Args:
            response_text: The response text to parse
            mode: Processing mode - either "jokbo-centric" or "lesson-centric"
            
        Returns:
            Dict containing either parsed JSON or error with partial recovery attempt
            
        Raises:
            JSONParsingError: If parsing completely fails
        """
        try:
            # First try direct JSON parsing
            return json.loads(response_text)
        except json.JSONDecodeError as e:
            logger.warning(f"JSON parsing error: {str(e)}")
            logger.info("Attempting partial parsing...")
            
            # Attempt partial parsing based on mode
            if mode == "jokbo-centric":
                partial_result = ResponseParser._parse_partial_jokbo(response_text)
            else:
                partial_result = ResponseParser._parse_partial_lesson(response_text)
            
            # If partial parsing completely failed, raise the original error
            if partial_result.get("error") and not partial_result.get("partial"):
                raise JSONParsingError(f"Complete JSON parsing failure: {partial_result['error']}")
            
            return partial_result
    
    @staticmethod
    def _parse_partial_jokbo(response_text: str) -> Dict[str, Any]:
        """
        Attempt to parse partial jokbo-centric response.
        
        Args:
            response_text: The response text to parse
            
        Returns:
            Dict with recovered data or error
        """
        logger.debug(f"Attempting partial jokbo parsing (response length: {len(response_text)})")
        
        # Find the jokbo_pages array start
        jokbo_pages_start = response_text.find('"jokbo_pages"')
        if jokbo_pages_start == -1:
            return {"error": "No jokbo_pages found", "partial": True}
        
        recovered_pages = []
        
        # Find all page objects
        page_starts = []
        for match in re.finditer(r'"jokbo_page"\s*:\s*(\d+)', response_text):
            page_starts.append((match.start(), match.group(1)))
        
        for i, (start_pos, page_num) in enumerate(page_starts):
            page_obj = ResponseParser._extract_json_object(
                response_text, start_pos, 
                next_pos=page_starts[i + 1][0] if i < len(page_starts) - 1 else len(response_text)
            )
            
            if page_obj and ResponseParser._validate_jokbo_page(page_obj):
                recovered_pages.append(page_obj)
                logger.debug(f"Recovered page {page_num}: {len(page_obj.get('questions', []))} questions")
        
        if recovered_pages:
            result = {
                "jokbo_pages": recovered_pages,
                "partial": True,
                "recovered_pages": len(recovered_pages),
                "total_questions_recovered": sum(len(p.get("questions", [])) for p in recovered_pages)
            }
            logger.info(f"Partial parsing success! Recovered {len(recovered_pages)} pages, "
                       f"{result['total_questions_recovered']} questions total")
            return result
        else:
            return {"error": "No complete pages could be recovered", "partial": True}
    
    @staticmethod
    def _parse_partial_lesson(response_text: str) -> Dict[str, Any]:
        """
        Attempt to parse partial lesson-centric response.
        
        Args:
            response_text: The response text to parse
            
        Returns:
            Dict with recovered data or error
        """
        logger.debug(f"Attempting partial lesson parsing (response length: {len(response_text)})")
        
        # Find the related_slides array start
        related_slides_start = response_text.find('"related_slides"')
        if related_slides_start == -1:
            return {"error": "No related_slides found", "partial": True}
        
        # Extract content after related_slides
        content_after_slides = response_text[related_slides_start:]
        
        # Try progressive closing of brackets
        for i in range(len(content_after_slides), max(0, len(content_after_slides) - 10000), -100):
            test_json = '{' + content_after_slides[:i]
            
            # Count open brackets and close them
            open_braces = test_json.count('{') - test_json.count('}')
            open_brackets = test_json.count('[') - test_json.count(']')
            
            test_json += ']' * open_brackets + '}' * open_braces
            
            try:
                parsed = json.loads(test_json)
                if "related_slides" in parsed and len(parsed["related_slides"]) > 0:
                    logger.info(f"Partial parsing success! Recovered {len(parsed['related_slides'])} slides")
                    parsed["partial"] = True
                    parsed["recovered_slides"] = len(parsed["related_slides"])
                    return parsed
            except json.JSONDecodeError:
                continue
        
        return {"error": "Failed to parse even partially", "partial": True}
    
    @staticmethod
    def _extract_json_object(text: str, start_pos: int, next_pos: int) -> Optional[Dict[str, Any]]:
        """
        Extract a complete JSON object from text.
        
        Args:
            text: The text containing JSON
            start_pos: Starting position to search from
            next_pos: End position to search to
            
        Returns:
            Extracted JSON object or None
        """
        # Find the start of the object
        obj_start = text.rfind('{', 0, start_pos)
        if obj_start == -1:
            return None
        
        # Track brace/bracket counts to find complete object
        brace_count = 0
        bracket_count = 0
        in_string = False
        escape_next = False
        obj_end = -1
        
        for j in range(obj_start, min(next_pos, len(text))):
            char = text[j]
            
            if escape_next:
                escape_next = False
                continue
            
            if char == '\\':
                escape_next = True
                continue
            
            if char == '"' and not escape_next:
                in_string = not in_string
                continue
            
            if not in_string:
                if char == '{':
                    brace_count += 1
                elif char == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        obj_end = j + 1
                        break
                elif char == '[':
                    bracket_count += 1
                elif char == ']':
                    bracket_count -= 1
        
        if obj_end > obj_start:
            try:
                return json.loads(text[obj_start:obj_end])
            except json.JSONDecodeError:
                return None
        
        return None
    
    @staticmethod
    def _validate_jokbo_page(page_obj: Dict[str, Any]) -> bool:
        """
        Validate a jokbo page object.
        
        Args:
            page_obj: Page object to validate
            
        Returns:
            True if valid, False otherwise
        """
        if "jokbo_page" not in page_obj or "questions" not in page_obj:
            return False
        
        # Check if questions array has valid questions
        questions = page_obj.get("questions", [])
        valid_questions = []
        
        for q in questions:
            # A complete question should have at least these fields
            if all(key in q for key in ["question_number", "question_text", "answer"]):
                valid_questions.append(q)
        
        if valid_questions:
            page_obj["questions"] = valid_questions
            return True
        
        return False
    
    @staticmethod
    def validate_response_structure(data: Dict[str, Any], mode: str) -> bool:
        """
        Validate the structure of parsed response.
        
        Args:
            data: Parsed response data
            mode: Processing mode
            
        Returns:
            True if structure is valid
        """
        if mode == "jokbo-centric":
            return "jokbo_pages" in data and isinstance(data["jokbo_pages"], list)
        else:
            return "related_slides" in data and isinstance(data["related_slides"], list)