"""
Result merger for combining analysis results from multiple sources.
Handles merging of chunk results and connection filtering.
"""

from typing import Dict, Any, List, Optional
from pathlib import Path
import json

from ..utils.logging import get_logger

logger = get_logger(__name__)


class ResultMerger:
    """Merges and filters analysis results."""
    
    @staticmethod
    def merge_chunk_results(chunk_results: List[Dict[str, Any]], mode: str) -> Dict[str, Any]:
        """
        Merge results from multiple chunks.
        
        Args:
            chunk_results: List of results from each chunk
            mode: Processing mode
            
        Returns:
            Merged result dictionary
        """
        if not chunk_results:
            return {}
        
        if mode == "jokbo-centric":
            merged = ResultMerger._merge_jokbo_results(chunk_results)
        else:
            merged = ResultMerger._merge_lesson_results(chunk_results)

        # Attach warnings when some chunks failed
        try:
            failed = sum(1 for r in (chunk_results or []) if isinstance(r, dict) and r.get("error"))
            if failed > 0 and isinstance(merged, dict):
                merged.setdefault("warnings", {})
                merged["warnings"].update({"failed_chunks": failed, "partial": True})
        except Exception:
            pass
        return merged
    
    @staticmethod
    def _merge_jokbo_results(chunk_results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Merge jokbo-centric results from chunks."""
        merged = {"jokbo_pages": []}

        # Per-chunk summary for debugging
        try:
            for idx, result in enumerate(chunk_results):
                pages = result.get("jokbo_pages") if isinstance(result, dict) else None
                if isinstance(pages, list):
                    q_total = sum(len(p.get("questions", [])) for p in pages if isinstance(p, dict))
                    logger.debug(f"Chunk {idx}: pages={len(pages)}, questions={q_total}")
        except Exception:
            pass

        for result in chunk_results:
            if "jokbo_pages" in result:
                merged["jokbo_pages"].extend(result["jokbo_pages"])

        # Sort by page number (normalize to int)
        merged["jokbo_pages"].sort(key=lambda x: int(str(x.get("jokbo_page", 0)) or 0))

        # Duplicate detection for debugging
        try:
            counts = {}
            for p in merged["jokbo_pages"]:
                n = int(str(p.get("jokbo_page", 0)) or 0)
                counts[n] = counts.get(n, 0) + 1
            dups = [k for k, v in counts.items() if v > 1]
            if dups:
                logger.info(f"Duplicate jokbo_page entries detected in chunk merge: {dups}")
        except Exception:
            pass

        logger.info(f"Merged {len(chunk_results)} chunks into {len(merged['jokbo_pages'])} pages")
        return merged
    
    @staticmethod
    def _merge_lesson_results(chunk_results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Merge lesson-centric results from chunks."""
        # Collect and de-duplicate by (lesson_filename, lesson_page)
        collected: List[Dict[str, Any]] = []
        for result in chunk_results:
            if isinstance(result, dict) and "related_slides" in result:
                slides = result.get("related_slides") or []
                if isinstance(slides, list):
                    collected.extend([s for s in slides if isinstance(s, dict)])

        seen = set()
        unique_slides: List[Dict[str, Any]] = []
        for s in collected:
            try:
                lf = str(s.get("lesson_filename") or "").strip()
            except Exception:
                lf = ""
            try:
                lp = int(str(s.get("lesson_page", 0)) or 0)
            except Exception:
                lp = 0
            key = (lf.lower(), lp)
            if lp > 0 and key not in seen:
                seen.add(key)
                unique_slides.append(s)

        # Sort by page number (normalize to int)
        unique_slides.sort(key=lambda x: int(str(x.get("lesson_page", 0)) or 0))

        logger.info(f"Merged {len(chunk_results)} chunks into {len(unique_slides)} slides")
        return {"related_slides": unique_slides}
    
    @staticmethod
    def filter_connections_by_score(connections: List[Dict[str, Any]], 
                                  min_score: int = 80,
                                  max_connections: int = 2) -> List[Dict[str, Any]]:
        """
        Filter connections by relevance score.
        
        Args:
            connections: List of connection dictionaries
            min_score: Minimum relevance score to include
            max_connections: Maximum number of connections to keep
            
        Returns:
            Filtered and sorted connections
        """
        # Filter by minimum score
        filtered = [c for c in connections if c.get("relevance_score", 0) >= min_score]
        
        # Sort by relevance score (descending)
        filtered.sort(key=lambda x: x.get("relevance_score", 0), reverse=True)
        
        # Limit to max connections
        return filtered[:max_connections]
    
    @staticmethod
    def merge_api_results(results: List[Dict[str, Any]], mode: str) -> Dict[str, Any]:
        """
        Merge results from multiple API calls (for multi-API mode).
        
        Args:
            results: List of API results
            mode: Processing mode
            
        Returns:
            Merged result
        """
        if not results:
            return {}
        
        # For single result, return as-is
        if len(results) == 1:
            return results[0]
        
        # Merge multiple results
        if mode == "jokbo-centric":
            # Collect all pages from all results
            all_pages = []
            for i, result in enumerate(results):
                pages = result.get("jokbo_pages") if isinstance(result, dict) else None
                if isinstance(pages, list):
                    all_pages.extend(pages)
                    logger.debug(
                        f"API result {i}: pages={len(pages)}, questions={sum(len(p.get('questions', [])) for p in pages if isinstance(p, dict))}"
                    )

            # Remove duplicates based on page number
            seen_pages = set()
            unique_pages = []
            dup_pages = []
            for page in all_pages:
                page_num = page.get("jokbo_page")
                if page_num in seen_pages:
                    dup_pages.append(page_num)
                    continue
                seen_pages.add(page_num)
                unique_pages.append(page)

            if dup_pages:
                logger.info(f"merge_api_results: removed duplicate jokbo_page entries: {sorted(set(dup_pages))}")

            # Sort by page number (normalize to int)
            unique_pages.sort(key=lambda x: int(str(x.get("jokbo_page", 0)) or 0))

            return {"jokbo_pages": unique_pages}
        
        else:  # lesson-centric
            # Collect all slides from all results
            all_slides = []
            for result in results:
                if "related_slides" in result:
                    all_slides.extend(result["related_slides"])
            
            # Remove duplicates based on page number
            seen_pages = set()
            unique_slides = []
            for slide in all_slides:
                page_num = slide.get("lesson_page")
                if page_num not in seen_pages:
                    seen_pages.add(page_num)
                    unique_slides.append(slide)
            
            # Sort by page number (normalize to int)
            unique_slides.sort(key=lambda x: int(str(x.get("lesson_page", 0)) or 0))
            
            return {"related_slides": unique_slides}
    
    @staticmethod
    def save_chunk_result(result: Dict[str, Any], chunk_file: Path) -> None:
        """
        Save a chunk result to file.
        
        Args:
            result: Result dictionary to save
            chunk_file: Path to save the result
        """
        try:
            with open(chunk_file, 'w', encoding='utf-8') as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            logger.debug(f"Saved chunk result to {chunk_file}")
        except Exception as e:
            logger.error(f"Failed to save chunk result: {str(e)}")
    
    @staticmethod
    def load_chunk_results(chunk_dir: Path) -> List[Dict[str, Any]]:
        """
        Load all chunk results from a directory.
        
        Args:
            chunk_dir: Directory containing chunk result files
            
        Returns:
            List of loaded results
        """
        results = []
        
        if not chunk_dir.exists():
            logger.warning(f"Chunk directory does not exist: {chunk_dir}")
            return results
        
        for chunk_file in sorted(chunk_dir.glob("*.json")):
            try:
                with open(chunk_file, 'r', encoding='utf-8') as f:
                    result = json.load(f)
                    results.append(result)
                logger.debug(f"Loaded chunk result from {chunk_file}")
            except Exception as e:
                logger.error(f"Failed to load chunk {chunk_file}: {str(e)}")
        
        logger.info(f"Loaded {len(results)} chunk results from {chunk_dir}")
        return results
