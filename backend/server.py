import json
import logging
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


ROOT_DIR = Path(__file__).resolve().parent.parent
DOCS_DIR = ROOT_DIR / "docs"
REFRESH_STATE_PATH = Path(__file__).resolve().with_name(".refresh-state.json")
REFRESH_LOCK = threading.Lock()
refresh_job = {"status": "idle", "message": ""}
API_URL = "https://api.the-odds-api.com/v4/sports/{sport}/odds/"
LEAGUES = {
    "ita-serie_a.json": "soccer_italy_serie_a",
    "ita-serie_b.json": "soccer_italy_serie_b",
    "eng-premier_league.json": "soccer_epl",
    "eng-championship.json": "soccer_efl_champ",
    "esp-la_liga.json": "soccer_spain_la_liga",
    "ger-bundesliga.json": "soccer_germany_bundesliga",
    "fra-ligue_1.json": "soccer_france_ligue_one",
}
CACHE_SECONDS = 300
odds_cache = {}


def today_in_italy():
    return datetime.now(ZoneInfo("Europe/Rome")).date().isoformat()


def load_refresh_state():
    try:
        return json.loads(REFRESH_STATE_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError):
        return {}


def run_daily_refresh():
    venv_python = ROOT_DIR / "venv" / "Scripts" / "python.exe"
    python_executable = str(venv_python if venv_python.is_file() else Path(sys.executable))
    subprocess.run(
        [python_executable, "-m", "backend.daily_refresh"],
        cwd=ROOT_DIR,
        check=True,
        capture_output=True,
        text=True,
        timeout=900,
    )


def refresh_worker(refresh_date):
    global refresh_job
    try:
        run_daily_refresh()
        state = {"date": refresh_date, "status": "complete"}
        temporary_path = REFRESH_STATE_PATH.with_suffix(".tmp")
        temporary_path.write_text(json.dumps(state), encoding="utf-8")
        temporary_path.replace(REFRESH_STATE_PATH)
        message = "Calendario e risultati aggiornati."
        status = "complete"
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or error.stdout or str(error)).strip()
        message = f"Aggiornamento non riuscito: {detail[-1000:]}"
        status = "failed"
    except (OSError, subprocess.TimeoutExpired) as error:
        message = f"Aggiornamento non riuscito: {error}"
        status = "failed"
    except Exception as error:
        logging.exception("Errore imprevisto durante l'aggiornamento giornaliero.")
        message = f"Aggiornamento non riuscito per un errore imprevisto: {error}"
        status = "failed"

    with REFRESH_LOCK:
        refresh_job = {"date": refresh_date, "status": status, "message": message}


def start_daily_refresh():
    global refresh_job
    today = today_in_italy()
    with REFRESH_LOCK:
        if refresh_job["status"] == "running" and refresh_job.get("date") == today:
            return dict(refresh_job)

        saved_state = load_refresh_state()
        if saved_state.get("date") == today and saved_state.get("status") == "complete":
            refresh_job = {
                "date": today,
                "status": "complete",
                "message": "Calendario e risultati già aggiornati oggi.",
            }
            return dict(refresh_job)

        refresh_job = {
            "date": today,
            "status": "running",
            "message": "Aggiornamento giornaliero di calendario e risultati in corso...",
        }
        threading.Thread(target=refresh_worker, args=(today,), daemon=True).start()
        return dict(refresh_job)


def load_api_key():
    api_key = os.environ.get("ODDS_API_KEY")
    if api_key:
        return api_key

    env_path = Path(__file__).resolve().with_name(".env")
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            name, separator, value = line.partition("=")
            if separator and name.strip() == "ODDS_API_KEY":
                return value.strip().strip("\"'")
    return None


def normalize_events(events):
    normalized = []
    for event in events:
        odds = {}
        for bookmaker in event.get("bookmakers", []):
            for market in bookmaker.get("markets", []):
                if market.get("key") != "h2h":
                    continue
                for outcome in market.get("outcomes", []):
                    name = outcome.get("name")
                    if name == event.get("home_team"):
                        market_key = "1"
                    elif name == "Draw":
                        market_key = "X"
                    elif name == event.get("away_team"):
                        market_key = "2"
                    else:
                        continue

                    try:
                        price = float(outcome["price"])
                    except (KeyError, TypeError, ValueError):
                        continue
                    if price <= 1:
                        continue

                    current = odds.get(market_key)
                    if current is None or price > current["price"]:
                        odds[market_key] = {
                            "price": price,
                            "bookmaker": bookmaker.get("title", bookmaker.get("key", "Bookmaker")),
                        }

        if odds:
            normalized.append({
                "home_team": event.get("home_team", ""),
                "away_team": event.get("away_team", ""),
                "commence_time": event.get("commence_time"),
                "odds": odds,
            })
    return normalized


def fetch_odds(league, api_key):
    sport = LEAGUES[league]
    cached = odds_cache.get(sport)
    if cached and time.monotonic() - cached[0] < CACHE_SECONDS:
        return cached[1]

    query = urlencode({
        "apiKey": api_key,
        "regions": "eu",
        "markets": "h2h",
        "oddsFormat": "decimal",
        "dateFormat": "iso",
    })
    request = Request(API_URL.format(sport=sport) + "?" + query)
    with urlopen(request, timeout=15) as response:
        events = json.loads(response.read().decode("utf-8"))
    if not isinstance(events, list):
        raise ValueError("Risposta non valida ricevuta dal servizio quote.")

    result = normalize_events(events)
    odds_cache[sport] = (time.monotonic(), result)
    return result


def load_dotenv():
    env_path = Path(__file__).resolve().with_name(".env")
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        name, separator, value = line.partition("=")
        if separator and name.strip() == "ODDS_API_KEY":
            os.environ.setdefault("ODDS_API_KEY", value.strip().strip("\"'"))


class CalcioRequestHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DOCS_DIR), **kwargs)

    def do_GET(self):
        request_path = urlsplit(self.path).path
        if request_path == "/api/odds":
            self.handle_odds_request()
            return
        if request_path == "/api/refresh":
            self.send_json(200, start_daily_refresh())
            return
        if request_path == "/api/refresh-status":
            with REFRESH_LOCK:
                self.send_json(200, dict(refresh_job))
            return
        if request_path == "/" or request_path == "/docs":
            self.path = "/index.html"
        elif request_path.startswith("/docs/"):
            self.path = request_path[len("/docs"):]
        else:
            self.send_error(404)
            return
        super().do_GET()

    def handle_odds_request(self):
        league = parse_qs(urlsplit(self.path).query).get("league", [""])[0]
        if league not in LEAGUES:
            self.send_json(400, {"error": "Campionato non supportato."})
            return

        api_key = load_api_key()
        if not api_key:
            self.send_json(
                503,
                {"error": "Configura ODDS_API_KEY nel file backend/.env e riavvia il server."},
            )
            return

        try:
            odds = fetch_odds(league, api_key)
        except HTTPError as error:
            if error.code in (401, 403):
                message = "Chiave API non valida o non autorizzata."
            elif error.code == 429:
                message = "Limite di richieste API raggiunto."
            else:
                message = f"Il servizio quote ha restituito un errore ({error.code})."
            self.send_json(502, {"error": message})
            return
        except (URLError, TimeoutError, json.JSONDecodeError, ValueError) as error:
            if isinstance(error, URLError):
                message = "Servizio quote non raggiungibile. Controlla la connessione."
            elif isinstance(error, TimeoutError):
                message = "Il servizio quote non ha risposto in tempo."
            else:
                message = str(error) or "Risposta non valida ricevuta dal servizio quote."
            self.send_json(502, {"error": message})
            return

        self.send_json(200, odds)

    def send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    load_dotenv()
    server = ThreadingHTTPServer(("127.0.0.1", 8000), CalcioRequestHandler)
    print("Server Calcio PWA avviato su http://localhost:8000/docs/")
    server.serve_forever()
