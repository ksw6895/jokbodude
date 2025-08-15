"""
JSON response parser with robust recovery and sanitation.
Handles parsing of Gemini API responses with:
- Code-fence/markdown cleanup and top-level JSON extraction
- Common JSON repair (trailing commas, smart quotes)
- Partial recovery for jokbo/lesson modes
- Schema normalization and placeholder filtering to avoid low-value outputs
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
        # 1) Preprocess: strip code fences/markdown noise and isolate JSON-ish region
        cleaned = ResponseParser._preprocess_response_text(response_text)

        # 2) Try direct JSON
        try:
            parsed = json.loads(cleaned)
            return ResponseParser._sanitize_parsed_response(parsed, mode)
        except Exception as e:
            logger.warning(f"JSON parsing error (direct): {e}")

        # 3) Attempt common repairs (trailing commas, smart quotes, NaN/Infinity)
        repaired = ResponseParser._repair_common_json_issues(cleaned)
        try:
            parsed = json.loads(repaired)
            return ResponseParser._sanitize_parsed_response(parsed, mode)
        except Exception as e:
            logger.warning(f"JSON parsing error (after repair): {e}")

        # 4) Try extracting the largest top-level JSON object and parse
        extracted = ResponseParser._extract_top_level_json(response_text)
        if extracted:
            try:
                parsed = json.loads(extracted)
                return ResponseParser._sanitize_parsed_response(parsed, mode)
            except Exception as e:
                logger.warning(f"JSON parsing error (after extraction): {e}")

        # 5) Partial parsing fallback per mode
        logger.info("Attempting partial parsing...")
        if mode == "jokbo-centric":
            partial_result = ResponseParser._parse_partial_jokbo(response_text)
        else:
            partial_result = ResponseParser._parse_partial_lesson(response_text)

        # If partial parsing completely failed, raise the original error
        if partial_result.get("error") and not partial_result.get("partial"):
            raise JSONParsingError(f"Complete JSON parsing failure: {partial_result['error']}")

        return ResponseParser._sanitize_parsed_response(partial_result, mode)

    # --------------------
    # Preprocessing helpers
    # --------------------
    @staticmethod
    def _preprocess_response_text(text: str) -> str:
        """
        Remove common markdown wrappers and isolate probable JSON region.
        - Strips code fences like ```json ... ``` or ``` ... ```
        - If multiple fences exist, prefer the first block containing '{'
        - Falls back to slicing from first '{' to last '}' if present
        """
        if not text:
            return text

        t = text.strip().lstrip("\ufeff")  # strip BOM

        # Prefer fenced code blocks
        fence_matches = list(re.finditer(r"```(json)?\s*([\s\S]*?)```", t, flags=re.IGNORECASE))
        for m in fence_matches:
            block = m.group(2) or ""
            if "{" in block:
                return block.strip()

        # Single-line JSON may be prefixed/suffixed by explanation text
        first = t.find("{")
        last = t.rfind("}")
        if first != -1 and last != -1 and last > first:
            return t[first:last + 1].strip()

        return t

    @staticmethod
    def _repair_common_json_issues(text: str) -> str:
        """
        Apply simple, safe repairs to improve JSON parseability:
        - Replace smart quotes with standard quotes
        - Remove trailing commas before '}' or ']'
        - Replace bare NaN/Infinity with null
        """
        if not text:
            return text
        s = text
        # Normalize curly/smart quotes frequently emitted by LLMs
        s = s.replace("“", '"').replace("”", '"').replace("’", "'").replace("‘", "'")
        # Remove trailing commas before closing braces/brackets
        s = re.sub(r",\s*([}\]])", r"\1", s)
        # Replace non-JSON numerics
        s = re.sub(r"\bNaN\b|\bInfinity\b|-Infinity", "null", s)
        return s

    @staticmethod
    def _extract_top_level_json(text: str) -> Optional[str]:
        """
        Scan the text to extract the first balanced top-level JSON object.
        Handles stray commentary before/after the JSON.
        """
        if not text:
            return None
        start = text.find("{")
        if start == -1:
            return None
        brace = 0
        in_string = False
        escape = False
        for i in range(start, len(text)):
            ch = text[i]
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == '{':
                brace += 1
            elif ch == '}':
                brace -= 1
                if brace == 0:
                    return text[start:i + 1]
        return None
    
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
                candidate = text[obj_start:obj_end]
                # Attempt with minor repairs for object-only segments
                candidate = ResponseParser._repair_common_json_issues(candidate)
                return json.loads(candidate)
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
            # A complete question should have at least these fields, and not placeholders
            if all(key in q for key in ["question_number", "question_text", "answer"]):
                if not ResponseParser._is_placeholder_value(q.get("answer")):
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

    # --------------------
    # Sanitation / normalization
    # --------------------
    @staticmethod
    def _is_placeholder_value(val: Any) -> bool:
        if val is None:
            return True
        s = str(val).strip().lower()
        if s in {"", "n/a", "na", "none", "null", "not provided", "not provided in jokbo"}:
            return True
        # Korean common placeholders
        if s in {"없음", "제공되지 않음", "정보 없음"}:
            return True
        return False

    @staticmethod
    def _to_int_safe(v: Any, default: int = 0) -> int:
        try:
            if isinstance(v, bool):
                return default
            if isinstance(v, (int, float)):
                return int(v)
            m = re.search(r"(\d+)", str(v) or "")
            return int(m.group(1)) if m else default
        except Exception:
            return default

    @staticmethod
    def _norm_wrong_answer_explanations(data: Any) -> Dict[str, str]:
        if not isinstance(data, dict):
            return {}
        out: Dict[str, str] = {}
        for k, v in data.items():
            ks = str(k).strip()
            # Normalize keys to "1번".."5번" patterns where possible
            m = re.search(r"(\d)", ks)
            if m:
                ks = f"{m.group(1)}번"
            vs = "" if v is None else str(v).strip()
            if not ResponseParser._is_placeholder_value(vs):
                out[ks] = vs
        return out

    @staticmethod
    def _sanitize_parsed_response(data: Dict[str, Any], mode: str) -> Dict[str, Any]:
        """
        Normalize schema, coerce types, and drop low-value entries so that
        downstream rendering avoids empty or placeholder content.
        """
        if not isinstance(data, dict):
            return data

        if mode == "jokbo-centric":
            pages = data.get("jokbo_pages")
            if not isinstance(pages, list):
                return {"jokbo_pages": []}

            cleaned_pages: List[Dict[str, Any]] = []
            for p in pages:
                if not isinstance(p, dict):
                    continue
                page_no = ResponseParser._to_int_safe(p.get("jokbo_page"), 0)
                questions = p.get("questions") or []
                if not isinstance(questions, list):
                    questions = []
                cleaned_questions: List[Dict[str, Any]] = []
                for q in questions:
                    if not isinstance(q, dict):
                        continue
                    qnum = str(q.get("question_number", "")).strip()
                    qtext = (q.get("question_text") or "").strip()
                    ans = (q.get("answer") or "").strip()
                    if not qnum or not qtext or ResponseParser._is_placeholder_value(ans):
                        continue
                    # Normalize wrong answers
                    wae = ResponseParser._norm_wrong_answer_explanations(q.get("wrong_answer_explanations"))
                    # Normalize related slides
                    slides = q.get("related_lesson_slides") or []
                    if not isinstance(slides, list):
                        slides = []
                    norm_slides: List[Dict[str, Any]] = []
                    for s in slides:
                        if not isinstance(s, dict):
                            continue
                        lf = (s.get("lesson_filename") or "").strip()
                        lp = ResponseParser._to_int_safe(s.get("lesson_page"), 0)
                        if not lf or lp <= 0:
                            continue
                        sc = ResponseParser._to_int_safe(s.get("relevance_score"), 0)
                        sc = max(0, min(sc, 110))
                        rs = (s.get("relevance_reason") or s.get("reason") or "").strip()
                        norm_slides.append({
                            "lesson_filename": lf,
                            "lesson_page": lp,
                            "relevance_score": sc,
                            "relevance_reason": rs
                        })
                    # Keep at most 2 slides by score desc
                    norm_slides.sort(key=lambda x: x.get("relevance_score", 0), reverse=True)
                    norm_slides = norm_slides[:2]
                    # Normalize question_numbers_on_page to strings
                    qn_on_page = q.get("question_numbers_on_page") or []
                    if isinstance(qn_on_page, list):
                        qn_on_page = [str(x) for x in qn_on_page if str(x).strip()]
                    else:
                        qn_on_page = []

                    expl = (q.get("explanation") or "").strip()
                    if ResponseParser._is_placeholder_value(expl):
                        expl = ""
                    cleaned_questions.append({
                        "jokbo_page": page_no,
                        "question_number": qnum,
                        "question_text": qtext,
                        "answer": ans,
                        "explanation": expl,
                        "wrong_answer_explanations": wae,
                        "related_lesson_slides": norm_slides,
                        "question_numbers_on_page": qn_on_page,
                        **({"jokbo_filename": (q.get("jokbo_filename") or "").strip()} if q.get("jokbo_filename") else {}),
                        # preserve optional fields if present and valid
                        **({"jokbo_end_page": ResponseParser._to_int_safe(q.get("jokbo_end_page"), 0)} if q.get("jokbo_end_page") else {})
                    })
                if cleaned_questions:
                    cleaned_pages.append({
                        "jokbo_page": page_no,
                        "questions": cleaned_questions
                    })
            # Sort pages and finalize
            cleaned_pages.sort(key=lambda x: int(str(x.get("jokbo_page", 0)) or 0))
            try:
                total_q = sum(len(p.get("questions", [])) for p in cleaned_pages)
                logger.info(f"Sanitized jokbo response: {len(cleaned_pages)} pages, {total_q} questions")
            except Exception:
                pass
            return {"jokbo_pages": cleaned_pages}

        # lesson-centric normalization
        slides = data.get("related_slides")
        if not isinstance(slides, list):
            return {"related_slides": []}
        cleaned_slides: List[Dict[str, Any]] = []
        for s in slides:
            if not isinstance(s, dict):
                continue
            lp = ResponseParser._to_int_safe(s.get("lesson_page"), 0)
            if lp <= 0:
                continue
            questions = s.get("related_jokbo_questions") or []
            if not isinstance(questions, list):
                questions = []
            norm_qs: List[Dict[str, Any]] = []
            for q in questions:
                if not isinstance(q, dict):
                    continue
                qnum = str(q.get("question_number", "")).strip()
                qtext = (q.get("question_text") or "").strip()
                ans = (q.get("answer") or "").strip()
                if not qnum or not qtext or ResponseParser._is_placeholder_value(ans):
                    continue
                expl = (q.get("explanation") or "").strip()
                if ResponseParser._is_placeholder_value(expl):
                    expl = ""
                norm_qs.append({
                    "jokbo_filename": (q.get("jokbo_filename") or "").strip(),
                    "jokbo_page": ResponseParser._to_int_safe(q.get("jokbo_page"), 0),
                    "jokbo_end_page": ResponseParser._to_int_safe(q.get("jokbo_end_page"), 0) if q.get("jokbo_end_page") else None,
                    "question_number": qnum,
                    "question_numbers_on_page": [str(x) for x in (q.get("question_numbers_on_page") or []) if str(x).strip()],
                    "question_text": qtext,
                    "answer": ans,
                    "explanation": expl,
                    "wrong_answer_explanations": ResponseParser._norm_wrong_answer_explanations(q.get("wrong_answer_explanations")),
                    "relevance_score": max(0, min(ResponseParser._to_int_safe(q.get("relevance_score"), 0), 110)),
                    "relevance_reason": (q.get("relevance_reason") or q.get("reason") or "").strip(),
                })
            # Skip slides that ended up with no valid questions
            if norm_qs:
                cleaned_slides.append({
                    "lesson_page": lp,
                    "related_jokbo_questions": norm_qs,
                    **({"importance_score": max(0, min(ResponseParser._to_int_safe(s.get("importance_score"), 0), 110))} if s.get("importance_score") is not None else {}),
                    **({"key_concepts": s.get("key_concepts", [])} if isinstance(s.get("key_concepts"), list) else {}),
                })
        cleaned_slides.sort(key=lambda x: x.get("lesson_page", 0))
        try:
            total_q = sum(len(s.get("related_jokbo_questions", [])) for s in cleaned_slides)
            logger.info(f"Sanitized lesson response: {len(cleaned_slides)} slides, {total_q} linked questions")
        except Exception:
            pass
        return {"related_slides": cleaned_slides}
