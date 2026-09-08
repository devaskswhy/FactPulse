# Deploying FactPulse

Backend on Railway, frontend on Vercel. Both connect to the same GitHub repo
and redeploy automatically on push to `main`.

**Before you start, be honest with yourself about the free tier.** Railway's
"free" plan is a one-time $5 usage credit that runs out — typically in a few
weeks of light use, not a recurring monthly allowance. After it's gone, Railway
asks for a card and bills the Hobby plan (~$5/mo). That's a fine tradeoff for a
graded evaluation window; it is not a permanently free host. Budget for that
before treating the deployed URL as long-lived.

---

## 1. Backend — Railway

1. **New Project → Deploy from GitHub repo**, select this repository.
2. Railway will try to build from the repo root. This is a monorepo, so in the
   service's **Settings → Root Directory**, set it to `backend`. Once set,
   Railway reads `backend/railway.json` (already committed) for the build and
   start command — Nixpacks auto-detects Python from `requirements.txt`, and
   the start command is pinned explicitly:

   ```
   uvicorn app.main:app --host 0.0.0.0 --port $PORT
   ```

   `$PORT` is injected by Railway; nothing to configure there.

3. **Attach a Volume** (Settings → Volumes → New Volume) so the SQLite
   database and uploaded PDFs survive a redeploy — without one, Railway's
   filesystem is ephemeral and a new deploy silently resets the corpus to
   empty. Mount it at `/data`.

4. **Environment variables** (Settings → Variables). Everything in
   `backend/.env.example` applies; the ones that matter for a deploy:

   | Variable | Value |
   | --- | --- |
   | `GEMINI_API_KEY` (+ `_2`, `_3`) | Your key(s). See the multi-key note in `.env.example` — a second key only helps if it's from a **different Google account**. |
   | `DATABASE_PATH` | `/data/factpulse.db` |
   | `UPLOAD_DIR` | `/data/uploads` |
   | `PAGE_CACHE_DIR` | `/data/page_cache` |
   | `STORAGE_DIR` | `/data/storage` |
   | `FRONTEND_ORIGIN` | Set after step 2 below. Comma-separated if you want both the deployed frontend and `http://localhost:3000` to work at once. |

   Everything else (`GEMINI_MODEL`, chunking, extraction concurrency, etc.)
   can keep its default from `config.py` — only set a variable if you want to
   override it.

5. Deploy. Check `https://<your-service>.up.railway.app/health` — it should
   report `"database": "ok"` and `"gemini_configured": true`.

   **No manual seeding step needed.** A fresh volume means an empty
   database, and the backend notices that on startup and loads the
   committed demo corpus (`backend/seed/demo_corpus.sql`) automatically —
   335 facts, 53 relationships, zero model calls. `GET /facts?limit=1`
   should immediately report `"total": 335`. This only ever fires on a
   genuinely empty database, so it never touches real data from later
   uploads, including after a redeploy (the volume persists them). See
   ARCHITECTURE.md, "The offline demo seed."

---

## 2. Frontend — Vercel

1. **New Project → Import** this repository. Vercel needs the subdirectory:
   set **Root Directory** to `frontend` in the project's General settings.
2. Vercel auto-detects Next.js — no build command override needed.
3. **Environment variable**: `NEXT_PUBLIC_API_BASE_URL` = your Railway URL
   from step 1.5 (e.g. `https://factpulse-backend.up.railway.app`, no
   trailing slash).
4. Deploy. Vercel gives you a `*.vercel.app` URL.

---

## 3. Close the loop

Go back to Railway and set `FRONTEND_ORIGIN` to the Vercel URL from step 2.4
(add it to the existing value, comma-separated, if you still want local dev
to work against the deployed backend). Redeploy the backend for the CORS
change to take effect — Railway does this automatically on a variable change.

Verify: open the Vercel URL, upload a PDF, confirm it ingests. If the browser
console shows a CORS error, `FRONTEND_ORIGIN` doesn't yet include the exact
origin the frontend is served from (scheme + host, no path).

---

## Known limits of this setup

- **Cold demo risk is now low, not zero.** Three keys × seven models is 21
  quota slots (`GET /health` reports how many remain), but a burst of
  simultaneous reviewer uploads could still exhaust it during a live grading
  window. There's no automatic alert for this — check `/health` before a demo.
- **The volume is one disk, not a backup.** If you want the corpus to survive
  a Railway project deletion (not just a redeploy), export `factpulse.db`
  separately.
- **No staging environment.** A push to `main` redeploys the live instance
  directly on both platforms. Fine for a solo project at this stage; worth a
  branch-based preview setup if this grows past the assignment.
