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
   - Returns structured JSON with slide-to-question mappings
   - Manages file cleanup on destruction

4. **pdf_creator.py**: Creates filtered output PDFs
   - Extracts relevant pages from original PDFs
   - `extract_jokbo_question()`: Crops specific question areas from jokbo PDFs
   - Combines lecture slides with cropped exam questions
   - Adds Gemini-generated explanations after each question
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
  - Cropped question portion from the jokbo PDF (preserving images)
  - Gemini-generated explanation page with answer and detailed analysis
- Summary page with overall statistics and study recommendations
- Organized by lecture page order with related questions following each slide