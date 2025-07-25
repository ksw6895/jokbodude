#!/usr/bin/env python3

import os
import sys
from pathlib import Path
from datetime import datetime
import argparse
from typing import List, Tuple, Dict

from config import create_model
from pdf_processor import PDFProcessor
from pdf_creator import PDFCreator


def find_pdf_files(directory: str, pattern: str = "*.pdf") -> List[Path]:
    """Find all PDF files in a directory"""
    path = Path(directory)
    pdf_files = list(path.glob(pattern))
    return [f for f in pdf_files if not f.name.endswith('.Zone.Identifier')]


def process_lesson_mode(lesson_files: List[Path], jokbo_files: List[Path], output_dir: Path, args) -> int:
    """강의자료 중심 모드 처리 (중복 제거 포함)"""
    print("강의자료 중심 모드로 처리합니다.")
    
    # 모든 분석 결과를 저장할 딕셔너리
    all_results = {}
    successful = 0
    failed = 0
    
    # 1단계: 각 강의자료별로 분석 수행
    for lesson_file in lesson_files:
        try:
            print(f"\n처리 중...")
            print(f"  강의자료: {lesson_file.name}")
            print(f"  족보 파일 {len(jokbo_files)}개와 비교")
            
            # Create new model instance for each lesson to avoid caching
            processor = PDFProcessor(model=None)
            
            print("  PDF 분석 중...")
            jokbo_path_strs = [str(path) for path in jokbo_files]
            
            if args.parallel:
                print("  (병렬 처리 모드)")
                analysis_result = processor.analyze_pdfs_for_lesson_parallel(jokbo_path_strs, str(lesson_file))
            else:
                analysis_result = processor.analyze_pdfs_for_lesson(jokbo_path_strs, str(lesson_file))
            
            if "error" in analysis_result:
                print(f"  오류 발생: {analysis_result['error']}")
                failed += 1
                continue
            
            all_results[lesson_file.name] = {
                'path': lesson_file,
                'result': analysis_result
            }
            
            if analysis_result.get("summary"):
                summary = analysis_result["summary"]
                print(f"  분석 완료! 관련 슬라이드: {summary['total_related_slides']}개")
                print(f"  총 관련 문제: {summary.get('total_questions', 'N/A')}개")
            
            successful += 1
            
        except Exception as e:
            print(f"  처리 중 오류 발생: {str(e)}")
            import traceback
            traceback.print_exc()
            failed += 1
    
    # 2단계: 중복 제거를 위한 최적화
    if len(all_results) > 1:
        print("\n중복 문제 최적화 중...")
        optimize_results_across_lessons(all_results)
    
    # 3단계: 최적화된 결과로 PDF 생성
    print("\n최적화된 PDF 생성 중...")
    creator = PDFCreator()
    
    for lesson_name, data in all_results.items():
        try:
            output_filename = f"filtered_{Path(lesson_name).stem}_optimized.pdf"
            output_path = output_dir / output_filename
            
            print(f"  {lesson_name} -> {output_filename}")
            creator.create_filtered_pdf(str(data['path']), data['result'], str(output_path), args.jokbo_dir)
            
        except Exception as e:
            print(f"  PDF 생성 중 오류 발생: {str(e)}")
    
    print(f"\n처리 완료!")
    print(f"  성공: {successful}개")
    print(f"  실패: {failed}개")
    print(f"  결과 저장 위치: {output_dir.absolute()}")
    
    return 0


def process_jokbo_mode(jokbo_files: List[Path], lesson_files: List[Path], output_dir: Path, args) -> int:
    """족보 중심 모드 처리"""
    print("족보 중심 모드로 처리합니다.")
    
    successful = 0
    failed = 0
    # Create new model instance for jokbo mode
    processor = PDFProcessor(model=None)
    creator = PDFCreator()
    
    # 각 족보 파일에 대해 처리
    for jokbo_file in jokbo_files:
        try:
            print(f"\n처리 중...")
            print(f"  족보: {jokbo_file.name}")
            print(f"  강의자료 {len(lesson_files)}개와 비교")
            
            # 각 강의자료와 1:1 분석
            lesson_path_strs = [str(path) for path in lesson_files]
            analysis_result = processor.analyze_jokbo_with_all_lessons(str(jokbo_file), lesson_path_strs)
            
            if "error" in analysis_result:
                print(f"  오류 발생: {analysis_result['error']}")
                failed += 1
                continue
            
            # 족보 중심 PDF 생성
            output_filename = f"jokbo_centered_{jokbo_file.stem}.pdf"
            output_path = output_dir / output_filename
            
            print("  필터링된 PDF 생성 중...")
            creator.create_jokbo_centered_pdf(str(jokbo_file), analysis_result, str(output_path), args.lesson_dir)
            
            successful += 1
            print(f"  완료! -> {output_filename}")
            
        except Exception as e:
            print(f"  처리 중 오류 발생: {str(e)}")
            import traceback
            traceback.print_exc()
            failed += 1
    
    print(f"\n처리 완료!")
    print(f"  성공: {successful}개")
    print(f"  실패: {failed}개")
    print(f"  결과 저장 위치: {output_dir.absolute()}")
    
    return 0


def optimize_results_across_lessons(all_results: Dict[str, Dict]) -> None:
    """여러 강의자료의 결과를 최적화하여 중복 제거"""
    # 모든 문제와 그 출현 정보를 수집
    question_appearances = {}  # {(jokbo_filename, question_number): [(lesson_name, slide_page, importance_score, question_data)]}
    
    # 1. 모든 문제 출현 수집
    for lesson_name, data in all_results.items():
        result = data['result']
        if 'related_slides' not in result:
            continue
            
        for slide in result['related_slides']:
            slide_page = slide['lesson_page']
            importance_score = slide.get('importance_score', 5)
            
            for question in slide.get('related_jokbo_questions', []):
                key = (question['jokbo_filename'], question['question_number'])
                if key not in question_appearances:
                    question_appearances[key] = []
                question_appearances[key].append((lesson_name, slide_page, importance_score, question))
    
    # 2. 각 문제에 대해 최적 매칭 선택
    question_assignments = {}  # {(jokbo_filename, question_number): lesson_name}
    
    for key, appearances in question_appearances.items():
        if len(appearances) > 1:
            # 중요도 순으로 정렬 (높은 순)
            appearances.sort(key=lambda x: -x[2])
            best_lesson = appearances[0][0]
            question_assignments[key] = best_lesson
            
            print(f"  문제 {key[1]}번: {len(appearances)}개 강의자료에서 발견 → {best_lesson}에만 유지 (중요도: {appearances[0][2]})")
        else:
            question_assignments[key] = appearances[0][0]
    
    # 3. 각 강의자료의 결과를 업데이트
    for lesson_name, data in all_results.items():
        result = data['result']
        if 'related_slides' not in result:
            continue
        
        new_slides = []
        removed_count = 0
        
        for slide in result['related_slides']:
            new_questions = []
            
            for question in slide.get('related_jokbo_questions', []):
                key = (question['jokbo_filename'], question['question_number'])
                if question_assignments.get(key) == lesson_name:
                    new_questions.append(question)
                else:
                    removed_count += 1
            
            if new_questions:  # 질문이 남아있는 슬라이드만 유지
                slide['related_jokbo_questions'] = new_questions
                new_slides.append(slide)
        
        result['related_slides'] = new_slides
        
        # 요약 정보 업데이트
        if 'summary' in result:
            total_questions = sum(len(slide.get('related_jokbo_questions', [])) for slide in new_slides)
            result['summary']['total_questions'] = total_questions
            result['summary']['total_related_slides'] = len(new_slides)
            if removed_count > 0:
                result['summary']['removed_duplicates'] = removed_count
                print(f"  {lesson_name}: {removed_count}개 중복 제거")


def process_lesson_with_all_jokbos(lesson_path: Path, jokbo_paths: List[Path], output_dir: Path, jokbo_dir: str, use_parallel: bool = False) -> bool:
    """Process one lesson PDF with all jokbo PDFs"""
    try:
        print(f"\n처리 중...")
        print(f"  강의자료: {lesson_path.name}")
        print(f"  족보 파일 {len(jokbo_paths)}개와 비교")
        
        # Create new model instance for each lesson to avoid caching
        processor = PDFProcessor(model=None)
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
    parser.add_argument("--parallel", action="store_true", help="병렬 처리 모드 사용 (더 빠른 처리)")
    parser.add_argument("--mode", choices=["lesson", "jokbo"], default="lesson", 
                        help="처리 모드 선택: lesson(강의자료 중심) 또는 jokbo(족보 중심)")
    
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
    print(f"모드: {'강의자료 중심' if args.mode == 'lesson' else '족보 중심'}")
    if args.parallel and args.mode == 'lesson':
        print("병렬 처리 모드가 활성화되었습니다. (더 빠른 처리)")
    print()
    
    if args.mode == "lesson":
        # 강의자료 중심 모드
        return process_lesson_mode(lesson_files, jokbo_files, output_dir, args)
    else:
        # 족보 중심 모드
        return process_jokbo_mode(jokbo_files, lesson_files, output_dir, args)


if __name__ == "__main__":
    sys.exit(main())