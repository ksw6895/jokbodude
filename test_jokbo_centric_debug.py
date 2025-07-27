#!/usr/bin/env python3
"""족보 중심 모드에서 question_numbers_on_page 필드 추적 디버깅"""

import json
from pathlib import Path
from config import create_model
from pdf_processor import PDFProcessor

def debug_jokbo_centric():
    """족보 중심 모드에서 question_numbers_on_page 필드가 어디서 사라지는지 추적"""
    
    print("=" * 80)
    print("족보 중심 모드 question_numbers_on_page 필드 디버깅")
    print("=" * 80)
    
    # 테스트 파일
    jokbo_path = "jokbo/240527 본1 인체병리학총론_정답.pdf"
    lesson_path = "lesson/0509_1,2교시_박지영 감염질환_신호준.pdf"
    
    if not Path(jokbo_path).exists() or not Path(lesson_path).exists():
        print("테스트 파일이 없습니다.")
        return
    
    # 모델 초기화
    model = create_model("pro")
    processor = PDFProcessor(model)
    
    print("\n1단계: analyze_single_lesson_with_jokbo 결과 확인")
    print("-" * 60)
    
    # 단일 분석 결과
    single_result = processor.analyze_single_lesson_with_jokbo(lesson_path, jokbo_path)
    
    # Q59가 있는지 확인
    q59_found = False
    for page_info in single_result.get("jokbo_pages", []):
        for question in page_info.get("questions", []):
            if question.get("question_number") == "59":
                q59_found = True
                print(f"✓ Q59 발견 (페이지 {page_info['jokbo_page']})")
                print(f"  question_numbers_on_page: {question.get('question_numbers_on_page', [])}")
                if question.get('question_numbers_on_page'):
                    print("  → 필드가 정상적으로 존재함!")
                else:
                    print("  → 필드가 비어있음!")
                break
        if q59_found:
            break
    
    if not q59_found:
        print("✗ Q59를 찾을 수 없음")
    
    # 디버그 파일 저장
    debug_file = "output/debug/test_single_analysis.json"
    Path(debug_file).parent.mkdir(parents=True, exist_ok=True)
    with open(debug_file, 'w', encoding='utf-8') as f:
        json.dump(single_result, f, ensure_ascii=False, indent=2)
    print(f"\n단일 분석 결과 저장: {debug_file}")
    
    print("\n2단계: analyze_lessons_for_jokbo 결과 확인 (일반 모드)")
    print("-" * 60)
    
    # 족보 중심 분석
    jokbo_centric_result = processor.analyze_lessons_for_jokbo([lesson_path], jokbo_path)
    
    # Q59 확인
    q59_found = False
    for page_info in jokbo_centric_result.get("jokbo_pages", []):
        for question in page_info.get("questions", []):
            if question.get("question_number") == "59":
                q59_found = True
                print(f"✓ Q59 발견 (페이지 {page_info['jokbo_page']})")
                qnums = question.get('question_numbers_on_page', None)
                if qnums is None:
                    print("  → question_numbers_on_page 필드가 아예 없음!!!")
                elif qnums == []:
                    print("  → question_numbers_on_page 필드가 빈 배열 []")
                else:
                    print(f"  question_numbers_on_page: {qnums}")
                    print("  → 필드가 정상적으로 존재함!")
                break
        if q59_found:
            break
    
    if not q59_found:
        print("✗ Q59를 찾을 수 없음 (관련 슬라이드가 없어서 제외되었을 가능성)")
    
    # 디버그 파일 저장
    debug_file2 = "output/debug/test_jokbo_centric_analysis.json"
    with open(debug_file2, 'w', encoding='utf-8') as f:
        json.dump(jokbo_centric_result, f, ensure_ascii=False, indent=2)
    print(f"\n족보 중심 분석 결과 저장: {debug_file2}")
    
    print("\n" + "=" * 80)
    print("디버깅 완료! 위 결과를 보면 어디서 필드가 사라지는지 알 수 있습니다.")
    print("=" * 80)
    
    # 정리
    processor.__del__()

if __name__ == "__main__":
    debug_jokbo_centric()