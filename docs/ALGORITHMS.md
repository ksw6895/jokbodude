# JokboDude Algorithms Guide

This document explains the algorithms and orchestrations that power JokboDude: analysis modes, API management and multi‑API failover, chunking, parsing + merging, and final PDF assembly.


## Overview

- Core Orchestrator: `pdf_processor/core/processor.py` coordinates analyzers, API/file management, chunking, and result merging.
- Analyzers: `pdf_processor/analyzers/`
  - `lesson_centric.py`: Finds jokbo questions relevant to each lesson slide.
  - `jokbo_centric.py`: Finds lesson slides relevant to each jokbo question.
  - `base.py`: Shared utilities (chunk processing, robust generation, parsing, filtering, debug saves).
  - `multi_api_analyzer.py`: Wraps analyzers with multi‑API distribution + failover.
- API Layer: `pdf_processor/api/`
  - `client.py`: Gemini client with global configure locking, retries, JSON mode.
  - `file_manager.py`: Tracks uploaded files per‑key, targeted cleanup, and “center file” retention.
  - `multi_api_manager.py`: Balances load across keys, tracks failures, cooldowns, and failover.
- Parsers/Merging: `pdf_processor/parsers/`
  - `response_parser.py`: JSON cleanup/repair, partial recovery, sanitation, and quality heuristics.
  - `result_merger.py`: Chunk merge, de‑duplication, and relevance filtering.
- PDF Ops: `pdf_processor/pdf/`
  - `operations.py`: Page counts, chunking, page extraction, PDF merge, validation, metadata.
  - `cache.py`: Thread‑safe global PDF cache and page count memoization.
- Job Pipeline: `web_server.py` (FastAPI endpoints) → `tasks.py` (Celery workers) → `pdf_creator.py` (final PDFs) with `storage_manager.py` (Redis‑backed file passing + progress).


## Modes: Lesson‑Centric vs. Jokbo‑Centric

Both modes build explicit prompts from `constants.py` and enforce strict rules: extract problems only from jokbo, use physical PDF page indices (1‑based), and exclude problem‑like slides in lesson PDFs.

### Lesson‑Centric Mode

Goal: For each lesson slide, find related jokbo questions and summarize with explanations.

Algorithm (`LessonCentricAnalyzer.analyze`):
- Build prompt with jokbo filename and strict constraints for JSON output.
- If lesson is large, split into chunks (default 30 pages) and analyze each chunk recursively with `chunk_info` for page offset correction.
- Upload order for single‑call analysis: `강의자료_<lesson>.pdf`, then `족보_<jokbo>.pdf`. A pre‑uploaded lesson may be reused when analyzing many jokbos.
- Robust content generation via `BaseAnalyzer._generate_with_quality_retry` uses `ResponseParser` heuristics to re‑try suspicious/invalid results.
- Parse + sanitize via `ResponseParser` then validate jokbo references with `PDFValidator.filter_valid_questions` and filter by the analyzer’s `min_relevance_score`.
- On chunked runs, apply `lesson_page` offset (`_post_process_results`) so chunk relative pages map to original.

Batch over many jokbos (`analyze_multiple_jokbos`):
- Pre‑upload the lesson once; iterate jokbos; persist intermediate results (debug) and track chunk progress.

### Jokbo‑Centric Mode

Goal: For each jokbo question, find the most relevant lesson slides with explanations.

Algorithm (`JokboCentricAnalyzer.analyze`):
- Build prompt with lesson filename and strict constraints for JSON output.
- If lesson is large: split into chunks; recursively analyze chunk PDFs while preserving references to the original lesson filename.
- Upload strategies:
  - Pre‑uploaded jokbo (center file) preferred when analyzing many lessons (reduces re‑uploads).
  - Otherwise upload both files for each analysis and clean up all but the center file.
- Parse + sanitize via `ResponseParser`; on chunked runs `_post_process_results` applies page offsets only to slides that belong to the current lesson (by normalized filename) and clamps to lesson page bounds.

Merging across lessons (`_merge_lesson_results`):
- Group by jokbo page + question number; aggregate candidate slide connections from all lessons.
- Filter connections with `ResultMerger.filter_connections_by_score(min_score, max_connections=2)`, honoring the analyzer’s `min_relevance_score`.
- Sort pages by `jokbo_page` and return normalized schema.


## API Management

### Gemini Client (`api/client.py`)

- Global Configure Lock: The Google SDK uses a process‑global key. To avoid cross‑key races in multi‑thread/multi‑key runs, calls to `genai.configure(...)` are guarded by a process‑wide `RLock` around both configuration and model calls.
- JSON Mode: `generate_content` enforces JSON responses via `response_mime_type="application/json"` and supports `max_output_tokens` when specified.
- Retry Policy: Exponential backoff for empty responses, safety blocks, and general exceptions. Finish‑reason diagnostics log token limits and safety blocks.
- Upload Lifecycle: Upload, poll `PROCESSING` → `OK` with backoff; failures raise `FileUploadError`. Deletion uses retries and logs.
- Safe Logging: Keys are masked in logs via `_key_tag()` like `k2:***abcd`.

### File Manager (`api/file_manager.py`)

- Per‑Key Scope: A `FileManager` is bound to a single `GeminiAPIClient` so list/delete operate under the correct key.
- Tracked Cleanup: Keeps a set of files uploaded by this instance; `delete_all_uploaded_files()` only removes those, not every file on the account.
- Center File Semantics: `cleanup_except_center_file(<display_name>)` keeps one file (e.g., the lesson or jokbo) to reduce re‑uploads across many pairings.


## Multi‑API Mode

The multi‑API layer distributes work across multiple keys and transparently fails over when a key errors repeatedly.

### Status + Failover (`api/multi_api_manager.py`)

- Key Tracking: `APIKeyStatus` maintains availability, consecutive failures, success rates, timestamps, and last error.
- Cooldowns: After ≥3 consecutive failures a key is cooled down for 10 minutes and excluded from selection.
- Selection: Round‑robin over available keys; `get_best_api()` can prefer the most successful when needed.
- `execute_with_failover`: Attempts operations across keys with retries; records successes/failures; surfaces a consolidated `APIError` if all attempts fail.
- `distribute_tasks`: ThreadPool distribution of independent tasks; each task itself executes under failover. Calls optional `on_progress` to integrate with progress tracking.

### Analyzer Wrapper (`analyzers/multi_api_analyzer.py`)

- Per‑Task Clients: Creates a fresh analyzer + file manager bound to the chosen key for each task.
- Distribution Strategies:
  - File pairs: `analyze_multiple_with_distribution(mode, file_pairs, parallel, max_workers)` spreads independent `(jokbo, lesson)` or `(lesson, jokbo)` pairs.
  - Chunked input: `analyze_with_chunk_retry(mode, file_path, center_file_path, chunks)` spreads chunk PDFs across keys, collects `(index, result)` pairs, and merges in original order.
- Normalization: For jokbo‑centric results, ensures any AI‑added display prefixes are removed so `lesson_filename` matches the original (critical for PDF assembly).

### Orchestration (`core/processor.py`)

- `analyze_*_multi_api(...)` builds a `MultiAPIManager` from keys and a `MultiAPIAnalyzer` wrapper.
- Chunk‑aware dispatch:
  - Lesson‑centric: If the lesson chunks, extract chunk PDFs once, distribute by jokbo with `analyze_with_chunk_retry("lesson-centric", ...)`, then merge.
  - Jokbo‑centric: Pre‑scan all lessons to see which chunk; chunked lessons use `analyze_with_chunk_retry("jokbo-centric", ...)`, single‑file lessons are analyzed once and counted as one unit of progress.
- Merging: Lesson‑centric uses `_merge_lesson_centric_results` (sorted union of slides). Jokbo‑centric defers to analyzer’s `_merge_lesson_results`.


## Chunking & Page Extraction

### Splitting and Extraction (`pdf/operations.py`)

- Page Counting: `get_page_count(path)` opens the PDF and returns length; errors surface as `PDFParsingError`.
- Splitting: `split_pdf_for_chunks(path, max_pages=30)` returns `(path, start_page, end_page)` tuples with 1‑based inclusive ranges.
- Extraction: `extract_pages(path, start_page, end_page)` writes a temporary PDF containing only those pages and returns its path.
- Merge: `merge_pdfs(pdf_paths, output_path)` concatenates multiple PDFs.
- Metadata: `get_page_metadata` and `get_page_text` provide per‑page details (used for debugging/heuristics).

### Offsets and Clamping

- Lesson‑centric: After analyzing a chunk, `_post_process_results` adds the chunk offset to `lesson_page` so the final results refer to original pages.
- Jokbo‑centric: `_post_process_results` applies offsets only when the `lesson_filename` matches the current lesson (after normalizing common prefixes) and clamps to the total page count to avoid runaway indices.

### Caching (`pdf/cache.py`)

- Thread‑safe global cache of open `fitz.Document` objects and page counts; used to avoid repeated I/O and provide quick `get_page_count` access.


## Response Parsing, Validation, and Merging

### Robust Parsing (`parsers/response_parser.py`)

- Preprocessing: Strips code fences/markdown; isolates top‑level JSON region.
- Repair: Replaces smart quotes, trims trailing commas, normalizes NaN/Infinity to `null`.
- Extraction: If needed, extracts the largest balanced JSON object from raw text.
- Partial Recovery:
  - Jokbo‑centric: Finds `"jokbo_page"` patterns, extracts nearby JSON objects with a brace scanner, and validates with `_validate_jokbo_page` (must include `question_number`, `question_text`, non‑placeholder `answer`).
  - Lesson‑centric: Reconstructs a minimal object around `related_slides` by closing braces/brackets progressively.
- Sanitation: `_sanitize_parsed_response` coerces numbers, drops placeholders/empties, normalizes wrong answer keys (e.g., `"1번".."5번"`), snaps scores to 5‑point grid with a special 110.
- Quality Heuristics: `is_result_suspicious` flags empty or low‑quality results to trigger `BaseAnalyzer._generate_with_quality_retry`.

### Merging (`parsers/result_merger.py`)

- Chunk Results: `merge_chunk_results(results, mode)`
  - Jokbo‑centric: Concatenate `jokbo_pages` and sort by `jokbo_page`.
  - Lesson‑centric: Collect all `related_slides`, de‑duplicate by `(lesson_filename, lesson_page)`, and sort.
- Relevance Filter: `filter_connections_by_score(connections, min_score, max_connections=2)` trims to high‑confidence matches.
- Multi‑API Union: `merge_api_results(results, mode)` de‑duplicates by page number across parallel runs.


## PDF Assembly

### Lesson‑Centric (`PDFCreator.create_filtered_pdf`)

- Guarantees every lesson slide appears, even if no matches; related jokbo questions are appended after the corresponding slide.
- For each question:
  - Extract the full jokbo page(s) via `extract_jokbo_question`:
    - Uses `question_numbers_on_page` (when available) to infer if the problem spills to the next page;
    - Clamps extraction ranges within jokbo bounds.
  - Insert an explanation page summarizing answer, wrong‑answer rationales, relevance reason, and the source jokbo filename/page.
- Uses a CJK‑capable font when available and normalizes Korean text (NFC) so Hangul renders correctly. Long filenames are made wrap‑friendly.

### Jokbo‑Centric (`PDFCreator.create_jokbo_centric_pdf`)

- Produces a jokbo‑first sequence: questions (extracted from jokbo PDFs) followed by related lesson slides and an explanation page.
- If there are no matches, generates a minimal “분석 결과 없음” PDF so downstream storage doesn’t fail.
 - `_resolve_lesson_path` and `_resolve_jokbo_path` normalize filenames to tolerate common prefixes and minor naming differences, and they refuse ambiguous matches so colliding filenames do not insert the wrong slide.


## Job Pipeline and Progress

### Web → Worker → PDF

- FastAPI Endpoints (`web_server.py`): Accept jokbo/lesson uploads, fixed `model` (`flash`), optional `multi_api`, and `min_relevance`. Files are stored via `StorageManager` with TTL verification.
- Celery Workers (`tasks.py`):
  - Initialize chunk‑based progress: total chunks reflect lesson chunk count × partner file count so the progress bar matches actual work.
  - Choose analysis path:
    - Single‑key: `PDFProcessor.analyze_*`.
    - Multi‑key: `PDFProcessor.analyze_*_multi_api` with `API_KEYS`.
  - Build final PDF via `PDFCreator` and persist results to Redis and disk (`output/results/<job_id>/`).
  - Finalize to 100% and clamp progress to avoid overshoot from delayed worker increments.

### Storage + Progress (`storage_manager.py`)

- Redis‑backed file passing; optional compression for large blobs; TTL refresh during queueing and processing.
- Progress Model:
  - `init_progress(job, total_chunks)`: sets totals and a `started_at` timestamp.
  - `increment_chunk(job, inc, message?)`: atomically increments completed chunks, computes ETA, average chunk time, and derives percent; caps to 99% until finalization.
  - `finalize_progress(job)`: sets percent to 100, completed_chunks to total_chunks, ETA to 0.
- Also tracks user→job lists, cancellation flags, job→task mappings, and safely persists result file paths.


## Configuration and Tuning

- Models: Fixed to `flash` (via `GEMINI_MODEL=flash`).
- Multi‑API: Enabled when multiple keys exist (`GEMINI_API_KEYS` via `config.py`). Keys are masked in logs.
- Relevance Threshold: `min_relevance_score` defaults to 80; callers can set via API (`min_relevance`) and it propagates to analyzers. Used in both lesson/jokbo workflows when filtering connections.
- Chunk Size: Default 30 pages; adjust in `PDFOperations.split_pdf_for_chunks` or override per call sites if needed.


## Safeguards and Edge Cases

- Slide Filtering: Prompts explicitly exclude “problem‑like” slides in lessons based on keywords/patterns; only content slides are considered.
- Page Validations: Jokbo references are filtered against real page bounds; chunk offsets are clamped to lesson page counts.
- Placeholder Hygiene: Parser drops empty/placeholder answers/explanations and normalizes wrong‑answer maps.
- Concurrency Safety: All Google SDK `configure` and model calls are guarded by a global lock to prevent cross‑key mixups.
- Cleanup: Uploaded files are tracked per‑client/key and cleaned aggressively; sessions and temporary chunk PDFs are removed at the end of processing.
- Debugging: Analyzers save raw responses to `output/debug/` and optionally mirror chunk results to Redis for inspection.


## High‑Level Sequence (Typical Runs)

1) User uploads jokbo(s) and lesson(s) via FastAPI; files stored in Redis; a Celery task is enqueued.
2) Worker determines chunk totals, configures model, and instantiates `PDFProcessor(session=job_id)`.
3) Processor runs either lesson‑centric or jokbo‑centric analyzer; large lessons are chunked automatically.
4) Content generation uses JSON mode with retries; responses are parsed, repaired if needed, sanitized, and merged across chunks/files.
5) Final PDFs are assembled and stored; progress is finalized and results exposed via `/results/{job_id}`.


## Where To Extend

- Custom Scoring: Adjust `constants.RELEVANCE_CRITERIA` and downstream filters to tune rigor.
- Alternate Chunk Sizes: Update `PDFOperations.split_pdf_for_chunks` or make it configurable per request.
- Additional Failover Logic: Enhance `APIKeyStatus` (e.g., long‑term health scoring) or dynamic max_workers in `distribute_tasks`.
- New Output Shapes: Extend `ResponseParser._sanitize_parsed_response` and `PDFCreator` if schema evolves.
