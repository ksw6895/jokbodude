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

class PDFCreator:
    def __init__(self):
        self.temp_files = []
        self.jokbo_pdfs = {}  # Cache for opened jokbo PDFs
        
    def __del__(self):
        for temp_file in self.temp_files:
            if os.path.exists(temp_file):
                os.remove(temp_file)
        # Close all cached PDFs
        for pdf in self.jokbo_pdfs.values():
            pdf.close()
    
    def get_jokbo_pdf(self, jokbo_path: str) -> fitz.Document:
        """Get or open a jokbo PDF (cached)"""
        if jokbo_path not in self.jokbo_pdfs:
            self.jokbo_pdfs[jokbo_path] = fitz.open(jokbo_path)
        return self.jokbo_pdfs[jokbo_path]
    
    def extract_jokbo_question(self, jokbo_filename: str, jokbo_page: int, question_number: int, question_text: str, jokbo_dir: str = "jokbo", jokbo_end_page: int = None) -> fitz.Document:
        """Extract full page containing the question from jokbo PDF"""
        jokbo_path = Path(jokbo_dir) / jokbo_filename
        if not jokbo_path.exists():
            print(f"Warning: Jokbo file not found: {jokbo_path}")
            return None
            
        jokbo_pdf = self.get_jokbo_pdf(str(jokbo_path))
        
        if jokbo_page > len(jokbo_pdf) or jokbo_page < 1:
            print(f"Warning: Page {jokbo_page} does not exist in {jokbo_filename}")
            return None
        
        # Determine the end page
        if jokbo_end_page is None:
            jokbo_end_page = jokbo_page
        
        # Validate end page
        if jokbo_end_page > len(jokbo_pdf) or jokbo_end_page < jokbo_page:
            print(f"Warning: Invalid end page {jokbo_end_page}, using single page")
            jokbo_end_page = jokbo_page
        
        # Extract the full page(s) containing the question
        question_doc = fitz.open()
        question_doc.insert_pdf(jokbo_pdf, from_page=jokbo_page-1, to_page=jokbo_end_page-1)
        
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
                        # Extract and insert the question from jokbo
                        question_doc = self.extract_jokbo_question(
                            question["jokbo_filename"], 
                            question["jokbo_page"],
                            question["question_number"],
                            question.get("question_text", ""),
                            jokbo_dir,
                            question.get("jokbo_end_page")  # Pass end page if available
                        )
                        if question_doc:
                            doc.insert_pdf(question_doc)
                            question_doc.close()
                        
                        # Add explanation page
                        explanation_page = doc.new_page()
                        page_rect = explanation_page.rect
                        
                        text = f"=== 문제 {question['question_number']} 해설 ===\n\n"
                        text += f"※ 앞 페이지의 문제 {question['question_number']}번을 참고하세요\n\n"
                        text += f"[출처: {question['jokbo_filename']} - {question['jokbo_page']}페이지]\n\n"
                        text += f"정답: {question['answer']}\n\n"
                        if question.get('explanation'):
                            text += f"해설:\n{question['explanation']}\n\n"
                        
                        # 오답 설명 추가
                        if question.get('wrong_answer_explanations'):
                            text += f"오답 설명:\n"
                            for choice, explanation in question['wrong_answer_explanations'].items():
                                text += f"• {choice}: {explanation}\n"
                            text += "\n"
                        
                        text += f"관련성:\n{question['relevance_reason']}\n\n"
                        text += f"관련 강의 페이지: {page_num}\n\n"
                        text += f"─" * 40 + "\n"
                        text += f"💡 이 문제는 강의자료 {page_num}페이지의 내용과 관련이 있습니다."
                        
                        # Use CJK font for Korean text support
                        font = fitz.Font("cjk")
                        fontname = "F0"
                        fontsize = 11
                        
                        # Insert font into page
                        explanation_page.insert_font(fontname=fontname, fontbuffer=font.buffer)
                        
                        text_rect = fitz.Rect(50, 50, page_rect.width - 50, page_rect.height - 50)
                        
                        rc = explanation_page.insert_textbox(
                            text_rect,
                            text,
                            fontsize=fontsize,
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
        jokbo_pdf = fitz.open(jokbo_path)
        jokbo_filename = Path(jokbo_path).name
        
        for page_info in analysis_result.get("jokbo_pages", []):
            jokbo_page_num = page_info["jokbo_page"]
            
            if jokbo_page_num <= len(jokbo_pdf):
                # Insert the jokbo page
                doc.insert_pdf(jokbo_pdf, from_page=jokbo_page_num-1, to_page=jokbo_page_num-1)
                
                # Process each question on this page
                for question in page_info.get("questions", []):
                    related_slides = question.get("related_lesson_slides", [])
                    
                    if related_slides:
                        # Add related lesson slides
                        for slide_info in related_slides:
                            slide_doc = self.extract_lesson_slide(
                                slide_info["lesson_filename"],
                                slide_info["lesson_page"],
                                lesson_dir
                            )
                            if slide_doc:
                                doc.insert_pdf(slide_doc)
                                slide_doc.close()
                        
                        # Add explanation page for this question
                        explanation_page = doc.new_page()
                        page_rect = explanation_page.rect
                        
                        text = f"=== 문제 {question['question_number']} 해설 ===\n\n"
                        text += f"[출처: {jokbo_filename} - {jokbo_page_num}페이지]\n\n"
                        text += f"정답: {question['answer']}\n\n"
                        if question.get('explanation'):
                            text += f"해설:\n{question['explanation']}\n\n"
                        
                        # 오답 설명 추가
                        if question.get('wrong_answer_explanations'):
                            text += f"오답 설명:\n"
                            for choice, explanation in question['wrong_answer_explanations'].items():
                                text += f"• {choice}: {explanation}\n"
                            text += "\n"
                        
                        text += f"관련 강의 슬라이드:\n"
                        for slide_info in related_slides:
                            text += f"• {slide_info['lesson_filename']} - {slide_info['lesson_page']}페이지\n"
                            text += f"  관련성: {slide_info['relevance_reason']}\n"
                        
                        text += f"\n─" * 40 + "\n"
                        text += f"💡 이 문제는 위의 강의자료들과 관련이 있습니다."
                        
                        # Use CJK font for Korean text support
                        font = fitz.Font("cjk")
                        fontname = "F0"
                        fontsize = 11
                        
                        # Insert font into page
                        explanation_page.insert_font(fontname=fontname, fontbuffer=font.buffer)
                        
                        text_rect = fitz.Rect(50, 50, page_rect.width - 50, page_rect.height - 50)
                        
                        rc = explanation_page.insert_textbox(
                            text_rect,
                            text,
                            fontsize=fontsize,
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
        jokbo_pdf.close()
        
        print(f"Filtered PDF created: {output_path}")