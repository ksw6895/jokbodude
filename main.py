#!/usr/bin/env python3

import os
import sys
from pathlib import Path
from datetime import datetime
import argparse
from typing import List, Tuple

from config import create_model
from pdf_processor import PDFProcessor
from pdf_creator import PDFCreator
from error_handler import ErrorHandler


def find_pdf_files(directory: str, pattern: str = "*.pdf") -> List[Path]:
    """Find all PDF files in a directory"""
    path = Path(directory)
    pdf_files = list(path.glob(pattern))
    return [f for f in pdf_files if not f.name.endswith('.Zone.Identifier')]


def process_lesson_with_all_jokbos(lesson_path: Path, jokbo_paths: List[Path], output_dir: Path, jokbo_dir: str, model, use_parallel: bool = False) -> bool:
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
        
        if use_parallel:
            print("  (병렬 처리 모드)")
            analysis_result = processor.analyze_pdfs_for_lesson_parallel(jokbo_path_strs, str(lesson_path))
        else:
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
        ErrorHandler.log_exception("PDF 처리", e, debug=True)
        return False


def process_jokbo_with_all_lessons(jokbo_path: Path, lesson_paths: List[Path], output_dir: Path, lesson_dir: str, model, use_parallel: bool = False) -> bool:
    """Process one jokbo PDF with all lesson PDFs (jokbo-centric)"""
    try:
        print(f"\n처리 중...")
        print(f"  족보: {jokbo_path.name}")
        print(f"  강의자료 파일 {len(lesson_paths)}개와 비교")
        
        processor = PDFProcessor(model)
        creator = PDFCreator()
        
        print("  PDF 분석 중...")
        # Convert Path objects to strings
        lesson_path_strs = [str(path) for path in lesson_paths]
        
        if use_parallel:
            print("  (병렬 처리 모드)")
            analysis_result = processor.analyze_lessons_for_jokbo_parallel(lesson_path_strs, str(jokbo_path))
        else:
            analysis_result = processor.analyze_lessons_for_jokbo(lesson_path_strs, str(jokbo_path))
        
        if "error" in analysis_result:
            print(f"  오류 발생: {analysis_result['error']}")
            return False
        
        output_filename = f"jokbo_centric_{jokbo_path.stem}_all_lessons.pdf"
        output_path = output_dir / output_filename
        
        print("  필터링된 PDF 생성 중...")
        try:
            creator.create_jokbo_centric_pdf(str(jokbo_path), analysis_result, str(output_path), lesson_dir)
            print(f"  PDF 생성 완료: {output_path}")
        except Exception as pdf_e:
            print(f"  PDF 생성 중 오류 발생: {str(pdf_e)}")
            import traceback
            traceback.print_exc()
            return False
        
        if analysis_result.get("summary"):
            summary = analysis_result["summary"]
            print(f"  완료! 족보 페이지: {summary['total_jokbo_pages']}개")
            print(f"  총 문제: {summary.get('total_questions', 'N/A')}개")
            print(f"  관련 강의 슬라이드: {summary.get('total_related_slides', 'N/A')}개")
        
        return True
        
    except Exception as e:
        ErrorHandler.log_exception("PDF 처리", e, debug=True)
        return False


def main():
    parser = argparse.ArgumentParser(description="족보 기반 강의자료 필터링 시스템")
    parser.add_argument("--jokbo-dir", default="jokbo", help="족보 PDF 디렉토리 (기본값: jokbo)")
    parser.add_argument("--lesson-dir", default="lesson", help="강의자료 PDF 디렉토리 (기본값: lesson)")
    parser.add_argument("--output-dir", default="output", help="출력 디렉토리 (기본값: output)")
    parser.add_argument("--single-lesson", help="특정 강의자료 파일만 사용")
    parser.add_argument("--parallel", action="store_true", help="병렬 처리 모드 사용 (더 빠른 처리)")
    parser.add_argument("--mode", choices=["lesson-centric", "jokbo-centric"], default="lesson-centric", 
                       help="분석 모드 선택 (기본값: lesson-centric)")
    parser.add_argument("--model", choices=["pro", "flash", "flash-lite"], default="pro",
                       help="Gemini 모델 선택 (기본값: pro)")
    parser.add_argument("--thinking-budget", type=int, default=None,
                       help="Flash/Flash-lite 모델의 thinking budget (0-24576, -1은 자동)")
    
    args = parser.parse_args()
    
    # Create model with specified configuration
    model = create_model(args.model, args.thinking_budget)
    
    if args.model != "pro":
        print(f"사용 모델: Gemini 2.5 {args.model.upper()}")
        if args.thinking_budget is not None:
            if args.thinking_budget == 0:
                print("  Thinking 비활성화 (최고 속도/최저 비용)")
            elif args.thinking_budget == -1:
                print("  Thinking 자동 설정")
            else:
                print(f"  Thinking budget: {args.thinking_budget}")
    else:
        print("사용 모델: Gemini 2.5 Pro")
    
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
    
    successful = 0
    failed = 0
    
    if args.mode == "lesson-centric":
        print(f"각 강의자료별로 모든 족보와 비교하여 처리합니다.")
        if args.parallel:
            print("병렬 처리 모드가 활성화되었습니다. (더 빠른 처리)")
        print()
        
        # Process each lesson with all jokbos
        for lesson_file in lesson_files:
            if process_lesson_with_all_jokbos(lesson_file, jokbo_files, output_dir, args.jokbo_dir, model, args.parallel):
                successful += 1
            else:
                failed += 1
    else:  # jokbo-centric
        print(f"각 족보별로 모든 강의자료와 비교하여 처리합니다.")
        if args.parallel:
            print("병렬 처리 모드가 활성화되었습니다. (더 빠른 처리)")
        print()
        
        # Process each jokbo with all lessons
        for jokbo_file in jokbo_files:
            if process_jokbo_with_all_lessons(jokbo_file, lesson_files, output_dir, args.lesson_dir, model, args.parallel):
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