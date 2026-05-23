# Spotify Crime Report

A dark, theatrical Spotify personality analyzer with a Next.js frontend and a FastAPI backend.

## What this project includes

- Spotify login via OAuth
- Playlist and listening-taste analysis
- Gemini-powered roast generator with a fallback analyzer
- FBI-style terminal UI
- PDF report export
- Demo mode for local development without Spotify secrets

## Project structure

```text
spotify-crime-report/
├── frontend/
└── backend/
```

## Local setup

### Backend

```bash
cd backend
python -m venv venv
venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
uvicorn main:app --reload
```

### Frontend

```bash
cd frontend
npm install
Copy-Item .env.local.example .env.local
npm run dev
```

## Environment variables

Backend `.env`:

```env
SPOTIFY_CLIENT_ID=your_spotify_client_id
SPOTIFY_CLIENT_SECRET=your_spotify_client_secret
SPOTIFY_REDIRECT_URI=http://127.0.0.1:8000/callback
FRONTEND_URL=http://localhost:3000
GEMINI_API_KEY=your_gemini_api_key
```

Frontend `.env.local`:

```env
NEXT_PUBLIC_BACKEND_URL=http://127.0.0.1:8000
```

## Demo flow

If you do not want to connect Spotify yet, open:

- `http://127.0.0.1:8000/api/demo`

That will generate a classified sample report and redirect you back to the frontend.

## Production notes

- Deploy the frontend to Vercel.
- Deploy the backend to Render or another FastAPI host.
- Set the frontend backend URL to the deployed backend.
- Update Spotify redirect URIs to match the deployed backend callback URL.

## Safety note

This app is intentionally comedic. It does not store your Spotify credentials and falls back to demo data when secrets are missing.
