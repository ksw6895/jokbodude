CBT (Closed Beta Test) setup

- Google Sign-In: Set `GOOGLE_OAUTH_CLIENT_ID` and `AUTH_SECRET_KEY` in `.env`.
- Limit testers: Set `ALLOWED_TESTERS` to a comma-separated list of emails.
- Initial grant: Configure `CBT_TOKENS_INITIAL` (default 200).
- Token costs per chunk: `FLASH_TOKENS_PER_CHUNK=1`, `PRO_TOKENS_PER_CHUNK=4`.
- Optional dev login: set `ALLOW_DEV_LOGIN=true` and `ADMIN_PASSWORD` for local testing.
- Optional feedback URL: `FEEDBACK_FORM_URL` shows a link in the navbar.

Endpoints

- POST `/auth/google` with `id_token` from Google Identity Services.
- POST `/auth/dev-login` with `email` + `password` (admin) if enabled.
- POST `/auth/logout` clears the session.
- GET `/me` returns `authenticated`, `user_id`, `email`, and `tokens`.
- Admin tokens:
  - POST `/admin/users/{user_id}/tokens?password=...` body: `amount` to set balance.
  - POST `/admin/users/{user_id}/tokens/add?password=...` body: `delta` to add/subtract.

Token consumption

- Each processed PDF chunk consumes tokens automatically:
  - flash: 1 token per chunk (default)
  - pro: 4 tokens per chunk (default)
- If the balance is insufficient, the job is cooperatively canceled mid-run with a progress message.
- Analyze endpoints preflight: if a user_id is present and balance ≤ 0, the submission is rejected (402).

Frontend

- Navbar shows Google Sign-In, current email, and token balance.
- If Google is not configured, dev login is available when enabled.
