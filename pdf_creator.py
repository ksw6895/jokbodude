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

class PDFCreator:
    def __init__(self):
        self.temp_files = []
        self.jokbo_pdfs = {}  # Cache for opened jokbo PDFs
        self.pdf_lock = threading.Lock()  # Thread-safe lock for PDF cache
        self.debug_log_path = Path("output/debug/pdf_creator_debug.log")
        self.debug_log_path.parent.mkdir(parents=True, exist_ok=True)
        
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
            
        # Check page validity with thread-safe access
        with self.pdf_lock:
            jokbo_pdf = self.get_jokbo_pdf(str(jokbo_path))
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
        
        # Thread-safe PDF access for extraction
        with self.pdf_lock:
            jokbo_pdf = self.get_jokbo_pdf(str(jokbo_path))
            question_doc.insert_pdf(jokbo_pdf, from_page=jokbo_page-1, to_page=jokbo_end_page-1)
        
        self.log_debug(f"  Extracted document has {len(question_doc)} pages")
        
        return question_doc
    
    def create_filtered_pdf(self, lesson_path: str, analysis_result: Dict[str, Any], output_path: str, jokbo_dir: str = "jokbo"):
        """Create new PDF with filtered slides and related jokbo questions"""
        
        if "error" in analysis_result:
            print(f"Cannot create PDF due to analysis error: {analysis_result['error']}")
            return
        
        doc = fitz.open()
        lesson_pdf = fitz.open(lesson_path)
        
        for slide_info in analysis_result.get("related_slides", []):
            page_num = slide_info["lesson_page"]
            
            if page_num <= len(lesson_pdf):
                # Insert the lesson slide
                doc.insert_pdf(lesson_pdf, from_page=page_num-1, to_page=page_num-1)
                
                if slide_info["related_jokbo_questions"]:
                    
                    # Create a page for each question with its explanation
                    for question in slide_info["related_jokbo_questions"]:
                        # Determine if this is the last question on the page
                        is_last_question = False
                        question_numbers = question.get("question_numbers_on_page", [])
                        if question_numbers and str(question["question_number"]) == question_numbers[-1]:
                            is_last_question = True
                        
                        # Extract and insert the question from jokbo
                        question_doc = self.extract_jokbo_question(
                            question["jokbo_filename"], 
                            question["jokbo_page"],
                            question["question_number"],
                            question.get("question_text", ""),
                            jokbo_dir,
                            question.get("jokbo_end_page"),  # Pass end page if available
                            is_last_question,  # Calculated flag
                            question_numbers  # Pass question numbers on page
                        )
                        if question_doc:
                            doc.insert_pdf(question_doc)
                            question_doc.close()
                        
                        # Add explanation page
                        explanation_page = doc.new_page()
                        
                        # Create text content
                        text_content = f"=== 문제 {question['question_number']} 해설 ===\n\n"
                        text_content += f"※ 앞 페이지의 문제 {question['question_number']}번을 참고하세요\n\n"
                        text_content += f"[출처: {question['jokbo_filename']} - {question['jokbo_page']}페이지]\n\n"
                        text_content += f"정답: {question['answer']}\n\n"
                        
                        if question.get('explanation'):
                            text_content += f"해설:\n{question['explanation']}\n\n"
                        
                        # 오답 설명 추가
                        if question.get('wrong_answer_explanations'):
                            text_content += "오답 설명:\n"
                            for choice, explanation in question['wrong_answer_explanations'].items():
                                text_content += f"  {choice}: {explanation}\n"
                            text_content += "\n"
                        
                        if question.get('relevance_reason'):
                            text_content += f"관련성:\n{question['relevance_reason']}\n\n"
                        
                        text_content += f"관련 강의 페이지: {page_num}\n\n"
                        text_content += f"💡 이 문제는 강의자료 {page_num}페이지의 내용과 관련이 있습니다."
                        
                        # Use CJK font for Korean text
                        font = fitz.Font("cjk")
                        fontname = "F1"
                        explanation_page.insert_font(fontname=fontname, fontbuffer=font.buffer)
                        
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
            
            summary_text = "=== 학습 요약 ===\n\n"
            summary_text += f"관련 슬라이드 수: {summary['total_related_slides']}\n"
            summary_text += f"총 관련 문제 수: {summary.get('total_questions', 'N/A')}\n\n"
            summary_text += f"주요 주제: {', '.join(summary['key_topics'])}\n\n"
            if 'study_recommendations' in summary:
                summary_text += f"학습 권장사항:\n{summary['study_recommendations']}"
            
            # Use CJK font for summary page
            font = fitz.Font("cjk")
            fontname = "F1"
            summary_page.insert_font(fontname=fontname, fontbuffer=font.buffer)
            
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
    
    def extract_lesson_slide(self, lesson_filename: str, lesson_page: int, lesson_dir: str = "lesson") -> fitz.Document:
        """Extract a single page from lesson PDF"""
        lesson_path = Path(lesson_dir) / lesson_filename
        if not lesson_path.exists():
            print(f"Warning: Lesson file not found: {lesson_path}")
            return None
            
        lesson_pdf = fitz.open(str(lesson_path))
        
        if lesson_page > len(lesson_pdf) or lesson_page < 1:
            print(f"Warning: Page {lesson_page} does not exist in {lesson_filename}")
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
        
        doc = fitz.open()
        jokbo_filename = Path(jokbo_path).name
        
        # Get PDF page count thread-safely
        with self.pdf_lock:
            jokbo_pdf = self.get_jokbo_pdf(jokbo_path)
            jokbo_page_count = len(jokbo_pdf)
        
        # Track which questions have been processed to avoid duplicates
        processed_questions = set()
        
        for page_info in analysis_result.get("jokbo_pages", []):
            jokbo_page_num = page_info["jokbo_page"]
            
            if jokbo_page_num <= jokbo_page_count:
                # Process each question on this page
                for question in page_info.get("questions", []):
                    related_slides = question.get("related_lesson_slides", [])
                    question_num = question.get("question_number", "Unknown")
                    
                    # Only process questions that have related lesson slides and haven't been processed
                    if related_slides and question_num not in processed_questions:
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
                            lesson_page = slide_info["lesson_page"]
                            lesson_filename = slide_info["lesson_filename"]
                            
                            # Validate page number
                            lesson_path = Path(lesson_dir) / lesson_filename
                            if lesson_path.exists():
                                max_pages = PDFValidator.get_pdf_page_count(str(lesson_path))
                                if not PDFValidator.validate_page_number(lesson_page, max_pages, lesson_filename):
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
                        
                        # Add explanation page
                        explanation_page = doc.new_page()
                        
                        # Create text content
                        text_content = f"=== 문제 {question['question_number']} 해설 ===\n\n"
                        text_content += f"[출처: {jokbo_filename} - {jokbo_page_num}페이지]\n\n"
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
                            score = slide_info.get('relevance_score', 0)
                            if score == 11:
                                score_text = "11/10 ⭐ 동일한 그림/도표"
                            else:
                                score_text = f"{score}/10"
                            text_content += f"{i}. {slide_info['lesson_filename']} - {slide_info['lesson_page']}페이지 (관련성 점수: {score_text})\n"
                            text_content += f"   관련성 이유: {slide_info['relevance_reason']}\n"
                        text_content += "\n"
                        
                        # 선택된 연결 개수에 따른 메시지
                        if len(related_slides) == 1:
                            text_content += "💡 이 문제와 가장 관련성이 높은 강의자료입니다."
                        else:
                            text_content += "💡 이 문제와 가장 관련성이 높은 상위 2개의 강의자료입니다."
                        
                        # Use CJK font for Korean text
                        font = fitz.Font("cjk")
                        fontname = "F1"
                        explanation_page.insert_font(fontname=fontname, fontbuffer=font.buffer)
                        
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
            font = fitz.Font("cjk")
            fontname = "F1"
            summary_page.insert_font(fontname=fontname, fontbuffer=font.buffer)
            
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