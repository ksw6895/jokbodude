#!/usr/bin/env python3
"""
Gemini API에 업로드된 모든 파일을 확인하고 삭제하는 스크립트
"""

import google.generativeai as genai
from config import API_KEY, configure_api
from datetime import datetime

# API 키 설정
configure_api()

def list_all_files():
    """Gemini API에 업로드된 모든 파일 나열"""
    print("=" * 60)
    print("Gemini API에 업로드된 파일 목록:")
    print("=" * 60)
    
    try:
        files = list(genai.list_files())
        
        if not files:
            print("업로드된 파일이 없습니다.")
            return []
        
        for i, file in enumerate(files, 1):
            print(f"\n[{i}] 파일 정보:")
            print(f"  - Name: {file.name}")
            print(f"  - Display Name: {file.display_name}")
            print(f"  - MIME Type: {file.mime_type}")
            print(f"  - Size: {file.size_bytes:,} bytes")
            print(f"  - State: {file.state.name}")
            print(f"  - Created: {file.create_time}")
            print(f"  - Updated: {file.update_time}")
            
        return files
    except Exception as e:
        print(f"파일 목록 조회 중 오류 발생: {e}")
        return []

def delete_all_files(files):
    """모든 파일 삭제"""
    if not files:
        return
    
    print("\n" + "=" * 60)
    response = input(f"{len(files)}개의 파일을 모두 삭제하시겠습니까? (y/N): ")
    
    if response.lower() != 'y':
        print("삭제를 취소했습니다.")
        return
    
    print("\n파일 삭제 중...")
    success_count = 0
    
    for file in files:
        try:
            genai.delete_file(file.name)
            print(f"  ✓ 삭제됨: {file.display_name}")
            success_count += 1
        except Exception as e:
            print(f"  ✗ 삭제 실패: {file.display_name} - {e}")
    
    print(f"\n총 {success_count}/{len(files)}개 파일이 삭제되었습니다.")

def delete_specific_files(files):
    """특정 파일만 선택하여 삭제"""
    if not files:
        return
    
    print("\n삭제할 파일 번호를 입력하세요 (쉼표로 구분, 예: 1,3,5):")
    print("전체 삭제는 'all', 취소는 Enter를 누르세요.")
    
    selection = input("> ").strip()
    
    if not selection:
        print("삭제를 취소했습니다.")
        return
    
    if selection.lower() == 'all':
        delete_all_files(files)
        return
    
    try:
        indices = [int(x.strip()) - 1 for x in selection.split(',')]
        selected_files = [files[i] for i in indices if 0 <= i < len(files)]
        
        if not selected_files:
            print("유효한 파일이 선택되지 않았습니다.")
            return
        
        print(f"\n선택된 {len(selected_files)}개 파일을 삭제합니다...")
        
        for file in selected_files:
            try:
                genai.delete_file(file.name)
                print(f"  ✓ 삭제됨: {file.display_name}")
            except Exception as e:
                print(f"  ✗ 삭제 실패: {file.display_name} - {e}")
                
    except ValueError:
        print("잘못된 입력입니다.")

def main():
    print(f"Gemini 파일 정리 도구")
    print(f"실행 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    
    # 파일 목록 조회
    files = list_all_files()
    
    if not files:
        return
    
    # 삭제 옵션 제공
    print("\n" + "=" * 60)
    print("작업을 선택하세요:")
    print("1. 모든 파일 삭제")
    print("2. 특정 파일만 삭제")
    print("3. 종료")
    
    choice = input("\n선택 (1-3): ").strip()
    
    if choice == '1':
        delete_all_files(files)
    elif choice == '2':
        delete_specific_files(files)
    else:
        print("프로그램을 종료합니다.")

if __name__ == "__main__":
    main()