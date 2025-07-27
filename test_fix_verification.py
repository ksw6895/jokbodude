#!/usr/bin/env python3
"""족보 중심 모드 수정 확인 테스트"""

import json
from pathlib import Path
from config import create_model
from pdf_processor import PDFProcessor

def verify_fix():
    """수정이 제대로 되었는지 확인"""
    
    print("족보 중심 모드 question_numbers_on_page 필드 수정 확인")
    print("=" * 60)
    
    # 테스트 파일
    jokbo_path = "jokbo/240527 본1 인체병리학총론_정답.pdf"
    lesson_path = "lesson/0509_1,2교시_박지영 감염질환_신호준.pdf"
    
    if not Path(jokbo_path).exists() or not Path(lesson_path).exists():
        print("테스트 파일이 없습니다.")
        return
    
    # 모델 초기화
    model = create_model("pro")
    processor = PDFProcessor(model)
    
    print("\n족보 중심 모드 실행 중...")
    
    # 족보 중심 분석 (일반 모드)
    result = processor.analyze_lessons_for_jokbo([lesson_path], jokbo_path)
    
    # Q59 확인
    found_q59 = False
    fixed = False
    
    for page_info in result.get("jokbo_pages", []):
        for question in page_info.get("questions", []):
            if question.get("question_number") == "59":
                found_q59 = True
                qnums = question.get('question_numbers_on_page', None)
                
                print(f"\n✓ Q59 발견 (페이지 {page_info['jokbo_page']})")
                
                if qnums is None:
                    print("  ✗ question_numbers_on_page 필드가 없음 - 수정 실패!")
                elif qnums == []:
                    print("  ✗ question_numbers_on_page 필드가 빈 배열 - 데이터 문제")
                else:
                    print(f"  ✓ question_numbers_on_page: {qnums}")
                    print("  ✓ 수정 성공! 필드가 정상적으로 전달됨!")
                    fixed = True
                break
        if found_q59:
            break
    
    if not found_q59:
        print("\n✗ Q59를 찾을 수 없음 (관련 슬라이드가 없을 수 있음)")
    elif fixed:
        print("\n" + "🎉" * 20)
        print("수정 완료! 이제 족보 중심 모드에서 다음 페이지 포함이 정상 작동합니다!")
        print("🎉" * 20)
    
    # 정리
    processor.__del__()

if __name__ == "__main__":
    verify_fix()