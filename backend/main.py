from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from dotenv import load_dotenv
import os
import requests
import base64

load_dotenv()

app = FastAPI()

CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")

REDIRECT_URI = "http://127.0.0.1:8000/callback"

# LOGIN ROUTE
from urllib.parse import quote

@app.get("/login")
def login():

    auth_url = (
        f"https://accounts.spotify.com/authorize"
        f"?client_id={CLIENT_ID}"
        f"&response_type=code"
        f"&redirect_uri={REDIRECT_URI}"
        f"&scope=user-top-read"
    )

    return RedirectResponse(auth_url)


# CALLBACK ROUTE
@app.get("/callback")
def callback(code: str):

    token_url = "https://accounts.spotify.com/api/token"

    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
    }

    response = requests.post(
        token_url,
        data=payload,
        auth=(CLIENT_ID, CLIENT_SECRET)
    )

    print("STATUS:", response.status_code)
    print("TEXT:", response.text)

    token_data = response.json()

    access_token = token_data.get("access_token")

    if not access_token:
        return token_data

    headers = {
        "Authorization": f"Bearer {access_token}"
    }

    # TOP TRACKS
    tracks_response = requests.get(
        "https://api.spotify.com/v1/me/top/tracks?limit=5",
        headers=headers
    )

    # TOP ARTISTS
    artists_response = requests.get(
        "https://api.spotify.com/v1/me/top/artists?limit=5",
        headers=headers
    )

    tracks_json = tracks_response.json()
    artists_json = artists_response.json()

    top_tracks = [
        item["name"]
        for item in tracks_json.get("items", [])
    ]

    top_artists = [
        item["name"]
        for item in artists_json.get("items", [])
    ]

    return {
        "top_tracks": top_tracks,
        "top_artists": top_artists
    }