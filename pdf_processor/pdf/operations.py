"""
PDF manipulation operations module.
Handles PDF reading, splitting, page extraction, and other PDF-related operations.
"""

import tempfile
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Any
import pymupdf as fitz

from ..utils.logging import get_logger
from ..utils.exceptions import PDFParsingError, FileNotFoundError

logger = get_logger(__name__)


class PDFOperations:
    """Handles all PDF manipulation operations."""

    # -----------------------------
    # Low-level marker search utils
    # -----------------------------
    @staticmethod
    def find_question_marker_y(page: fitz.Page, target_num: int) -> Optional[float]:
        """Find the Y-coordinate of the question-number marker on a page.

        - Prefers text extraction via PyMuPDF words with left-margin heuristic
        - Falls back to OCR with pytesseract when text is not extractable
        - Returns the top Y (in PDF units) of the detected marker, or None if not found
        """
        try:
            words = page.get_text("words") or []
        except Exception:
            words = []
        w = page.rect.width
        candidates: list[tuple[float, float]] = []  # (y0, x0)
        if words:
            import re as _re
            pats = [
                rf"^\(?{target_num}\)?[\.)]$",
                rf"^\(?{target_num}\)?$",
                rf"^{target_num}번$",
                rf"^Q\s*{target_num}$",
            ]
            compiled = [_re.compile(p) for p in pats]
            for x0, y0, x1, y1, text, *_ in words:
                t = str(text or "").strip()
                if not t:
                    continue
                # left margin constraint to reduce false positives
                if x0 > (w * 0.35):
                    continue
                for cp in compiled:
                    if cp.match(t):
                        candidates.append((y0, x0))
                        break
        # OCR fallback if no words or no match
        if not candidates:
            try:
                from PIL import Image
                import io as _io
                import pytesseract
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
                img = Image.open(_io.BytesIO(pix.tobytes("png")))
                data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
                ih = img.height
                # map pixels to PDF units
                scale_y = page.rect.height / float(ih or 1)
                import re as _re
                pats = [
                    rf"^\(?{target_num}\)?[\.)]$",
                    rf"^\(?{target_num}\)?$",
                    rf"^{target_num}번$",
                    rf"^Q\s*{target_num}$",
                ]
                compiled = [_re.compile(p) for p in pats]
                n = len(data.get("text", []))
                for i in range(n):
                    t = (data["text"][i] or "").strip()
                    if not t:
                        continue
                    for cp in compiled:
                        if cp.match(t):
                            y0 = float(data["top"][i]) * scale_y
                            x0 = float(data["left"][i]) * (page.rect.width / float(img.width or 1))
                            if x0 <= (page.rect.width * 0.4):
                                candidates.append((y0, x0))
                            break
            except Exception:
                pass
        if not candidates:
            return None
        candidates.sort(key=lambda t: (t[0], t[1]))
        return candidates[0][0]
    
    @staticmethod
    def get_page_count(pdf_path: str) -> int:
        """
        Get the total number of pages in a PDF file.
        
        Args:
            pdf_path: Path to the PDF file
            
        Returns:
            Total number of pages
            
        Raises:
            FileNotFoundError: If PDF file doesn't exist
            PDFParsingError: If PDF cannot be opened
        """
        pdf_path = Path(pdf_path)
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF file not found: {pdf_path}")
            
        try:
            with fitz.open(str(pdf_path)) as pdf:
                return len(pdf)
        except Exception as e:
            logger.error(f"Failed to open PDF {pdf_path}: {str(e)}")
            raise PDFParsingError(f"Cannot open PDF file: {str(e)}")
    
    @staticmethod
    def split_pdf_for_chunks(pdf_path: str, max_pages: int = 30) -> List[Tuple[str, int, int]]:
        """
        Split PDF into chunks for processing.
        
        Args:
            pdf_path: Path to the PDF file
            max_pages: Maximum pages per chunk
            
        Returns:
            List of tuples (pdf_path, start_page, end_page)
        """
        pdf_path = Path(pdf_path)
        total_pages = PDFOperations.get_page_count(str(pdf_path))
        
        if total_pages <= max_pages:
            # Small enough to process as one chunk
            logger.debug(f"PDF {pdf_path.name} fits in single chunk ({total_pages} pages)")
            return [(str(pdf_path), 1, total_pages)]
        
        # Split into chunks
        chunks = []
        for start in range(0, total_pages, max_pages):
            end = min(start + max_pages, total_pages)
            chunks.append((str(pdf_path), start + 1, end))
        
        logger.info(f"Split {pdf_path.name} into {len(chunks)} chunks ({total_pages} pages total)")
        return chunks
    
    @staticmethod
    def extract_pages(pdf_path: str, start_page: int, end_page: int, 
                     output_path: Optional[str] = None) -> str:
        """
        Extract specific pages from a PDF and save to a new file.
        
        Args:
            pdf_path: Path to source PDF
            start_page: First page to extract (1-based)
            end_page: Last page to extract (1-based)
            output_path: Optional output path (creates temp file if not provided)
            
        Returns:
            Path to the extracted PDF file
            
        Raises:
            PDFParsingError: If extraction fails
        """
        try:
            with fitz.open(str(pdf_path)) as src_pdf:
                # Validate page numbers
                total_pages = len(src_pdf)
                if start_page < 1 or start_page > total_pages:
                    raise ValueError(f"Invalid start page {start_page} (total pages: {total_pages})")
                if end_page < start_page or end_page > total_pages:
                    raise ValueError(f"Invalid end page {end_page} (total pages: {total_pages})")
                
                # Create new PDF with selected pages
                output = fitz.open()
                
                # Convert to 0-based indexing
                for page_num in range(start_page - 1, end_page):
                    output.insert_pdf(src_pdf, from_page=page_num, to_page=page_num)
                
                # Determine output path
                if output_path is None:
                    temp_file = tempfile.NamedTemporaryFile(suffix='.pdf', delete=False)
                    output_path = temp_file.name
                
                # Save the extracted pages
                output.save(output_path)
                output.close()

                logger.debug(f"Extracted pages {start_page}-{end_page} from {pdf_path} to {output_path}")
                return output_path

        except Exception as e:
            logger.error(f"Failed to extract pages from {pdf_path}: {str(e)}")
            raise PDFParsingError(f"Page extraction failed: {str(e)}")

    @staticmethod
    def extract_question_region(
        pdf_path: str,
        page_start: int,
        next_question_start: Optional[int] = None,
        question_number: Optional[object] = None,
        next_question_number_for_same_page: Optional[object] = None,
        output_path: Optional[str] = None,
    ) -> str:
        """Extract just the question region across one or more pages.

        - Start page is cropped from the detected question-number marker
          to the bottom of the page.
        - Middle pages (if any) are included in full.
        - If the next question starts on the same page, the end Y is
          cropped to that next marker; otherwise the last page is kept
          in full.

        Falls back to full-page extraction if cropping is not possible.
        Attempts OCR (pytesseract) only when text is not extractable.
        """

        def _to_int(val: object, default: int = 0) -> int:
            try:
                if isinstance(val, bool):
                    return default
                if isinstance(val, (int, float)):
                    return int(val)
                import re
                m = re.search(r"(\d+)", str(val) or "")
                return int(m.group(1)) if m else default
            except Exception:
                return default

        try:
            with fitz.open(str(pdf_path)) as src:
                total_pages = len(src)
                if page_start < 1 or page_start > total_pages:
                    raise ValueError("Invalid page_start")

                # Determine page_end handling possible same-page next start
                same_page_next = (
                    next_question_start is not None and _to_int(next_question_start) == _to_int(page_start)
                )
                if next_question_start is None:
                    page_end = total_pages
                elif same_page_next:
                    page_end = page_start
                else:
                    page_end = max(page_start, min(total_pages, int(next_question_start) - 1))

                out_doc = fitz.open()
                qnum_int = _to_int(question_number, 0) if question_number is not None else 0
                next_same_int = _to_int(next_question_number_for_same_page, 0) if next_question_number_for_same_page is not None else 0

                for pno in range(page_start, page_end + 1):
                    page = src[pno - 1]
                    rect = page.rect
                    y0 = 0.0
                    y1 = rect.height
                    if pno == page_start:
                        if qnum_int > 0:
                            y_found = PDFOperations.find_question_marker_y(page, qnum_int)
                            if y_found is not None:
                                y0 = max(0.0, y_found - 4)  # small margin
                        # If next question is also on this same page, crop to its marker
                        if same_page_next and (qnum_int > 0 or next_same_int > 0):
                            target_next = next_same_int if next_same_int > 0 else (qnum_int + 1 if qnum_int > 0 else 0)
                            yn = PDFOperations.find_question_marker_y(page, target_next) if target_next > 0 else None
                            if yn is not None and yn > y0:
                                y1 = yn - 4
                    elif pno == page_end and same_page_next is False:
                        # last page of a multi-page question: keep full page (robust)
                        y0 = 0.0
                        y1 = rect.height
                    clip = fitz.Rect(0, max(0, y0), rect.width, min(rect.height, y1))
                    # Render into a cropped page of matching height
                    new_h = max(10.0, clip.height)
                    out_page = out_doc.new_page(width=rect.width, height=new_h)
                    out_page.show_pdf_page(
                        fitz.Rect(0, 0, rect.width, new_h), src, pno - 1, clip=clip
                    )

                # Persist output
                if output_path is None:
                    temp_file = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
                    output_path = temp_file.name
                out_doc.save(output_path)
                out_doc.close()
                return output_path
        except Exception as e:
            logger.warning(f"Cropping failed; falling back to full pages: {e}")
            # Fallback to page-span extraction
            end_page = (
                next_question_start - 1
                if next_question_start and (int(next_question_start) > int(page_start))
                else PDFOperations.get_page_count(pdf_path)
            )
            return PDFOperations.extract_pages(pdf_path, page_start, end_page, output_path)

    @staticmethod
    def extract_top_until_marker(
        pdf_path: str,
        page_num: int,
        target_number: Optional[object],
        output_path: Optional[str] = None,
    ) -> Optional[str]:
        """Extract the top region of a page until a question-number marker.

        - Useful for capturing the continuation of a multi-page question that spills
          onto the next page: returns from top to the next question marker.
        - If the marker is not found, returns the full page as a conservative fallback.
        - Returns the path to a single-page PDF, or None on failure.
        """
        def _to_int(val: object, default: int = 0) -> int:
            try:
                if isinstance(val, bool):
                    return default
                if isinstance(val, (int, float)):
                    return int(val)
                import re
                m = re.search(r"(\d+)", str(val) or "")
                return int(m.group(1)) if m else default
            except Exception:
                return default

        try:
            with fitz.open(str(pdf_path)) as src:
                if page_num < 1 or page_num > len(src):
                    return None
                page = src[page_num - 1]
                rect = page.rect
                y1 = rect.height
                tnum = _to_int(target_number, 0)
                if tnum > 0:
                    yn = PDFOperations.find_question_marker_y(page, tnum)
                    if yn is not None and yn > 0:
                        # crop to just above the marker
                        y1 = max(10.0, min(rect.height, yn - 4))
                out_doc = fitz.open()
                out_page = out_doc.new_page(width=rect.width, height=y1)
                out_page.show_pdf_page(
                    fitz.Rect(0, 0, rect.width, y1), src, page_num - 1, clip=fitz.Rect(0, 0, rect.width, y1)
                )
                if output_path is None:
                    temp_file = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
                    output_path = temp_file.name
                out_doc.save(output_path)
                out_doc.close()
                return output_path
        except Exception as e:
            logger.warning(f"Top-until-marker crop failed on page {page_num}: {e}")
            return None
    
    @staticmethod
    def get_page_text(pdf_path: str, page_num: int) -> str:
        """
        Extract text from a specific page.
        
        Args:
            pdf_path: Path to PDF file
            page_num: Page number (1-based)
            
        Returns:
            Text content of the page
        """
        try:
            with fitz.open(str(pdf_path)) as pdf:
                if page_num < 1 or page_num > len(pdf):
                    raise ValueError(f"Invalid page number {page_num}")
                    
                page = pdf[page_num - 1]  # Convert to 0-based
                return page.get_text()
                
        except Exception as e:
            logger.error(f"Failed to extract text from page {page_num}: {str(e)}")
            return ""
    
    @staticmethod
    def merge_pdfs(pdf_paths: List[str], output_path: str) -> None:
        """
        Merge multiple PDFs into one.
        
        Args:
            pdf_paths: List of PDF file paths to merge
            output_path: Path for the merged PDF
        """
        try:
            output = fitz.open()
            
            for pdf_path in pdf_paths:
                with fitz.open(str(pdf_path)) as pdf:
                    output.insert_pdf(pdf)
            
            output.save(output_path)
            output.close()
            
            logger.info(f"Merged {len(pdf_paths)} PDFs into {output_path}")
            
        except Exception as e:
            logger.error(f"Failed to merge PDFs: {str(e)}")
            raise PDFParsingError(f"PDF merge failed: {str(e)}")
    
    @staticmethod
    def validate_pdf(pdf_path: str) -> bool:
        """
        Validate if a file is a valid PDF.
        
        Args:
            pdf_path: Path to the PDF file
            
        Returns:
            True if valid PDF, False otherwise
        """
        try:
            with fitz.open(str(pdf_path)) as pdf:
                # Try to access first page to ensure it's readable
                if len(pdf) > 0:
                    _ = pdf[0]
                return True
        except:
            return False
    
    @staticmethod
    def get_page_metadata(pdf_path: str, page_num: int) -> Dict[str, Any]:
        """
        Get metadata for a specific page.
        
        Args:
            pdf_path: Path to PDF file
            page_num: Page number (1-based)
            
        Returns:
            Dictionary containing page metadata
        """
        try:
            with fitz.open(str(pdf_path)) as pdf:
                if page_num < 1 or page_num > len(pdf):
                    raise ValueError(f"Invalid page number {page_num}")
                
                page = pdf[page_num - 1]
                
                return {
                    "page_number": page_num,
                    "width": page.rect.width,
                    "height": page.rect.height,
                    "rotation": page.rotation,
                    "has_text": len(page.get_text()) > 0,
                    "text_length": len(page.get_text())
                }
                
        except Exception as e:
            logger.error(f"Failed to get page metadata: {str(e)}")
            return {}
