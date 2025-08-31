# CLAUDE.md

Guidance for Claude Code when working on this repository.

Overview
- Purpose: Analyze exam PDFs (jokbo) against lecture PDFs using Google Gemini, then produce polished result PDFs with matches and explanations.
- Stack: FastAPI backend + Celery workers + Redis storage manager + Tailwind CSS frontend.
- Auth: Cookie-based session (Google Sign-In or dev login). Most API routes require authentication.

Project Structure
- `server/main.py`: FastAPI app factory, CORS, lifespan cleanup, router includes.
- `server/routes/`:
  - `misc.py`: `/`, `/guide`, `/profile`, `/styles.css`, `/config`, `/health`, admin cleanup and storage stats.
  - `auth.py`: `/auth/config`, `/auth/google`, `/auth/dev-login`, `/auth/logout`, `/me`, admin testers and users APIs.
  - `analyze.py`: `/analyze/jokbo-centric`, `/analyze/lesson-centric`, `/analyze/partial-jokbo`, `/analyze/batch`.
  - `jobs.py`: `/status/{task_id}`, `/progress/{job_id}`, `/results/{job_id}`, `/result/{job_id}[/{filename}]`, `/user/{user_id}/jobs`, cancel/delete job APIs.
- `storage_manager.py`: Redis-backed file passing, results store, progress tracking, tokens, testers, job ownership.
- `tasks.py`: Celery tasks that run analyses and emit PDFs.
- `pdf_processor/`: Core analysis engine (orchestration, analyzers, parsers, Gemini API integration).
- `pdf_creator.py`: Builds final PDFs.
- `frontend/`: Tailwind static UI served at `/`.

Environment
- Core: `GEMINI_API_KEY` or `GEMINI_API_KEYS` (comma-separated), `REDIS_URL`.
- Auth: `AUTH_SECRET_KEY`, `GOOGLE_OAUTH_CLIENT_ID`, `ADMIN_EMAILS` (comma-separated), optional `ADMIN_PASSWORD`.
- Cookies: `COOKIE_SECURE` (true/false), `COOKIE_SAMESITE` (Lax/None), `SESSION_EXPIRES_SECONDS`.
- Tokens: `CBT_TOKENS_INITIAL`, `FLASH_TOKENS_PER_CHUNK`, `PRO_TOKENS_PER_CHUNK`.
- Storage: `RENDER_STORAGE_PATH` (writable), retention: `DEBUG_RETENTION_HOURS`, `RESULT_RETENTION_HOURS`.

Runbook
- Setup:
  - `python -m venv venv && source venv/bin/activate`
  - `pip install -r requirements.txt`
  - `cp .env.example .env` and set the variables above.
- Local dev:
  - API: `uvicorn web_server:app --reload`
  - Worker: `celery -A tasks:celery_app worker -Q analysis,default --loglevel=info`
  - Optional: `export ALLOW_DEV_LOGIN=true` and set `ADMIN_PASSWORD` + `ALLOWED_TESTERS` for local sign-in.
- Smoke:
  - `python scripts/generate_dummy_pdfs.py`
  - `bash scripts/smoke.sh` (requires API + worker + Redis)

Frontend Notes
- The Tailwind UI uses `fetch` extensively. Always include `credentials: 'include'` so the session cookie is sent.
- File upload fields must be named `jokbo_files` and `lesson_files`. Per-file size limit is 50MB (enforced server-side).
- Common endpoints used by the UI: `/config`, `/auth/config`, `/auth/google`, `/me`, `/analyze/*`, `/status/*`, `/progress/*`, `/results/*`, `/result/*`, `/jobs/*`, `/user/{id}/jobs`, `/admin/*`.

Backend Contracts
- Analyze routes accept query params and optional form fallbacks:
  - `model`: `flash|pro` (default `flash`)
  - `multi_api`: bool (default `false`)
  - `min_relevance`: integer [0, 110] (stored as metadata)
  - Multipart: `jokbo_files[]`, `lesson_files[]`
- Progress: `{ progress, message, total_chunks?, completed_chunks?, eta_seconds? }`.
- Results: list via `/results/{job_id}`, download via `/result/{job_id}/{filename}`.

Development Practices
- Style: PEP 8, 4-space indents, add type hints where practical; brief docstrings on public APIs.
- Keep changes minimal and focused; do not change route names/fields/response shapes without explicit instruction.
- Prefer small, testable functions; explicit over implicit.
- Use Conventional Commits (e.g., `feat:`, `fix:`) with a clear rationale.
- Do not commit secrets or personal PDFs.

Troubleshooting
- Frontend “Failed to fetch”: ensure `credentials: 'include'` on fetch, verify cookie flags (`COOKIE_SECURE`, `COOKIE_SAMESITE`) and CORS settings.
- 401 on analyze/status/results: user not authenticated; sign in first.
- 413 on upload: a file exceeded 50MB.

