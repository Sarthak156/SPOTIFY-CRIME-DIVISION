from __future__ import annotations

import hashlib
import html
import io
import os
from collections import Counter
import textwrap
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, Response
from PIL import Image, ImageColor, ImageDraw, ImageFont
from pydantic import BaseModel, Field
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen.canvas import Canvas

try:
    from google import genai
except Exception:  # pragma: no cover - optional dependency
    genai = None

load_dotenv()

app = FastAPI(title="Spotify Crime Report API", version="1.0.0")

FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000").rstrip("/")
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
SPOTIFY_REDIRECT_URI = os.getenv(
    "SPOTIFY_REDIRECT_URI",
    "http://127.0.0.1:8000/callback",
)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

allowed_origins = {
    FRONTEND_URL,
    "http://localhost:3000",
    "http://127.0.0.1:3000",
}

app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(allowed_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

REPORT_STORE: dict[str, dict[str, Any]] = {}

SUPPORTED_MODES = {
    "fbi": {
        "label": "FBI Psychological Report",
        "summary": "Classic federal dossier with the strongest all-purpose roast tone.",
        "case_type": "psychological dossier",
        "signal": "FEDERAL OVERSIGHT",
    },
    "court-case": {
        "label": "Court Case Mode",
        "summary": "Turns the playlist into evidence, testimony, and a closing argument.",
        "case_type": "court exhibit packet",
        "signal": "PLAINTIFF ENERGY",
    },
    "cia-threat": {
        "label": "CIA Threat Assessment",
        "summary": "Frames the subject like a national-security briefing with elite paranoia.",
        "case_type": "threat brief",
        "signal": "NATIONAL INTEREST",
    },
    "breakup-survivor": {
        "label": "Breakup Survivor Index",
        "summary": "Focuses on heartbreak residue, rebound choices, and emotional reconstruction.",
        "case_type": "recovery dossier",
        "signal": "EMOTIONAL AFTERMATH",
    },
    "npc-detection": {
        "label": "NPC Detection System",
        "summary": "Measures whether the playlist feels hand-picked by a background character.",
        "case_type": "behavioral scan",
        "signal": "AUTOMATION SUSPECTED",
    },
}
DEFAULT_MODE = "fbi"


class AnalyzeRequest(BaseModel):
    display_name: str = Field(default="Anonymous Listener", min_length=1)
    top_tracks: list[str] = Field(default_factory=list)
    top_artists: list[str] = Field(default_factory=list)
    top_genres: list[str] = Field(default_factory=list)
    source: str = Field(default="manual")
    mode: str = Field(default=DEFAULT_MODE)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/status")
def api_status() -> dict[str, Any]:
    spotify_ready = bool(SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET)
    gemini_ready = bool(GEMINI_API_KEY)
    available_modes = [
        {
            "key": key,
            "label": value["label"],
            "summary": value["summary"],
            "signal": value["signal"],
        }
        for key, value in SUPPORTED_MODES.items()
    ]
    return {
        "spotify_ready": spotify_ready,
        "spotify_client_id_set": bool(SPOTIFY_CLIENT_ID),
        "spotify_client_secret_set": bool(SPOTIFY_CLIENT_SECRET),
        "spotify_redirect_uri": SPOTIFY_REDIRECT_URI,
        "frontend_url": FRONTEND_URL,
        "gemini_ready": gemini_ready,
        "mode": "spotify" if spotify_ready else "demo-only",
        "login_hint": "Spotify credentials are configured." if spotify_ready else "Missing Spotify CLIENT_ID or CLIENT_SECRET; demo fallback is active.",
        "available_modes": available_modes,
        "default_mode": DEFAULT_MODE,
    }


@app.get("/login")
def login(mode: str = DEFAULT_MODE) -> RedirectResponse:
    mode_key = _normalize_mode(mode)
    if not SPOTIFY_CLIENT_ID:
        return _demo_redirect("missing_spotify_client_id", mode_key)

    params = {
        "client_id": SPOTIFY_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": SPOTIFY_REDIRECT_URI,
        "scope": "user-read-email user-read-private user-top-read playlist-read-private playlist-read-collaborative",
        "show_dialog": "true",
        "state": mode_key,
    }
    auth_url = f"https://accounts.spotify.com/authorize?{urlencode(params)}"
    return RedirectResponse(url=auth_url)


@app.get("/callback")
def callback(code: str | None = None, error: str | None = None, state: str | None = None):
    if error:
        return JSONResponse(status_code=400, content={"detail": error})
    if not code:
        return JSONResponse(status_code=400, content={"detail": "Missing authorization code."})
    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        return _demo_redirect("missing_spotify_config", _normalize_mode(state))

    mode_key = _normalize_mode(state)

    token_response = requests.post(
        "https://accounts.spotify.com/api/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": SPOTIFY_REDIRECT_URI,
        },
        auth=(SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET),
        timeout=20,
    )
    token_response.raise_for_status()
    token_data = token_response.json()
    access_token = token_data.get("access_token")
    if not access_token:
        raise HTTPException(status_code=502, detail="Spotify did not return an access token.")

    profile = _spotify_get("https://api.spotify.com/v1/me", access_token)
    top_tracks = _spotify_get("https://api.spotify.com/v1/me/top/tracks?limit=10&time_range=short_term", access_token)
    top_artists = _spotify_get("https://api.spotify.com/v1/me/top/artists?limit=10&time_range=short_term", access_token)
    top_tracks_medium = _spotify_get("https://api.spotify.com/v1/me/top/tracks?limit=10&time_range=medium_term", access_token)
    top_tracks_long = _spotify_get("https://api.spotify.com/v1/me/top/tracks?limit=10&time_range=long_term", access_token)
    top_artists_medium = _spotify_get("https://api.spotify.com/v1/me/top/artists?limit=10&time_range=medium_term", access_token)
    top_artists_long = _spotify_get("https://api.spotify.com/v1/me/top/artists?limit=10&time_range=long_term", access_token)
    playlists = _spotify_get("https://api.spotify.com/v1/me/playlists?limit=20", access_token)
    track_items = top_tracks.get("items", [])
    artist_items = top_artists.get("items", [])
    track_ids = [track.get("id") for track in track_items if track.get("id")]
    audio_features_data = _spotify_audio_features(track_ids, access_token)
    audio_feature_items = audio_features_data.get("audio_features", []) if isinstance(audio_features_data, dict) else []

    track_names = [item.get("name", "Unknown Track") for item in track_items]
    artist_names = [item.get("name", "Unknown Artist") for item in artist_items]
    genres = _collect_genres(artist_items)

    report = build_report(
        display_name=profile.get("display_name") or "Anonymous Listener",
        top_tracks=track_names,
        top_artists=artist_names,
        top_genres=genres,
        source="spotify",
        mode=mode_key,
        profile={
            **profile,
            "track_items": track_items,
            "artist_items": artist_items,
            "audio_features": audio_feature_items,
            "top_tracks_medium": top_tracks_medium.get("items", []),
            "top_tracks_long": top_tracks_long.get("items", []),
            "top_artists_medium": top_artists_medium.get("items", []),
            "top_artists_long": top_artists_long.get("items", []),
            "playlists": playlists.get("items", []),
        },
    )
    report_id = _store_report(report)
    return RedirectResponse(url=f"{FRONTEND_URL}/?report_key={report_id}&mode={mode_key}")


@app.get("/api/demo")
def demo(mode: str = DEFAULT_MODE) -> RedirectResponse:
    return _demo_redirect("demo_mode", _normalize_mode(mode))


@app.get("/api/report/{report_id}")
def get_report(report_id: str) -> dict[str, Any]:
    report = REPORT_STORE.get(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found.")
    return report


@app.get("/api/report/{report_id}/pdf")
def download_report_pdf(report_id: str) -> Response:
    report = REPORT_STORE.get(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found.")

    pdf_bytes = _generate_pdf(report)
    filename = f"spotify-crime-report-{report_id}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/report/{report_id}/share-card.png")
def download_share_card(report_id: str) -> Response:
    report = REPORT_STORE.get(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found.")

    png_bytes = _generate_share_image(report, variant="card")
    filename = f"spotify-crime-report-{report_id}-card.png"
    return Response(
        content=png_bytes,
        media_type="image/png",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/report/{report_id}/story.png")
def download_story_image(report_id: str) -> Response:
    report = REPORT_STORE.get(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found.")

    png_bytes = _generate_share_image(report, variant="story")
    filename = f"spotify-crime-report-{report_id}-story.png"
    return Response(
        content=png_bytes,
        media_type="image/png",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/analyze")
def analyze(payload: AnalyzeRequest) -> dict[str, Any]:
    report = build_report(
        display_name=payload.display_name,
        top_tracks=payload.top_tracks,
        top_artists=payload.top_artists,
        top_genres=payload.top_genres,
        source=payload.source,
        mode=_normalize_mode(payload.mode),
        profile={"display_name": payload.display_name},
    )
    report_id = _store_report(report)
    return {"report_id": report_id, "report": report}


def _demo_redirect(reason: str, mode: str = DEFAULT_MODE) -> RedirectResponse:
    demo_report = build_report(
        display_name="Demo Listener",
        top_tracks=["After Hours", "505", "N95", "Do I Wanna Know?", "Starboy"],
        top_artists=["The Weeknd", "Arctic Monkeys", "Kendrick Lamar", "Drake"],
        top_genres=["alt z", "indie rock", "hip hop", "r&b"],
        source=reason,
        mode=mode,
        profile={"display_name": "Demo Listener"},
    )
    report_id = _store_report(demo_report)
    return RedirectResponse(url=f"{FRONTEND_URL}/?report_key={report_id}&mode={mode}")


def _spotify_get(url: str, access_token: str) -> dict[str, Any]:
    response = requests.get(
        url,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


def _collect_genres(artists: list[dict[str, Any]]) -> list[str]:
    genres: list[str] = []
    for artist in artists:
        for genre in artist.get("genres", []):
            if genre not in genres:
                genres.append(genre)
    return genres[:8]


def _spotify_audio_features(track_ids: list[str], access_token: str) -> dict[str, Any]:
    if not track_ids:
        return {"audio_features": []}
    ids_param = ",".join(track_ids[:100])
    try:
        response = _spotify_get(f"https://api.spotify.com/v1/audio-features?ids={ids_param}", access_token)
    except requests.HTTPError:
        response = {"audio_features": []}

    features = response.get("audio_features", []) if isinstance(response, dict) else []
    feature_ids = {
        item.get("id")
        for item in features
        if isinstance(item, dict) and item.get("id")
    }

    for track_id in track_ids:
        if track_id in feature_ids:
            continue
        try:
            detail = _spotify_get(f"https://api.spotify.com/v1/audio-features/{track_id}", access_token)
        except requests.HTTPError:
            continue
        if isinstance(detail, dict) and detail.get("id"):
            features.append(detail)
            feature_ids.add(detail["id"])

    return {"audio_features": features}


def _normalize_mode(mode: str | None) -> str:
    if not mode:
        return DEFAULT_MODE
    normalized = mode.strip().lower().replace("_", "-")
    return normalized if normalized in SUPPORTED_MODES else DEFAULT_MODE


def _mode_profile(mode: str) -> dict[str, str]:
    return SUPPORTED_MODES.get(mode, SUPPORTED_MODES[DEFAULT_MODE])


def _average(values: list[float]) -> float:
    if not values:
        return 0.0
    return round(sum(values) / len(values), 2)


def _percentage(value: float) -> float:
    return round(value * 100, 1)


def _track_artist_name(track: dict[str, Any]) -> str:
    artists = track.get("artists") or []
    if not artists:
        return "Unknown Artist"
    first_artist = artists[0]
    return first_artist.get("name", "Unknown Artist")


def _track_features_map(audio_features: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    mapped: dict[str, dict[str, Any]] = {}
    for feature in audio_features:
        feature_id = feature.get("id")
        if feature_id:
            mapped[feature_id] = feature
    return mapped


def _extract_track_details(track_items: list[dict[str, Any]], audio_feature_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    feature_map = _track_features_map(audio_feature_items)
    details: list[dict[str, Any]] = []
    for track in track_items:
        feature = feature_map.get(track.get("id", ""), {})
        details.append(
            {
                "id": track.get("id"),
                "name": track.get("name", "Unknown Track"),
                "artist": _track_artist_name(track),
                "album": track.get("album", {}).get("name", "Unknown Album"),
                "popularity": track.get("popularity", 0),
                "explicit": bool(track.get("explicit")),
                "duration_ms": track.get("duration_ms", 0),
                "danceability": feature.get("danceability"),
                "energy": feature.get("energy"),
                "valence": feature.get("valence"),
                "tempo": feature.get("tempo"),
                "acousticness": feature.get("acousticness"),
                "instrumentalness": feature.get("instrumentalness"),
                "speechiness": feature.get("speechiness"),
                "liveness": feature.get("liveness"),
                "track_number": track.get("track_number", 0),
            }
        )
    return details


def _extract_artist_details(artist_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    details: list[dict[str, Any]] = []
    for artist in artist_items:
        details.append(
            {
                "id": artist.get("id"),
                "name": artist.get("name", "Unknown Artist"),
                "popularity": artist.get("popularity", 0),
                "followers": artist.get("followers", {}).get("total", 0) if isinstance(artist.get("followers"), dict) else artist.get("followers", 0),
                "genres": artist.get("genres", []),
                "image_url": artist.get("images", [{}])[0].get("url") if artist.get("images") else None,
            }
        )
    return details


def _build_chart_series(track_details: list[dict[str, Any]], artist_details: list[dict[str, Any]], top_genres: list[str]) -> dict[str, list[dict[str, Any]]]:
    popularity_chart = [
        {"label": track["name"], "value": float(track.get("popularity") or 0)}
        for track in track_details[:8]
    ]
    energy_chart = [
        {"label": track["name"], "value": _percentage(float(track.get("energy") or 0))}
        for track in track_details[:8]
    ]
    artist_chart = [
        {"label": artist["name"], "value": float(artist.get("popularity") or 0)}
        for artist in artist_details[:8]
    ]
    genre_counts = Counter(top_genres)
    genre_chart = [
        {"label": genre, "value": float(count)}
        for genre, count in genre_counts.most_common(8)
    ]
    return {
        "track_popularity": popularity_chart,
        "track_energy": energy_chart,
        "artist_popularity": artist_chart,
        "genre_breakdown": genre_chart,
    }


def _mode_insights(mode: str, damage: int, villain: int, stability: int, archetype: str) -> tuple[list[str], list[str], str]:
    config = _mode_profile(mode)
    insights = [
        f"Mode profile: {config['label']}.",
        f"Signal classification: {config['signal']}.",
        f"Listener archetype: {archetype}.",
        f"Case type: {config['case_type']}.",
    ]
    recommendations = [
        "Audit the most repeated emotional themes in the playlist.",
        "Cross-check artist rotation against mood volatility.",
        "Reduce doom-loop tracks if you want a calmer recovery arc.",
    ]
    if mode == "breakup-survivor":
        recommendations = [
            "Add one stabilizing playlist to balance the recovery arc.",
            "Avoid sending messages at 2 a.m. after hearing the same chorus three times.",
            "Replace high-villain tracks with something that has a survivable ending.",
        ]
    elif mode == "court-case":
        recommendations = [
            "Prepare exhibits for the most repeated tracks and artists.",
            "Review the timeline of musical decisions before entering the hearing.",
            "Use a calmer closing statement if the playlist is already on edge.",
        ]
    elif mode == "npc-detection":
        recommendations = [
            "Inject one unpredictable album to break the automation pattern.",
            "Increase genre variance before the system fully flags the subject as scripted.",
            "Retest after a manual playlist refresh.",
        ]
    elif mode == "cia-threat":
        recommendations = [
            "Isolate the highest-risk listening clusters for surveillance.",
            "Monitor the overlap between low-stability tracks and late-night sessions.",
            "Treat the playlist as a sensitive asset until the temperature drops.",
        ]
    verdict = "stable" if stability > villain else "volatile"
    if damage > 80:
        verdict = "elevated"
    return insights, recommendations, verdict


def _top_name_list(items: list[dict[str, Any]], field: str = "name", limit: int = 5) -> list[str]:
    names: list[str] = []
    for item in items[:limit]:
        names.append(item.get(field, "Unknown"))
    return names


def _timeline_summary(profile: dict[str, Any], current_tracks: list[str], current_artists: list[str]) -> dict[str, Any]:
    medium_tracks = _top_name_list(profile.get("top_tracks_medium", []))
    long_tracks = _top_name_list(profile.get("top_tracks_long", []))
    medium_artists = _top_name_list(profile.get("top_artists_medium", []))
    long_artists = _top_name_list(profile.get("top_artists_long", []))
    playlists = profile.get("playlists", [])
    playlist_total_tracks = sum(
        (playlist.get("tracks", {}) or {}).get("total", 0)
        for playlist in playlists
        if isinstance(playlist, dict)
    )
    current_set = set(current_tracks + current_artists)
    medium_set = set(medium_tracks + medium_artists)
    long_set = set(long_tracks + long_artists)
    overlap_medium = len(current_set.intersection(medium_set))
    overlap_long = len(current_set.intersection(long_set))

    return {
        "short_term_tracks": current_tracks[:5],
        "medium_term_tracks": medium_tracks,
        "long_term_tracks": long_tracks,
        "short_term_artists": current_artists[:5],
        "medium_term_artists": medium_artists,
        "long_term_artists": long_artists,
        "overlap_short_medium": overlap_medium,
        "overlap_short_long": overlap_long,
        "playlist_count": len(playlists),
        "playlist_track_coverage": playlist_total_tracks,
    }


def build_report(
    *,
    display_name: str,
    top_tracks: list[str],
    top_artists: list[str],
    top_genres: list[str],
    source: str,
    mode: str,
    profile: dict[str, Any],
) -> dict[str, Any]:
    mode_key = _normalize_mode(mode)
    mode_config = _mode_profile(mode_key)
    analysis_seed = "|".join([display_name, *top_tracks, *top_artists, *top_genres, source, mode_key])
    emotional_damage_index = _score(analysis_seed + ":ed", 41, 98)
    villain_arc_score = _score(analysis_seed + ":va", 33, 99)
    relationship_stability = _score(analysis_seed + ":rs", 8, 84)
    track_details = _extract_track_details(profile.get("track_items", []), profile.get("audio_features", []))
    artist_details = _extract_artist_details(profile.get("artist_items", []))
    chart_data = _build_chart_series(track_details, artist_details, top_genres)
    avg_energy = _average([float(track.get("energy") or 0) for track in track_details])
    avg_valence = _average([float(track.get("valence") or 0) for track in track_details])
    avg_danceability = _average([float(track.get("danceability") or 0) for track in track_details])
    avg_acousticness = _average([float(track.get("acousticness") or 0) for track in track_details])
    avg_instrumentalness = _average([float(track.get("instrumentalness") or 0) for track in track_details])
    avg_tempo = _average([float(track.get("tempo") or 0) for track in track_details])
    explicit_ratio = _percentage(sum(1 for track in track_details if track.get("explicit")) / len(track_details)) if track_details else 0.0
    avg_popularity = _average([float(track.get("popularity") or 0) for track in track_details])
    genre_counts = Counter(top_genres)
    dominant_genre = genre_counts.most_common(1)[0][0] if genre_counts else "unknown"
    genre_spread = len(genre_counts)
    artist_count = len(artist_details)
    track_count = len(track_details)
    mood_index = _score(f"{analysis_seed}:mood", 15, 97)
    archetype = _determine_archetype(mode_key, avg_energy, avg_valence, avg_acousticness, mood_index, dominant_genre)
    insights, recommendations, verdict = _mode_insights(mode_key, emotional_damage_index, villain_arc_score, relationship_stability, archetype)
    timeline = _timeline_summary(profile, top_tracks, top_artists)

    threat_level = _classify_threat_level(emotional_damage_index, villain_arc_score, relationship_stability)
    roast_summary = _generate_roast(display_name, top_tracks, top_artists, top_genres, mode_key)

    classified_notes = [
        f"Subject profile indexed as {display_name}.",
        f"Top artist cluster suggests a {threat_level.lower()} emotional event.",
        f"Playlist motif detected in {', '.join(top_genres[:3]) or 'unknown genres' }.",
        "Report generated for entertainment purposes only.",
    ]

    dashboard_cards = [
        {"label": "Track Count", "value": track_count, "detail": "Top tracks fetched from Spotify."},
        {"label": "Artist Count", "value": artist_count, "detail": "Top artists and genre graph sources."},
        {"label": "Genre Spread", "value": genre_spread, "detail": "Number of unique genres detected."},
        {"label": "Explicit Ratio", "value": f"{explicit_ratio}%", "detail": "Tracks marked explicit."},
        {"label": "Avg Energy", "value": avg_energy, "detail": "Normalized audio energy score."},
        {"label": "Avg Valence", "value": avg_valence, "detail": "Mood positivity / emotional brightness."},
    ]

    return {
        "report_id": uuid.uuid4().hex,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "mode": mode_key,
        "mode_label": mode_config["label"],
        "mode_summary": mode_config["summary"],
        "case_signal": mode_config["signal"],
        "archetype": archetype,
        "verdict": verdict,
        "profile": {
            "display_name": profile.get("display_name") or display_name,
            "country": profile.get("country"),
            "email": profile.get("email"),
            "followers": profile.get("followers", {}).get("total", 0) if isinstance(profile.get("followers"), dict) else profile.get("followers", 0),
        },
        "top_tracks": top_tracks[:10],
        "top_artists": top_artists[:10],
        "top_genres": top_genres[:10],
        "track_details": track_details[:10],
        "artist_details": artist_details[:10],
        "chart_data": chart_data,
        "dashboard_cards": dashboard_cards,
        "insights": insights,
        "recommendations": recommendations,
        "timeline": timeline,
        "analytics": {
            "avg_popularity": avg_popularity,
            "avg_energy": avg_energy,
            "avg_valence": avg_valence,
            "avg_danceability": avg_danceability,
            "avg_acousticness": avg_acousticness,
            "avg_instrumentalness": avg_instrumentalness,
            "avg_tempo": avg_tempo,
            "explicit_ratio": explicit_ratio,
            "genre_spread": genre_spread,
            "track_count": track_count,
            "artist_count": artist_count,
            "mood_index": mood_index,
            "dominant_genre": dominant_genre,
            "playlist_count": timeline["playlist_count"],
            "playlist_track_coverage": timeline["playlist_track_coverage"],
            "overlap_short_medium": timeline["overlap_short_medium"],
            "overlap_short_long": timeline["overlap_short_long"],
        },
        "threat_level": threat_level,
        "emotional_damage_index": emotional_damage_index,
        "villain_arc_score": villain_arc_score,
        "relationship_stability": relationship_stability,
        "roast_summary": roast_summary,
        "classified_notes": classified_notes,
    }


def _store_report(report: dict[str, Any]) -> str:
    report_id = report.get("report_id") or uuid.uuid4().hex
    report["report_id"] = report_id
    REPORT_STORE[report_id] = report
    return report_id


def _score(seed: str, minimum: int, maximum: int) -> int:
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    value = int(digest[:8], 16)
    return minimum + value % (maximum - minimum + 1)


def _classify_threat_level(emotional_damage_index: int, villain_arc_score: int, relationship_stability: int) -> str:
    danger = emotional_damage_index + villain_arc_score - relationship_stability
    if danger >= 150:
        return "APOCALYPTIC"
    if danger >= 110:
        return "SEVERE"
    return "LOW"


def _determine_archetype(mode: str, avg_energy: float, avg_valence: float, avg_acousticness: float, mood_index: int, dominant_genre: str) -> str:
    if mode == "breakup-survivor":
        if avg_valence < 0.45:
            return "Heartbreak Forensics Specialist"
        return "Recovery Arc Architect"
    if mode == "court-case":
        return "Plaintiff of the Playlist"
    if mode == "cia-threat":
        return "Low-Visibility Operative"
    if mode == "npc-detection":
        return "Patterned Background Character"
    if mood_index > 70 and avg_energy > 0.65:
        return f"Volatile Main-Character from {dominant_genre.title()}"
    if avg_acousticness > 0.5:
        return "Late-Night Acoustic Witness"
    if avg_valence < 0.35:
        return "Clinically Suspicious Sadness Collector"
    return "Generalized Chaos Enthusiast"


def _generate_roast(
    display_name: str,
    top_tracks: list[str],
    top_artists: list[str],
    top_genres: list[str],
    mode: str,
) -> str:
    mode_config = _mode_profile(mode)
    prompt = f"""
You are writing a funny, dramatic Spotify intelligence report for a listener.
Keep it playful, avoid hate, slurs, or protected-trait attacks.
Use a dark government-terminal tone.
Mode: {mode_config['label']}
Case type: {mode_config['case_type']}
Tone signal: {mode_config['signal']}

Subject: {display_name}
Top tracks: {', '.join(top_tracks[:5]) or 'Unknown'}
Top artists: {', '.join(top_artists[:5]) or 'Unknown'}
Top genres: {', '.join(top_genres[:5]) or 'Unknown'}

Return 4 to 6 short sentences with a roast, a mock diagnosis, and a classified warning.
""".strip()

    if GEMINI_API_KEY and genai is not None:
        try:
            client = genai.Client(api_key=GEMINI_API_KEY)
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
            )
            text = getattr(response, "text", None)
            if text:
                return text.strip()
        except Exception:
            pass

    subject = display_name or "the subject"
    tracks = ", ".join(top_tracks[:3]) or "an empty queue"
    artists = ", ".join(top_artists[:3]) or "no obvious alibi"
    genres = ", ".join(top_genres[:3]) or "classified silence"
    return (
        f"{subject} has compiled a listening history that reads like a controlled substance for unresolved feelings. "
        f"The playlist evidence points to {tracks}, which is less a music taste and more a witness statement. "
        f"Primary accomplices include {artists}, while the genre file lists {genres}. "
        "Conclusion: subject exhibits high confidence, low self-preservation, and an alarming willingness to romanticize chaos."
    )


def _generate_pdf(report: dict[str, Any]) -> bytes:
    buffer = io.BytesIO()
    canvas = Canvas(buffer, pagesize=letter)
    width, height = letter

    canvas.setTitle(f"Spotify Crime Report - {report['report_id']}")
    canvas.setFillColor(colors.HexColor("#07130a"))
    canvas.rect(0, 0, width, height, fill=1, stroke=0)

    canvas.setFillColor(colors.HexColor("#73ff8a"))
    canvas.setFont("Helvetica-Bold", 22)
    canvas.drawString(48, height - 52, "SPOTIFY CRIME DIVISION")
    canvas.setFont("Helvetica", 10)
    canvas.drawString(48, height - 70, f"{html.escape(report.get('mode_label', 'CLASSIFIED REPORT')).upper()} / CASE {report['report_id'][:10].upper()}")

    y = height - 110
    y = _write_section(canvas, "SUBJECT", [f"Display name: {report['profile'].get('display_name', 'Unknown')}", f"Source: {report['source']}", f"Generated: {report['generated_at']}"], y)
    y = _write_section(canvas, "MODE PROFILE", [f"Mode: {report.get('mode_label', 'Unknown')}", f"Case signal: {report.get('case_signal', 'Unknown')}", f"Archetype: {report.get('archetype', 'Unknown')}", f"Verdict: {report.get('verdict', 'Unknown')}"], y)
    y = _write_section(canvas, "THREAT ASSESSMENT", [f"Threat level: {report['threat_level']}", f"Emotional damage index: {report['emotional_damage_index']}", f"Villain arc score: {report['villain_arc_score']}", f"Relationship stability: {report['relationship_stability']}"], y)
    y = _write_section(canvas, "ANALYTICS", [
        f"Track count: {report.get('analytics', {}).get('track_count', 0)}",
        f"Artist count: {report.get('analytics', {}).get('artist_count', 0)}",
        f"Genre spread: {report.get('analytics', {}).get('genre_spread', 0)}",
        f"Avg energy: {report.get('analytics', {}).get('avg_energy', 0)}",
        f"Avg valence: {report.get('analytics', {}).get('avg_valence', 0)}",
        f"Avg danceability: {report.get('analytics', {}).get('avg_danceability', 0)}",
        f"Avg tempo: {report.get('analytics', {}).get('avg_tempo', 0)}",
        f"Explicit ratio: {report.get('analytics', {}).get('explicit_ratio', 0)}%",
    ], y)
    y = _write_section(canvas, "TOP TRACKS", [f"- {track}" for track in report.get("top_tracks", [])[:10]] or ["- No tracks detected"], y)
    y = _write_section(canvas, "TOP ARTISTS", [f"- {artist}" for artist in report.get("top_artists", [])[:10]] or ["- No artists detected"], y)
    y = _write_section(canvas, "TOP GENRES", [f"- {genre}" for genre in report.get("top_genres", [])[:10]] or ["- No genres detected"], y)
    y = _write_section(canvas, "TIMELINE", [
        f"Short-Medium overlap: {report.get('timeline', {}).get('overlap_short_medium', 0)}",
        f"Short-Long overlap: {report.get('timeline', {}).get('overlap_short_long', 0)}",
        f"Playlist count: {report.get('timeline', {}).get('playlist_count', 0)}",
        f"Playlist track coverage: {report.get('timeline', {}).get('playlist_track_coverage', 0)}",
    ], y)
    y = _write_section(canvas, "INSIGHTS", [f"- {insight}" for insight in report.get("insights", [])] or ["- No insights detected"], y)
    y = _write_section(canvas, "RECOMMENDATIONS", [f"- {item}" for item in report.get("recommendations", [])] or ["- No recommendations available"], y)
    y = _write_section(canvas, "ROAST SUMMARY", [report.get("roast_summary", "No roast summary available.")], y, wrap=True)
    y = _write_section(canvas, "CLASSIFIED NOTES", [f"- {note}" for note in report.get("classified_notes", [])], y)

    canvas.setFillColor(colors.HexColor("#ff5f5f"))
    canvas.setFont("Helvetica-Bold", 14)
    canvas.drawString(48, 42, "CLASSIFIED: FOR ENTERTAINMENT USE ONLY")

    canvas.showPage()
    canvas.save()
    return buffer.getvalue()


def _write_section(canvas: Canvas, title: str, lines: list[str], y: float, wrap: bool = False) -> float:
    if y < 100:
        canvas.showPage()
        canvas.setFillColor(colors.HexColor("#07130a"))
        canvas.rect(0, 0, 612, 792, fill=1, stroke=0)
        canvas.setFillColor(colors.HexColor("#73ff8a"))
        y = 740

    canvas.setFont("Helvetica-Bold", 12)
    canvas.drawString(48, y, title)
    y -= 18
    canvas.setFont("Helvetica", 10)
    for line in lines:
        if wrap:
            for wrapped_line in textwrap.wrap(line, width=88):
                canvas.drawString(58, y, wrapped_line)
                y -= 14
        else:
            canvas.drawString(58, y, line)
            y -= 14
    return y - 10


def _generate_share_image(report: dict[str, Any], variant: str) -> bytes:
    width, height = (1200, 1600) if variant == "card" else (1080, 1920)
    image = Image.new("RGB", (width, height), ImageColor.getrgb("#041009"))
    draw = ImageDraw.Draw(image)

    _draw_gradient(draw, width, height, "#041009", "#071d10")
    draw.rounded_rectangle((30, 30, width - 30, height - 30), radius=36, outline=ImageColor.getrgb("#73ff8a"), width=2)

    title_font = _load_font(52 if variant == "card" else 60, bold=True)
    subtitle_font = _load_font(24 if variant == "card" else 28)
    body_font = _load_font(22 if variant == "card" else 24)
    small_font = _load_font(18 if variant == "card" else 20)
    tiny_font = _load_font(14 if variant == "card" else 16)

    draw.text((60, 60), "SPOTIFY CRIME DIVISION", fill=ImageColor.getrgb("#73ff8a"), font=subtitle_font)
    draw.text((60, 110), report.get("mode_label", "CLASSIFIED REPORT"), fill=ImageColor.getrgb("#effff1"), font=title_font)
    draw.text((60, 190), report.get("case_signal", "CLASSIFIED SIGNAL"), fill=ImageColor.getrgb("#9fffb0"), font=subtitle_font)

    _draw_stamp(draw, image.size, report.get("verdict", "CLASSIFIED"))

    meta_top = 280 if variant == "card" else 310
    meta_cards = [
        ("Threat", report.get("threat_level", "Unknown")),
        ("Archetype", report.get("archetype", "Unknown")),
        ("Tracks", str(report.get("analytics", {}).get("track_count", len(report.get("top_tracks", []))))),
        ("Artists", str(report.get("analytics", {}).get("artist_count", len(report.get("top_artists", []))))),
    ]
    card_width = (width - 140) // 2
    card_height = 112
    for index, (label, value) in enumerate(meta_cards):
        x = 60 + (index % 2) * (card_width + 20)
        y = meta_top + (index // 2) * (card_height + 18)
        draw.rounded_rectangle((x, y, x + card_width, y + card_height), radius=22, fill=ImageColor.getrgb("#071c0f"), outline=ImageColor.getrgb("#1f5a2a"))
        draw.text((x + 18, y + 16), label.upper(), fill=ImageColor.getrgb("#8fe79c"), font=tiny_font)
        draw.text((x + 18, y + 48), str(value), fill=ImageColor.getrgb("#effff1"), font=body_font)

    chart_y = meta_top + 260
    _draw_progress_section(draw, report.get("top_tracks", [])[:5], report.get("chart_data", {}).get("track_popularity", []), 60, chart_y, width - 120, title_font=small_font, body_font=tiny_font)
    chart_y += 300 if variant == "card" else 330
    _draw_progress_section(draw, report.get("top_artists", [])[:5], report.get("chart_data", {}).get("artist_popularity", []), 60, chart_y, width - 120, title_font=small_font, body_font=tiny_font)

    analytics = report.get("analytics", {})
    summary_text = [
        f"Mode: {report.get('mode_label', 'Unknown')}",
        f"Mood index: {analytics.get('mood_index', 'n/a')}",
        f"Avg energy: {analytics.get('avg_energy', 'n/a')}",
        f"Explicit ratio: {analytics.get('explicit_ratio', 'n/a')}%",
        f"Playlist count: {analytics.get('playlist_count', 0)}",
        f"Overlaps: short/medium {analytics.get('overlap_short_medium', 0)} | short/long {analytics.get('overlap_short_long', 0)}",
    ]
    summary_y = height - 340 if variant == "card" else height - 500
    draw.rounded_rectangle((60, summary_y, width - 60, summary_y + 220), radius=24, fill=ImageColor.getrgb("#071b0f"), outline=ImageColor.getrgb("#244f2f"))
    draw.text((84, summary_y + 20), "SUMMARY", fill=ImageColor.getrgb("#73ff8a"), font=small_font)
    current_y = summary_y + 60
    for line in summary_text:
        for wrapped in _wrap_text(draw, line, tiny_font, width - 180):
            draw.text((84, current_y), wrapped, fill=ImageColor.getrgb("#effff1"), font=tiny_font)
            current_y += 24

    notes = report.get("insights", [])[:3] + report.get("recommendations", [])[:2]
    footer_y = height - 90
    draw.text((60, footer_y), "CLASSIFIED / FOR ENTERTAINMENT USE ONLY", fill=ImageColor.getrgb("#ff6666"), font=tiny_font)
    draw.text((60, footer_y + 22), "Generated by Spotify Crime Report", fill=ImageColor.getrgb("#9fffb0"), font=tiny_font)

    note_y = 60 if variant == "story" else height - 260
    note_x = width - 470 if variant == "story" else 60
    note_w = 410 if variant == "story" else width - 120
    draw.rounded_rectangle((note_x, note_y, note_x + note_w, note_y + 170), radius=20, fill=ImageColor.getrgb("#0b1a10"), outline=ImageColor.getrgb("#325f3c"))
    draw.text((note_x + 18, note_y + 16), "KEY NOTES", fill=ImageColor.getrgb("#73ff8a"), font=tiny_font)
    running_y = note_y + 44
    for note in notes:
        for wrapped in _wrap_text(draw, f"• {note}", tiny_font, note_w - 36):
            draw.text((note_x + 18, running_y), wrapped, fill=ImageColor.getrgb("#effff1"), font=tiny_font)
            running_y += 20

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _draw_gradient(draw: ImageDraw.ImageDraw, width: int, height: int, top_color: str, bottom_color: str) -> None:
    top_rgb = ImageColor.getrgb(top_color)
    bottom_rgb = ImageColor.getrgb(bottom_color)
    for y in range(height):
        ratio = y / max(height - 1, 1)
        color = tuple(int(top_rgb[i] * (1 - ratio) + bottom_rgb[i] * ratio) for i in range(3))
        draw.line((0, y, width, y), fill=color)


def _draw_stamp(draw: ImageDraw.ImageDraw, size: tuple[int, int], text: str) -> None:
    width, height = size
    stamp = Image.new("RGBA", (420, 180), (0, 0, 0, 0))
    stamp_draw = ImageDraw.Draw(stamp)
    stamp_draw.rounded_rectangle((8, 8, 412, 172), radius=24, outline=(255, 90, 90, 255), width=6)
    stamp_font = _load_font(44, bold=True)
    stamp_draw.text((44, 58), text.upper(), fill=(255, 90, 90, 255), font=stamp_font)
    rotated = stamp.rotate(-18, expand=True)
    draw.bitmap((width - rotated.size[0] - 50, 90), rotated, fill=None)


def _draw_progress_section(
    draw: ImageDraw.ImageDraw,
    labels: list[str],
    series: list[dict[str, Any]],
    x: int,
    y: int,
    width: int,
    *,
    title_font: ImageFont.ImageFont,
    body_font: ImageFont.ImageFont,
) -> None:
    section_height = 250
    draw.rounded_rectangle((x, y, x + width, y + section_height), radius=24, fill=ImageColor.getrgb("#08160d"), outline=ImageColor.getrgb("#204d2d"))
    draw.text((x + 18, y + 16), "TOP SIGNALS", fill=ImageColor.getrgb("#73ff8a"), font=title_font)
    bar_y = y + 62
    bar_count = max(1, min(len(labels), len(series)))
    max_value = max([float(item.get("value") or 0) for item in series[:bar_count]] + [1])
    for index in range(bar_count):
        label = labels[index]
        value = float(series[index].get("value") or 0)
        text = _shorten(label, 38)
        draw.text((x + 18, bar_y), text, fill=ImageColor.getrgb("#effff1"), font=body_font)
        draw.text((x + width - 96, bar_y), f"{value:.1f}", fill=ImageColor.getrgb("#9fffb0"), font=body_font)
        bar_top = bar_y + 28
        draw.rounded_rectangle((x + 18, bar_top, x + width - 18, bar_top + 18), radius=8, fill=ImageColor.getrgb("#0d2213"))
        fill_width = int((value / max_value) * (width - 36))
        draw.rounded_rectangle((x + 18, bar_top, x + 18 + fill_width, bar_top + 18), radius=8, fill=ImageColor.getrgb("#73ff8a"))
        bar_y += 42


def _load_font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidates = [
        "arialbd.ttf" if bold else "arial.ttf",
        "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
        "LiberationSans-Bold.ttf" if bold else "LiberationSans-Regular.ttf",
    ]
    for font_name in candidates:
        try:
            return ImageFont.truetype(font_name, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if draw.textbbox((0, 0), candidate, font=font)[2] <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def _shorten(text: str, length: int) -> str:
    return text if len(text) <= length else f"{text[: length - 1].rstrip()}…"
