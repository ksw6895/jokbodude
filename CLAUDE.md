# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a PDF processing system that filters lecture materials based on exam questions (족보). It uses Google's Gemini AI API to analyze the relationship between lecture slides and exam questions, then creates filtered PDFs containing only the relevant lecture content paired with related exam questions.

## Development Commands

### Setup and Running
```bash
# Create virtual environment (if not exists)
python -m venv venv

# Activate virtual environment
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run the main application (processes all files)
python main.py

# Run with specific files
python main.py --single-lesson "lesson/specific_file.pdf"

# Run with custom directories
python main.py --jokbo-dir "custom_jokbo" --lesson-dir "custom_lesson" --output-dir "custom_output"

# Run with parallel processing (faster)
python main.py --parallel
```

### Environment Configuration
1. Copy `.env.example` to `.env`
2. Add your Gemini API key: `GEMINI_API_KEY=your_actual_api_key_here`

## Architecture

### Core Components

1. **main.py**: Entry point that orchestrates the PDF processing workflow
   - Finds PDF files in jokbo and lesson directories (ignores Zone.Identifier files)
   - Processes each lesson file against all jokbo files
   - Manages output directory and file naming
   - Supports both sequential and parallel processing modes
   - Progress tracking with colored output

2. **config.py**: Gemini AI configuration
   - Loads API key from environment
   - Configures Gemini 2.5 Pro model with JSON response format
   - Sets safety settings to avoid content blocking
   - Temperature: 0.3 for consistent results
   - Max output tokens: 100,000

3. **pdf_processor.py**: Handles AI analysis
   - Uploads PDFs to Gemini API (one jokbo at a time with the lesson)
   - `analyze_single_jokbo_with_lesson()`: Analyzes one jokbo-lesson pair
   - `analyze_single_jokbo_with_lesson_preloaded()`: Analyzes with pre-uploaded lesson file
   - `analyze_pdfs_for_lesson()`: Processes multiple jokbo files sequentially
   - `analyze_pdfs_for_lesson_parallel()`: True parallel processing with pre-uploaded lesson
   - Returns structured JSON with slide-to-question mappings
   - Manages file cleanup on destruction
   - Enforces 1:1 question-to-slide mapping
   - Includes image matching with higher importance scores (9-10)

4. **pdf_creator.py**: Creates filtered output PDFs
   - Uses PyMuPDF as primary PDF manipulation library
   - `extract_jokbo_question()`: Extracts full pages from jokbo PDFs (supports multi-page questions)
   - Combines lecture slides with full jokbo question pages
   - Adds Gemini-generated explanations with wrong answer analysis
   - Caches opened PDFs for performance
   - Creates summary page with statistics

### Data Flow

1. User runs `main.py` with optional arguments
2. System scans directories for PDF files (ignoring Zone.Identifier files)
3. For each lesson PDF:
   - Each jokbo PDF is uploaded to Gemini API one at a time (1 lesson + 1 jokbo)
   - AI analyzes relationships and returns JSON mapping for each pair
   - Results from all jokbo files are merged
   - System creates a single output PDF with filtered content
   - Original lecture slides are preserved, followed by:
     - Full jokbo question page(s) from the PDF (preserving images and choices)
     - Gemini-generated explanations and answers for each question

### Key Dependencies

- **google-generativeai**: Gemini AI API client (v0.8.4)
- **PyMuPDF (fitz)**: Primary PDF reading and manipulation
- **PyPDF2**: Additional PDF support
- **reportlab**: PDF creation capabilities
- **Pillow**: Image processing
- **python-dotenv**: Environment variable management

### Output Structure

Filtered PDFs are saved as: `filtered_{lesson_name}_all_jokbos.pdf`

Each output PDF contains:
- Original lecture slides that have related exam questions
- For each related question:
  - Full jokbo question page(s) from the PDF (preserving images and choices)
  - Gemini-generated explanation page with:
    - Correct answer
    - Detailed explanation
    - Wrong answer explanations (why each option is incorrect)
    - Relevance to lecture content
- Summary page with overall statistics and study recommendations
- Organized by lecture page order with related questions following each slide

## Recent Improvements (2025-07-24)

1. **Enhanced Prompt for Better Accuracy**
   - More strict criteria for slide relevance
   - Focus on "directly related" content only
   - Higher importance score thresholds (8-10 for direct relevance)
   - Enforces 1:1 question-to-slide mapping

2. **Wrong Answer Explanations**
   - Added `wrong_answer_explanations` field in JSON response
   - Each choice explained why it's incorrect
   - Helps students understand common mistakes

3. **Multi-Page Question Support**
   - Added `jokbo_end_page` field for questions spanning multiple pages
   - Automatically extracts all pages for a single question
   - Preserves complete question context

4. **True Parallel Processing**
   - Added `--parallel` flag for faster processing
   - Pre-uploads lesson file once before parallel processing
   - Each thread has independent PDFProcessor instance
   - Uses ThreadPoolExecutor with configurable workers (default: 3)
   - Real concurrent processing with timestamp logging
   - Significant speed improvement for multiple jokbo files

5. **Future Considerations**
   - Context Caching implementation for cost reduction
   - Upgrading to latest google-genai SDK
   - Async support for even better performance

## Key Implementation Details

- File uploads are managed with 2-second polling for status
- JSON response format is enforced through model configuration
- PDFProcessor instances manage their own file cleanup
- Progress is displayed with colored output (green for success, red for errors)
- Empty jokbo directories are handled gracefully
- Zone.Identifier files are automatically ignored