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

    # Cache of detected dominant numbering style per PDF path
    _style_cache: Dict[str, Optional[str]] = {}

    @staticmethod
    def _style_patterns() -> Dict[str, str]:
        """Return canonical token regex (no capture) for numbering styles.

        Keys:
        - dot: "1."
        - right_paren: "1)"
        - paren: "(1)"
        - paren_dot: "(1)."
        - hangul: "1번"
        - q: "Q 1"
        - plain: "1"
        """
        return {
            "dot": r"^\d{1,3}\.$",
            "right_paren": r"^\d{1,3}\)$",
            "paren": r"^\(\s*\d{1,3}\s*\)$",
            "paren_dot": r"^\(\s*\d{1,3}\s*\)\.$",
            "hangul": r"^\d{1,3}\s*번$",
            "q": r"^Q\s*\d{1,3}$",
            "plain": r"^\d{1,3}$",
        }

    @staticmethod
    def _pattern_for_number(style: str, n: int) -> str:
        """Return exact-token regex for a given number in a given style."""
        s = str(int(n))
        if style == "dot":
            return rf"^{s}\.$"
        if style == "right_paren":
            return rf"^{s}\)$"
        if style == "paren":
            return rf"^\(\s*{s}\s*\)$"
        if style == "paren_dot":
            return rf"^\(\s*{s}\s*\)\.$"
        if style == "hangul":
            return rf"^{s}\s*번$"
        if style == "q":
            return rf"^Q\s*{s}$"
        if style == "plain":
            return rf"^{s}$"
        # Fallback: strict generic
        return rf"^\(?\s*{s}\s*\)?[\.)]?$"

    @staticmethod
    def detect_dominant_number_style(pdf_path: str, sample_numbers: int = 25) -> Optional[str]:
        """Detect the predominant numbering style by checking actual question numbers.

        Strategy: build an index of detected question numbers by page. For the
        first up-to N unique numbers in appearance order, find which canonical
        style token (e.g., "1.", "(1)") appears on that page for that number,
        preferring the left-most/top-most token. The style with the highest
        frequency wins. This avoids bias from multiple-choice options (1~5).
        """
        try:
            key = str(Path(pdf_path))
            if key in PDFOperations._style_cache:
                return PDFOperations._style_cache[key]
            pats = PDFOperations._style_patterns()
            import re as _re
            compiled = {k: _re.compile(v) for k, v in pats.items()}

            # Build ordered list of unique question numbers by first appearance
            index = PDFOperations.index_questions(pdf_path)
            if not index:
                PDFOperations._style_cache[key] = None
                return None
            ordered_unique: list[int] = []
            first_page: Dict[int, int] = {}
            for p, q in sorted(index, key=lambda t: (t[0], t[1])):
                if q not in first_page:
                    first_page[q] = p
                    ordered_unique.append(q)
            if not ordered_unique:
                PDFOperations._style_cache[key] = None
                return None

            counts: Dict[str, int] = {k: 0 for k in pats}
            limit = min(len(ordered_unique), max(1, int(sample_numbers)))
            with fitz.open(str(pdf_path)) as doc:
                for i in range(limit):
                    qn = int(ordered_unique[i])
                    pno = int(first_page.get(qn, 1))
                    pno = max(1, min(len(doc), pno))
                    page = doc[pno - 1]
                    try:
                        words = page.get_text("words") or []
                    except Exception:
                        words = []
                    w = page.rect.width
                    # Among tokens that match ANY style for this specific number,
                    # choose the one with smallest x0 (left-most), break ties by y0 (top-most)
                    candidates: list[tuple[float, float, str]] = []  # (x0, y0, style)
                    for x0, y0, x1, y1, text, *_ in words:
                        t = str(text or "").strip()
                        if not t:
                            continue
                        if float(x0) > (w * 0.45):
                            continue
                        for name, cp in compiled.items():
                            # Replace the number with concrete value pattern
                            cn = _re.compile(PDFOperations._pattern_for_number(name, qn))
                            if cn.match(t):
                                candidates.append((float(x0), float(y0), name))
                                break
                    if candidates:
                        candidates.sort(key=lambda t: (t[0], t[1]))
                        style = candidates[0][2]
                        counts[style] += 1
            # Choose max; tie-breaker priority
            priority = ["dot", "paren", "right_paren", "paren_dot", "hangul", "q", "plain"]
            best_style = None
            best_count = 0
            for name in priority:
                c = counts.get(name, 0)
                if c > best_count:
                    best_style = name
                    best_count = c
            PDFOperations._style_cache[key] = best_style
            return best_style
        except Exception:
            return None

    @staticmethod
    def _preferred_style(pdf_path: str) -> Optional[str]:
        """Get cached dominant numbering style using a robust detector.

        Preference order:
        1) Consecutive-run detector (1..k with a single style)
        2) Fallback to frequency-based detector
        """
        key = str(Path(pdf_path))
        if key in PDFOperations._style_cache:
            return PDFOperations._style_cache[key]
        style = PDFOperations.detect_question_style_consecutive(pdf_path)
        if style is None:
            style = PDFOperations.detect_dominant_number_style(pdf_path)
        PDFOperations._style_cache[key] = style
        return style

    @staticmethod
    def _style_capture_patterns() -> Dict[str, str]:
        """Return capturing regex for each numbering style (group(1) = number)."""
        return {
            "dot": r"^(\d{1,3})\.$",
            "right_paren": r"^(\d{1,3})\)$",
            "paren": r"^\(\s*(\d{1,3})\s*\)$",
            "paren_dot": r"^\(\s*(\d{1,3})\s*\)\.$",
            "hangul": r"^(\d{1,3})\s*번$",
            "q": r"^Q\s*(\d{1,3})$",
            "plain": r"^(\d{1,3})$",
        }

    @staticmethod
    def detect_question_style_consecutive(pdf_path: str, max_pages: int = 50) -> Optional[str]:
        """Detect question-number style by longest consecutive run from 1.

        Scans up to `max_pages` pages for tokens at the left margin that match a
        style-specific numeric marker. Builds a number set per style, then selects
        the style with the longest 1..k consecutive run. Ties resolved by a
        stable priority.
        """
        try:
            pats = PDFOperations._style_capture_patterns()
            import re as _re
            compiled = {k: _re.compile(v) for k, v in pats.items()}
            with fitz.open(str(pdf_path)) as doc:
                total = len(doc)
                limit = min(total, max(1, int(max_pages)))
                numbers_by_style: Dict[str, set] = {k: set() for k in pats}
                for i in range(limit):
                    page = doc[i]
                    try:
                        words = page.get_text("words") or []
                    except Exception:
                        words = []
                    if not words:
                        continue
                    w = page.rect.width
                    for x0, y0, x1, y1, text, *_ in words:
                        t = str(text or "").strip()
                        if not t:
                            continue
                        # left-margin bias to reduce choice tokens
                        if float(x0) > (w * 0.4):
                            continue
                        for name, cp in compiled.items():
                            m = cp.match(t)
                            if not m:
                                continue
                            try:
                                n = int(m.group(1))
                            except Exception:
                                continue
                            if 0 < n < 1000:
                                numbers_by_style[name].add(n)
                            break
                # Compute longest 1..k run per style
                def longest_run_from_one(nums: set[int]) -> int:
                    k = 0
                    n = 1
                    while n in nums:
                        k += 1
                        n += 1
                    return k
                runs = {name: longest_run_from_one(nums) for name, nums in numbers_by_style.items()}
                # Choose the style with largest run; require at least 2 to be meaningful
                priority = ["dot", "paren", "right_paren", "paren_dot", "hangul", "q", "plain"]
                best = None
                best_len = 0
                for name in priority:
                    rl = int(runs.get(name, 0))
                    if rl > best_len:
                        best = name
                        best_len = rl
                if best and best_len >= 2:
                    return best
                return None
        except Exception:
            return None

    @staticmethod
    def _find_next_marker_in_doc(doc: fitz.Document, style: Optional[str], start_page: int, current_y: float, lookahead_pages: int = 3) -> Optional[Tuple[int, int, float]]:
        """Find the next question marker after (start_page, current_y).

        Returns (page_number, number, y) or None if not found.
        - Same-page: next token with y > current_y that matches `style`.
        - Next pages: the top-most token that matches `style`.
        """
        import re as _re
        if doc is None:
            return None
        total = len(doc)
        # Compile capture for selected style or accept any style when None
        if style:
            pats = {style: PDFOperations._style_capture_patterns().get(style)}
        else:
            pats = PDFOperations._style_capture_patterns()
        compiled = {k: _re.compile(v) for k, v in pats.items() if v}

        # Same-page search
        try:
            page = doc[int(start_page) - 1]
            words = page.get_text("words") or []
        except Exception:
            words = []
        candidates: list[tuple[float, float, int]] = []  # (y, x, num)
        if words:
            w = page.rect.width
            for x0, y0, x1, y1, text, *_ in words:
                t = str(text or "").strip()
                if not t:
                    continue
                if float(x0) > (w * 0.4):
                    continue
                if float(y0) <= float(current_y) + 6.0:
                    continue
                for name, cp in compiled.items():
                    m = cp.match(t)
                    if m:
                        try:
                            n = int(m.group(1))
                            if 0 < n < 1000:
                                candidates.append((float(y0), float(x0), int(n)))
                                break
                        except Exception:
                            pass
        if candidates:
            candidates.sort(key=lambda t: (t[0], t[1]))
            y, x, n = candidates[0]
            return (int(start_page), int(n), float(y))

        # Next pages search (limited lookahead)
        max_p = min(total, int(start_page) + int(max(0, lookahead_pages)))
        for p in range(int(start_page) + 1, max_p + 1):
            try:
                page = doc[int(p) - 1]
                words = page.get_text("words") or []
            except Exception:
                words = []
            if not words:
                continue
            w = page.rect.width
            pg_candidates: list[tuple[float, float, int]] = []
            for x0, y0, x1, y1, text, *_ in words:
                t = str(text or "").strip()
                if not t:
                    continue
                if float(x0) > (w * 0.4):
                    continue
                for name, cp in compiled.items():
                    m = cp.match(t)
                    if m:
                        try:
                            n = int(m.group(1))
                            if 0 < n < 1000:
                                pg_candidates.append((float(y0), float(x0), int(n)))
                                break
                        except Exception:
                            pass
            if pg_candidates:
                pg_candidates.sort(key=lambda t: (t[0], t[1]))
                y, x, n = pg_candidates[0]
                return (int(p), int(n), float(y))
        return None

    # -----------------------------
    # Low-level marker search utils
    # -----------------------------
    @staticmethod
    def find_question_marker_y(page: fitz.Page, target_num: int, preferred_style: Optional[str] = None) -> Optional[float]:
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
            if preferred_style:
                pats = [PDFOperations._pattern_for_number(preferred_style, target_num)]
            else:
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
                if preferred_style:
                    pats = [PDFOperations._pattern_for_number(preferred_style, target_num)]
                else:
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
            style = PDFOperations._preferred_style(pdf_path)
            with fitz.open(str(pdf_path)) as src:
                total_pages = len(src)
                if page_start < 1 or page_start > total_pages:
                    raise ValueError("Invalid page_start")

                # Determine page_end handling possible same-page next start
                same_page_next = False  # prefer scanning-based boundary
                page_end = page_start  # conservative default

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
                            y_found = PDFOperations.find_question_marker_y(page, qnum_int, preferred_style=style)
                            if y_found is not None:
                                y0 = max(0.0, y_found - 4)  # small margin
                        # Attempt to find the next question marker on this page by scanning
                        scan_next = PDFOperations._find_next_marker_in_doc(src, style, page_start, y0)
                        if scan_next and int(scan_next[0]) == int(page_start):
                            yn = float(scan_next[2])
                            if yn > y0:
                                y1 = min(y1, yn - 4)
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

                # After cropping the start page, scan ahead for the next header and append accordingly
                try:
                    next_marker = PDFOperations._find_next_marker_in_doc(src, style, page_start, y0)
                except Exception:
                    next_marker = None
                if next_marker and int(next_marker[0]) > int(page_start):
                    np, nn, ny = int(next_marker[0]), int(next_marker[1]), float(next_marker[2])
                    # Include any full middle pages
                    if np - page_start > 1:
                        try:
                            out_doc.insert_pdf(src, from_page=page_start, to_page=np - 2)
                        except Exception:
                            pass
                    # Crop the end (next-marker) page from top until the marker using number
                    try:
                        end_crop_path = PDFOperations.extract_top_until_marker(pdf_path, np, nn)
                    except Exception:
                        end_crop_path = None
                    if end_crop_path:
                        try:
                            with fitz.open(end_crop_path) as tmp:
                                out_doc.insert_pdf(tmp)
                        except Exception:
                            pass

                # Persist output
                if output_path is None:
                    temp_file = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
                    output_path = temp_file.name
                out_doc.save(output_path)
                out_doc.close()
                return output_path
        except Exception as e:
            logger.warning(f"Cropping failed; falling back to conservative page-span: {e}")
            # Fallback to page-span extraction; do not include the entire document when
            # next_question_start is missing. Default to a single page.
            try:
                if next_question_start and (int(next_question_start) > int(page_start)):
                    end_page = int(next_question_start) - 1
                else:
                    end_page = int(page_start)
            except Exception:
                end_page = int(page_start)
            return PDFOperations.extract_pages(pdf_path, int(page_start), int(end_page), output_path)

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
            style = PDFOperations._preferred_style(pdf_path)
            with fitz.open(str(pdf_path)) as src:
                if page_num < 1 or page_num > len(src):
                    return None
                page = src[page_num - 1]
                rect = page.rect
                y1 = rect.height
                tnum = _to_int(target_number, 0)
                if tnum > 0:
                    yn = PDFOperations.find_question_marker_y(page, tnum, preferred_style=style)
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

    # -----------------------------
    # Exam-only helpers (OCR-aided)
    # -----------------------------
    @staticmethod
    def _detect_question_numbers_on_page(page: fitz.Page) -> list[int]:
        """Return a list of candidate question numbers detected on a page.

        - Uses text extraction with left-margin heuristic first
        - Falls back to pytesseract OCR if no reliable words extracted
        - Returns unique numbers (ascending)
        """
        nums: set[int] = set()
        try:
            words = page.get_text("words") or []
        except Exception:
            words = []
        w = page.rect.width
        if words:
            import re as _re
            for x0, y0, x1, y1, text, *_ in words:
                t = str(text or "").strip()
                if not t:
                    continue
                # left margin filter to reduce noise from choices like "1)" in center
                if float(x0) > (w * 0.4):
                    continue
                # Strict token match: the token must be exactly a question marker
                # Examples that should match: "1", "(1)", "1.", "1)", "1번", "Q 1"
                # Avoid partial matches like "2020년" (previously captured as 202).
                m = _re.search(r"^(?:\(?\s*(\d{1,3})\s*\)?[\.)]?|((?:\d{1,3}))번|Q\s*(\d{1,3}))$", t)
                if m:
                    try:
                        n = int(next(g for g in m.groups() if g))
                        if 0 < n < 1000:
                            nums.add(n)
                    except Exception:
                        pass
        # OCR fallback if nothing found
        if not nums:
            try:
                from PIL import Image
                import io as _io
                import pytesseract
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
                img = Image.open(_io.BytesIO(pix.tobytes("png")))
                data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
                import re as _re
                n = len(data.get("text", []))
                for i in range(n):
                    t = (data["text"][i] or "").strip()
                    if not t:
                        continue
                    # Same strict token rule for OCR tokens
                    m = _re.search(r"^(?:\(?\s*(\d{1,3})\s*\)?[\.)]?|((?:\d{1,3}))번|Q\s*(\d{1,3}))$", t)
                    if m:
                        try:
                            n = int(next(g for g in m.groups() if g))
                            if 0 < n < 1000:
                                nums.add(n)
                        except Exception:
                            pass
            except Exception:
                pass
        return sorted(nums)

    @staticmethod
    def index_questions(pdf_path: str) -> list[tuple[int, int]]:
        """Scan the PDF and return a list of (page_number, question_number) tuples.

        The list is sorted by page_number then by question_number.
        """
        out: list[tuple[int, int]] = []
        try:
            with fitz.open(str(pdf_path)) as doc:
                for i in range(len(doc)):
                    page = doc[i]
                    qnums = PDFOperations._detect_question_numbers_on_page(page)
                    for q in qnums:
                        out.append((i + 1, int(q)))
        except Exception:
            return []
        # De-duplicate per (page, qnum)
        try:
            out = sorted(set(out), key=lambda t: (t[0], t[1]))
        except Exception:
            pass
        return out

    @staticmethod
    def split_by_question_groups(pdf_path: str, group_size: int = 20) -> list[tuple[int, int, int, int]]:
        """Compute contiguous page ranges for groups of questions by appearance order.

        More robust than numeric bucketing (1–20, 21–40...), this uses the
        first appearance order of detected question numbers across pages and
        chunks them into blocks of `group_size` questions. This avoids creating
        spurious groups like 201–220 when headers such as "2020" are present.

        Returns a list of tuples: (start_page, end_page, group_start_q, group_end_q)
        where group_start_q/group_end_q are the first/last detected question numbers
        in that block (not synthetic ranges).
        """
        index = PDFOperations.index_questions(pdf_path)
        # Fallback: whole file as one chunk if nothing detected
        if not index:
            try:
                with fitz.open(str(pdf_path)) as doc:
                    total_pages = len(doc)
            except Exception:
                total_pages = 1
            return [(1, max(1, int(total_pages)), 1, int(group_size))]

        # Build qnum -> first page map, and an ordered list by first appearance (page ascending)
        first_page_for_q: dict[int, int] = {}
        ordered_unique_q: list[int] = []
        for p, q in sorted(index, key=lambda t: (t[0], t[1])):
            if q not in first_page_for_q:
                first_page_for_q[q] = p
                ordered_unique_q.append(q)

        if not ordered_unique_q:
            try:
                with fitz.open(str(pdf_path)) as doc:
                    total_pages = len(doc)
            except Exception:
                total_pages = 1
            return [(1, max(1, int(total_pages)), 1, int(group_size))]

        try:
            with fitz.open(str(pdf_path)) as doc:
                total_pages = len(doc)
        except Exception:
            total_pages = 0

        groups: list[tuple[int, int, int, int]] = []
        n = len(ordered_unique_q)
        i = 0
        while i < n:
            block = ordered_unique_q[i : min(i + int(max(1, group_size)), n)]
            g_start_q = int(block[0])
            g_end_q = int(block[-1])
            start_page = int(first_page_for_q[g_start_q])
            # Determine end_page by looking at next block's first page
            j = i + int(max(1, group_size))
            if j < n:
                next_first_q = int(ordered_unique_q[j])
                next_first_page = int(first_page_for_q.get(next_first_q, total_pages))
                # Include the next block's first-page as overlap to allow the model
                # to see the boundary marker for the last question of this chunk.
                end_page = max(start_page, next_first_page)
            else:
                end_page = total_pages
            if total_pages and end_page > total_pages:
                end_page = total_pages
            groups.append((start_page, int(end_page or start_page), g_start_q, g_end_q))
            i = j

        # Ensure sorted by page order
        groups.sort(key=lambda t: (t[0], t[2]))
        return groups
    
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
