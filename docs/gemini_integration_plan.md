# Gemini Integration With Problem Snipper

This document describes how to combine the new `ProblemSnipper`-based metadata
extractor with Gemini API calls and the existing JokboDude pipeline. It covers
prompt design, response handling, PDF merge updates, and reliability
considerations.

## 1. End-to-End Flow Overview

1. **Problem extraction** – `ProblemSnipper` parses the uploaded jokbo PDFs and
   produces structured `ProblemSegment` records, including question text,
   options, per-page rectangles, and metadata such as `exam_year` (see
   `pdf_processor/core/problem_snipper.py`).
2. **Feature enrichment** – For each segment we generate a stable `qid`
   (details below) and persist the augmented payload in Redis or the storage
   backend already used by `pdf_processor.core.processor.PDFProcessor`.
3. **Prompt construction** – Build Gemini requests containing:
   - The question text and options.
   - Inline metadata (`qid`, `display_number`, `page_index`, `exam_year`).
   - An explicit instruction asking Gemini to echo the page number and problem
     number in its response.
4. **Gemini inference** – Send prompts via `GeminiAPIClient` (existing class in
   `pdf_processor/api/client.py`).
5. **Response merge** – Merge Gemini JSON responses back into the
   `ProblemSegment` records by `qid`, preserving the page rectangles captured by
   the extractor.
6. **PDF composition** – `pdf_creator.py` consumes the enriched segments to draw
   highlights and embed Gemini-generated explanations in the final booklet.

## 2. Problem Extraction Integration

### 2.1. Module Usage

```python
from pdf_processor.core.problem_snipper import ProblemSnipper

snipper = ProblemSnipper()
segments = snipper.extract(jokbo_path)
```

Each `ProblemSegment` currently contains:

- `display_number`: textual problem number (e.g., `"47"`).
- `options`: list of answer choice strings.
- `page_spans`: mapping of page index → list of `RectTuple` bounding boxes.
- `metadata`: dictionary with fields such as `exam_year` and `author` when
  present.

### 2.2. QID Generation

Create a helper (e.g., `pdf_processor/utils/qid.py`) that derives a stable ID
from:

```
qid = sha1(f"{pdf_hash}:{page_index}:{display_number}:{segment.text[:120]}")
```

Store the `qid` in the segment metadata before persisting to storage. This ID
becomes the join key between extraction data and Gemini responses.

### 2.3. Persistence

Extend the existing storage layer (`storage_manager.py`) to keep the enriched
segments while a job is running. Suggested structure:

```python
{
    "segments": [
        {
            "qid": "...",
            "display_number": "47",
            "page_index": 12,
            "page_spans": [...],
            "metadata": {...},
            "text": "...",
            "options": [...],
        },
        ...
    ]
}
```

## 3. Gemini Prompt Engineering

### 3.1. Prompt Template

We reuse the existing constants-based prompts (예: `JOKBO_CENTRIC_TASK`) and
강조 문구(`COMMON_WARNINGS`) to instruct Gemini. The prompt now 명시적으로
"해당 문제가 족보 PDF 몇 페이지, 몇 번 문제인지 JSON으로 보고" 하도록
업데이트되어 있으니 별도의 빌더 없이도 페이지/문항 번호를 회신받을 수
있습니다.

### 3.2. Multi-Modal Considerations

For purely textual questions, the prompt above is sufficient. If future PDFs
include figures, store references (e.g., `metadata['figure_path']`) during
extraction and embed them in the prompt as base64 images or give textual alt
explanations.

## 4. Response Handling & Validation

1. Parse Gemini JSON and validate it against the schema. Reject responses that
   lack required keys or contain mismatched `qid`s.
2. For each valid response, update the corresponding `ProblemSegment` record
   with:
   - `gemini_answer`
   - `gemini_rationale`
   - `gemini_page_confirmation`
3. If Gemini returns a different page/problem number than expected, log the
   discrepancy for manual review and fall back to the extractor’s data.

## 5. PDF Merge Updates

Modify `pdf_creator.py` to consume enriched segments:

- Use `gemini_answer` to mark correct choices.
- Include `gemini_rationale` in the output PDF (e.g., as side notes).
- Use `page_spans` for drawing boxes; this remains unchanged from earlier
  highlighting logic.
- Display metadata such as `exam_year` or `author` if desired.

## 6. Error Scenarios & Robustness Strategies

### 6.1. Extraction Issues

| Failure Mode | Manifestation | Mitigation |
|--------------|---------------|------------|
| Anchor miss (question not detected) | Question text merged with previous item | Expand regex/prompt cues (`_looks_like_question`), add fallback by scanning for `display_number` gaps and splitting heuristically. |
| False option merge | Next question appended to options (previous bugs) | Current guard prevents segments with known anchors from merging. Keep regression tests on tricky PDFs. |
| Missing metadata (year/author) | JSON lacks context for Gemini | Treat metadata as optional; prompts still include page/problem numbers derived from extraction. |
| Bounding boxes misaligned | Highlights drawn in wrong spot | Cross-check with PyMuPDF debug overlays (use `ProblemSnipper.write_outputs(..., annotate=True)` to inspect). |

### 6.2. Gemini Response Issues

| Failure Mode | Manifestation | Mitigation |
|--------------|---------------|------------|
| Missing `qid`/page/problem fields | Cannot join response | Validate schema; re-prompt or log for manual correction. |
| Wrong page/problem echoed | Mismatched highlight | Prefer extractor data; only surface mismatch in logs/QA dashboards. |
| Long rationale exceeding layout | PDF overflow | Truncate or wrap with templating rules; optionally store full rationale externally. |

### 6.3. Operational Considerations

- **Rate limiting**: Batch prompts and use existing multi-API manager
  (`pdf_processor/api/multi_api_manager.py`) to distribute across keys.
- **Retry strategy**: Retry transient Gemini errors with exponential backoff,
  but avoid re-sending when schema validation fails—adjust prompt first.
- **Progress reporting**: Update `StorageManager` counters when extraction,
  Gemini calls, and PDF merges complete so `/progress/{job_id}` reflects the new
  stages.

## 7. Implementation Checklist

1. **Extractor integration**
   - [ ] Add QID generation utility.
   - [ ] Persist enriched segments via `StorageManager`.
   - [ ] Extend unit tests to cover year/author handling.
   - [x] `pdf_processor/utils/qid.py` generates 안정적인 파일 해시와 QID.
   - [x] `ProblemSnipper.extract(..., assign_qids=True)` stores QIDs in
         `segment.metadata['qid']` and offers serialization utilities.
   - [x] `StorageManager.store_problem_segments()` exposes a Redis/local
         persistence path for downstream workers.

2. **Gemini response merge**
   - [ ] Update result merger (`pdf_processor/parsers/result_merger.py`) to
         join responses on `qid`.
   - [ ] Log any disagreements between Gemini-confirmed page/problem and
         extractor metadata.

3. **PDF creator enhancements**
   - [ ] Accept Gemini fields when rendering.
   - [ ] Add toggle for including rationales vs. answers only (configurable in
         `.env`).
   - [x] `PDFCreator.register_gemini_annotations()` stores Gemini answers for
         later rendering hooks.

4. **QA & Monitoring**
   - [ ] Create smoke tests that run the full pipeline on a small jokbo sample.
   - [ ] Add metric/log entries for extraction failures, schema validation
         errors, and Gemini mismatches.

## 8. Future Improvements

- **Column detection**: Integrate column-span heuristics (planned in
  `docs/problem_extraction_plan_en.md`) to further reduce cross-question
  merges.
- **Image snippets**: Capture figure crops alongside text to give Gemini more
  context when needed.
- **Interactive review UI**: Surface mismatched or low-confidence items for
  manual correction before final PDF export.

By following this plan we can merge the robust PDF snipping output with Gemini
responses while keeping the final PDF annotations aligned with the original
questions.

- **Testing hooks**: Run `scripts/run_problem_snipper.py` to verify QID와
  메타데이터 캡처가 기대대로 동작하는지 확인하세요. 추출 결과를 저장하려면:

  ```bash
  python - <<'PY'
  from storage_manager import StorageManager
  from pdf_processor.core.problem_snipper import ProblemSnipper

  sm = StorageManager()
  snipper = ProblemSnipper()
  segments = snipper.extract('jokbo/sample.pdf')
  sm.store_problem_segments('job-demo', ProblemSnipper.serialize_segments(segments))
  print(sm.load_problem_segments('job-demo')[0]['metadata'])
  PY
  ```
