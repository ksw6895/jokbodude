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
    
    def extract_jokbo_question(self, jokbo_filename: str, jokbo_page: int, question_number: int, question_text: str, jokbo_dir: str = "jokbo") -> fitz.Document:
        """Extract specific question from jokbo PDF page as a cropped document"""
        jokbo_path = Path(jokbo_dir) / jokbo_filename
        if not jokbo_path.exists():
            print(f"Warning: Jokbo file not found: {jokbo_path}")
            return None
            
        jokbo_pdf = self.get_jokbo_pdf(str(jokbo_path))
        
        if jokbo_page > len(jokbo_pdf) or jokbo_page < 1:
            print(f"Warning: Page {jokbo_page} does not exist in {jokbo_filename}")
            return None
        
        # Get the page
        page = jokbo_pdf[jokbo_page-1]
        
        # Search for question text to find its location
        text_instances = page.search_for(str(question_number))
        
        if text_instances:
            # Create a rect that encompasses the question area
            # Start with the question number location and expand
            question_rect = text_instances[0]
            
            # Expand the rect to include more content (estimated question area)
            # This is a heuristic - adjust based on your PDF layout
            expansion_factor = 5.0  # Expand to include the full question
            question_rect.x0 = max(0, question_rect.x0 - 20)
            question_rect.y0 = max(0, question_rect.y0 - 10)
            question_rect.x1 = min(page.rect.width, question_rect.x1 + 200)
            question_rect.y1 = min(page.rect.height, question_rect.y1 + 150)
            
            # Create new document with cropped question
            question_doc = fitz.open()
            new_page = question_doc.new_page(width=question_rect.width, height=question_rect.height)
            new_page.show_pdf_page(new_page.rect, jokbo_pdf, jokbo_page-1, clip=question_rect)
            
            return question_doc
        else:
            # If we can't find the question number, return the full page
            question_doc = fitz.open()
            question_doc.insert_pdf(jokbo_pdf, from_page=jokbo_page-1, to_page=jokbo_page-1)
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
                            jokbo_dir
                        )
                        if question_doc:
                            doc.insert_pdf(question_doc)
                            question_doc.close()
                        
                        # Add explanation page
                        explanation_page = doc.new_page()
                        page_rect = explanation_page.rect
                        
                        text = f"=== 문제 {question['question_number']} 해설 ===\n\n"
                        text += f"[출처: {question['jokbo_filename']}]\n\n"
                        text += f"문제: {question.get('question_text', 'N/A')}\n\n"
                        text += f"정답: {question['answer']}\n\n"
                        if question.get('explanation'):
                            text += f"해설:\n{question['explanation']}\n\n"
                        text += f"관련성:\n{question['relevance_reason']}\n\n"
                        text += f"관련 강의 페이지: {page_num}"
                        
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