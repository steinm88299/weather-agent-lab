# These imports come from Python's standard library
import json
import os
from datetime import datetime, timezone
from pathlib import Path

# These imports came from the pip that was done within the venv
# Stored in requirements.txt
import requests
from dotenv import load_dotenv

load_dotenv() # by default, looks for a .env file in the current directory and works its way up. 
API_KEY = os.environ["OWM_API_KEY"] #looks for an actual variable. Otherwise, this may fail silently. Raises a KeyError immediately if the key is missing.
BASE_URL = "https://api.openweathermap.org/data/2.5/weather"

print(type(os.environ))

# This function returns a dictionary
def get_current_weather(city:str, units: str = "imperial") -> dict:
    params = {"q": city, "appid": API_KEY, "units": units} # A dictionary that defines the API parameters
    resp = requests.get(BASE_URL, params=params, timeout=10) # By default, there is no timeout in requests.get()
    resp.raise_for_status() # Will raise an error on 4xx or 5xx errors. Nothing done for 2xx responses
    return resp.json() # Parses the JSON body into Python objects. JSON objects become dicts, arrays become lists, true becomes True, and null becomes None

def save_json(data: dict, city: str) -> Path:
    out_dir = Path("output")
    out_dir.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = out_dir / f"{city.split(',')[0].lower()}_{stamp}.json"
    path.write_text(json.dumps(data, indent=2))
    return path

if __name__ == "__main__":
    city = "New York,US"
    data = get_current_weather(city)
    print(f"{data['name']}: {data['main']['temp']}°F, {data['weather'][0]['description']}")
    print(f"Saved to {save_json(data, city)}")
