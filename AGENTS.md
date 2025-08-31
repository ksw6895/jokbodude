# Repository Guidelines

## Project Structure & Module Organization
- `pdf_processor/`: Core engine
  - `core/` (orchestration), `analyzers/` (lesson/jokbo logic), `api/` (Gemini + file APIs), `parsers/` (response/merge).
- `web_server.py`: FastAPI app (uploads, status, results).
- `tasks.py`: Celery workers that run analyses and emit PDFs.
- `pdf_creator.py`: Builds final PDFs; `storage_manager.py`: Redis-backed file passing and progress.
- `frontend/`: Static UI served at `/`; `jokbo/`, `lesson/`: input PDFs; `output/`: generated results.
- Config: `.env(.example)`, `config.py`, `celeryconfig.py`, `requirements.txt`.

## API Surface (Current)
- Analyze: `POST /analyze/jokbo-centric`, `POST /analyze/lesson-centric`, `POST /analyze/partial-jokbo`, `POST /analyze/batch`
  - Multipart fields: `jokbo_files`, `lesson_files` (50MB per file)
  - Query params: `model=flash|pro`, `multi_api=true|false`, `min_relevance=0..110`
- Jobs: `GET /status/{task_id}`, `GET /progress/{job_id}`, `GET /results/{job_id}`, `GET /result/{job_id}/{filename}`, `DELETE /result/{job_id}/{filename}`, `POST /jobs/{job_id}/cancel`, `DELETE /jobs/{job_id}`
- Users: `GET /user/{user_id}/jobs`
- Auth: `GET /auth/config`, `POST /auth/google`, `POST /auth/dev-login` (optional), `POST /auth/logout`, `GET /me`, admin testers/users
- Misc: `GET /`, `GET /guide`, `GET /profile`, `GET /styles.css`, `GET /config`, `GET /health`, `POST /admin/cleanup`, `GET /admin/storage-stats`

## Build, Test, and Development Commands
- Setup (Python 3.8+):
  ```bash
  python -m venv venv && source venv/bin/activate
  pip install -r requirements.txt
  cp .env.example .env  # set GEMINI_API_KEY or GEMINI_API_KEYS
  ```
- Run API (dev): `uvicorn web_server:app --reload` (default port 8000).
- Start worker: `celery -A tasks:celery_app worker -Q analysis,default --loglevel=info`.
- Quick manual test (replace files):
  ```bash
  curl -F "jokbo_files=@jokbo/sample.pdf" -F "lesson_files=@lesson/sample.pdf" \
       "http://localhost:8000/analyze/jokbo-centric?model=flash"
  # then poll /status/{task_id} and fetch /results/{job_id}
  ```
 - Frontend login (local): set `ALLOW_DEV_LOGIN=true`, `ADMIN_PASSWORD`, and add your email to `ALLOWED_TESTERS` to use dev login.

## Coding Style & Naming Conventions
- Follow PEP 8; 4-space indents; include type hints where practical.
- Names: modules/files `lower_snake_case`, functions `snake_case`, classes `PascalCase`.
- Keep functions small and testable; prefer explicit over implicit; add short docstrings on public APIs.

## Testing Guidelines
- Framework tests are not set up yet; use endpoint smoke tests with small PDFs.
- Add new tests under `tests/` as `test_*.py` if contributing logic; aim to cover analyzers and parser merge paths.
- Validate outputs exist in `output/` and that `/health`, `/status/{task_id}`, and `/results/{job_id}` respond as expected.
- Smoke tools: `python scripts/generate_dummy_pdfs.py` then `bash scripts/smoke.sh` (requires API + Celery + Redis running). Polls and downloads a result to `output/`.

## Frontend Guidelines
- Tailwind UI in `frontend/index.html` and `frontend/profile.html`.
- All `fetch` calls must include `credentials: 'include'` so session cookies are sent to protected endpoints.
- Do not change multipart field names (`jokbo_files`, `lesson_files`) or route shapes without coordinating backend changes.
- Keep request paths, query params, and polling intervals in sync with routes under `server/routes/`.

## Commit & Pull Request Guidelines
- Commits follow Conventional Commits seen in history (e.g., `feat: add multi-API`, `fix: Redis storage fallback`).
- PRs should include: clear description, rationale, before/after behavior, linked issues, and logs/screenshots for API flows.
- Keep diffs focused; update docs/comments when changing behavior; ensure local smoke tests pass.

## Security & Configuration Tips
- Do not commit secrets or personal PDFs. Configure `GEMINI_API_KEY` or `GEMINI_API_KEYS`; set `REDIS_URL` for web/worker; on Render ensure `RENDER_STORAGE_PATH` is writable.
- `GEMINI_MODEL` is fixed to `flash`; prefer multi-API for throughput.
 - Auth/session: set `AUTH_SECRET_KEY`, `GOOGLE_OAUTH_CLIENT_ID`, `ADMIN_EMAILS`; adjust `COOKIE_SECURE`/`COOKIE_SAMESITE` for your environment.
