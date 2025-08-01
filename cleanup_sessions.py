#!/usr/bin/env python3
"""
세션 정리 유틸리티
대화형 세션 관리 도구
"""

import json
import shutil
import argparse
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any


def get_session_info(session_dir: Path) -> Dict[str, Any]:
    """세션 디렉토리에서 정보 추출"""
    state_file = session_dir / "processing_state.json"
    chunk_dir = session_dir / "chunk_results"
    
    info = {
        'id': session_dir.name,
        'path': session_dir,
        'created': datetime.fromtimestamp(session_dir.stat().st_mtime),
        'size': sum(f.stat().st_size for f in session_dir.rglob('*') if f.is_file()),
        'chunk_count': len(list(chunk_dir.glob('*.json'))) if chunk_dir.exists() else 0
    }
    
    # processing_state.json 읽기
    if state_file.exists():
        try:
            with open(state_file, 'r', encoding='utf-8') as f:
                state = json.load(f)
                info['status'] = state.get('status', 'unknown')
                info['jokbo_path'] = state.get('jokbo_path', 'N/A')
                info['session_id'] = state.get('session_id', info['id'])
                info['total_chunks'] = state.get('total_chunks', 0)
                info['processed_chunks'] = state.get('processed_chunks', 0)
        except (json.JSONDecodeError, IOError, OSError) as e:
            print(f"Error reading state file for session {info['id']}: {e}")
            info['status'] = 'error'
            info['jokbo_path'] = 'N/A'
    else:
        info['status'] = 'no_state'
        info['jokbo_path'] = 'N/A'
    
    return info


def scan_sessions() -> List[Dict[str, Any]]:
    """모든 세션 스캔"""
    sessions_dir = Path("output/temp/sessions")
    if not sessions_dir.exists():
        return []
    
    sessions = []
    for session_dir in sessions_dir.iterdir():
        if session_dir.is_dir():
            sessions.append(get_session_info(session_dir))
    
    return sorted(sessions, key=lambda x: x['created'], reverse=True)


def display_sessions_table(sessions: List[Dict[str, Any]]):
    """세션 테이블 표시"""
    if not sessions:
        print("세션이 없습니다.")
        return
    
    print(f"\n{'번호':<4} {'세션 ID':<25} {'상태':<10} {'생성 시간':<20} {'진행':<12} {'크기':<8} {'족보'}")
    print("=" * 100)
    
    for i, session in enumerate(sessions, 1):
        size_mb = session['size'] / (1024 * 1024)
        progress = f"{session.get('processed_chunks', 0)}/{session.get('total_chunks', 0)}"
        jokbo_name = Path(session['jokbo_path']).name if session['jokbo_path'] != 'N/A' else 'N/A'
        
        print(f"{i:<4} {session['id']:<25} {session['status']:<10} "
              f"{session['created'].strftime('%Y-%m-%d %H:%M:%S'):<20} "
              f"{progress:<12} {size_mb:>6.1f}MB  {jokbo_name}")
    
    total_size = sum(s['size'] for s in sessions) / (1024 * 1024)
    print(f"\n총 {len(sessions)}개 세션, 총 크기: {total_size:.1f}MB")


def prompt_session_selection(sessions: List[Dict[str, Any]]) -> List[int]:
    """삭제할 세션 선택"""
    print("\n삭제할 세션 번호를 입력하세요 (쉼표로 구분, 0=취소, all=전체):")
    user_input = input("> ").strip()
    
    if user_input == '0':
        return []
    elif user_input.lower() == 'all':
        return list(range(len(sessions)))
    else:
        try:
            indices = []
            for num in user_input.split(','):
                idx = int(num.strip()) - 1
                if 0 <= idx < len(sessions):
                    indices.append(idx)
                else:
                    print(f"경고: 잘못된 번호 {num}")
            return indices
        except ValueError:
            print("오류: 잘못된 입력")
            return []


def confirm_deletion(sessions: List[Dict[str, Any]], indices: List[int]) -> bool:
    """삭제 확인"""
    if not indices:
        return False
    
    print("\n다음 세션을 삭제하시겠습니까?")
    total_size = 0
    for idx in indices:
        session = sessions[idx]
        size_mb = session['size'] / (1024 * 1024)
        total_size += session['size']
        print(f"  - {session['id']} ({size_mb:.1f}MB)")
    
    print(f"\n총 {len(indices)}개 세션, {total_size / (1024 * 1024):.1f}MB")
    print("삭제하시겠습니까? (y/N): ", end='')
    return input().lower() == 'y'


def delete_sessions(sessions: List[Dict[str, Any]], indices: List[int]):
    """선택된 세션 삭제"""
    deleted = 0
    total_size = 0
    
    for idx in indices:
        session = sessions[idx]
        try:
            shutil.rmtree(session['path'])
            deleted += 1
            total_size += session['size']
            print(f"삭제됨: {session['id']}")
        except Exception as e:
            print(f"삭제 실패: {session['id']} - {str(e)}")
    
    if deleted > 0:
        print(f"\n총 {deleted}개 세션 삭제됨, {total_size / (1024 * 1024):.1f}MB 공간 확보")


def cleanup_by_age(days: int):
    """N일 이상 된 세션 자동 삭제"""
    sessions = scan_sessions()
    now = datetime.now()
    
    old_sessions = []
    for i, session in enumerate(sessions):
        age_days = (now - session['created']).days
        if age_days >= days:
            old_sessions.append(i)
    
    if not old_sessions:
        print(f"{days}일 이상 된 세션이 없습니다.")
        return
    
    print(f"{days}일 이상 된 세션 {len(old_sessions)}개 발견:")
    for idx in old_sessions:
        session = sessions[idx]
        age_days = (now - session['created']).days
        size_mb = session['size'] / (1024 * 1024)
        print(f"  - {session['id']} ({age_days}일 전, {size_mb:.1f}MB)")
    
    if confirm_deletion(sessions, old_sessions):
        delete_sessions(sessions, old_sessions)


def main():
    parser = argparse.ArgumentParser(description='세션 정리 유틸리티')
    parser.add_argument('--interactive', '-i', action='store_true', 
                       help='대화형 모드 (기본값)')
    parser.add_argument('--days', '-d', type=int, 
                       help='N일 이상 된 세션 자동 삭제')
    parser.add_argument('--all', '-a', action='store_true',
                       help='모든 세션 삭제')
    
    args = parser.parse_args()
    
    if args.all:
        sessions = scan_sessions()
        if sessions and confirm_deletion(sessions, list(range(len(sessions)))):
            delete_sessions(sessions, list(range(len(sessions))))
    elif args.days:
        cleanup_by_age(args.days)
    else:
        # 대화형 모드
        sessions = scan_sessions()
        if not sessions:
            print("세션이 없습니다.")
            return
        
        display_sessions_table(sessions)
        selected = prompt_session_selection(sessions)
        
        if selected and confirm_deletion(sessions, selected):
            delete_sessions(sessions, selected)


if __name__ == "__main__":
    main()