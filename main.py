from fastapi import FastAPI
from datetime import datetime
from zoneinfo import ZoneInfo
import requests

app = FastAPI()

@app.get("/")
def home():
    return {"message":"Sky Guide API çalışıyor"}

@app.get("/data")
def get_data(lat: float, lon: float):

    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}"
        f"&longitude={lon}"
        "&current=temperature_2m,surface_pressure"
        "&timezone=Europe%2FIstanbul"
    )

    r = requests.get(url)
    weather = r.json()

    return {
        "time": datetime.now(
            ZoneInfo("Europe/Istanbul")
        ).strftime("%H:%M:%S"),

        "temperature": weather["current"]["temperature_2m"],

        "pressure": weather["current"]["surface_pressure"],

        "light_pollution": 35.5
    }
