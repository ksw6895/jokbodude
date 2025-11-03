# Render Environment Troubleshooting for Jokbo-Centric Multi-API Mode

This guide outlines common configuration pitfalls when deploying JokboDude on [Render](https://render.com) and running **jokbo-centric** analysis with **multi-API** support.

## 1. Environment Variables
- **GEMINI_API_KEYS**
  - Must contain comma-separated API keys with no spaces. If missing or empty, `config.py` raises `ValueError` and the service fails to start.
  - Set in Render dashboard for both `jokbodude-api` and `jokbodude-worker` services.
- **GEMINI_MODEL**
  - Accepts `pro`, `flash`, or `flash-lite`. Using an unsupported value causes the model fallback to `pro`.
- **PYTHONPATH**
  - Ensure `PYTHONPATH=/opt/render/project/src` so imports resolve correctly.
 
## 2. Service Components & Plan Capacity
- **API service (`jokbodude-api`)** – handles HTTP requests. Choose a plan with enough RAM for concurrent uploads (≥1 GB recommended); free tiers often crash under load.
- **Worker service (`jokbodude-worker`)** – executes Celery tasks and PDF analysis. It benefits from higher CPU and memory; standard plans with ≥2 GB RAM reduce timeouts.
- **Redis (`jokbodude-redis`)** – message broker and result backend. Ensure it is sized for your expected queue depth.
- Confirm component plans in the Render dashboard; mismatched capacities cause slowdowns or OOM kills.

## 3. Shared Storage Path
- `RENDER_STORAGE_PATH` differs between services in `render.yaml`:
  - API service: `/data/storage`
  - Worker service: `/tmp/storage`
- For correct operation, configure both services to use the same persistent mount. Mismatched paths cause the API to miss results produced by the worker.

## 4. Redis Connectivity
- Both services rely on `REDIS_URL` provided by the `jokbodude-redis` instance. If the Redis service name changes, update the `fromService` section accordingly.
- Misconfigured TCP keepalive options can prevent Celery from reaching Redis. An error such as:
  
  ```
  TypeError: 'str' object cannot be interpreted as an integer
  ```
  
  indicates string values were supplied for socket options (e.g., in `REDIS_CLIENT_KWARGS`). Use integers or remove the options so the default connection settings apply.

## 5. Multi-API Behavior
- The processor distributes work across keys using a thread pool, allowing requests to run **simultaneously** when multiple tasks are queued.
- Logs emit `Attempting operation with API key N` from different threads. Interleaved entries confirm parallel usage instead of sequential round-robin.
- Keys that fail repeatedly enter a 10‑minute cooldown. Ensure every key has adequate quota so the pool never empties.
- Supplying only one key disables true multi-API benefits; include at least two keys for failover and parallelism.

## 6. File Isolation Across API Keys
- Each API key manages its own uploads on Gemini. The `FileManager` only deletes files it created, preventing cross-key cleanup.
- Double-check isolation by running `genai.list_files()` under each key—files uploaded by key A are invisible to key B.
- Worker logs show `Deleted file:` messages for each key. Verify corresponding creation and deletion lines to ensure cleanup succeeds.

## 7. Jokbo-Centric Execution
- Invoke multi-API jokbo-centric analysis via `python main.py --mode jokbo-centric --multi-api`.
- Run from API or worker services that share the same storage mount so results are accessible.
- Large lesson files may be chunked across APIs; monitor logs for `chunk` retry messages to diagnose failures.

## 8. Monitoring and Logs
- Check service logs in Render dashboard for startup errors or missing environment variables.
- Enable [Prometheus metrics](https://prometheus.io/) to monitor task counts and processing time.

Following these tips should help avoid the most common deployment issues on Render.
