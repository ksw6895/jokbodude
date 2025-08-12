"""JSON parsing and recovery for API responses"""

import json
import re
from typing import Dict, Any, Optional


class JSONParser:
    """Handles JSON parsing and partial recovery from API responses"""
    
    def parse_response_json(self, response_text: str, mode: str = "jokbo-centric") -> Dict[str, Any]:
        """Parse JSON response with error recovery
        
        Args:
            response_text: Raw response text from API
            mode: Processing mode
            
        Returns:
            Parsed JSON dictionary
        """
        # Clean up common JSON errors
        cleaned_text = self._clean_json_text(response_text)
        
        try:
            # Try to parse the cleaned JSON
            return json.loads(cleaned_text)
        except json.JSONDecodeError as e:
            print(f"  JSON 파싱 오류: {str(e)}")
            # Try partial parsing as fallback
            return self.parse_partial_json(response_text, mode)
    
    def _clean_json_text(self, response_text: str) -> str:
        """Clean up common JSON errors from Gemini
        
        Args:
            response_text: Raw response text from Gemini
            
        Returns:
            Cleaned text ready for JSON parsing
        """
        # Fix incorrectly quoted keys like "4"번" -> "4번"
        cleaned_text = re.sub(r'"(\d+)"번"', r'"\1번"', response_text)
        return cleaned_text
    
    def parse_partial_json(self, response_text: str, mode: str = "jokbo-centric") -> Dict[str, Any]:
        """Try to parse partial JSON response and salvage what's possible
        
        Args:
            response_text: Partial response text
            mode: Processing mode
            
        Returns:
            Recovered data dictionary
        """
        print(f"  부분 JSON 파싱 시도 중... (응답 길이: {len(response_text)})")
        
        if mode == "jokbo-centric":
            return self._parse_partial_jokbo_centric(response_text)
        elif mode == "lesson-centric":
            return self._parse_partial_lesson_centric(response_text)
        else:
            return {"error": f"Partial parsing not implemented for mode: {mode}", "partial": True}
    
    def _parse_partial_jokbo_centric(self, response_text: str) -> Dict[str, Any]:
        """Parse partial jokbo-centric response
        
        Args:
            response_text: Partial response text
            
        Returns:
            Recovered jokbo data
        """
        try:
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
                page_obj = self._extract_page_object(
                    response_text, start_pos, i, page_starts, page_num
                )
                if page_obj:
                    recovered_pages.append(page_obj)
            
            if recovered_pages:
                result = {
                    "jokbo_pages": recovered_pages,
                    "partial": True,
                    "recovered_pages": len(recovered_pages),
                    "total_questions_recovered": sum(
                        len(p.get("questions", [])) for p in recovered_pages
                    )
                }
                print(f"  부분 파싱 성공! {len(recovered_pages)}개 페이지, "
                      f"총 {result['total_questions_recovered']}개 문제 복구")
                return result
            else:
                return {"error": "No complete pages could be recovered", "partial": True}
                
        except Exception as e:
            print(f"  부분 파싱 실패: {str(e)}")
            return {"error": str(e), "partial": True}
    
    def _extract_page_object(
        self, 
        response_text: str, 
        start_pos: int, 
        index: int,
        page_starts: list, 
        page_num: str
    ) -> Optional[Dict[str, Any]]:
        """Extract a single page object from response text
        
        Args:
            response_text: Full response text
            start_pos: Start position of page reference
            index: Index in page_starts list
            page_starts: List of all page starts
            page_num: Page number string
            
        Returns:
            Extracted page object or None
        """
        # Find object boundaries
        obj_start = response_text.rfind('{', 0, start_pos)
        if obj_start == -1:
            return None
        
        # Determine search end position
        if index < len(page_starts) - 1:
            next_start = page_starts[index + 1][0]
            search_end = response_text.rfind('{', 0, next_start)
        else:
            search_end = len(response_text)
        
        # Extract object with balanced braces
        obj_end = self._find_object_end(response_text, obj_start, search_end)
        
        if obj_end > obj_start:
            try:
                page_obj_str = response_text[obj_start:obj_end]
                page_obj = json.loads(page_obj_str)
                
                # Validate page object
                if "jokbo_page" in page_obj and "questions" in page_obj:
                    valid_questions = self._validate_questions(page_obj.get("questions", []))
                    
                    if valid_questions:
                        page_obj["questions"] = valid_questions
                        print(f"  페이지 {page_num} 복구 성공: {len(valid_questions)}개 문제")
                        return page_obj
            except json.JSONDecodeError:
                pass
        
        return None
    
    def _find_object_end(self, text: str, start: int, search_end: int) -> int:
        """Find the end position of a JSON object
        
        Args:
            text: Text to search in
            start: Start position
            search_end: End of search range
            
        Returns:
            End position of object or -1 if not found
        """
        brace_count = 0
        in_string = False
        escape_next = False
        
        for i in range(start, min(search_end, len(text))):
            char = text[i]
            
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
                        return i + 1
        
        return -1
    
    def _validate_questions(self, questions: list) -> list:
        """Validate and filter complete questions
        
        Args:
            questions: List of question objects
            
        Returns:
            List of valid questions
        """
        valid_questions = []
        
        for q in questions:
            # A complete question should have at least these fields
            if all(key in q for key in ["question_number", "question_text", "answer"]):
                valid_questions.append(q)
        
        return valid_questions
    
    def _parse_partial_lesson_centric(self, response_text: str) -> Dict[str, Any]:
        """Parse partial lesson-centric response
        
        Args:
            response_text: Partial response text
            
        Returns:
            Recovered lesson data
        """
        try:
            # Find related_slides array
            related_slides_start = response_text.find('"related_slides"')
            if related_slides_start == -1:
                return {"error": "No related_slides found", "partial": True}
            
            content_after_slides = response_text[related_slides_start:]
            
            # Try progressive closing of brackets
            for i in range(len(content_after_slides), max(0, len(content_after_slides) - 10000), -100):
                test_json = '{' + content_after_slides[:i]
                
                # Count open brackets
                open_braces = test_json.count('{') - test_json.count('}')
                open_brackets = test_json.count('[') - test_json.count(']')
                
                # Add closing brackets
                test_json += ']' * open_brackets + '}' * open_braces
                
                try:
                    parsed = json.loads(test_json)
                    if "related_slides" in parsed and len(parsed["related_slides"]) > 0:
                        print(f"  부분 파싱 성공! {len(parsed['related_slides'])}개 슬라이드 복구")
                        parsed["partial"] = True
                        parsed["recovered_slides"] = len(parsed["related_slides"])
                        return parsed
                except json.JSONDecodeError:
                    continue
            
            return {"error": "Failed to parse even partially", "partial": True}
            
        except Exception as e:
            print(f"  부분 파싱 실패: {str(e)}")
            return {"error": str(e), "partial": True}