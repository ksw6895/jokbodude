import pymupdf as fitz
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak, Image
from reportlab.lib.units import inch
import io
from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path
import tempfile
import os
from datetime import datetime
import threading
from validators import PDFValidator
import unicodedata
import re
from pdf_processor.pdf.operations import PDFOperations

class PDFCreator:
    def __init__(self):
        self.temp_files = []
        self.jokbo_pdfs = {}  # Cache for opened jokbo PDFs
        self.pdf_lock = threading.Lock()  # Thread-safe lock for PDF cache
        self.debug_log_path = Path("output/debug/pdf_creator_debug.log")
        self.debug_log_path.parent.mkdir(parents=True, exist_ok=True)
        self._lesson_dir_cache = {}
        self._jokbo_dir_cache = {}

    # ---------- Text / Filename utilities ----------
    @staticmethod
    def _normalize_korean(text: str) -> str:
        """Normalize text to NFC (모아쓰기) so Hangul renders correctly in PDF."""
        try:
            return unicodedata.normalize('NFC', text or '')
        except Exception:
            return text or ''

    @staticmethod
    def _insert_soft_breaks(token: str, every: int = 24) -> str:
        """Insert zero-width spaces into very long tokens to enable wrapping.

        - Adds zero-width spaces after common separators ("_", "-", ")", "]")
        - If token has no spaces/separators and is long, inserts ZWSP every N chars
        """
        if not token:
            return token
        # Add soft breaks after separators
        token = token.replace('_', '_\u200b').replace('-', '-\u200b')
        token = token.replace(')', ')\u200b').replace(']', ']\u200b')
        # If still a single long run, inject ZWSP periodically
        parts = re.split(r"(\s+|[_\-\)\]])", token)
        rebuilt = []
        for part in parts:
            if not part:
                continue
            if re.fullmatch(r"\s+|[_\-\)\]]", part):
                rebuilt.append(part)
                continue
            if len(part) > every:
                chunks = [part[i:i+every] for i in range(0, len(part), every)]
                rebuilt.append('\u200b'.join(chunks))
            else:
                rebuilt.append(part)
        return ''.join(rebuilt)

    def _format_display_filename(self, name: str) -> str:
        """Normalize Hangul and make long filenames wrap-friendly for PDF text boxes."""
        normalized = self._normalize_korean(name)
        return self._insert_soft_breaks(normalized)

    @staticmethod
    def _safe_int(value, default: int = 0) -> int:
        """Coerce common stringy numbers like "12", "p12", "12-13" to an int (first number)."""
        try:
            if isinstance(value, int):
                return value
            if isinstance(value, float):
                return int(value)
            s = str(value)
            m = re.search(r"(\d+)", s)
            return int(m.group(1)) if m else default
        except Exception:
            return default

    # ---------- File resolution helpers ----------
    def _resolve_jokbo_path(self, jokbo_dir: str, jokbo_filename: str) -> Path:
        """Resolve a jokbo file robustly, stripping common AI-added prefixes.

        Matching strategy (in order):
        1) Direct path exists
        2) Exact sanitized filename match
        3) Prefix-stripped sanitized filename exact match (strip: 족보/jokbo/exam/시험/기출/중간/기말)
        4) Exact sanitized stem match (unique only)
        5) Prefix-stripped sanitized stem exact match (unique only)

        If no match is found or ambiguous, return the direct path (likely non-existent)
        so caller can skip rather than insert a wrong page.
        """
        base_dir = Path(jokbo_dir)
        direct = base_dir / (jokbo_filename or '')
        if direct.exists():
            return direct

        # Build directory cache once per directory
        cache_key = str(base_dir.resolve())
        if cache_key not in self._jokbo_dir_cache:
            try:
                files = list(base_dir.glob('*.pdf'))
            except Exception:
                files = []
            mapping_full = {}
            mapping_full_stripped = {}
            mapping_stem = {}
            mapping_stem_stripped = {}
            for p in files:
                name_norm = self._normalize_korean(p.name)
                name_key = re.sub(r"[\s_\-]+", "", name_norm).lower()
                mapping_full.setdefault(name_key, []).append(p)
                # Stem without extension
                stem_norm = self._normalize_korean(p.stem)
                stem_key = re.sub(r"[\s_\-]+", "", stem_norm).lower()
                mapping_stem.setdefault(stem_key, []).append(p)
                # Prefix-stripped variants
                try:
                    stripped_full_norm = re.sub(r"^(족보|jokbo|exam|시험|기출|중간|기말)[\s_\-]+", "", name_norm, flags=re.IGNORECASE)
                except Exception:
                    stripped_full_norm = name_norm
                full_stripped_key = re.sub(r"[\s_\-]+", "", stripped_full_norm).lower()
                mapping_full_stripped.setdefault(full_stripped_key, []).append(p)
                try:
                    stripped_stem_norm = re.sub(r"^(족보|jokbo|exam|시험|기출|중간|기말)[\s_\-]+", "", stem_norm, flags=re.IGNORECASE)
                except Exception:
                    stripped_stem_norm = stem_norm
                stem_stripped_key = re.sub(r"[\s_\-]+", "", stripped_stem_norm).lower()
                mapping_stem_stripped.setdefault(stem_stripped_key, []).append(p)
            self._jokbo_dir_cache[cache_key] = {
                'full': mapping_full,
                'full_stripped': mapping_full_stripped,
                'stem': mapping_stem,
                'stem_stripped': mapping_stem_stripped,
            }

        cache = self._jokbo_dir_cache.get(cache_key, {})
        mapping_full = cache.get('full', {})
        mapping_full_stripped = cache.get('full_stripped', {})
        mapping_stem = cache.get('stem', {})
        mapping_stem_stripped = cache.get('stem_stripped', {})

        # Helpers to sanitize for matching
        def _keyify(name: str) -> str:
            n = self._normalize_korean(name or '')
            return re.sub(r"[\s_\-]+", "", n).lower()

        def _strip_prefix(name: str) -> str:
            return re.sub(r"^(족보|jokbo|exam|시험|기출|중간|기말)[\s_\-]+", "", (name or ''), flags=re.IGNORECASE)

        # Prepare candidate keys (full + stem)
        needle_full = _keyify(jokbo_filename)
        needle_full_stripped = _keyify(_strip_prefix(jokbo_filename))
        needle_stem = _keyify(Path(jokbo_filename or '').stem)
        needle_stem_stripped = _keyify(Path(_strip_prefix(jokbo_filename) or '').stem)

        # 2) Exact sanitized filename match
        candidates = mapping_full.get(needle_full) or []
        if len(candidates) == 1 and candidates[0].exists():
            self.log_debug(f"Matched jokbo file by exact full name: '{jokbo_filename}' -> '{candidates[0].name}'")
            return candidates[0]

        # 3) Prefix-stripped exact filename match
        if needle_full_stripped and needle_full_stripped != needle_full:
            candidates = mapping_full_stripped.get(needle_full_stripped) or []
            if len(candidates) == 1 and candidates[0].exists():
                self.log_debug(f"Matched by prefix-stripped jokbo name: '{jokbo_filename}' -> '{candidates[0].name}'")
                return candidates[0]

        # 4) Exact sanitized stem match (unique)
        if needle_stem:
            candidates = mapping_stem.get(needle_stem) or []
            if len(candidates) == 1 and candidates[0].exists():
                self.log_debug(f"Matched jokbo by exact stem: '{jokbo_filename}' -> '{candidates[0].name}'")
                return candidates[0]

        # 5) Prefix-stripped sanitized stem match (unique)
        if needle_stem_stripped and needle_stem_stripped != needle_stem:
            candidates = mapping_stem_stripped.get(needle_stem_stripped) or []
            if len(candidates) == 1 and candidates[0].exists():
                self.log_debug(f"Matched jokbo by prefix-stripped stem: '{jokbo_filename}' -> '{candidates[0].name}'")
                return candidates[0]

        # Not found or ambiguous — avoid fuzzy matching
        self.log_debug(f"Jokbo file not found or ambiguous: '{jokbo_filename}' in {jokbo_dir}")
        return direct
    def _resolve_lesson_path(self, lesson_dir: str, lesson_filename: str) -> Path:
        """Resolve a lesson file in directory with strict, deterministic matching.

        Matching strategy (in order):
        1) Direct path exists
        2) Exact sanitized filename match (unique only)
        3) Prefix-stripped sanitized filename exact match (strip: 강의자료/강의/lesson/lecture; unique only)
        4) Exact sanitized stem match (unique only)
        5) Prefix-stripped sanitized stem exact match (unique only)

        We intentionally avoid fuzzy "contains" matching to prevent selecting
        the wrong file when multiple similarly named PDFs exist. If no match is
        found, we return the direct path (likely non-existent) and let callers
        skip insertion rather than insert the wrong slide.
        """
        base_dir = Path(lesson_dir)
        direct = base_dir / (lesson_filename or '')
        if direct.exists():
            return direct

        # Build directory cache once per directory
        cache_key = str(base_dir.resolve())
        if cache_key not in self._lesson_dir_cache:
            try:
                files = list(base_dir.glob('*.pdf'))
            except Exception:
                files = []
            mapping_full = {}
            mapping_full_stripped = {}
            mapping_stem = {}
            mapping_stem_stripped = {}
            for p in files:
                name_norm = self._normalize_korean(p.name)
                name_key = re.sub(r"[\s_\-]+", "", name_norm).lower()
                mapping_full.setdefault(name_key, []).append(p)
                # Stem without extension
                stem_norm = self._normalize_korean(p.stem)
                stem_key = re.sub(r"[\s_\-]+", "", stem_norm).lower()
                mapping_stem.setdefault(stem_key, []).append(p)
                # Prefix-stripped variants
                try:
                    stripped_full_norm = re.sub(r"^(강의자료|강의|lesson|lecture)[\s_\-]+", "", name_norm, flags=re.IGNORECASE)
                except Exception:
                    stripped_full_norm = name_norm
                full_stripped_key = re.sub(r"[\s_\-]+", "", stripped_full_norm).lower()
                mapping_full_stripped.setdefault(full_stripped_key, []).append(p)
                try:
                    stripped_stem_norm = re.sub(r"^(강의자료|강의|lesson|lecture)[\s_\-]+", "", stem_norm, flags=re.IGNORECASE)
                except Exception:
                    stripped_stem_norm = stem_norm
                stem_stripped_key = re.sub(r"[\s_\-]+", "", stripped_stem_norm).lower()
                mapping_stem_stripped.setdefault(stem_stripped_key, []).append(p)
            self._lesson_dir_cache[cache_key] = {
                'full': mapping_full,
                'full_stripped': mapping_full_stripped,
                'stem': mapping_stem,
                'stem_stripped': mapping_stem_stripped,
            }

        cache = self._lesson_dir_cache.get(cache_key, {})
        mapping_full = cache.get('full', {})
        mapping_full_stripped = cache.get('full_stripped', {})
        mapping_stem = cache.get('stem', {})
        mapping_stem_stripped = cache.get('stem_stripped', {})

        # Helpers to sanitize for matching
        def _keyify(name: str) -> str:
            n = self._normalize_korean(name or '')
            return re.sub(r"[\s_\-]+", "", n).lower()

        def _strip_prefix(name: str) -> str:
            return re.sub(r"^(강의자료|강의|lesson|lecture)[\s_\-]+", "", (name or ''), flags=re.IGNORECASE)

        # Prepare candidate keys (full + stem)
        needle_full = _keyify(lesson_filename)
        needle_full_stripped = _keyify(_strip_prefix(lesson_filename))
        needle_stem = _keyify(Path(lesson_filename or '').stem)
        needle_stem_stripped = _keyify(Path(_strip_prefix(lesson_filename) or '').stem)

        # 2) Exact sanitized filename match
        candidates = mapping_full.get(needle_full) or []
        if len(candidates) == 1 and candidates[0].exists():
            self.log_debug(f"Matched lesson file by exact full name: '{lesson_filename}' -> '{candidates[0].name}'")
            return candidates[0]

        # 3) Prefix-stripped exact filename match
        if needle_full_stripped and needle_full_stripped != needle_full:
            candidates = mapping_full_stripped.get(needle_full_stripped) or []
            if len(candidates) == 1 and candidates[0].exists():
                self.log_debug(f"Matched by prefix-stripped full name: '{lesson_filename}' -> '{candidates[0].name}'")
                return candidates[0]

        # 4) Exact sanitized stem match (unique)
        if needle_stem:
            candidates = mapping_stem.get(needle_stem) or []
            if len(candidates) == 1 and candidates[0].exists():
                self.log_debug(f"Matched lesson file by exact stem: '{lesson_filename}' -> '{candidates[0].name}'")
                return candidates[0]

        # 5) Prefix-stripped sanitized stem match (unique)
        if needle_stem_stripped and needle_stem_stripped != needle_stem:
            candidates = mapping_stem_stripped.get(needle_stem_stripped) or []
            if len(candidates) == 1 and candidates[0].exists():
                self.log_debug(f"Matched by prefix-stripped stem: '{lesson_filename}' -> '{candidates[0].name}'")
                return candidates[0]

        # Not found or ambiguous — avoid fuzzy contains to prevent wrong pages
        self.log_debug(f"Lesson file not found or ambiguous: '{lesson_filename}' in {lesson_dir}")
        return direct
        
    def _register_font(self, page: fitz.Page) -> str:
        """Register a Unicode-capable font on the given page and return its name.
        Tries CJK-capable font for Korean; falls back to Helvetica if unavailable.
        """
        try:
            font = fitz.Font("cjk")
            fontname = "F1"
            page.insert_font(fontname=fontname, fontbuffer=font.buffer)
            return fontname
        except Exception as e:
            # Fallback to base font (may not render CJK but avoids crash)
            self.log_debug(f"CJK font registration failed: {e}")
            try:
                page.insert_font(fontname="helv")
                return "helv"
            except Exception:
                return "helv"
        
    def __del__(self):
        for temp_file in self.temp_files:
            if os.path.exists(temp_file):
                os.remove(temp_file)
        # Close all cached PDFs
        for pdf in self.jokbo_pdfs.values():
            pdf.close()
    
    def log_debug(self, message: str):
        """Write debug message to file"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        with open(self.debug_log_path, 'a', encoding='utf-8') as f:
            f.write(f"[{timestamp}] {message}\n")
            f.flush()  # Ensure the message is written immediately
    
    def get_jokbo_pdf(self, jokbo_path: str) -> fitz.Document:
        """Get or open a jokbo PDF (thread-safe cached)"""
        with self.pdf_lock:
            if jokbo_path not in self.jokbo_pdfs:
                self.jokbo_pdfs[jokbo_path] = fitz.open(jokbo_path)
            return self.jokbo_pdfs[jokbo_path]
    
    def extract_jokbo_question(self, jokbo_filename: str, jokbo_page: int, question_number, question_text: str, jokbo_dir: str = "jokbo", jokbo_end_page: int = None, is_last_question_on_page: bool = False, question_numbers_on_page = None, computed_next_start_page: Optional[int] = None, computed_next_question_number: Optional[object] = None):
        """Extract the problem region from jokbo PDF.

        - Crops the start page from the detected question-number marker to page bottom.
        - If the question spans multiple pages (jokbo_end_page provided), inserts
          full middle pages and crops the end page from top to the next marker.
        - If it's the last question on the page, includes a cropped slice from
          the next page (top to next marker) when available.
        - Falls back to inserting full pages when markers cannot be found.
        """
        self.log_debug(f"extract_jokbo_question: Q={question_number}, start_page={jokbo_page}, initial_end={jokbo_end_page}")
        self.log_debug(f"  is_last_question_on_page={is_last_question_on_page}")
        self.log_debug(f"  question_numbers_on_page(raw)={question_numbers_on_page}")
        
        jokbo_path = self._resolve_jokbo_path(jokbo_dir, jokbo_filename)
        if not jokbo_path.exists():
            print(f"Warning: Jokbo file not found: {jokbo_path}")
            self.log_debug(f"  ERROR: Jokbo file not found: {jokbo_path}")
            return None
            
        # Check page validity
        jokbo_pdf = self.get_jokbo_pdf(str(jokbo_path))  # get_jokbo_pdf already handles locking
        pdf_page_count = len(jokbo_pdf)
        
        if jokbo_page > pdf_page_count or jokbo_page < 1:
            print(f"Warning: Page {jokbo_page} does not exist in {jokbo_filename}")
            return None
        
        # Determine the end page
        decision_reason = ""
        explicit_end = jokbo_end_page
        if jokbo_end_page is None:
            # Normalize question number and page list to digits for robust matching
            qnum_int = self._safe_int(question_number, 0)
            qnum_str = str(qnum_int) if qnum_int > 0 else str(question_number)
            self.log_debug(f"  question_num_str(norm): {repr(qnum_str)}")

            page_qnums: List[str] = []
            try:
                if isinstance(question_numbers_on_page, list):
                    page_qnums = [str(self._safe_int(x, 0)) for x in question_numbers_on_page if self._safe_int(x, 0) > 0]
            except Exception:
                page_qnums = []

            # First, try to use question_numbers_on_page for more accurate detection
            if page_qnums and qnum_str in page_qnums:
                self.log_debug(f"  Found in question_numbers_on_page (normalized): {page_qnums}")
                # Check if current question is the last one on the page
                try:
                    last_q = str(max([int(x) for x in page_qnums]))
                except Exception:
                    last_q = page_qnums[-1]
                if qnum_str == last_q and jokbo_page < pdf_page_count:
                    # This is the last question on the page, include next page
                    jokbo_end_page = jokbo_page + 1
                    print(f"  DEBUG: Question {question_number} is last in {page_qnums} on page {jokbo_page}")
                    print(f"  DEBUG: Including next page {jokbo_end_page} (PDF has {pdf_page_count} pages)")
                    decision_reason = "last_on_page_numbers"
                    self.log_debug(f"  DECISION: last_on_page -> include next page {jokbo_end_page}")
                else:
                    jokbo_end_page = jokbo_page
                    print(f"  DEBUG: Question {question_number} on page {jokbo_page}, not last or no next page (page list: {page_qnums})")
                    decision_reason = "not_last_by_numbers"
                    self.log_debug(f"  DECISION: not_last_by_numbers -> use single page {jokbo_end_page}")
            # Fallback to is_last_question_on_page flag
            elif is_last_question_on_page and jokbo_page < pdf_page_count:
                # Automatically include the next page
                jokbo_end_page = jokbo_page + 1
                print(f"  Question {question_number} is last on page {jokbo_page}, including next page")
                decision_reason = "last_flag"
                self.log_debug(f"  DECISION: last_flag -> include next page {jokbo_end_page}")
            else:
                jokbo_end_page = jokbo_page
                decision_reason = "default_single"
                self.log_debug(f"  DECISION: default_single -> use single page {jokbo_end_page}")
        
        # Validate end page
        if jokbo_end_page > pdf_page_count or jokbo_end_page < jokbo_page:
            print(f"Warning: Invalid end page {jokbo_end_page}, using single page")
            self.log_debug(f"  VALIDATION: clamped end_page from {jokbo_end_page} to {jokbo_page}")
            jokbo_end_page = jokbo_page
        
        # Start constructing a cropped document
        self.log_debug(
            f"  Final extraction (cropped): start={jokbo_page}, end={jokbo_end_page}, explicit_end={explicit_end}, reason={decision_reason}, next_start={computed_next_start_page}, next_q={computed_next_question_number}"
        )
        question_doc = fitz.open()

        qnum_int = self._safe_int(question_number, 0)
        # 1) Crop start page from marker to bottom. Prefer computed next when same-page,
        #    otherwise just crop from current marker to page bottom.
        same_page_next = (
            isinstance(computed_next_start_page, int)
            and computed_next_start_page > 0
            and int(computed_next_start_page) == int(jokbo_page)
        )
        try:
            if same_page_next:
                cropped_start = PDFOperations.extract_question_region(
                    str(jokbo_path), int(jokbo_page), next_question_start=int(jokbo_page), question_number=qnum_int,
                    next_question_number_for_same_page=self._safe_int(computed_next_question_number, 0)
                )
            else:
                cropped_start = PDFOperations.extract_question_region(
                    str(jokbo_path), int(jokbo_page), next_question_start=None, question_number=qnum_int
                )
        except Exception as e:
            self.log_debug(f"  WARN: extract_question_region failed on start page {jokbo_page}: {e}")
            cropped_start = None

        jokbo_pdf = self.get_jokbo_pdf(str(jokbo_path))
        if cropped_start and Path(cropped_start).exists():
            try:
                with fitz.open(cropped_start) as tmp:
                    question_doc.insert_pdf(tmp)
                self.temp_files.append(cropped_start)
            except Exception as e:
                self.log_debug(f"  WARN: failed to insert cropped start page, falling back to full: {e}")
                question_doc.insert_pdf(jokbo_pdf, from_page=jokbo_page-1, to_page=jokbo_page-1)
        else:
            # Fallback to inserting the full start page
            question_doc.insert_pdf(jokbo_pdf, from_page=jokbo_page-1, to_page=jokbo_page-1)

        # 2) Handle multi-page questions with explicit end page
        if isinstance(jokbo_end_page, int) and jokbo_end_page and jokbo_end_page > jokbo_page:
            # Insert any full middle pages
            if jokbo_end_page - jokbo_page > 1:
                try:
                    question_doc.insert_pdf(
                        jokbo_pdf, from_page=jokbo_page, to_page=jokbo_end_page-2
                    )
                except Exception as e:
                    self.log_debug(f"  WARN: failed inserting middle pages {jokbo_page+1}..{jokbo_end_page-1}: {e}")
            # Crop the end page from top to next marker (qnum+1)
            end_crop_path = None
            try:
                target_next = self._safe_int(computed_next_question_number, 0) if computed_next_question_number is not None else (qnum_int + 1)
                end_crop_path = PDFOperations.extract_top_until_marker(
                    str(jokbo_path), int(jokbo_end_page), target_next
                )
            except Exception as e:
                self.log_debug(f"  WARN: extract_top_until_marker failed on end page {jokbo_end_page}: {e}")
                end_crop_path = None
            if end_crop_path and Path(end_crop_path).exists():
                try:
                    with fitz.open(end_crop_path) as tmp:
                        question_doc.insert_pdf(tmp)
                    self.temp_files.append(end_crop_path)
                except Exception as e:
                    self.log_debug(f"  WARN: failed to insert end-cropped page, using full: {e}")
                    question_doc.insert_pdf(jokbo_pdf, from_page=jokbo_end_page-1, to_page=jokbo_end_page-1)
            else:
                question_doc.insert_pdf(jokbo_pdf, from_page=jokbo_end_page-1, to_page=jokbo_end_page-1)
        # 3) Otherwise, prefer computed next-start; fallback to last-on-page heuristic
        elif isinstance(computed_next_start_page, int) and computed_next_start_page and computed_next_start_page > jokbo_page:
            # Insert any full middle pages up to next_start-1
            next_p = int(computed_next_start_page)
            if next_p - jokbo_page > 1:
                try:
                    question_doc.insert_pdf(jokbo_pdf, from_page=jokbo_page, to_page=next_p-2)
                except Exception as e:
                    self.log_debug(f"  WARN: failed inserting middle pages {jokbo_page+1}..{next_p-1}: {e}")
            # Then crop the next-start page from top until its marker
            next_crop_path = None
            try:
                target_next = self._safe_int(computed_next_question_number, 0)
                next_crop_path = PDFOperations.extract_top_until_marker(
                    str(jokbo_path), next_p, target_next if target_next > 0 else (qnum_int + 1)
                )
            except Exception as e:
                self.log_debug(f"  WARN: extract_top_until_marker failed on page {next_p}: {e}")
                next_crop_path = None
            if next_crop_path and Path(next_crop_path).exists():
                try:
                    with fitz.open(next_crop_path) as tmp:
                        question_doc.insert_pdf(tmp)
                    self.temp_files.append(next_crop_path)
                except Exception as e:
                    self.log_debug(f"  WARN: failed to insert next-start cropped page, using full next: {e}")
                    question_doc.insert_pdf(jokbo_pdf, from_page=next_p-1, to_page=next_p-1)
            else:
                question_doc.insert_pdf(jokbo_pdf, from_page=next_p-1, to_page=next_p-1)
        elif is_last_question_on_page and jokbo_page < pdf_page_count:
            next_crop_path = None
            try:
                next_crop_path = PDFOperations.extract_top_until_marker(
                    str(jokbo_path), int(jokbo_page) + 1, qnum_int + 1
                )
            except Exception as e:
                self.log_debug(f"  WARN: extract_top_until_marker failed on page {jokbo_page+1}: {e}")
                next_crop_path = None
            if next_crop_path and Path(next_crop_path).exists():
                try:
                    with fitz.open(next_crop_path) as tmp:
                        question_doc.insert_pdf(tmp)
                    self.temp_files.append(next_crop_path)
                except Exception as e:
                    self.log_debug(f"  WARN: failed to insert next-cropped page, using full next: {e}")
                    question_doc.insert_pdf(jokbo_pdf, from_page=jokbo_page, to_page=jokbo_page)
            else:
                # Conservative fallback: include full next page to avoid losing content
                question_doc.insert_pdf(jokbo_pdf, from_page=jokbo_page, to_page=jokbo_page)

        self.log_debug(f"  Cropped extraction pages={len(question_doc)}")
        return question_doc

    # -------------------------------
    # Next-boundary calculation utils
    # -------------------------------
    def _safe_qnum(self, v: object) -> int:
        try:
            return self._safe_int(v, 0)
        except Exception:
            return 0

    def _build_next_map_for_lesson(self, analysis_result: Dict[str, Any]) -> Dict[tuple, Tuple[Optional[int], Optional[object]]]:
        """Build mapping: (file, start_page, qnum) -> (next_start_page, next_qnum).

        Uses all questions across related_slides, grouped by jokbo file, ordered by
        (page asc, within-page order via question_numbers_on_page if available, else qnum asc).
        """
        items_by_file: Dict[str, list] = {}
        page_orders: Dict[tuple, list[int]] = {}
        explicit_next: Dict[tuple, Tuple[int, Optional[int]]] = {}
        for slide in analysis_result.get("related_slides", []) or []:
            for q in slide.get("related_jokbo_questions", []) or []:
                fn = str(q.get("jokbo_filename") or "")
                sp = self._safe_int(q.get("jokbo_page"), 0)
                qn = self._safe_qnum(q.get("question_number"))
                if not fn or sp <= 0 or qn <= 0:
                    continue
                items_by_file.setdefault(fn, []).append((sp, qn))
                # record a per-page order list once available
                qlist = q.get("question_numbers_on_page") or []
                if isinstance(qlist, list) and qlist:
                    key = (fn, sp)
                    if key not in page_orders:
                        try:
                            page_orders[key] = [self._safe_qnum(x) for x in qlist if self._safe_qnum(x) > 0]
                        except Exception:
                            pass
                # Prefer explicit next_question_start when provided by the model
                try:
                    ns = q.get("next_question_start")
                    if isinstance(ns, int) and ns > 0:
                        explicit_next[(fn, sp, qn)] = (int(ns), None)
                except Exception:
                    pass
        result: Dict[tuple, Tuple[Optional[int], Optional[object]]] = {}
        # Apply explicit next-page hints first and try to infer next question number
        for (fn, sp, qn), (ns, _) in list(explicit_next.items()):
            nq: Optional[int] = None
            try:
                if ns == sp:
                    order = page_orders.get((fn, sp)) or []
                    if qn in order:
                        idx = order.index(qn)
                        if idx + 1 < len(order):
                            nq = order[idx + 1]
                else:
                    order_ns = page_orders.get((fn, ns)) or []
                    if order_ns:
                        nq = order_ns[0]
            except Exception:
                pass
            result[(fn, sp, qn)] = (ns, nq)
        for fn, arr in items_by_file.items():
            # dedupe
            uniq = sorted(set(arr))
            # sort with within-page order preference
            def sort_key(t):
                sp, qn = t
                order = page_orders.get((fn, sp))
                if order and qn in order:
                    return (sp, order.index(qn))
                return (sp, qn)
            ordered = sorted(uniq, key=sort_key)
            for i, (sp, qn) in enumerate(ordered):
                ns, nq = (None, None)
                if i + 1 < len(ordered):
                    ns, nq = ordered[i + 1]
                # Do not overwrite explicit mapping
                if (fn, sp, qn) not in result:
                    result[(fn, sp, qn)] = (ns, nq)
        return result

    def _build_next_map_for_jokbo(self, analysis_result: Dict[str, Any]) -> Dict[tuple, Tuple[Optional[int], Optional[object]]]:
        """Build mapping: (file, start_page, qnum) -> (next_start_page, next_qnum) for jokbo-centric."""
        items_by_file: Dict[str, list] = {}
        page_orders: Dict[tuple, list[int]] = {}
        explicit_next: Dict[tuple, Tuple[int, Optional[int]]] = {}
        for p in analysis_result.get("jokbo_pages", []) or []:
            sp = self._safe_int(p.get("jokbo_page"), 0)
            for q in p.get("questions", []) or []:
                fn = str(q.get("jokbo_filename") or "__single__")
                qn = self._safe_qnum(q.get("question_number"))
                if not fn or sp <= 0 or qn <= 0:
                    continue
                items_by_file.setdefault(fn, []).append((sp, qn))
                qlist = q.get("question_numbers_on_page") or []
                if isinstance(qlist, list) and qlist:
                    key = (fn, sp)
                    if key not in page_orders:
                        try:
                            page_orders[key] = [self._safe_qnum(x) for x in qlist if self._safe_qnum(x) > 0]
                        except Exception:
                            pass
                # Prefer explicit next_question_start when provided by the model
                try:
                    ns = q.get("next_question_start")
                    if isinstance(ns, int) and ns > 0:
                        explicit_next[(fn, sp, qn)] = (int(ns), None)
                except Exception:
                    pass
        result: Dict[tuple, Tuple[Optional[int], Optional[object]]] = {}
        # Apply explicit next-page hints first with best-effort next-q inference
        for (fn, sp, qn), (ns, _) in list(explicit_next.items()):
            nq: Optional[int] = None
            try:
                if ns == sp:
                    order = page_orders.get((fn, sp)) or []
                    if qn in order:
                        idx = order.index(qn)
                        if idx + 1 < len(order):
                            nq = order[idx + 1]
                else:
                    order_ns = page_orders.get((fn, ns)) or []
                    if order_ns:
                        nq = order_ns[0]
            except Exception:
                pass
            result[(fn, sp, qn)] = (ns, nq)
        for fn, arr in items_by_file.items():
            uniq = sorted(set(arr))
            def sort_key(t):
                sp, qn = t
                order = page_orders.get((fn, sp))
                if order and qn in order:
                    return (sp, order.index(qn))
                return (sp, qn)
            ordered = sorted(uniq, key=sort_key)
            for i, (sp, qn) in enumerate(ordered):
                ns, nq = (None, None)
                if i + 1 < len(ordered):
                    ns, nq = ordered[i + 1]
                if (fn, sp, qn) not in result:
                    result[(fn, sp, qn)] = (ns, nq)
        return result
    
    def create_filtered_pdf(self, lesson_path: str, analysis_result: Dict[str, Any], output_path: str, jokbo_dir: str = "jokbo"):
        """Create new PDF for lesson-centric mode.
        Ensures all slides of the original lesson are present, marking slides without matches.
        """
        
        if "error" in analysis_result:
            print(f"Cannot create PDF due to analysis error: {analysis_result['error']}")
            return
        
        doc = fitz.open()
        lesson_pdf = fitz.open(lesson_path)
        lesson_basename = Path(lesson_path).name
        display_lesson_basename = self._format_display_filename(lesson_basename)

        # Build an index of related questions by lesson page
        related_by_page: Dict[int, List[Dict[str, Any]]] = {}
        importance_by_page: Dict[int, Any] = {}
        for slide_info in analysis_result.get("related_slides", []):
            page_num = int(slide_info.get("lesson_page", 0))
            if page_num <= 0:
                continue
            related_by_page.setdefault(page_num, [])
            if "importance_score" in slide_info:
                importance_by_page[page_num] = slide_info.get("importance_score")
            for q in slide_info.get("related_jokbo_questions", []) or []:
                related_by_page[page_num].append(q)

        # Build next-boundary map across all questions from analysis
        try:
            next_map = self._build_next_map_for_lesson(analysis_result)
        except Exception:
            next_map = {}

        total_pages = len(lesson_pdf)
        # Iterate through every slide to ensure none are skipped
        for page_num in range(1, total_pages + 1):
            # Always insert the lesson slide
            doc.insert_pdf(lesson_pdf, from_page=page_num-1, to_page=page_num-1)

            # If there are related questions, append them after the slide
            for question in related_by_page.get(page_num, []):
                # Determine if this is the last question on the page (numeric compare)
                is_last_question = False
                question_numbers = question.get("question_numbers_on_page", [])
                try:
                    qnum_int = self._safe_int(question.get("question_number"))
                    page_qnums = [self._safe_int(x) for x in (question_numbers or []) if self._safe_int(x) > 0]
                    last_q = max(page_qnums) if page_qnums else None
                    if last_q is not None and qnum_int > 0 and qnum_int == last_q:
                        is_last_question = True
                except Exception:
                    pass

                # Extract and insert the question from jokbo (handles next-page inclusion)
                fn = str(question.get("jokbo_filename") or "")
                sp = int(question.get("jokbo_page", 0))
                qn_norm = self._safe_int(question.get("question_number"), 0)
                ns, nq = next_map.get((fn, sp, qn_norm), (None, None))
                question_doc = self.extract_jokbo_question(
                    fn,
                    sp,
                    question.get("question_number"),
                    question.get("question_text", ""),
                    jokbo_dir,
                    question.get("jokbo_end_page"),
                    is_last_question,
                    question_numbers,
                    computed_next_start_page=ns,
                    computed_next_question_number=nq,
                )
                if question_doc:
                    doc.insert_pdf(question_doc)
                    question_doc.close()

                # Add explanation page
                explanation_page = doc.new_page()
                text_content = f"=== 문제 {question.get('question_number')} 해설 ===\n\n"
                text_content += f"※ 앞 페이지의 문제 {question.get('question_number')}번을 참고하세요\n\n"
                src_name = self._format_display_filename(str(question.get('jokbo_filename') or ''))
                text_content += f"[출처: {src_name} - {question.get('jokbo_page')}페이지]\n\n"
                # Slide-level importance score (if available)
                if page_num in importance_by_page:
                    text_content += f"관련성 점수(슬라이드): {importance_by_page[page_num]} / 110\n\n"
                text_content += f"정답: {question.get('answer')}\n\n"
                if question.get('explanation'):
                    text_content += f"해설:\n{question['explanation']}\n\n"
                if question.get('wrong_answer_explanations'):
                    text_content += "오답 설명:\n"
                    for choice, explanation in question['wrong_answer_explanations'].items():
                        text_content += f"  {choice}: {explanation}\n"
                    text_content += "\n"
                if question.get('relevance_reason'):
                    text_content += f"관련성:\n{question['relevance_reason']}\n\n"
                text_content += f"관련 강의 페이지: {page_num} ({display_lesson_basename})\n\n"
                text_content += f"참고: 이 문제는 강의자료 {page_num}페이지의 내용과 관련이 있습니다."

                fontname = self._register_font(explanation_page)
                text_rect = fitz.Rect(50, 50, explanation_page.rect.width - 50, explanation_page.rect.height - 50)
                explanation_page.insert_textbox(
                    text_rect,
                    text_content,
                    fontsize=11,
                    fontname=fontname,
                    align=fitz.TEXT_ALIGN_LEFT
                )
        
        if analysis_result.get("summary"):
            summary_page = doc.new_page()
            summary = analysis_result["summary"]
            
            summary_text = "=== 학습 요약 ===\n\n"
            summary_text += f"관련 슬라이드 수: {summary['total_related_slides']}\n"
            summary_text += f"총 관련 문제 수: {summary.get('total_questions', 'N/A')}\n\n"
            summary_text += f"주요 주제: {', '.join(summary['key_topics'])}\n\n"
            if 'study_recommendations' in summary:
                summary_text += f"학습 권장사항:\n{summary['study_recommendations']}"
            
            # Use CJK font for summary page
            fontname = self._register_font(summary_page)
            
            text_rect = fitz.Rect(50, 50, summary_page.rect.width - 50, summary_page.rect.height - 50)
            summary_page.insert_textbox(
                text_rect,
                summary_text,
                fontsize=12,
                fontname=fontname,
                align=fitz.TEXT_ALIGN_LEFT
            )
        
        doc.save(output_path)
        doc.close()
        lesson_pdf.close()
        
        print(f"Filtered PDF created: {output_path}")
    
    # Backward-compatible alias used by tasks.py
    def create_lesson_centric_pdf(self, lesson_path: str, analysis_result: Dict[str, Any], output_path: str, jokbo_dir: str = "jokbo"):
        return self.create_filtered_pdf(lesson_path, analysis_result, output_path, jokbo_dir)
    
    def extract_lesson_slide(self, lesson_filename: str, lesson_page: int, lesson_dir: str = "lesson") -> fitz.Document:
        """Extract a single page from lesson PDF"""
        lesson_path = self._resolve_lesson_path(lesson_dir, lesson_filename)
        if not lesson_path.exists():
            print(f"Warning: Lesson file not found: {lesson_path}")
            self.log_debug(f"WARN extract_lesson_slide: missing file {lesson_path}")
            return None
            
        lesson_pdf = fitz.open(str(lesson_path))
        # Defensive: coerce lesson_page to int
        lesson_page = self._safe_int(lesson_page, 0)
        if lesson_page > len(lesson_pdf) or lesson_page < 1:
            print(f"Warning: Page {lesson_page} does not exist in {lesson_filename}")
            self.log_debug(f"WARN extract_lesson_slide: invalid page {lesson_page} for {lesson_path.name} (max {len(lesson_pdf)})")
            lesson_pdf.close()
            return None
        
        # Extract the page
        slide_doc = fitz.open()
        slide_doc.insert_pdf(lesson_pdf, from_page=lesson_page-1, to_page=lesson_page-1)
        lesson_pdf.close()
        
        return slide_doc
    
    def create_jokbo_centric_pdf(self, jokbo_path: str, analysis_result: Dict[str, Any], output_path: str, lesson_dir: str = "lesson"):
        """Create new PDF with jokbo questions as primary content, followed by related lesson slides"""
        
        if "error" in analysis_result:
            print(f"Cannot create PDF due to analysis error: {analysis_result['error']}")
            return
        
        # Debug: 분석 결과 확인
        jokbo_pages = analysis_result.get("jokbo_pages", [])
        total_questions = sum(len(page.get("questions", [])) for page in jokbo_pages)
        print(f"  PDF 생성 시작: {len(jokbo_pages)}개 페이지, {total_questions}개 문제")
        
        if not jokbo_pages:
            # Produce a minimal PDF with a friendly message so downstream
            # storage does not fail when there are no matches.
            print(f"  경고: jokbo_pages가 비어있습니다. PDF를 생성할 내용이 없습니다.")
            doc = fitz.open()
            page = doc.new_page()
            fontname = self._register_font(page)
            msg = (
                "분석 결과 없음\n\n"
                "선택한 족보와 강의자료 조합에서 연결된 문제가 없습니다.\n"
                "입력 파일, 모델 설정, 또는 분석 기준을 확인하세요."
            )
            rect = fitz.Rect(72, 72, page.rect.width - 72, page.rect.height - 72)
            page.insert_textbox(rect, msg, fontsize=14, fontname=fontname, align=fitz.TEXT_ALIGN_LEFT)
            doc.save(output_path)
            doc.close()
            print(f"Placeholder PDF created: {output_path}")
            return
        
        doc = fitz.open()
        jokbo_filename = Path(jokbo_path).name
        display_jokbo_filename = self._format_display_filename(jokbo_filename)
        
        # Get PDF page count thread-safely
        jokbo_pdf = self.get_jokbo_pdf(jokbo_path)  # get_jokbo_pdf already handles locking
        jokbo_page_count = len(jokbo_pdf)
        
        # Collect all questions with their page info
        all_questions = []
        for page_info in analysis_result.get("jokbo_pages", []):
            jokbo_page_num = page_info["jokbo_page"]
            if jokbo_page_num <= jokbo_page_count:
                for question in page_info.get("questions", []):
                    if question.get("related_lesson_slides", []):
                        # Add page number to question for later use
                        question["_jokbo_page_num"] = jokbo_page_num
                        all_questions.append(question)

        # Build next-boundary map across this jokbo
        try:
            next_map = self._build_next_map_for_jokbo(analysis_result)
        except Exception:
            next_map = {}
        
        # Sort questions by question number
        def get_question_number_for_sort(question):
            """Extract numeric value from question number for sorting"""
            question_num = question.get("question_number", "Unknown")
            if question_num == "Unknown" or question_num == "번호없음":
                return float('inf')  # Put unknown numbers at the end
            try:
                # Extract numeric part from strings like "21", "21번", etc.
                import re
                match = re.search(r'(\d+)', str(question_num))
                if match:
                    return int(match.group(1))
                return float('inf')
            except (ValueError, AttributeError) as e:
                print(f"Error parsing question number '{question_num}': {e}")
                return float('inf')
        
        all_questions.sort(key=get_question_number_for_sort)
        
        # Show sorted order
        print(f"  문제 번호 순서대로 정렬됨: {[q.get('question_number', 'Unknown') for q in all_questions[:10]]}{'...' if len(all_questions) > 10 else ''}")
        
        # Track which questions have been processed to avoid duplicates
        processed_questions = set()
        
        # Process questions in sorted order
        for question in all_questions:
            question_num = question.get("question_number", "Unknown")
            jokbo_page_num = question["_jokbo_page_num"]
            related_slides = question.get("related_lesson_slides", [])
            
            # Only process questions that haven't been processed
            if question_num not in processed_questions:
                processed_questions.add(question_num)
                
                # Determine if this is the last question on the page (numeric compare)
                is_last_question = False
                question_numbers = question.get("question_numbers_on_page", [])
                self.log_debug(f"Processing Q{question_num}: question_numbers = {question_numbers}")
                try:
                    qnum_int = self._safe_int(question_num)
                    page_qnums = [self._safe_int(x) for x in (question_numbers or []) if self._safe_int(x) > 0]
                    last_q = max(page_qnums) if page_qnums else None
                    if last_q is not None and qnum_int > 0 and qnum_int == last_q:
                        is_last_question = True
                        print(f"DEBUG: Question {question_num} is last on page {jokbo_page_num}, questions: {page_qnums}")
                        self.log_debug(f"  Q{question_num} is LAST on page {jokbo_page_num}")
                    else:
                        self.log_debug(f"  Q{question_num} is NOT last on page {jokbo_page_num}")
                except Exception:
                    self.log_debug(f"  WARN: could not compute last-on-page for Q{question_num}")
                
                # Extract the question pages (handles multi-page questions)
                before_pages = len(doc)
                key1 = (jokbo_filename, int(jokbo_page_num), self._safe_int(question_num, 0))
                key2 = ("__single__", int(jokbo_page_num), self._safe_int(question_num, 0))
                ns, nq = next_map.get(key1, next_map.get(key2, (None, None)))
                question_doc = self.extract_jokbo_question(
                    jokbo_filename,
                    jokbo_page_num,
                    question_num,
                    question.get("question_text", ""),
                    str(Path(jokbo_path).parent),
                    # Respect jokbo_end_page if present (multi-page questions)
                    question.get("jokbo_end_page"),
                    is_last_question,
                    question_numbers,
                    computed_next_start_page=ns,
                    computed_next_question_number=nq,
                )
                if question_doc:
                    try:
                        self.log_debug(
                            f"insert_question: Q={question_num}, src_page={jokbo_page_num},"
                            f" doc_pages_before={before_pages}, insert_pages={len(question_doc)}"
                        )
                    except Exception:
                        pass
                    doc.insert_pdf(question_doc)
                    question_doc.close()
                    after_pages = len(doc)
                    self.log_debug(
                        f"insert_question_done: Q={question_num}, total_pages_now={after_pages}"
                    )
                
                # Add related lesson slides for this specific question
                for slide_info in related_slides:
                    lesson_page = self._safe_int(slide_info.get("lesson_page"))
                    lesson_filename = str(slide_info.get("lesson_filename") or "")
                    
                    # Validate page number
                    lesson_path = self._resolve_lesson_path(lesson_dir, lesson_filename)
                    if lesson_path.exists():
                        max_pages = PDFValidator.get_pdf_page_count(str(lesson_path))
                        if not PDFValidator.validate_page_number(int(lesson_page), max_pages, lesson_path.name):
                            self.log_debug(f"  WARNING: Page {lesson_page} > max {max_pages} in {lesson_filename}")
                            continue
                    
                    slide_doc = self.extract_lesson_slide(
                        lesson_filename,
                        lesson_page,
                        lesson_dir
                    )
                    if slide_doc:
                        before_slide_insert = len(doc)
                        doc.insert_pdf(slide_doc)
                        slide_doc.close()
                        self.log_debug(
                            f"insert_slide: lesson='{lesson_filename}', page={lesson_page},"
                            f" pages_before={before_slide_insert}, total_pages_now={len(doc)}"
                        )
                    else:
                        self.log_debug(f"SKIP inserting slide: file='{lesson_filename}', page={lesson_page}")
                
                # Add explanation page
                explanation_page = doc.new_page()
                
                # Create text content
                text_content = f"=== 문제 {question['question_number']} 해설 ===\n\n"
                text_content += f"[출처: {display_jokbo_filename} - {jokbo_page_num}페이지]\n\n"
                text_content += f"정답: {question['answer']}\n\n"
                
                if question.get('explanation'):
                    text_content += f"해설:\n{question['explanation']}\n\n"
                
                # 오답 설명 추가
                if question.get('wrong_answer_explanations'):
                    text_content += "오답 설명:\n"
                    for choice, explanation in question['wrong_answer_explanations'].items():
                        text_content += f"  {choice}: {explanation}\n"
                    text_content += "\n"
                
                text_content += "관련 강의 슬라이드:\n"
                for i, slide_info in enumerate(related_slides, 1):
                    # Defensive fetches
                    fname = self._format_display_filename(slide_info.get('lesson_filename') or 'Unknown.pdf')
                    page_no = slide_info.get('lesson_page')
                    score = slide_info.get('relevance_score')
                    if score is None:
                        score = slide_info.get('importance_score')
                    # Normalize score text for jokbo-centric (5~110 scale)
                    score_text = "N/A"
                    try:
                        if score is not None:
                            score_num = int(score)
                            # Cap within expected range to avoid odd strings
                            score_num = max(0, min(score_num, 110))
                            score_text = f"{score_num}/110"
                    except Exception:
                        pass
                    header = f"{i}. {fname}"
                    if page_no:
                        header += f" - {page_no}페이지"
                    if score_text != "N/A":
                        header += f" (관련성 점수: {score_text})"
                    text_content += header + "\n"
                    reason = slide_info.get('relevance_reason') or slide_info.get('reason') or ''
                    if reason:
                        text_content += f"   관련성 이유: {reason}\n"
                text_content += "\n"
                
                # 선택된 연결 개수에 따른 메시지
                if len(related_slides) == 1:
                    text_content += "참고: 이 문제와 가장 관련성이 높은 강의자료입니다."
                else:
                    text_content += "참고: 이 문제와 가장 관련성이 높은 상위 2개의 강의자료입니다."
                
                # Use CJK font for Korean text
                fontname = self._register_font(explanation_page)
                
                # Insert text into the page
                text_rect = fitz.Rect(50, 50, explanation_page.rect.width - 50, explanation_page.rect.height - 50)
                explanation_page.insert_textbox(
                    text_rect,
                    text_content,
                    fontsize=11,
                    fontname=fontname,
                    align=fitz.TEXT_ALIGN_LEFT
                )
        
        if analysis_result.get("summary"):
            summary_page = doc.new_page()
            summary = analysis_result["summary"]
            
            summary_text = "=== 학습 요약 (족보 중심) ===\n\n"
            summary_text += f"족보 페이지 수: {summary['total_jokbo_pages']}\n"
            summary_text += f"총 문제 수: {summary.get('total_questions', 'N/A')}\n"
            summary_text += f"관련 강의 슬라이드 수: {summary.get('total_related_slides', 'N/A')}\n\n"
            if 'study_recommendations' in summary:
                summary_text += f"학습 권장사항:\n{summary['study_recommendations']}"
            
            # Use CJK font for summary page
            fontname = self._register_font(summary_page)
            
            text_rect = fitz.Rect(50, 50, summary_page.rect.width - 50, summary_page.rect.height - 50)
            summary_page.insert_textbox(
                text_rect,
                summary_text,
                fontsize=12,
                fontname=fontname,
                align=fitz.TEXT_ALIGN_LEFT
            )
        
        doc.save(output_path)
        doc.close()
        # Don't close jokbo_pdf since it's cached

        print(f"Filtered PDF created: {output_path}")

    def create_partial_jokbo_pdf(
        self, questions: List[Dict[str, Any]], output_path: str
    ) -> None:
        """Create a PDF containing cropped jokbo questions with explanations.

        Each entry in ``questions`` should contain:

        - ``question_pdf``: path to a PDF with the extracted question
          pages.
        - ``explanation``: textual explanation for the question.

        The output PDF is structured as ``[question pages] ->
        [explanation page]`` for each question.
        """

        doc = fitz.open()
        for q in questions or []:
            q_pdf = q.get("question_pdf")
            explanation = q.get("explanation", "")

            if q_pdf and Path(q_pdf).exists():
                with fitz.open(q_pdf) as src:
                    doc.insert_pdf(src)

            page = doc.new_page()
            fontname = self._register_font(page)
            text_rect = fitz.Rect(50, 50, page.rect.width - 50, page.rect.height - 50)
            page.insert_textbox(
                text_rect,
                self._normalize_korean(explanation),
                fontsize=11,
                fontname=fontname,
                align=fitz.TEXT_ALIGN_LEFT,
            )

        doc.save(output_path)
        doc.close()
