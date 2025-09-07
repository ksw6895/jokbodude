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
        elif mode == "lesson-centric":
            partial_result = ResponseParser._parse_partial_lesson(response_text)
        else:
            # partial-jokbo and other new modes: fall back to generic extraction of array
            partial_result = ResponseParser._parse_partial_partialjokbo(response_text)

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
        if mode == "lesson-centric":
            return "related_slides" in data and isinstance(data["related_slides"], list)
        if mode == "partial-jokbo":
            return "questions" in data and isinstance(data["questions"], list)
        if mode == "exam-only":
            return "questions" in data and isinstance(data["questions"], list)
        return False

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
        if s in {
            "없음", "제공되지 않음", "정보 없음", "정답 없음", "해설 없음", "오답 설명 없음",
            "설명 없음", "관련성 이유 없음"
        }:
            return True
        # Frequently seen variants like "~ 없음" at the end
        try:
            if s.endswith("없음"):
                return True
        except Exception:
            return True
        return False

    @staticmethod
    def _snap_score(value: Any, allow_zero: bool = True) -> int:
        """
        Snap scores to 5-point grid within [5..100], with a special-case 110.
        If allow_zero and the input is exactly 0, keep 0.
        """
        try:
            # Keep explicit 0 for exclusion semantics
            if allow_zero and (str(value).strip() == "0" or value == 0):
                return 0
            v = float(ResponseParser._to_int_safe(value, 0))
        except Exception:
            v = 0.0
        # Special 110 allowance
        if v >= 107.5:
            return 110
        # Round to nearest 5
        snapped = int(round(v / 5.0) * 5)
        # Clamp to [5..100] and avoid 105
        if snapped < 5:
            snapped = 5
        if snapped > 100:
            snapped = 100
        if snapped == 105:
            snapped = 100
        return snapped

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
                    # Normalize question_number: prefer digit-only string if present
                    qnum_raw = str(q.get("question_number", "")).strip()
                    qnum_int = ResponseParser._to_int_safe(qnum_raw, 0)
                    qnum = str(qnum_int) if qnum_int > 0 else qnum_raw
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
                    # Deduplicate by (lesson_filename, lesson_page) keeping highest score
                    best_by_key: Dict[tuple, Dict[str, Any]] = {}
                    for s in slides:
                        if not isinstance(s, dict):
                            continue
                        # Normalize to base lesson filename (strip common prefixes the prompt may add)
                        lf_raw = (s.get("lesson_filename") or "").strip()
                        try:
                            lf = re.sub(r"^(강의자료|강의|lesson|lecture)[\s_\-]+", "", lf_raw, flags=re.IGNORECASE)
                        except Exception:
                            lf = lf_raw
                        lp = ResponseParser._to_int_safe(s.get("lesson_page"), 0)
                        if not lf or lp <= 0:
                            continue
                        sc = ResponseParser._snap_score(s.get("relevance_score"), allow_zero=True)
                        rs = (s.get("relevance_reason") or s.get("reason") or "").strip()
                        # Parser-level filtering: drop slides with score < 80
                        if sc >= 80:
                            key = (lf.lower(), lp)
                            cand = {
                                "lesson_filename": lf,
                                "lesson_page": lp,
                                "relevance_score": sc,
                                "relevance_reason": rs
                            }
                            prev = best_by_key.get(key)
                            if not prev or sc > int(prev.get("relevance_score", 0)):
                                best_by_key[key] = cand
                    norm_slides: List[Dict[str, Any]] = list(best_by_key.values())
                    # Keep at most 2 slides by score desc
                    norm_slides.sort(key=lambda x: x.get("relevance_score", 0), reverse=True)
                    norm_slides = norm_slides[:2]
                    # Normalize question_numbers_on_page to digit-only strings
                    qn_on_page_raw = q.get("question_numbers_on_page") or []
                    qn_on_page: List[str] = []
                    if isinstance(qn_on_page_raw, list):
                        for x in qn_on_page_raw:
                            xi = ResponseParser._to_int_safe(x, 0)
                            if xi > 0:
                                qn_on_page.append(str(xi))
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
                        **({"jokbo_end_page": ResponseParser._to_int_safe(q.get("jokbo_end_page"), 0)} if q.get("jokbo_end_page") else {}),
                        **({"next_question_start": ResponseParser._to_int_safe(q.get("next_question_start"), 0)} if q.get("next_question_start") is not None else {})
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

        # partial-jokbo normalization (page ranges + explanation)
        if mode == "partial-jokbo":
            qs = data.get("questions")
            if not isinstance(qs, list):
                return {"questions": []}
            cleaned_qs: List[Dict[str, Any]] = []
            for q in qs:
                if not isinstance(q, dict):
                    continue
                qnum_raw = str(q.get("question_number", "")).strip()
                qnum_int = ResponseParser._to_int_safe(qnum_raw, 0)
                qnum = str(qnum_int) if qnum_int > 0 else qnum_raw
                ps = ResponseParser._to_int_safe(q.get("page_start"), 0)
                nqs = ResponseParser._to_int_safe(q.get("next_question_start"), 0) if q.get("next_question_start") is not None else None
                expl = (q.get("explanation") or "").strip()
                if ps <= 0 or not qnum:
                    continue
                cleaned_qs.append({
                    "question_number": qnum,
                    "lecture_title": (q.get("lecture_title") or "").strip(),
                    "page_start": ps,
                    "next_question_start": nqs if (isinstance(nqs, int) and nqs >= 0) else None,
                    "explanation": expl,
                })
            try:
                cleaned_qs.sort(key=lambda x: (x.get("page_start", 0), ResponseParser._to_int_safe(x.get("question_number"), 0)))
            except Exception:
                pass
            return {"questions": cleaned_qs}

        if mode == "exam-only":
            qs = data.get("questions")
            if not isinstance(qs, list):
                return {"questions": []}
            cleaned_qs: List[Dict[str, Any]] = []
            for q in qs:
                if not isinstance(q, dict):
                    continue
                qnum_raw = str(q.get("question_number", "")).strip()
                qnum_int = ResponseParser._to_int_safe(qnum_raw, 0)
                qnum = str(qnum_int) if qnum_int > 0 else qnum_raw
                ps = ResponseParser._to_int_safe(q.get("page_start"), 0)
                if ps <= 0 or not qnum:
                    continue
                nqs = None
                if q.get("next_question_start") is not None:
                    nqs = ResponseParser._to_int_safe(q.get("next_question_start"), 0)
                    if nqs <= 0:
                        nqs = None
                ans = (q.get("answer") or "").strip()
                expl = (q.get("explanation") or "").strip()
                bg = (q.get("background_knowledge") or q.get("background") or "").strip()
                wae = ResponseParser._norm_wrong_answer_explanations(q.get("wrong_answer_explanations"))
                item = {
                    "question_number": qnum,
                    "page_start": ps,
                    **({"next_question_start": nqs} if nqs is not None else {}),
                    **({"question_text": (q.get("question_text") or "").strip()} if isinstance(q.get("question_text"), str) else {}),
                    **({"answer": ans} if ans else {}),
                    **({"explanation": expl} if expl else {}),
                    **({"background_knowledge": bg} if bg else {}),
                    **({"wrong_answer_explanations": wae} if wae else {}),
                }
                cleaned_qs.append(item)
            try:
                cleaned_qs.sort(key=lambda x: (x.get("page_start", 0), ResponseParser._to_int_safe(x.get("question_number"), 0)))
            except Exception:
                pass
            return {"questions": cleaned_qs}

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
                # Normalize question_number: prefer digit-only string if present
                qnum_raw = str(q.get("question_number", "")).strip()
                qnum_int = ResponseParser._to_int_safe(qnum_raw, 0)
                qnum = str(qnum_int) if qnum_int > 0 else qnum_raw
                qtext = (q.get("question_text") or "").strip()
                ans = (q.get("answer") or "").strip()
                if not qnum or not qtext or ResponseParser._is_placeholder_value(ans):
                    continue
                expl = (q.get("explanation") or "").strip()
                if ResponseParser._is_placeholder_value(expl):
                    expl = ""
                # Normalize question_numbers_on_page to digit-only strings for lesson-centric
                qn_on_page_raw = q.get("question_numbers_on_page") or []
                qn_on_page: List[str] = []
                if isinstance(qn_on_page_raw, list):
                    for x in qn_on_page_raw:
                        xi = ResponseParser._to_int_safe(x, 0)
                        if xi > 0:
                            qn_on_page.append(str(xi))
                else:
                    qn_on_page = []

                # Parser-level filtering: drop questions with relevance_score < 80
                rs_val = max(0, min(ResponseParser._to_int_safe(q.get("relevance_score"), 0), 110))
                if rs_val < 80:
                    continue

                norm_qs.append({
                    "jokbo_filename": (q.get("jokbo_filename") or "").strip(),
                    "jokbo_page": ResponseParser._to_int_safe(q.get("jokbo_page"), 0),
                    "jokbo_end_page": ResponseParser._to_int_safe(q.get("jokbo_end_page"), 0) if q.get("jokbo_end_page") else None,
                    "next_question_start": ResponseParser._to_int_safe(q.get("next_question_start"), 0) if q.get("next_question_start") is not None else None,
                    "question_number": qnum,
                    "question_numbers_on_page": qn_on_page,
                    "question_text": qtext,
                    "answer": ans,
                    "explanation": expl,
                    "wrong_answer_explanations": ResponseParser._norm_wrong_answer_explanations(q.get("wrong_answer_explanations")),
                    "relevance_score": rs_val,
                    "relevance_reason": (q.get("relevance_reason") or q.get("reason") or "").strip(),
                })
            # Skip slides that ended up with no valid questions
            if norm_qs:
                cleaned_slides.append({
                    "lesson_page": lp,
                    "related_jokbo_questions": norm_qs,
                    **({"importance_score": ResponseParser._snap_score(s.get("importance_score"), allow_zero=True)} if s.get("importance_score") is not None else {}),
                    **({"key_concepts": s.get("key_concepts", [])} if isinstance(s.get("key_concepts"), list) else {}),
                })
        cleaned_slides.sort(key=lambda x: x.get("lesson_page", 0))
        try:
            total_q = sum(len(s.get("related_jokbo_questions", [])) for s in cleaned_slides)
            logger.info(f"Sanitized lesson response: {len(cleaned_slides)} slides, {total_q} linked questions")
        except Exception:
            pass
        return {"related_slides": cleaned_slides}

    @staticmethod
    def _parse_partial_partialjokbo(response_text: str) -> Dict[str, Any]:
        """
        Very lightweight recovery for partial-jokbo mode: try to find an array of
        objects with keys like "questions" / "page_start" in the text.
        """
        try:
            # Prefer fenced JSON block
            cleaned = ResponseParser._preprocess_response_text(response_text)
            repaired = ResponseParser._repair_common_json_issues(cleaned)
            parsed = json.loads(repaired)
            if isinstance(parsed, dict) and isinstance(parsed.get("questions"), list):
                return parsed
        except Exception:
            pass
        # Minimal fallback
        return {"questions": []}

    # --------------------
    # Result quality checks
    # --------------------
    @staticmethod
    def is_result_suspicious(data: Dict[str, Any], mode: str) -> bool:
        """
        Heuristic checks to detect obviously bad or low-value responses
        (e.g., empty content or pervasive placeholders) to trigger retries.
        """
        try:
            if mode == "jokbo-centric":
                pages = data.get("jokbo_pages") or []
                if not pages:
                    return True
                total_q = 0
                good_q = 0
                with_slides = 0
                for p in pages:
                    for q in (p.get("questions") or []):
                        total_q += 1
                        ans = (q.get("answer") or "").strip()
                        expl = (q.get("explanation") or "").strip()
                        wae = q.get("wrong_answer_explanations") or {}
                        slides = q.get("related_lesson_slides") or []
                        # Consider "good" if we have an answer and at least one of explanation/wae
                        if ans and (expl or (isinstance(wae, dict) and len(wae) > 0)):
                            good_q += 1
                        if isinstance(slides, list) and len(slides) > 0:
                            with_slides += 1
                if total_q == 0:
                    return True
                # If fewer than 25% questions look complete, or no slides linked, suspicious
                if good_q / max(1, total_q) < 0.25:
                    return True
                if with_slides == 0:
                    return True
                return False
            elif mode == "partial-jokbo":
                qs = data.get("questions") or []
                if not isinstance(qs, list) or len(qs) == 0:
                    return True
                total = len(qs)
                valid_ps = 0
                non_placeholder_expl = 0
                pages = []
                for q in qs:
                    try:
                        ps = int((q or {}).get("page_start") or 0)
                    except Exception:
                        ps = 0
                    if ps > 0:
                        valid_ps += 1
                    expl = (q or {}).get("explanation")
                    if isinstance(expl, str) and expl.strip() and not ResponseParser._is_placeholder_value(expl):
                        non_placeholder_expl += 1
                    try:
                        pages.append(int((q or {}).get("page_start") or 0))
                    except Exception:
                        pages.append(0)
                if valid_ps == 0:
                    return True
                # Heuristic: if fewer than half have valid page_start, suspicious
                if valid_ps / max(1, total) < 0.5:
                    return True
                # If all page_start are identical (>=3 items) and no next_question_start provided, likely low-quality
                try:
                    unique_pages = {p for p in pages if p > 0}
                    if total >= 3 and len(unique_pages) == 1:
                        any_nqs = any((q or {}).get("next_question_start") for q in qs)
                        if not any_nqs:
                            return True
                except Exception:
                    pass
                # Require at least one non-placeholder explanation across the set
                if non_placeholder_expl == 0:
                    return True
                return False
            elif mode == "exam-only":
                qs = data.get("questions") or []
                if not isinstance(qs, list) or len(qs) == 0:
                    return True
                total = len(qs)
                valid_ps = 0
                have_expl = 0
                for q in qs:
                    try:
                        ps = int((q or {}).get("page_start") or 0)
                    except Exception:
                        ps = 0
                    if ps > 0:
                        valid_ps += 1
                    if isinstance((q or {}).get("explanation"), str) and (q or {}).get("explanation").strip():
                        have_expl += 1
                if valid_ps == 0:
                    return True
                if have_expl == 0:
                    return True
                return False
            else:
                slides = data.get("related_slides") or []
                if not slides:
                    return True
                total_q = 0
                good_q = 0
                for s in slides:
                    for q in (s.get("related_jokbo_questions") or []):
                        total_q += 1
                        ans = (q.get("answer") or "").strip()
                        expl = (q.get("explanation") or "").strip()
                        wae = q.get("wrong_answer_explanations") or {}
                        if ans and (expl or (isinstance(wae, dict) and len(wae) > 0)):
                            good_q += 1
                if total_q == 0:
                    return True
                if good_q / max(1, total_q) < 0.25:
                    return True
                return False
        except Exception:
            # On any error, err on the side of retry
            return True
