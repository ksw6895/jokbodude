#!/usr/bin/env python3

import os
import sys
from pathlib import Path
from datetime import datetime
import argparse
from typing import List, Tuple

from config import model
from pdf_processor import PDFProcessor
from pdf_creator import PDFCreator


def find_pdf_files(directory: str, pattern: str = "*.pdf") -> List[Path]:
    """Find all PDF files in a directory"""
    path = Path(directory)
    pdf_files = list(path.glob(pattern))
    return [f for f in pdf_files if not f.name.endswith('.Zone.Identifier')]


def process_lesson_with_all_jokbos(lesson_path: Path, jokbo_paths: List[Path], output_dir: Path, jokbo_dir: str) -> bool:
    """Process one lesson PDF with all jokbo PDFs"""
    try:
        print(f"\n처리 중...")
        print(f"  강의자료: {lesson_path.name}")
        print(f"  족보 파일 {len(jokbo_paths)}개와 비교")
        
        processor = PDFProcessor(model)
        creator = PDFCreator()
        
        print("  PDF 분석 중...")
        # Convert Path objects to strings
        jokbo_path_strs = [str(path) for path in jokbo_paths]
        analysis_result = processor.analyze_pdfs_for_lesson(jokbo_path_strs, str(lesson_path))
        
        if "error" in analysis_result:
            print(f"  오류 발생: {analysis_result['error']}")
            return False
        
        output_filename = f"filtered_{lesson_path.stem}_all_jokbos.pdf"
        output_path = output_dir / output_filename
        
        print("  필터링된 PDF 생성 중...")
        creator.create_filtered_pdf(str(lesson_path), analysis_result, str(output_path), jokbo_dir)
        
        if analysis_result.get("summary"):
            summary = analysis_result["summary"]
            print(f"  완료! 관련 슬라이드: {summary['total_related_slides']}개")
            print(f"  총 관련 문제: {summary.get('total_questions', 'N/A')}개")
        
        return True
        
    except Exception as e:
        print(f"  처리 중 오류 발생: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


def main():
    parser = argparse.ArgumentParser(description="족보 기반 강의자료 필터링 시스템")
    parser.add_argument("--jokbo-dir", default="jokbo", help="족보 PDF 디렉토리 (기본값: jokbo)")
    parser.add_argument("--lesson-dir", default="lesson", help="강의자료 PDF 디렉토리 (기본값: lesson)")
    parser.add_argument("--output-dir", default="output", help="출력 디렉토리 (기본값: output)")
    parser.add_argument("--single-lesson", help="특정 강의자료 파일만 사용")
    
    args = parser.parse_args()
    
    output_dir = Path(args.output_dir)
    output_dir.mkdir(exist_ok=True)
    
    # Find all jokbo files
    jokbo_files = find_pdf_files(args.jokbo_dir)
    
    if not jokbo_files:
        print(f"오류: {args.jokbo_dir} 디렉토리에 PDF 파일이 없습니다.")
        return 1
    
    # Find lesson files
    if args.single_lesson:
        lesson_files = [Path(args.single_lesson)]
    else:
        lesson_files = find_pdf_files(args.lesson_dir)
    
    if not lesson_files:
        print(f"오류: {args.lesson_dir} 디렉토리에 PDF 파일이 없습니다.")
        return 1
    
    print(f"족보 파일 {len(jokbo_files)}개, 강의자료 파일 {len(lesson_files)}개 발견")
    print(f"각 강의자료별로 모든 족보와 비교하여 처리합니다.\n")
    
    successful = 0
    failed = 0
    
    # Process each lesson with all jokbos
    for lesson_file in lesson_files:
        if process_lesson_with_all_jokbos(lesson_file, jokbo_files, output_dir, args.jokbo_dir):
            successful += 1
        else:
            failed += 1
    
    print(f"\n처리 완료!")
    print(f"  성공: {successful}개")
    print(f"  실패: {failed}개")
    print(f"  결과 저장 위치: {output_dir.absolute()}")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())