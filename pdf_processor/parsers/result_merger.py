"""Result merging functionality for chunk processing"""

import json
from pathlib import Path
from typing import Dict, Any, List, Optional
from constants import MAX_CONNECTIONS_PER_QUESTION, RELEVANCE_SCORE_THRESHOLD


class ResultMerger:
    """Handles merging and filtering of processing results"""
    
    def merge_jokbo_centric_results(self, results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Merge multiple jokbo-centric results
        
        Args:
            results: List of result dictionaries
            
        Returns:
            Merged result dictionary
        """
        if not results:
            return {"jokbo_pages": []}
        
        # If only one result, return it
        if len(results) == 1:
            return results[0]
        
        # Merge multiple results
        merged_pages = []
        seen_pages = set()
        
        for result in results:
            if "jokbo_pages" in result:
                for page in result["jokbo_pages"]:
                    page_num = page.get("jokbo_page")
                    if page_num not in seen_pages:
                        seen_pages.add(page_num)
                        merged_pages.append(page)
        
        # Sort by page number
        merged_pages.sort(key=lambda x: x.get("jokbo_page", 0))
        
        return {"jokbo_pages": merged_pages}
    
    def load_and_merge_chunk_results(self, temp_dir: Path) -> Dict[str, Any]:
        """Load and merge chunk results from temporary files
        
        Args:
            temp_dir: Directory containing chunk result files
            
        Returns:
            Merged results dictionary
        """
        chunk_files = sorted(temp_dir.glob("chunk_*.json"))
        lesson_files = sorted(temp_dir.glob("lesson_*.json"))
        
        all_connections = {}
        
        # Process chunk files
        for chunk_file in chunk_files:
            try:
                with open(chunk_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    result = data.get("result", {})
                    
                    if "jokbo_pages" in result:
                        for page in result["jokbo_pages"]:
                            for question in page.get("questions", []):
                                q_num = str(question.get("question_number"))
                                
                                if q_num not in all_connections:
                                    all_connections[q_num] = {
                                        "question_number": question.get("question_number"),
                                        "question_text": question.get("question_text"),
                                        "answer": question.get("answer"),
                                        "jokbo_page": page.get("jokbo_page"),
                                        "jokbo_end_page": page.get("jokbo_end_page"),
                                        "question_numbers_on_page": page.get("question_numbers_on_page", []),
                                        "is_last_question_on_page": question.get("is_last_question_on_page", False),
                                        "connections": []
                                    }
                                
                                # Add connections from this chunk
                                for conn in question.get("connections", []):
                                    all_connections[q_num]["connections"].append(conn)
                                    
            except Exception as e:
                print(f"  청크 파일 처리 실패 {chunk_file}: {e}")
        
        # Process lesson files
        for lesson_file in lesson_files:
            try:
                with open(lesson_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    result = data.get("result", {})
                    
                    if "jokbo_pages" in result:
                        for page in result["jokbo_pages"]:
                            for question in page.get("questions", []):
                                q_num = str(question.get("question_number"))
                                
                                if q_num not in all_connections:
                                    all_connections[q_num] = {
                                        "question_number": question.get("question_number"),
                                        "question_text": question.get("question_text"),
                                        "answer": question.get("answer"),
                                        "jokbo_page": page.get("jokbo_page"),
                                        "jokbo_end_page": page.get("jokbo_end_page"),
                                        "question_numbers_on_page": page.get("question_numbers_on_page", []),
                                        "is_last_question_on_page": question.get("is_last_question_on_page", False),
                                        "connections": []
                                    }
                                
                                # Add connections
                                for conn in question.get("connections", []):
                                    all_connections[q_num]["connections"].append(conn)
                                    
            except Exception as e:
                print(f"  레슨 파일 처리 실패 {lesson_file}: {e}")
        
        return all_connections
    
    def apply_final_filtering_and_sorting(self, all_connections: Dict[str, Any]) -> Dict[str, Any]:
        """Apply final filtering and sorting to connections
        
        Args:
            all_connections: Dictionary of all connections by question number
            
        Returns:
            Filtered and sorted results
        """
        final_result = {"jokbo_pages": []}
        pages_dict = {}
        
        for q_num, q_data in all_connections.items():
            # Sort connections by relevance score
            connections = q_data.get("connections", [])
            connections.sort(key=lambda x: x.get("relevance_score", 0), reverse=True)
            
            # Apply filtering
            filtered_connections = []
            for conn in connections:
                score = conn.get("relevance_score", 0)
                if score >= RELEVANCE_SCORE_THRESHOLD:
                    filtered_connections.append(conn)
                    if len(filtered_connections) >= MAX_CONNECTIONS_PER_QUESTION:
                        break
            
            if filtered_connections:
                page_num = q_data.get("jokbo_page")
                
                if page_num not in pages_dict:
                    pages_dict[page_num] = {
                        "jokbo_page": page_num,
                        "jokbo_end_page": q_data.get("jokbo_end_page"),
                        "question_numbers_on_page": q_data.get("question_numbers_on_page", []),
                        "questions": []
                    }
                
                pages_dict[page_num]["questions"].append({
                    "question_number": q_data.get("question_number"),
                    "question_text": q_data.get("question_text"),
                    "answer": q_data.get("answer"),
                    "is_last_question_on_page": q_data.get("is_last_question_on_page", False),
                    "connections": filtered_connections
                })
        
        # Convert to sorted list
        for page_num in sorted(pages_dict.keys()):
            page_data = pages_dict[page_num]
            # Sort questions within page
            page_data["questions"].sort(key=lambda x: x.get("question_number", 0))
            final_result["jokbo_pages"].append(page_data)
        
        # Add summary statistics
        total_questions = sum(len(p["questions"]) for p in final_result["jokbo_pages"])
        total_connections = sum(
            len(q["connections"]) 
            for p in final_result["jokbo_pages"] 
            for q in p["questions"]
        )
        
        print(f"\n최종 결과: {len(final_result['jokbo_pages'])}개 페이지, "
              f"{total_questions}개 문제, {total_connections}개 연결")
        
        return final_result