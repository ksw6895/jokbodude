#!/usr/bin/env python3
"""
Quick multi-API smoke test without Redis/Celery.

Usage:
  python scripts/smoke_multi_api.py \
      --mode jokbo-centric \
      --jokbo jokbo/sample.pdf \
      --lessons lesson/sample.pdf lesson/another.pdf \
      --workers 2

Env:
  .env with GEMINI_API_KEYS or GEMINI_API_KEY must be present.
"""

import argparse
import json
import os
from pathlib import Path
from typing import List

from dotenv import load_dotenv

# Local imports after dotenv
load_dotenv()

from config import create_model, configure_api, API_KEYS
from pdf_processor.core.processor import PDFProcessor


def pick_default_files() -> tuple[str, List[str]]:
    jokbo_dir = Path('jokbo')
    lesson_dir = Path('lesson')
    jokbos = sorted([p for p in jokbo_dir.glob('*.pdf')])
    lessons = sorted([p for p in lesson_dir.glob('*.pdf')])
    if not jokbos:
        raise SystemExit('No jokbo/*.pdf files found')
    if not lessons:
        raise SystemExit('No lesson/*.pdf files found')
    return str(jokbos[0]), [str(lessons[0])]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--mode', choices=['jokbo-centric', 'lesson-centric'], default='jokbo-centric')
    ap.add_argument('--jokbo', help='Path to jokbo PDF')
    ap.add_argument('--lessons', nargs='+', help='Paths to lesson PDFs')
    ap.add_argument('--workers', type=int, default=2, help='Max workers for multi-API')
    ap.add_argument('--model', choices=['flash', 'pro'], default=os.getenv('GEMINI_MODEL','flash'))
    ap.add_argument('--output', default='output/smoke_result.json')
    args = ap.parse_args()

    # Check API keys
    if not API_KEYS or len(API_KEYS) < 2:
        print('Warning: Multi-API test expects GEMINI_API_KEYS with 2+ keys; proceeding anyway.')

    # Select files
    if args.jokbo and args.lessons:
        jokbo_path = args.jokbo
        lesson_paths = args.lessons
    else:
        jokbo_path, lesson_paths = pick_default_files()

    # Configure API and model
    configure_api()  # uses default key
    model = create_model(args.model)
    processor = PDFProcessor(model, session_id='smoke')

    # Ensure output directory exists
    Path('output').mkdir(parents=True, exist_ok=True)

    if args.mode == 'jokbo-centric':
        print(f"Running jokbo-centric multi-API: jokbo={Path(jokbo_path).name} lessons={len(lesson_paths)}")
        result = processor.analyze_jokbo_centric_multi_api(lesson_paths, jokbo_path, api_keys=API_KEYS, max_workers=args.workers)
        with open(args.output, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        pages = (result.get('jokbo_pages') or []) if isinstance(result, dict) else []
        total_q = sum(len(p.get('questions', [])) for p in pages)
        print(f"OK: {len(pages)} jokbo pages, {total_q} questions. Saved to {args.output}")
    else:
        # lesson-centric: one lesson vs many jokbos
        print(f"Running lesson-centric multi-API: lesson={Path(lesson_paths[0]).name}")
        # gather jokbos
        jokbo_dir = Path('jokbo')
        jokbos = sorted([str(p) for p in jokbo_dir.glob('*.pdf')])
        if not jokbos:
            raise SystemExit('No jokbo/*.pdf files for lesson-centric test')
        result = processor.analyze_lesson_centric_multi_api(jokbos, lesson_paths[0], api_keys=API_KEYS, max_workers=args.workers)
        with open(args.output, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        slides = (result.get('related_slides') or []) if isinstance(result, dict) else []
        total_q = sum(len(s.get('related_jokbo_questions', [])) for s in slides)
        print(f"OK: {len(slides)} slides, {total_q} linked questions. Saved to {args.output}")


if __name__ == '__main__':
    main()
