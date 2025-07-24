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
source venv/bin/activate

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
   - Finds PDF files in jokbo and lesson directories
   - Processes each lesson file against all jokbo files
   - Manages output directory and file naming

2. **config.py**: Gemini AI configuration
   - Loads API key from environment
   - Configures model with JSON response format
   - Sets safety settings to avoid content blocking

3. **pdf_processor.py**: Handles AI analysis
   - Uploads PDFs to Gemini API (one jokbo at a time with the lesson)
   - `analyze_single_jokbo_with_lesson()`: Analyzes one jokbo-lesson pair
   - `analyze_pdfs_for_lesson()`: Processes multiple jokbo files and merges results
   - `analyze_pdfs_for_lesson_parallel()`: Parallel processing version using ThreadPoolExecutor
   - Returns structured JSON with slide-to-question mappings
   - Manages file cleanup on destruction
   - Improved prompts for more accurate slide matching
   - Includes wrong answer explanations in analysis

4. **pdf_creator.py**: Creates filtered output PDFs
   - Extracts relevant pages from original PDFs
   - `extract_jokbo_question()`: Extracts full pages from jokbo PDFs (supports multi-page questions)
   - Combines lecture slides with full jokbo question pages
   - Adds Gemini-generated explanations with wrong answer analysis
   - Uses PyMuPDF for PDF manipulation
   - Caches opened PDFs for performance

### Data Flow

1. User runs `main.py` with optional arguments
2. System scans directories for PDF files (ignoring Zone.Identifier files)
3. For each lesson PDF:
   - Each jokbo PDF is uploaded to Gemini API one at a time (1 lesson + 1 jokbo)
   - AI analyzes relationships and returns JSON mapping for each pair
   - Results from all jokbo files are merged
   - System creates a single output PDF with filtered content
   - Original lecture slides are preserved, followed by:
     - Cropped question portions from jokbo PDFs (with images preserved)
     - Gemini-generated explanations and answers for each question

### Key Dependencies

- **google-generativeai**: Gemini AI API client
- **PyMuPDF (fitz)**: PDF reading and manipulation
- **reportlab**: PDF creation (though primarily using PyMuPDF)
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

2. **Wrong Answer Explanations**
   - Added `wrong_answer_explanations` field in JSON response
   - Each choice explained why it's incorrect
   - Helps students understand common mistakes

3. **Multi-Page Question Support**
   - Added `jokbo_end_page` field for questions spanning multiple pages
   - Automatically extracts all pages for a single question
   - Preserves complete question context

4. **Parallel Processing**
   - Added `--parallel` flag for faster processing
   - Uses ThreadPoolExecutor with configurable workers
   - Significant speed improvement for multiple jokbo files

5. **Future Considerations**
   - Context Caching implementation for cost reduction
   - Upgrading to latest google-genai SDK
   - Async support for even better performance