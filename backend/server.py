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
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

if __package__:
    from .formula1.updater import default_data_directory
    from .formula1.predictor import build_formula1_view_payload
    from .tennis.tennis_api import TennisApiHandlerMixin
else:
    from formula1.updater import default_data_directory
    from formula1.predictor import build_formula1_view_payload
    from tennis.tennis_api import TennisApiHandlerMixin


ROOT_DIR = Path(__file__).resolve().parent.parent
DOCS_DIR = ROOT_DIR / "docs"
FORMULA1_DATA_DIR = default_data_directory()
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
    "tennis_atp.json": "tennis_atp",
    "tennis_wta.json": "tennis_wta",
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
        [python_executable, "-m", "backend.calcio.daily_refresh"],
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


def normalize_events(events, is_tennis=False):
    normalized = []
    for event in events:
        odds = {}
        home = event.get("home_team")
        away = event.get("away_team")
        for bookmaker in event.get("bookmakers", []):
            for market in bookmaker.get("markets", []):
                if market.get("key") != "h2h":
                    continue
                for outcome in market.get("outcomes", []):
                    name = outcome.get("name")
                    if is_tennis:
                        if name == home:
                            market_key = "player_a"
                        elif name == away:
                            market_key = "player_b"
                        else:
                            continue
                    else:
                        if name == home:
                            market_key = "1"
                        elif name == "Draw":
                            market_key = "X"
                        elif name == away:
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
            if is_tennis:
                normalized.append({
                    "player_a": home,
                    "player_b": away,
                    "commence_time": event.get("commence_time"),
                    "odds": odds,
                })
            else:
                normalized.append({
                    "home_team": home,
                    "away_team": away,
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

    is_tennis = league.startswith("tennis_")
    result = normalize_events(events, is_tennis=is_tennis)
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


class CalcioRequestHandler(TennisApiHandlerMixin, SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DOCS_DIR), **kwargs)

    def do_GET(self):
        request_path = urlsplit(self.path).path
        if request_path == "/api/odds":
            self.handle_odds_request()
            return
        if request_path == "/api/formula1/status":
            self.handle_formula1_status_request()
            return
        if request_path in ("/api/formula1/data", "/api/formula1/season"):
            self.handle_formula1_data_request()
            return
        if request_path == "/api/formula1/refresh":
            self.handle_formula1_refresh_request()
            return
        if self.handle_tennis_request(request_path):
            return
        if request_path == "/api/refresh":
            try:
                self.send_json(200, start_daily_refresh())
            except ZoneInfoNotFoundError:
                logging.exception("Dati dei fusi orari non disponibili.")
                self.send_json(
                    503,
                    {
                        "error": (
                            "Dati dei fusi orari non disponibili. Avvia il server "
                            "con venv\\Scripts\\python.exe o installa requirements.txt."
                        )
                    },
                )
            return
        if request_path == "/api/refresh-status":
            with REFRESH_LOCK:
                self.send_json(200, dict(refresh_job))
            return
        if request_path == "/" or request_path == "/docs":
            self.send_response(302)
            self.send_header("Location", "/docs/")
            self.end_headers()
            return
        if request_path == "/docs/":
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
            self.send_json(400, {"error": "Campionato o sport non supportato."})
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

    def handle_formula1_status_request(self):
        manifest_path = FORMULA1_DATA_DIR / "manifest.json"
        try:
            with manifest_path.open(encoding="utf-8") as file:
                manifest = json.load(file)
        except FileNotFoundError:
            self.send_json(
                503,
                {
                    "error": (
                        "Archivio Formula 1 non ancora creato. Avvia "
                        "`python -m backend.formula1 --update`."
                    )
                },
            )
            return
        except (OSError, json.JSONDecodeError) as error:
            logging.exception("Impossibile leggere il manifest Formula 1.")
            self.send_json(
                500,
                {"error": f"Impossibile leggere il manifest Formula 1: {error}"},
            )
            return

        seasons = manifest.get("seasons")
        if not isinstance(seasons, list):
            self.send_json(500, {"error": "Manifest Formula 1 non valido: stagioni mancanti."})
            return
        current_year = manifest.get("end_year")
        current_season = next(
            (season for season in seasons if season.get("year") == current_year),
            None,
        )
        if current_season is None:
            self.send_json(
                500,
                {"error": "Manifest Formula 1 non valido: stagione corrente mancante."},
            )
            return

        self.send_json(200, {
            "generated_at": manifest.get("generated_at"),
            "start_year": manifest.get("start_year"),
            "end_year": current_year,
            "season_count": len(seasons),
            "scheduled_races": current_season.get("scheduled_races", 0),
            "completed_races": current_season.get("completed_races", 0),
            "qualifying_records": current_season.get("qualifying_records", 0),
            "sprint_records": current_season.get("sprint_records", 0),
            "driver_standings": current_season.get("driver_standings", 0),
            "constructor_standings": current_season.get("constructor_standings", 0),
            "pre_race_feature_rows": current_season.get("pre_race_feature_rows", 0),
            "lap_events_available": current_season.get("lap_events_available", 0),
            "unavailable_count": len(manifest.get("unavailable", [])),
        })

    def handle_formula1_data_request(self):
        try:
            payload = build_formula1_view_payload(FORMULA1_DATA_DIR)
            self.send_json(200, payload)
        except FileNotFoundError as error:
            self.send_json(503, {"error": str(error)})
        except Exception as error:
            logging.exception("Impossibile elaborare i dati Formula 1.")
            self.send_json(500, {"error": f"Errore interno Formula 1: {error}"})

    def handle_formula1_refresh_request(self):
        try:
            if __package__:
                from .formula1.updater import ArchiveUpdater
            else:
                from formula1.updater import ArchiveUpdater
            updater = ArchiveUpdater(data_directory=FORMULA1_DATA_DIR)
            updater.update(start_year=2024, include_details=False)
            payload = build_formula1_view_payload(FORMULA1_DATA_DIR)
            docs_json = DOCS_DIR / "formula1" / "formula1-data.json"
            docs_js = DOCS_DIR / "formula1" / "formula1-data.js"
            if docs_json.parent.is_dir():
                with docs_json.open("w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)
                with docs_js.open("w", encoding="utf-8") as f:
                    f.write("window.__FORMULA1_DATA__ = " + json.dumps(payload, ensure_ascii=False) + ";\n")
            self.send_json(200, {"status": "ok", "message": "Dati Formula 1 aggiornati", "data": payload})
        except Exception as error:
            logging.exception("Aggiornamento Formula 1 non riuscito.")
            self.send_json(500, {"error": f"Aggiornamento non riuscito: {error}"})

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