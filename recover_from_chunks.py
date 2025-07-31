#!/usr/bin/env python3
"""
청크 파일에서 PDF 복원 스크립트
기존에 생성된 청크 JSON 파일들을 사용하여 PDF를 생성합니다.
"""

import json
import os
import sys
from pathlib import Path
from datetime import datetime
import re
from typing import Dict, Any, List
import argparse

# 상위 디렉토리의 모듈 임포트
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from pdf_creator import PDFCreator
from constants import RELEVANCE_SCORE_THRESHOLD, MAX_CONNECTIONS_PER_QUESTION


class PDFCreatorWithProgress(PDFCreator):
    """진행률 표시가 추가된 PDF 생성기"""
    
    def create_jokbo_centric_pdf(self, jokbo_path: str, analysis_result: Dict[str, Any], output_path: str, lesson_dir: str = "lesson"):
        """진행률 표시와 함께 PDF 생성"""
        
        if "error" in analysis_result:
            print(f"Cannot create PDF due to analysis error: {analysis_result['error']}")
            return
        
        # 문제 개수 세기
        total_questions = 0
        for page_info in analysis_result.get("jokbo_pages", []):
            total_questions += len(page_info.get("questions", []))
        
        print(f"  총 {total_questions}개 문제 처리 예정")
        
        # 원본 함수 호출하되 진행률 표시를 위해 약간 수정
        processed_questions = 0
        
        # 원본 create_jokbo_centric_pdf 호출
        super().create_jokbo_centric_pdf(jokbo_path, analysis_result, output_path, lesson_dir)
        
        print(f"  PDF 생성 완료!")


def load_valid_chunk_files(chunk_dir: Path) -> List[Dict[str, Any]]:
    """정상적인 청크 파일만 로드"""
    valid_chunks = []
    chunk_files = sorted(chunk_dir.glob("chunk_*.json"))
    
    print(f"청크 디렉토리에서 {len(chunk_files)}개 파일 발견")
    
    for chunk_file in chunk_files:
        try:
            with open(chunk_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                
            # 오류가 있는 청크는 제외
            if "result" in data and "error" in data["result"]:
                print(f"  오류 청크 건너뛰기: {chunk_file.name}")
                continue
                
            # 정상 청크
            if "result" in data and "jokbo_pages" in data["result"]:
                valid_chunks.append(data)
                print(f"  정상 청크 로드: {chunk_file.name}")
                
        except Exception as e:
            print(f"  청크 로드 실패 ({chunk_file.name}): {str(e)}")
    
    print(f"\n정상 청크 {len(valid_chunks)}개 로드 완료")
    return valid_chunks


def merge_chunk_results(chunks: List[Dict[str, Any]]) -> Dict[str, Any]:
    """청크 결과들을 병합"""
    all_connections = {}
    
    for chunk_data in chunks:
        result = chunk_data["result"]
        for page_info in result.get("jokbo_pages", []):
            for question in page_info.get("questions", []):
                question_num = question.get("question_number", "Unknown")
                
                if question_num not in all_connections:
                    all_connections[question_num] = {
                        "question": question,
                        "jokbo_page": page_info["jokbo_page"],
                        "related_slides": []
                    }
                
                # 관련 슬라이드 추가
                for slide in question.get("related_lesson_slides", []):
                    all_connections[question_num]["related_slides"].append(slide)
    
    print(f"병합 완료: {len(all_connections)}개 문제")
    return all_connections


def apply_filtering_and_sorting(all_connections: Dict[str, Any]) -> Dict[str, Any]:
    """필터링 및 정렬 적용"""
    final_pages = {}
    total_questions = 0
    total_related_slides = 0
    
    # 문제별로 처리
    for question_num, conn_info in all_connections.items():
        question = conn_info["question"]
        jokbo_page = conn_info["jokbo_page"]
        related_slides = conn_info["related_slides"]
        
        # 관련성 점수로 정렬하고 상위 N개만 선택
        related_slides.sort(key=lambda x: x.get('relevance_score', 0), reverse=True)
        filtered_slides = [
            slide for slide in related_slides[:MAX_CONNECTIONS_PER_QUESTION]
            if slide.get('relevance_score', 0) >= RELEVANCE_SCORE_THRESHOLD
        ]
        
        if filtered_slides:
            # 페이지별로 그룹화
            if jokbo_page not in final_pages:
                final_pages[jokbo_page] = {
                    "jokbo_page": jokbo_page,
                    "questions": []
                }
            
            question["related_lesson_slides"] = filtered_slides
            final_pages[jokbo_page]["questions"].append(question)
            total_questions += 1
            total_related_slides += len(filtered_slides)
    
    # 페이지 번호순 정렬
    sorted_pages = sorted(final_pages.values(), key=lambda x: x["jokbo_page"])
    
    print(f"필터링 완료: {len(sorted_pages)}개 페이지, {total_questions}개 문제, {total_related_slides}개 연결")
    
    return {
        "jokbo_pages": sorted_pages,
        "summary": {
            "total_jokbo_pages": len(sorted_pages),
            "total_questions": total_questions,
            "total_related_slides": total_related_slides
        }
    }


def extract_jokbo_info_from_state(session_dir: Path = None) -> str:
    """processing_state.json에서 족보 경로 추출"""
    # 세션 디렉토리가 주어지면 그 안의 processing_state.json 사용
    if session_dir:
        state_file = session_dir / "processing_state.json"
    else:
        # 기본값: 이전 방식 (호환성)
        state_file = Path("output/temp/processing_state.json")
    
    if state_file.exists():
        try:
            with open(state_file, 'r', encoding='utf-8') as f:
                state = json.load(f)
                jokbo_path = state.get("jokbo_path")
                if jokbo_path:
                    print(f"처리 상태 파일에서 족보 경로 발견: {jokbo_path}")
                    return jokbo_path
        except Exception as e:
            print(f"처리 상태 파일 읽기 실패: {str(e)}")
    
    return None


def list_recoverable_sessions():
    """복원 가능한 세션 목록 표시"""
    sessions_dir = Path("output/temp/sessions")
    if not sessions_dir.exists():
        print("세션 디렉토리가 없습니다.")
        return
    
    recoverable = []
    for session_dir in sessions_dir.iterdir():
        if session_dir.is_dir():
            chunk_dir = session_dir / "chunk_results"
            state_file = session_dir / "processing_state.json"
            
            if chunk_dir.exists() and state_file.exists():
                chunk_count = len(list(chunk_dir.glob('*.json')))
                if chunk_count > 0:
                    # 상태 파일 읽기
                    try:
                        with open(state_file, 'r', encoding='utf-8') as f:
                            state = json.load(f)
                            status = state.get('status', 'unknown')
                            jokbo_path = state.get('jokbo_path', 'N/A')
                            
                            recoverable.append({
                                'session_id': session_dir.name,
                                'chunk_count': chunk_count,
                                'status': status,
                                'jokbo': Path(jokbo_path).name if jokbo_path != 'N/A' else 'N/A',
                                'created': datetime.fromtimestamp(session_dir.stat().st_mtime)
                            })
                    except:
                        pass
    
    if not recoverable:
        print("복원 가능한 세션이 없습니다.")
        return
    
    print(f"\n{'\uc138\uc158 ID':<30} {'\uc0c1\ud0dc':<10} {'\uccad\ud06c':<6} {'\uc871\ubcf4':<20} {'\uc0dd\uc131 \uc2dc\uac04'}")
    print("=" * 80)
    for session in recoverable:
        print(f"{session['session_id']:<30} {session['status']:<10} {session['chunk_count']:<6} "
              f"{session['jokbo']:<20} {session['created'].strftime('%Y-%m-%d %H:%M:%S')}")


def main():
    """메인 함수"""
    parser = argparse.ArgumentParser(description='청크 파일에서 PDF 복원')
    parser.add_argument('--session', type=str, help='복원할 세션 ID')
    parser.add_argument('--list-sessions', action='store_true', help='복원 가능한 세션 목록')
    
    args = parser.parse_args()
    
    if args.list_sessions:
        list_recoverable_sessions()
        return 0
    
    # 세션 디렉토리 결정
    if args.session:
        session_dir = Path("output/temp/sessions") / args.session
        if not session_dir.exists():
            print(f"오류: 세션 디렉토리가 없습니다: {session_dir}")
            return 1
        chunk_dir = session_dir / "chunk_results"
    else:
        # 기본값: 이전 방식 (호환성)
        chunk_dir = Path("output/temp/chunk_results")
        session_dir = None
    
    if not chunk_dir.exists():
        print(f"오류: 청크 디렉토리가 없습니다: {chunk_dir}")
        return 1
    
    # 1. 정상 청크 파일 로드
    print("1. 청크 파일 로드 중...")
    valid_chunks = load_valid_chunk_files(chunk_dir)
    
    if not valid_chunks:
        print("오류: 정상적인 청크 파일이 없습니다.")
        return 1
    
    # 2. 청크 결과 병합
    print("\n2. 청크 결과 병합 중...")
    all_connections = merge_chunk_results(valid_chunks)
    
    # 3. 필터링 및 정렬
    print("\n3. 필터링 및 정렬 중...")
    final_result = apply_filtering_and_sorting(all_connections)
    
    if final_result["summary"]["total_questions"] == 0:
        print(f"경고: 필터링 후 관련 문제가 없습니다. (THRESHOLD={RELEVANCE_SCORE_THRESHOLD})")
        return 1
    
    # 4. 족보 파일 경로 추출
    jokbo_path_str = extract_jokbo_info_from_state(session_dir)
    if not jokbo_path_str:
        print("오류: 족보 파일 경로를 찾을 수 없습니다.")
        return 1
    
    jokbo_path = Path(jokbo_path_str)
    jokbo_filename = jokbo_path.name
    
    print(f"\n4. 족보 파일: {jokbo_filename}")
    
    # 족보 파일 존재 확인
    if not jokbo_path.exists():
        print(f"오류: 족보 파일이 존재하지 않습니다: {jokbo_path}")
        return 1
    
    print(f"   족보 파일 확인됨: {jokbo_path}")
    
    # 5. PDF 생성
    print("\n5. PDF 생성 중...")
    creator = PDFCreatorWithProgress()
    
    output_filename = f"recovered_{jokbo_filename.replace('.pdf', '')}_all_lessons_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
    output_path = Path("output") / output_filename
    
    try:
        creator.create_jokbo_centric_pdf(str(jokbo_path), final_result, str(output_path), "lesson")
        print(f"\n성공! PDF 생성 완료: {output_path}")
        return 0
    except Exception as e:
        print(f"\nPDF 생성 중 오류 발생: {str(e)}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())