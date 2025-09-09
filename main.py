#!/usr/bin/env python3

import os
import sys
from pathlib import Path
from datetime import datetime, timedelta
import argparse
from typing import List, Tuple, Optional
import shutil
import json
from multiprocessing import Process, Queue
import multiprocessing
import atexit
import signal

import google.generativeai as genai

from config import create_model, configure_api
from pdf_processor import PDFProcessor
from pdf_creator import PDFCreator
from error_handler import ErrorHandler
from path_validator import PathValidator


# 전역 변수로 Multi-API 모드 상태 저장
_multi_api_mode = False
_original_api_key = None


def cleanup_on_exit():
    """프로그램 종료 시 업로드된 파일 정리"""
    if _multi_api_mode:
        print("\n프로그램 종료 중... 업로드된 파일 정리")
        from config import API_KEYS
        
        # 각 API별로 파일 정리 시도
        for i, api_key in enumerate(API_KEYS):
            try:
                from config import configure_api
                configure_api(api_key)
                files = list(genai.list_files())
                if files:
                    print(f"  API #{i+1}: {len(files)}개 파일 정리 중...")
                    deleted = 0
                    for file in files:
                        try:
                            genai.delete_file(file.name)
                            deleted += 1
                        except:
                            pass
                    if deleted > 0:
                        print(f"    {deleted}개 삭제됨")
            except:
                pass
        
        # 원래 API 키로 복원
        if _original_api_key:
            configure_api(_original_api_key)
    else:
        # 일반 모드에서도 파일 정리
        try:
            files = list(genai.list_files())
            if files:
                print(f"\n프로그램 종료 중... {len(files)}개 파일 정리")
                deleted = 0
                for file in files:
                    try:
                        genai.delete_file(file.name)
                        deleted += 1
                    except:
                        pass
                if deleted > 0:
                    print(f"  {deleted}개 파일 삭제됨")
        except:
            pass


def signal_handler(signum, frame):
    """Ctrl+C 등의 시그널 처리"""
    print("\n\n중단 신호 감지...")
    cleanup_on_exit()
    sys.exit(1)


# 종료 핸들러 등록
atexit.register(cleanup_on_exit)
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


def find_pdf_files(directory: str, pattern: str = "*.pdf") -> List[Path]:
    """Find all PDF files in a directory"""
    path = Path(directory)
    pdf_files = list(path.glob(pattern))
    # Filter out Zone.Identifier files and validate filenames
    valid_files = []
    for f in pdf_files:
        if not f.name.endswith('.Zone.Identifier') and PathValidator.validate_pdf_filename(f.name):
            valid_files.append(f)
    return valid_files


def list_sessions():
    """모든 세션 목록을 표시"""
    sessions_dir = Path("output/temp/sessions")
    if not sessions_dir.exists():
        print("세션 디렉토리가 없습니다.")
        return
    
    sessions = []
    for session_dir in sessions_dir.iterdir():
        if session_dir.is_dir():
            state_file = session_dir / "processing_state.json"
            chunk_dir = session_dir / "chunk_results"
            
            # 세션 정보 수집
            session_info = {
                'id': session_dir.name,
                'path': session_dir,
                'created': datetime.fromtimestamp(session_dir.stat().st_mtime),
                'state_exists': state_file.exists(),
                'chunk_count': len(list(chunk_dir.glob('*.json'))) if chunk_dir.exists() else 0,
                'size': sum(f.stat().st_size for f in session_dir.rglob('*') if f.is_file())
            }
            
            # processing_state.json 읽기
            if state_file.exists():
                try:
                    with open(state_file, 'r', encoding='utf-8') as f:
                        state = json.load(f)
                        session_info['status'] = state.get('status', 'unknown')
                        session_info['jokbo_path'] = state.get('jokbo_path', 'N/A')
                except (json.JSONDecodeError, IOError, OSError) as e:
                    print(f"Error reading state file: {e}")
                    session_info['status'] = 'error'
                    session_info['jokbo_path'] = 'N/A'
            
            sessions.append(session_info)
    
    if not sessions:
        print("세션이 없습니다.")
        return
    
    # 생성 시간순 정렬
    sessions.sort(key=lambda x: x['created'], reverse=True)
    
    # 테이블 형태로 출력
    print(f"\n{'세션 ID':<30} {'상태':<10} {'생성 시간':<20} {'청크':<6} {'크기':<10} {'족보 파일'}")
    print("=" * 100)
    for session in sessions:
        size_mb = session['size'] / (1024 * 1024)
        print(f"{session['id']:<30} {session['status']:<10} {session['created'].strftime('%Y-%m-%d %H:%M:%S'):<20} "
              f"{session['chunk_count']:<6} {size_mb:.1f}MB{':<10'} {Path(session['jokbo_path']).name if session['jokbo_path'] != 'N/A' else 'N/A'}")
    
    print(f"\n총 {len(sessions)}개 세션, 총 크기: {sum(s['size'] for s in sessions) / (1024 * 1024):.1f}MB")


def cleanup_sessions(days_old=None, cleanup_all=False):
    """세션 정리"""
    sessions_dir = Path("output/temp/sessions")
    if not sessions_dir.exists():
        print("세션 디렉토리가 없습니다.")
        return
    
    now = datetime.now()
    cleaned = 0
    total_size = 0
    
    for session_dir in sessions_dir.iterdir():
        if session_dir.is_dir():
            # 생성 시간 확인
            created = datetime.fromtimestamp(session_dir.stat().st_mtime)
            age_days = (now - created).days
            
            should_delete = cleanup_all or (days_old and age_days >= days_old)
            
            if should_delete:
                # 크기 계산
                size = sum(f.stat().st_size for f in session_dir.rglob('*') if f.is_file())
                total_size += size
                
                # 삭제
                shutil.rmtree(session_dir)
                cleaned += 1
                print(f"삭제됨: {session_dir.name} ({age_days}일 전, {size / (1024 * 1024):.1f}MB)")
    
    if cleaned > 0:
        print(f"\n총 {cleaned}개 세션 삭제됨, {total_size / (1024 * 1024):.1f}MB 공간 확보")
    else:
        print("삭제할 세션이 없습니다.")


def validate_api_key(api_key: str) -> bool:
    """API 키 유효성 검증
    
    Args:
        api_key: 검증할 API 키
        
    Returns:
        True if valid, False otherwise
    """
    try:
        # 임시로 API 키 설정
        configure_api(api_key)
        # 간단한 모델 정보 요청으로 유효성 확인
        # gemini-2.5-flash로 변경 (현재 사용 가능한 모델)
        model = genai.get_model('models/gemini-2.5-flash')
        return True
    except Exception as e:
        # 검증 실패는 조용히 처리 (어차피 나중에 유효하지 않다고 표시됨)
        return False


def cleanup_files_for_api_process(api_key: str, api_index: int, result_queue):
    """단일 API 키로 파일 정리 (별도 프로세스에서 실행)
    
    Args:
        api_key: 사용할 API 키
        api_index: API 키 인덱스 (1-based)
        result_queue: 결과를 담을 Queue
    """
    # 프로세스 내에서 필요한 모듈 import
    import os
    import sys
    from datetime import datetime
    from concurrent.futures import ThreadPoolExecutor, as_completed
    
    pid = os.getpid()
    
    try:
        import google.generativeai as genai
        from config import configure_api
        import time
        
        # 프로세스별로 독립적인 API 설정
        configure_api(api_key)
        time.sleep(0.5)  # API 초기화 대기
        
        # 파일 목록 조회
        files = list(genai.list_files())
        print(f"    API #{api_index}: {len(files)}개 파일 발견")
        
        if not files:
            result_queue.put({
                'api_index': api_index,
                'deleted': 0,
                'skipped_403': 0,
                'errors': 0,
                'total': 0,
                'success': True
            })
            return
        
        deleted = 0
        errors = 0
        skipped_403 = 0
        
        # 병렬로 파일 삭제 (각 파일마다 독립적으로 처리)
        def delete_file_safe(file_info):
            file, idx = file_info
            try:
                genai.delete_file(file.name)
                return 'deleted', idx, file.name
            except Exception as e:
                error_str = str(e)
                if "403" in error_str or "permission" in error_str.lower():
                    return 'skipped_403', idx, file.name
                else:
                    return 'error', idx, file.name
        
        # 모든 파일을 병렬로 처리 (최대 10개 스레드)
        with ThreadPoolExecutor(max_workers=min(10, len(files))) as executor:
            # 모든 파일에 대한 작업 제출
            futures = {executor.submit(delete_file_safe, (file, i+1)): i 
                      for i, file in enumerate(files)}
            
            # 각 작업이 완료될 때마다 결과 처리 (타임아웃 없음)
            completed = 0
            for future in as_completed(futures):
                completed += 1
                try:
                    result, idx, filename = future.result()
                    if result == 'deleted':
                        deleted += 1
                        if completed % 10 == 0:  # 10개마다 진행상황 표시
                            print(f"      진행률: {completed}/{len(files)}")
                    elif result == 'skipped_403':
                        skipped_403 += 1
                    else:
                        errors += 1
                except Exception:
                    errors += 1
        
        # 결과 요약
        summary_parts = []
        if deleted > 0:
            summary_parts.append(f"삭제 {deleted}개")
        if skipped_403 > 0:
            summary_parts.append(f"권한없음 {skipped_403}개")
        if errors > 0:
            summary_parts.append(f"실패 {errors}개")
        
        if summary_parts:
            print(f"    API #{api_index} 완료: {', '.join(summary_parts)}")
        else:
            print(f"    API #{api_index} 완료: 파일 없음")
        
        result_queue.put({
            'api_index': api_index,
            'deleted': deleted,
            'skipped_403': skipped_403,
            'errors': errors,
            'total': len(files),
            'success': True
        })
        
    except Exception as e:
        print(f"    API #{api_index} 프로세스 실패: {str(e)}")
        result_queue.put({
            'api_index': api_index,
            'deleted': 0,
            'skipped_403': 0,
            'errors': 0,
            'total': 0,
            'success': False,
            'error_msg': str(e)
        })


def auto_cleanup_old_sessions(keep_days: int):
    """프로그램 시작 시 오래된 세션 자동 정리"""
    sessions_dir = Path("output/temp/sessions")
    if not sessions_dir.exists():
        return
    
    now = datetime.now()
    for session_dir in sessions_dir.iterdir():
        if session_dir.is_dir():
            created = datetime.fromtimestamp(session_dir.stat().st_mtime)
            age_days = (now - created).days
            
            if age_days > keep_days:
                try:
                    shutil.rmtree(session_dir)
                    print(f"오래된 세션 자동 삭제: {session_dir.name} ({age_days}일 전)")
                except (OSError, PermissionError) as e:
                    print(f"Failed to delete session {session_dir.name}: {e}")


def handle_session_cleanup(args):
    """세션 정리 명령 처리"""
    if args.list_sessions:
        list_sessions()
    elif args.cleanup:
        print("모든 세션을 삭제하시겠습니까? (y/N): ", end='')
        if input().lower() == 'y':
            cleanup_sessions(cleanup_all=True)
    elif args.cleanup_old:
        cleanup_sessions(days_old=args.cleanup_old)
    
    return 0


def process_lesson_with_all_jokbos(lesson_path: Path, jokbo_paths: List[Path], output_dir: Path, jokbo_dir: str, model) -> bool:
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
        
        analysis_result = processor.analyze_lesson_centric(jokbo_path_strs, str(lesson_path))
        
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


def process_jokbo_with_all_lessons(jokbo_path: Path, lesson_paths: List[Path], output_dir: Path, lesson_dir: str, model, use_multi_api: bool = False, model_type: str = "pro", thinking_budget: Optional[int] = None) -> bool:
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
        
        if use_multi_api:
            from config import API_KEYS
            if len(API_KEYS) > 1:
                print(f"  (Multi-API 모드 - {len(API_KEYS)}개 API 키 사용)")
                analysis_result = processor.analyze_jokbo_centric_multi_api(lesson_path_strs, str(jokbo_path), API_KEYS)
            else:
                print("  경고: Multi-API 모드가 요청되었지만 API 키가 1개뿐입니다. 일반 모드로 실행합니다.")
                analysis_result = processor.analyze_jokbo_centric(lesson_path_strs, str(jokbo_path))
        else:
            analysis_result = processor.analyze_jokbo_centric(lesson_path_strs, str(jokbo_path))
        
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
    parser.add_argument("--multi-api", action="store_true", help="Multi-API 모드 사용 (여러 API 키로 분산 처리)")
    parser.add_argument("--mode", choices=["lesson-centric", "jokbo-centric"], default="lesson-centric", 
                       help="분석 모드 선택 (기본값: lesson-centric)")
    parser.add_argument("--model", choices=["pro", "flash", "flash-lite"], default="flash",
                       help="Gemini 모델 선택 (기본값: flash)")
    parser.add_argument("--thinking-budget", type=int, default=None,
                       help="Flash/Flash-lite 모델의 thinking budget (0-24576, -1은 자동)")
    # 청크 정리 옵션 추가
    parser.add_argument('--cleanup', action='store_true', help='모든 임시 세션 파일 정리')
    parser.add_argument('--cleanup-old', type=int, help='N일 이상 된 세션 정리')
    parser.add_argument('--list-sessions', action='store_true', help='모든 세션 목록 표시')
    parser.add_argument('--keep-days', type=int, default=7, help='자동 정리 시 보관 기간 (기본값: 7일)')
    
    args = parser.parse_args()
    
    # 세션 정리 기능 처리
    if args.cleanup or args.cleanup_old or args.list_sessions:
        return handle_session_cleanup(args)
    
    # 시작 시 오래된 세션 자동 정리 (--keep-days 설정 사용)
    auto_cleanup_old_sessions(args.keep_days)
    
    # Multi-API mode is only supported for jokbo-centric mode
    if args.multi_api and args.mode != "jokbo-centric":
        print("오류: --multi-api 옵션은 jokbo-centric 모드에서만 사용 가능합니다.")
        print("      --mode jokbo-centric 옵션을 함께 사용하세요.")
        return 1
    
    # Configure API before creating model
    configure_api()
    
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
        # Validate single lesson path
        single_lesson_path = Path(args.single_lesson)
        if not PathValidator.validate_pdf_filename(single_lesson_path.name):
            print(f"오류: 잘못된 PDF 파일명: {single_lesson_path.name}")
            return 1
        lesson_files = [single_lesson_path]
    else:
        lesson_files = find_pdf_files(args.lesson_dir)
    
    if not lesson_files:
        print(f"오류: {args.lesson_dir} 디렉토리에 PDF 파일이 없습니다.")
        return 1
    
    print(f"족보 파일 {len(jokbo_files)}개, 강의자료 파일 {len(lesson_files)}개 발견")
    
    # 전역 변수 설정
    global _multi_api_mode, _original_api_key
    _multi_api_mode = args.multi_api
    _original_api_key = os.getenv('GEMINI_API_KEY')
    
    # 프로그램 시작 시 기존 업로드 파일 정리
    if args.multi_api:
        # Multi-API 모드에서는 모든 API 키에 대해 정리 수행
        print("\nMulti-API 모드: 모든 API 키로 기존 업로드 파일 정리 중...")
        from config import API_KEYS
        
        # 현재 API 키 백업
        original_api_key = os.getenv('GEMINI_API_KEY')
        
        # 먼저 각 API 키 유효성 검증
        valid_keys = []
        for i, api_key in enumerate(API_KEYS):
            print(f"  API #{i+1} 검증 중...")
            if validate_api_key(api_key):
                valid_keys.append((i+1, api_key))
                print(f"    ✓ 유효한 API 키")
            else:
                print(f"    ✗ 유효하지 않은 API 키")
        
        if not valid_keys:
            print("  경고: 유효한 API 키가 없습니다.")
        else:
            # 순차적으로 각 API 키별 파일 정리 (각각 별도 프로세스)
            total_deleted = 0
            total_errors = 0
            
            for api_index, api_key in valid_keys:
                print(f"  API #{api_index} 파일 정리 중...")
                
                # Manager를 사용한 Queue 생성
                manager = multiprocessing.Manager()
                result_queue = manager.Queue()
                
                # 단일 프로세스 시작
                p = Process(target=cleanup_files_for_api_process, 
                           args=(api_key, api_index, result_queue))
                p.start()
                
                # 타임아웃 없이 완료까지 대기
                p.join()
                
                # 결과 수집 (타임아웃 1초)
                try:
                    result = result_queue.get(timeout=1.0)
                    if result['success']:
                        total_deleted += result.get('deleted', 0)
                        total_errors += result.get('errors', 0)
                except:
                    print(f"    API #{api_index} 결과 수집 실패")
                    total_errors += 1
            
            print(f"\n  전체 정리 완료: 총 {total_deleted}개 파일 삭제")
            if total_errors > 0:
                print(f"  오류 발생: {total_errors}개")
        
        # 원래 API 키로 복원
        if original_api_key:
            configure_api(original_api_key)
        else:
            configure_api()  # 기본 키로 복원
        print()  # 공백 줄 추가
    else:
        # 일반 모드에서는 기본 API 키로만 정리
        print("\n기존 업로드 파일 정리 중...")
        try:
            files = list(genai.list_files())
            deleted = 0
            for file in files:
                try:
                    genai.delete_file(file.name)
                    deleted += 1
                except Exception as e:
                    # 403 오류 등은 무시
                    error_str = str(e)
                    if "403" not in error_str and "permission" not in error_str.lower():
                        print(f"  삭제 실패: {file.display_name} - {error_str[:50]}...")
            if deleted > 0:
                print(f"  {deleted}개 파일 삭제됨")
        except Exception as e:
            print(f"  파일 정리 중 오류: {e}")
    
    print()  # 빈 줄 추가
    
    successful = 0
    failed = 0
    
    if args.mode == "lesson-centric":
        print(f"각 강의자료별로 모든 족보와 비교하여 처리합니다.")
        print()
        
        # Process each lesson with all jokbos
        for lesson_file in lesson_files:
            if process_lesson_with_all_jokbos(lesson_file, jokbo_files, output_dir, args.jokbo_dir, model):
                successful += 1
            else:
                failed += 1
    else:  # jokbo-centric
        print(f"각 족보별로 모든 강의자료와 비교하여 처리합니다.")
        if args.multi_api:
            print("Multi-API 모드가 활성화되었습니다. (여러 API 키로 병렬 처리)")
        print()
        
        # Process each jokbo with all lessons
        for jokbo_file in jokbo_files:
            if process_jokbo_with_all_lessons(jokbo_file, lesson_files, output_dir, args.lesson_dir, model, 
                                             args.multi_api, args.model, args.thinking_budget):
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
