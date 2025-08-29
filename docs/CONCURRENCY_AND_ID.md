# Concurrency, Isolation, and the User ID System

This document explains how JokboDude behaves when multiple users make requests at the same time, what data is isolated per job, how progress and results are tracked, and what the current “User ID” mechanism does (and does not) provide. It also lists security gaps and concrete recommendations.

## TL;DR

- Requests from many users are accepted concurrently and queued to Celery workers. Each request creates a unique `job_id` and all data is namespaced by that ID. No cross‑job file or progress collisions.
- The “User ID” collected in the UI is a tagging mechanism to let users list their jobs; it is not authentication/authorization. Anyone who knows a `job_id` can fetch its results.
- Protect results and progress with per‑job access tokens; protect the admin cleanup route; optionally protect “My Jobs” with a user secret.

---

## Request Lifecycle and Isolation

1) Client uploads files to a FastAPI endpoint (`/analyze/jokbo-centric`, `/analyze/lesson-centric`, or `/analyze/batch`).
   - The server assigns a `job_id` (UUID).
   - Files are written to a temp dir and then stored in Redis under: `file:{job_id}:{type}:{filename}:{hash}` with TTL (default 24h).
   - Job metadata is saved to `job:{job_id}:metadata` (48h TTL), including `model`, `multi_api`, and optional `user_id`.

2) The server enqueues a Celery task (`analysis` queue). For batch mode, a chord of subtasks is created.

3) The worker downloads files for that `job_id` to a per‑job temp area, runs the analyzers, and writes output PDFs to:
   - Redis: `result:{job_id}:{filename}` (48h TTL)
   - Disk: `{RENDER_STORAGE_PATH or ./output}/results/{job_id}/{filename}`

4) Progress is maintained per job at `progress:{job_id}` with percent, ETA, and chunk counters.

Implication: Multiple users can submit jobs at once; each job’s files, progress, and results are isolated by `job_id` keys and output folders.

---

## Concurrency Model

- Web API: Accepts concurrent HTTP requests. Uploads and metadata writes are independent per `job_id`.
- Queueing: Jobs are pushed to Celery; actual parallelism depends on worker `CELERY_CONCURRENCY` and number of workers.
- Worker processing: Each task uses its own temp directory. The PDF engine does not share temp files across jobs.
- Progress accounting: Atomic Redis ops per `job_id` prevent cross‑job progress corruption.

### Multi‑API Concurrency (when enabled)

- The system can use multiple Gemini API keys for throughput and resilience.
- `MultiAPIManager` creates one client+model per API key and gates concurrency per key (default 1; configurable via `GEMINI_PER_KEY_CONCURRENCY`).
- Chunk tasks are distributed across keys with failover and completion callbacks increment the current job’s chunk counts.

---

## User ID System — What It Is and Isn’t

The UI allows users to enter a “User ID” (stored only in localStorage). When provided on upload:

- The server associates the `job_id` with that `user_id` via:
  - `user:{user_id}:jobs` (list of recent job IDs) and
  - `job:{job_id}:user` (reverse pointer)
- Endpoint `GET /user/{user_id}/jobs` returns recent jobs with status, progress, and files.

Important: This is not access control. Result and progress endpoints (`/results/{job_id}`, `/result/{job_id}/…`, `/progress/{job_id}`) do not verify `user_id`. Knowledge of a `job_id` is sufficient to fetch its artifacts.

Guidance:
- Treat `user_id` as a convenience tag (e.g., a nickname), not an identity.
- Avoid personal identifiers; anyone who knows your `user_id` can list its jobs.

---

## Security Posture and Gaps

- Results/Progress Access: `job_id` alone grants access to progress and results.
- “My Jobs” Listing: Publicly lists jobs for any `user_id` without proving ownership.
- Admin Cleanup: `/admin/cleanup` is publicly callable.

Risk factors are partially mitigated by random UUID `job_id`s, but URLs may be shared or logged. Strengthen authorization if you expect multi‑tenant use.

---

## Recommendations (Actionable)

1) Per‑Job Token
- Generate `access_token = secrets.token_urlsafe(24)` on job creation.
- Store `job:{job_id}:token = access_token` (48h TTL).
- Require `access_token` for:
  - `GET /progress/{job_id}`
  - `GET /results/{job_id}` and `GET /result/{job_id}/{filename}`
  - `POST /jobs/{job_id}/cancel`
- Accept via header `X-Job-Token` or query `?token=...`.

2) Protect “My Jobs”
- Issue a `user_secret` (e.g., once per browser and persisted in localStorage) and send it with uploads.
- Store `user:{user_id}:secret` and require it for `GET /user/{user_id}/jobs`.
- Alternatively, migrate to proper authenticated sessions.

3) Lock Down Admin
- Require an admin key in header (e.g., `X-Admin-Key`) for `/admin/cleanup` and verify against `ADMIN_KEY` env.

4) Optional: Rate Limiting
- Add request rate limits (e.g., `slowapi`) on upload and status endpoints to prevent abuse.

5) Operational Tuning (throughput)
- Increase `CELERY_CONCURRENCY` for parallel jobs.
- Add more workers for horizontal scaling.
- If needed, increase `GEMINI_PER_KEY_CONCURRENCY` carefully to avoid API rate errors.

---

## Implementation Sketch

Per‑Job Token (server‐side):

```python
# When creating a job
import secrets
token = secrets.token_urlsafe(24)
storage_manager.set_job_token(job_id, token)

# Verifier utility
def verify_job_token(job_id: str, provided: str) -> bool:
    expected = storage_manager.get_job_token(job_id)
    return bool(expected) and secrets.compare_digest(expected, provided or "")

# Example guard in /progress/{job_id}
token = request.headers.get('X-Job-Token') or request.query_params.get('token')
if not verify_job_token(job_id, token):
    raise HTTPException(status_code=403, detail="Forbidden")
```

StorageManager additions:

```python
def set_job_token(self, job_id: str, token: str) -> None:
    self.redis_client.setex(f"job:{job_id}:token", 172800, token)

def get_job_token(self, job_id: str) -> Optional[str]:
    v = self.redis_client.get(f"job:{job_id}:token")
    return v.decode() if isinstance(v, (bytes, bytearray)) else v
```

Admin protection:

```python
def require_admin(request: Request):
    key = request.headers.get('X-Admin-Key')
    if not key or key != os.getenv('ADMIN_KEY'):
        raise HTTPException(403, "Forbidden")

@app.post("/admin/cleanup")
def admin_cleanup(..., request: Request):
    require_admin(request)
    ...
```

“My Jobs” protection (simple variant):

```python
# On first use for a user_id, set a secret and return it to the client.
# Require that secret in header for listing jobs.
def set_user_secret(self, user_id: str, secret: str):
    self.redis_client.setex(f"user:{user_id}:secret", 2592000, secret)

def get_user_secret(self, user_id: str) -> Optional[str]:
    v = self.redis_client.get(f"user:{user_id}:secret")
    return v.decode() if isinstance(v, (bytes, bytearray)) else v
```

---

## Operational Notes

- Model is fixed to `flash`; this avoids per‑request model variance.
- Results are also persisted to disk to reduce Redis memory pressure and enable later downloads without cache hits.
- Cancellation is cooperative: a cancel flag is stored, and the worker checks it between steps; Celery revoke is attempted if a task ID is known.

---

## Checklist

- [ ] Issue per‑job `access_token` and enforce on results/progress/cancel endpoints.
- [ ] Add admin key requirement to `/admin/cleanup`.
- [ ] Optionally add `user_secret` to protect `GET /user/{user_id}/jobs`.
- [ ] Add rate limiting on upload/status endpoints.
- [ ] Tune `CELERY_CONCURRENCY` / add workers for expected load.

