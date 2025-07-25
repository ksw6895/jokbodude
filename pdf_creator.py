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
        
        print(f"    추출 시도: {jokbo_filename} - 문제 {question_number}번 (페이지 {jokbo_page})")
        
        if not jokbo_path.exists():
            print(f"    ❌ 오류: 족보 파일을 찾을 수 없습니다: {jokbo_path}")
            return None
            
        jokbo_pdf = self.get_jokbo_pdf(str(jokbo_path))
        
        if jokbo_page > len(jokbo_pdf) or jokbo_page < 1:
            print(f"    ❌ 오류: {jokbo_filename}에 페이지 {jokbo_page}가 없습니다 (총 {len(jokbo_pdf)}페이지)")
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
        
        print(f"    ✓ 추출 성공: {jokbo_end_page - jokbo_page + 1}페이지")
        
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
                    for i, question in enumerate(slide_info["related_jokbo_questions"]):
                        print(f"\n  슬라이드 {page_num}의 문제 {i+1}/{len(slide_info['related_jokbo_questions'])} 처리 중...")
                        
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
                        text += f"정답: {question.get('answer', '정답 정보 없음')}\n\n"
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
    
    def create_jokbo_centered_pdf(self, jokbo_path: str, analysis_result: Dict[str, Any], output_path: str, lesson_dir: str = "lesson"):
        """Create PDF with jokbo questions followed by related lesson slides"""
        
        if "error" in analysis_result:
            print(f"Cannot create PDF due to analysis error: {analysis_result['error']}")
            return
        
        doc = fitz.open()
        jokbo_pdf = self.get_jokbo_pdf(jokbo_path)
        
        # Cache for lesson PDFs
        lesson_pdfs = {}
        
        def get_lesson_pdf(lesson_filename: str) -> fitz.Document:
            """Get or open a lesson PDF (cached)"""
            if lesson_filename not in lesson_pdfs:
                lesson_path = Path(lesson_dir) / lesson_filename
                if lesson_path.exists():
                    lesson_pdfs[lesson_filename] = fitz.open(str(lesson_path))
                else:
                    print(f"Warning: Lesson file not found: {lesson_path}")
                    return None
            return lesson_pdfs[lesson_filename]
        
        # Process each optimized question
        for question in analysis_result.get('optimized_questions', []):
            # 1. Insert the jokbo question page(s)
            jokbo_page = question['jokbo_page']
            jokbo_end_page = question.get('jokbo_end_page', jokbo_page)
            
            if jokbo_page <= len(jokbo_pdf):
                doc.insert_pdf(jokbo_pdf, from_page=jokbo_page-1, to_page=jokbo_end_page-1)
            
            # 2. Insert the best matching lesson slide
            best_lesson_filename = question.get('best_lesson_filename')
            best_lesson_page = question.get('best_lesson_page')
            
            if best_lesson_filename and best_lesson_page:
                lesson_pdf = get_lesson_pdf(best_lesson_filename)
                if lesson_pdf and best_lesson_page <= len(lesson_pdf):
                    doc.insert_pdf(lesson_pdf, from_page=best_lesson_page-1, to_page=best_lesson_page-1)
            
            # 3. Add explanation page
            explanation_page = doc.new_page()
            page_rect = explanation_page.rect
            
            text = f"=== 문제 {question['question_number']} 상세 분석 ===\\n\\n"
            text += f"[족보: {analysis_result.get('jokbo_filename', 'Unknown')} - {jokbo_page}페이지]\\n"
            text += f"[최적 매칭 강의자료: {best_lesson_filename} - {best_lesson_page}페이지]\\n"
            text += f"[중요도 점수: {question.get('importance_score', 'N/A')}/11]\\n\\n"
            
            text += f"정답: {question.get('answer', 'N/A')}\\n\\n"
            
            if question.get('explanation'):
                text += f"해설:\\n{question['explanation']}\\n\\n"
            
            # 오답 설명 추가
            if question.get('wrong_answer_explanations'):
                text += f"오답 설명:\\n"
                for choice, explanation in question['wrong_answer_explanations'].items():
                    text += f"• {choice}: {explanation}\\n"
                text += "\\n"
            
            if question.get('relevance_reason'):
                text += f"관련성 이유:\\n{question['relevance_reason']}\\n\\n"
            
            text += f"─" * 40 + "\\n"
            text += f"💡 이 문제는 {best_lesson_filename}의 {best_lesson_page}페이지 내용과 가장 밀접한 관련이 있습니다."
            
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
        
        # Add summary page
        if 'summary' in analysis_result:
            summary_page = doc.new_page()
            summary = analysis_result['summary']
            
            summary_text = "=== 족보 분석 요약 ===\\n\\n"
            summary_text += f"족보 파일: {analysis_result.get('jokbo_filename', 'Unknown')}\\n"
            summary_text += f"분석된 문제 수: {summary.get('total_questions', 0)}\\n"
            summary_text += f"비교한 강의자료 수: {summary.get('analyzed_lessons', 0)}\\n\\n"
            summary_text += f"학습 권장사항:\\n"
            summary_text += f"이 족보의 문제들은 여러 강의자료와 연관되어 있습니다.\\n"
            summary_text += f"각 문제별로 가장 중요도가 높은 강의자료 페이지를 선택하여 정리했습니다.\\n"
            summary_text += f"특히 그림이 일치하는 경우(중요도 11점) 출제 가능성이 매우 높으니 주의깊게 학습하세요."
            
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
        
        # Save and close
        doc.save(output_path)
        doc.close()
        
        # Close all cached lesson PDFs
        for pdf in lesson_pdfs.values():
            pdf.close()
        
        print(f"Jokbo-centered PDF created: {output_path}")