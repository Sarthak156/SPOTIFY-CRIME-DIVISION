# Deployment Guide — Render & Vercel

This guide covers step-by-step deployment of the `spotify-crime-report` monorepo:

- `backend/` — FastAPI app (recommended to run on Render or similar)
- `frontend/` — Next.js (recommended to run on Vercel; can run on Render too)

Follow these steps to deploy successfully and wire up OAuth with Spotify.

---

## 1. Prerequisites

- A Git repository with the project root containing `backend/` and `frontend/`.
- A Spotify Developer App created at https://developer.spotify.com/dashboard/ with a Client ID and Client Secret.
- Domains where you'll host apps (Render or Vercel URLs or custom domains).
- Make sure `backend/requirements.txt` and `frontend/package.json` are up to date.

Environment variables used by the project:

- `SPOTIFY_CLIENT_ID` — Spotify Client ID
- `SPOTIFY_CLIENT_SECRET` — Spotify Client Secret
- `SPOTIFY_REDIRECT_URI` — e.g. `https://<BACKEND_HOST>/callback`
- `NEXT_PUBLIC_BACKEND_URL` — e.g. `https://<BACKEND_HOST>` (used by the Next app)
- Optional: `GOOGLE_*` or other service keys if used

Replace `<BACKEND_HOST>` and `<FRONTEND_HOST>` with the real domain names provided by Render/Vercel.

---

## 2. Deploy the backend (Render — recommended)

1. Sign in to Render (https://dashboard.render.com) and click **New** → **Web Service**.
2. Connect your repository and select the branch to deploy.
3. For **Root Directory**, set to `backend` (or set the service's working directory accordingly).
4. Runtime: select **Python 3.x**.
5. Build command: (optional)

   ```bash
   pip install -r requirements.txt
   ```

6. Start command (Render exposes `$PORT`):

   ```bash
   uvicorn main:app --host 0.0.0.0 --port $PORT
   ```

7. Add environment variables in Render service settings:

   - `SPOTIFY_CLIENT_ID`
   - `SPOTIFY_CLIENT_SECRET`
   - `SPOTIFY_REDIRECT_URI` -> `https://<BACKEND_HOST>/callback`
   - `FRONTEND_URL` or `NEXT_PUBLIC_BACKEND_URL` -> `https://<FRONTEND_HOST>`

8. Set the health check or readiness path to `/api/status`.

Notes and tips:

- If your backend uses Pillow / ReportLab, Render's Python environment should install wheels automatically. If you see build failures, check Render logs for missing system libraries. Using Render's default runtime usually works.
- Ensure `backend/main.py` allows CORS for your frontend domain. Add `CORSMiddleware` if not present.

Example `render.yaml` (optional) to commit:

```yaml
services:
  - type: web
    name: spotify-crime-backend
    env: python
    buildCommand: pip install -r backend/requirements.txt
    startCommand: uvicorn main:app --host 0.0.0.0 --port $PORT
    root: backend
  - type: web
    name: spotify-crime-frontend
    env: node
    buildCommand: cd frontend && npm ci && npm run build
    startCommand: cd frontend && npx next start -p $PORT
    root: frontend
```

---

## 3. Deploy the frontend (Vercel — recommended)

1. Sign in to Vercel (https://vercel.com) and create a new project from your Git repository.
2. In Project Settings, set **Root Directory** to `frontend` (so Vercel uses that folder for builds).
3. Vercel detects Next.js automatically. Build & Output settings usually don't need changes.
4. Add environment variables (Vercel → Settings → Environment Variables):

   - `NEXT_PUBLIC_BACKEND_URL` = `https://<BACKEND_HOST>`

   Add variables to the appropriate environment (Production/Preview/Development).

5. Deploy — Vercel automatically builds and serves your frontend.

Notes:

- Use Vercel for the Next.js app to get full SSR/edge benefits. If you prefer, you can deploy the Next app to Render as a Node web service (use `npx next start -p $PORT`).
- For preview deployments, set identical env variables under the Preview scope.

---

## 4. OAuth / Spotify redirect setup

1. In the Spotify Dashboard, add the backend callback URL to **Redirect URIs** exactly as used in `SPOTIFY_REDIRECT_URI`. Example:

   - `https://api.myapp.com/callback`

2. The backend will perform the OAuth exchange and then typically redirect the user to the frontend with `?report_key=...` so the frontend can fetch `/api/report/{id}`.

3. If you use a different host for frontend, ensure the backend is configured to redirect to the correct `FRONTEND_URL`.

---

## 5. CORS (FastAPI)

If you get CORS errors, add the following to `backend/main.py` (near app creation):

```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://<FRONTEND_HOST>"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

Replace `<FRONTEND_HOST>` with your Vercel/Render domain.

---

## 6. Local testing & tunneling

- For local OAuth testing, use a tunnel like `ngrok` to expose your local backend to the internet and register that URL in Spotify. Example:

```bash
ngrok http 8000
# set SPOTIFY_REDIRECT_URI to https://<ngrok-id>.ngrok.io/callback
``` 

---

## 7. Post-deploy checks

1. Visit `https://<BACKEND_HOST>/api/status` — it should show `spotify_ready: true` when secrets and redirect URI are correct.
2. Open the frontend and click **Generate Report**, complete Spotify login, and confirm the app redirects back and produces a report with non-empty `track_details` and non-zero analytics.
3. Check logs (Render/Vercel) for any Python exceptions or build failures.

---

## 8. Troubleshooting

- OAuth redirect mismatch: ensure the exact callback URI is registered in the Spotify app settings.
- 500 on image or PDF generation: missing Pillow/ReportLab or insufficient system libs — check backend logs and `requirements.txt`.
- Missing env vars: ensure both Render and Vercel have environment variables set in their dashboards and that values are correct.

---

## 9. Optional automation

- Add `render.yaml` and a Vercel `vercel.json` (if you need custom rewrites) to your repo for reproducible service configuration.

---

If you want, I can:

- Commit and add the `render.yaml` snippet to the repo.
- Add the `CORSMiddleware` snippet into `backend/main.py` behind a safe check.
- Add a small `deploy-check.sh` script that tests `/api/status` and attempts to create a demo report.

Pick one and I'll implement it.
