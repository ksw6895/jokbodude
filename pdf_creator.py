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
from typing import List, Dict, Any
from pathlib import Path
import tempfile
import os
from datetime import datetime
import threading
from validators import PDFValidator
import unicodedata
import re

class PDFCreator:
    def __init__(self):
        self.temp_files = []
        self.jokbo_pdfs = {}  # Cache for opened jokbo PDFs
        self.pdf_lock = threading.Lock()  # Thread-safe lock for PDF cache
        self.debug_log_path = Path("output/debug/pdf_creator_debug.log")
        self.debug_log_path.parent.mkdir(parents=True, exist_ok=True)
        self._lesson_dir_cache = {}

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
    def _resolve_lesson_path(self, lesson_dir: str, lesson_filename: str) -> Path:
        """Resolve a lesson file in directory, with normalization and fuzzy fallback.

        Handles Hangul normalization differences, minor separator differences, and
        AI/display-name prefixes like '강의자료_<name>.pdf'.
        """
        base_dir = Path(lesson_dir)
        direct = base_dir / (lesson_filename or '')
        if direct.exists():
            return direct

        # Build directory cache once
        cache_key = str(base_dir.resolve())
        if cache_key not in self._lesson_dir_cache:
            try:
                files = list(base_dir.glob('*.pdf'))
            except Exception:
                files = []
            mapping = {}
            for p in files:
                key = self._normalize_korean(p.name)
                key = re.sub(r"[\s_\-]+", "", key)
                mapping[key.lower()] = p
            self._lesson_dir_cache[cache_key] = mapping

        mapping = self._lesson_dir_cache.get(cache_key, {})

        # Helper to sanitize for matching
        def _keyify(name: str) -> str:
            n = self._normalize_korean(name or '')
            return re.sub(r"[\s_\-]+", "", n).lower()

        # Try exact sanitized match
        needle_key = _keyify(lesson_filename)
        candidate = mapping.get(needle_key)
        if candidate and candidate.exists():
            self.log_debug(f"Fuzzy-matched lesson file: '{lesson_filename}' -> '{candidate.name}'")
            return candidate

        # Try removing common prefixes like '강의자료_', '강의_', 'lesson_', 'lecture_'
        reduced = re.sub(r"^(강의자료|강의|lesson|lecture)[\s_\-]+", "", (lesson_filename or ''), flags=re.IGNORECASE)
        if reduced != (lesson_filename or ''):
            reduced_key = _keyify(reduced)
            candidate = mapping.get(reduced_key)
            if candidate and candidate.exists():
                self.log_debug(f"Prefix-stripped match: '{lesson_filename}' -> '{candidate.name}'")
                return candidate

        # Last resort: bidirectional contains/suffix match on sanitized names
        best = None
        best_len = 0
        try:
            for k, p in mapping.items():
                if not needle_key:
                    continue
                # If filename from AI includes extra prefix/suffix, prefer longest overlap
                if needle_key in k or k in needle_key:
                    l = min(len(k), len(needle_key))
                    if l > best_len:
                        best = p
                        best_len = l
        except Exception:
            pass
        if best and best.exists():
            self.log_debug(f"Contains-matched lesson file: '{lesson_filename}' -> '{best.name}'")
            return best

        # Not found
        self.log_debug(f"Lesson file not found (after fallback): {lesson_filename} in {lesson_dir}")
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
    
    def extract_jokbo_question(self, jokbo_filename: str, jokbo_page: int, question_number, question_text: str, jokbo_dir: str = "jokbo", jokbo_end_page: int = None, is_last_question_on_page: bool = False, question_numbers_on_page = None):
        """Extract full page containing the question from jokbo PDF"""
        self.log_debug(f"extract_jokbo_question called for Q{question_number} on page {jokbo_page}")
        self.log_debug(f"  is_last_question_on_page: {is_last_question_on_page}")
        self.log_debug(f"  question_numbers_on_page: {question_numbers_on_page}")
        
        jokbo_path = Path(jokbo_dir) / jokbo_filename
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
        if jokbo_end_page is None:
            # Convert question_number to string for consistent comparison
            question_num_str = str(question_number)
            self.log_debug(f"  question_num_str: {repr(question_num_str)}")
            
            # First, try to use question_numbers_on_page for more accurate detection
            if question_numbers_on_page and question_num_str in question_numbers_on_page:
                self.log_debug(f"  Found in question_numbers_on_page")
                # Check if current question is the last one on the page
                if question_num_str == question_numbers_on_page[-1] and jokbo_page < pdf_page_count:
                    # This is the last question on the page, include next page
                    jokbo_end_page = jokbo_page + 1
                    print(f"  DEBUG: Question {question_number} is last in {question_numbers_on_page} on page {jokbo_page}")
                    print(f"  DEBUG: Including next page {jokbo_end_page} (PDF has {pdf_page_count} pages)")
                    self.log_debug(f"  LAST QUESTION: Including next page {jokbo_end_page}")
                else:
                    jokbo_end_page = jokbo_page
                    print(f"  DEBUG: Question {question_number} on page {jokbo_page}, not last or no next page")
                    self.log_debug(f"  NOT LAST: Using single page {jokbo_end_page}")
            # Fallback to is_last_question_on_page flag
            elif is_last_question_on_page and jokbo_page < pdf_page_count:
                # Automatically include the next page
                jokbo_end_page = jokbo_page + 1
                print(f"  Question {question_number} is last on page {jokbo_page}, including next page")
                self.log_debug(f"  FALLBACK: Using is_last_question_on_page flag")
            else:
                jokbo_end_page = jokbo_page
                self.log_debug(f"  NO CONDITIONS MET: Using single page")
        
        # Validate end page
        if jokbo_end_page > pdf_page_count or jokbo_end_page < jokbo_page:
            print(f"Warning: Invalid end page {jokbo_end_page}, using single page")
            jokbo_end_page = jokbo_page
        
        # Extract the full page(s) containing the question
        self.log_debug(f"  Final extraction: pages {jokbo_page} to {jokbo_end_page} (0-indexed: {jokbo_page-1} to {jokbo_end_page-1})")
        question_doc = fitz.open()
        
        # Extract pages
        jokbo_pdf = self.get_jokbo_pdf(str(jokbo_path))  # get_jokbo_pdf already handles locking
        question_doc.insert_pdf(jokbo_pdf, from_page=jokbo_page-1, to_page=jokbo_end_page-1)
        
        self.log_debug(f"  Extracted document has {len(question_doc)} pages")
        
        return question_doc
    
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

        total_pages = len(lesson_pdf)
        # Iterate through every slide to ensure none are skipped
        for page_num in range(1, total_pages + 1):
            # Always insert the lesson slide
            doc.insert_pdf(lesson_pdf, from_page=page_num-1, to_page=page_num-1)

            # If there are related questions, append them after the slide
            for question in related_by_page.get(page_num, []):
                # Determine if this is the last question on the page
                is_last_question = False
                question_numbers = question.get("question_numbers_on_page", [])
                if question_numbers and str(question.get("question_number")) == str(question_numbers[-1]):
                    is_last_question = True

                # Extract and insert the question from jokbo (handles next-page inclusion)
                question_doc = self.extract_jokbo_question(
                    question.get("jokbo_filename"), 
                    int(question.get("jokbo_page", 0)),
                    question.get("question_number"),
                    question.get("question_text", ""),
                    jokbo_dir,
                    question.get("jokbo_end_page"),
                    is_last_question,
                    question_numbers
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
            print(f"  경고: jokbo_pages가 비어있습니다. PDF를 생성할 내용이 없습니다.")
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
                
                # Determine if this is the last question on the page
                is_last_question = False
                question_numbers = question.get("question_numbers_on_page", [])
                self.log_debug(f"Processing Q{question_num}: question_numbers = {question_numbers}")
                if question_numbers and str(question_num) == question_numbers[-1]:
                    is_last_question = True
                    print(f"DEBUG: Question {question_num} is last on page {jokbo_page_num}, questions: {question_numbers}")
                    self.log_debug(f"  Q{question_num} is LAST on page {jokbo_page_num}")
                else:
                    self.log_debug(f"  Q{question_num} is NOT last on page {jokbo_page_num}")
                
                # Extract the question pages (handles multi-page questions)
                question_doc = self.extract_jokbo_question(
                    jokbo_filename,
                    jokbo_page_num,
                    question_num,
                    question.get("question_text", ""),
                    str(Path(jokbo_path).parent),
                    None,  # jokbo_end_page not available in jokbo-centric mode yet
                    is_last_question,
                    question_numbers
                )
                if question_doc:
                    doc.insert_pdf(question_doc)
                    question_doc.close()
                
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
                        doc.insert_pdf(slide_doc)
                        slide_doc.close()
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
