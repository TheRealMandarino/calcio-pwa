import csv
import io
import logging
import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


MIN_HISTORY_YEAR = 1968
MAX_YEARS_PER_REQUEST = 60
MAX_SEASON_BYTES = 8 * 1024 * 1024
MAX_ARCHIVE_BYTES = 120 * 1024 * 1024
REQUEST_TIMEOUT = 20
CACHE_SECONDS = 24 * 60 * 60
SOURCE_REPOSITORY = "Aneeshers/tennis-sackmann-archive"
SOURCE_BASE_URL = f"https://raw.githubusercontent.com/{SOURCE_REPOSITORY}/main"
REQUIRED_COLUMNS = {"surface", "winner_name", "loser_name"}
MODEL_COLUMNS = (
    "tourney_date",
    "date",
    "surface",
    "winner_name",
    "loser_name",
    "tourney_name",
    "score",
    "minutes",
    "w_svpt",
    "w_1stWon",
    "w_2ndWon",
    "w_bpSaved",
    "w_bpFaced",
    "l_svpt",
    "l_1stWon",
    "l_2ndWon",
    "l_bpSaved",
    "l_bpFaced",
)
CACHE_LOCK = threading.Lock()


class TennisHistoryError(ValueError):
    pass


def validate_request(tour, start_year, end_year):
    if tour not in {"atp", "wta"}:
        raise TennisHistoryError("Seleziona un circuito ATP o WTA valido.")
    if type(start_year) is not int or type(end_year) is not int:
        raise TennisHistoryError("Gli anni dello storico devono essere numeri interi.")
    current_year = date.today().year
    if not MIN_HISTORY_YEAR <= start_year <= end_year <= current_year:
        raise TennisHistoryError(
            f"Seleziona un intervallo valido tra {MIN_HISTORY_YEAR} e {current_year}."
        )
    if end_year - start_year + 1 > MAX_YEARS_PER_REQUEST:
        raise TennisHistoryError(
            f"Puoi scaricare al massimo {MAX_YEARS_PER_REQUEST} stagioni per volta."
        )


def _cache_directory():
    configured = os.environ.get("CALCIOPWA_TENNIS_CACHE")
    if configured:
        return Path(configured)
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / ".cache"
    return base / "CalcioPWA" / "tennis"


def _read_cache(path):
    try:
        if time.time() - path.stat().st_mtime < CACHE_SECONDS:
            data = path.read_bytes()
            if len(data) <= MAX_SEASON_BYTES:
                return data
    except FileNotFoundError:
        return None
    except OSError:
        logging.exception("Impossibile leggere la cache tennis %s.", path)
    return None


def fetch_season_csv(tour, year):
    validate_request(tour, year, year)
    cache_path = _cache_directory() / f"aneeshers_{tour}_{year}.csv"
    with CACHE_LOCK:
        cached = _read_cache(cache_path)
    if cached is not None:
        try:
            _inspect_csv(cached, tour, year)
            return cached
        except TennisHistoryError as error:
            logging.warning("Cache tennis non valida per %s %s: %s", tour, year, error)

    filename = f"{tour}_matches_{year}.csv"
    url = f"{SOURCE_BASE_URL}/{tour}/{filename}"
    request = Request(
        url,
        headers={
            "User-Agent": "CalcioPWA-Tennis/1.0 (historical match archive)",
            "Accept": "text/csv",
        },
    )
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            final_url = urlsplit(response.geturl())
            if final_url.scheme != "https" or final_url.hostname != "raw.githubusercontent.com":
                raise TennisHistoryError("La fonte ha reindirizzato a un host non previsto.")
            data = response.read(MAX_SEASON_BYTES + 1)
    except HTTPError as error:
        if error.code == 404:
            raise TennisHistoryError(
                f"Archivio {tour.upper()} {year} non disponibile nella fonte (404)."
            ) from error
        raise TennisHistoryError(
            f"La fonte dello storico ha restituito un errore HTTP {error.code} "
            f"per {tour.upper()} {year}."
        ) from error
    except (URLError, TimeoutError) as error:
        raise TennisHistoryError(
            f"Impossibile raggiungere la fonte dello storico per {tour.upper()} {year}."
        ) from error

    if len(data) > MAX_SEASON_BYTES:
        raise TennisHistoryError(
            f"L'archivio {tour.upper()} {year} supera il limite di 8 MB."
        )
    _inspect_csv(data, tour, year)
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = cache_path.with_name(
            f"{cache_path.name}.{threading.get_ident()}.tmp"
        )
        temporary_path.write_bytes(data)
        temporary_path.replace(cache_path)
    except OSError:
        logging.exception("Impossibile salvare la cache tennis %s.", cache_path)
    return data


def _inspect_csv(data, tour, year):
    try:
        reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig"), newline=""))
        fieldnames = reader.fieldnames or []
        missing = REQUIRED_COLUMNS - set(fieldnames)
        if missing:
            raise TennisHistoryError(
                f"L'archivio {tour.upper()} {year} non contiene le colonne "
                f"richieste: {', '.join(sorted(missing))}."
            )
        match_count = 0
        for row in reader:
            if not row or not row.get("winner_name") or not row.get("loser_name"):
                continue
            match_count += 1
        if not match_count:
            raise TennisHistoryError(
                f"L'archivio {tour.upper()} {year} non contiene match validi."
            )
        return fieldnames, match_count
    except UnicodeDecodeError as error:
        raise TennisHistoryError(
            f"L'archivio {tour.upper()} {year} non e' un CSV UTF-8 valido."
        ) from error
    except csv.Error as error:
        raise TennisHistoryError(
            f"Impossibile leggere il CSV {tour.upper()} {year}: {error}"
        ) from error


def _validated_model_row(row):
    cleaned = dict(row)
    invalid = False
    for side in ("w", "l"):
        service_keys = (
            f"{side}_svpt",
            f"{side}_1stWon",
            f"{side}_2ndWon",
        )
        service_values = _finite_values(row, service_keys)
        if service_values and (
            any(value < 0 for value in service_values)
            or sum(service_values[1:]) > service_values[0]
        ):
            for key in service_keys:
                cleaned[key] = ""
            invalid = True

        break_point_keys = (f"{side}_bpSaved", f"{side}_bpFaced")
        break_point_values = _finite_values(row, break_point_keys)
        if break_point_values and (
            any(value < 0 for value in break_point_values)
            or break_point_values[0] > break_point_values[1]
        ):
            for key in break_point_keys:
                cleaned[key] = ""
            invalid = True
    return cleaned, invalid


def _finite_values(row, keys):
    if any(row.get(key) in (None, "") for key in keys):
        return None
    try:
        values = [float(row[key]) for key in keys]
    except (TypeError, ValueError):
        return None
    return values if all(math.isfinite(value) for value in values) else None


def fetch_historical_archive(tour, start_year, end_year):
    validate_request(tour, start_year, end_year)
    years = list(range(start_year, end_year + 1))
    season_data = {}
    total_bytes = 0
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(fetch_season_csv, tour, year): year
            for year in years
        }
        for future in as_completed(futures):
            year = futures[future]
            data = future.result()
            total_bytes += len(data)
            if total_bytes > MAX_ARCHIVE_BYTES:
                for pending in futures:
                    pending.cancel()
                raise TennisHistoryError(
                    "L'intervallo selezionato supera il limite di download di 120 MB."
                )
            season_data[year] = data

    available_columns = set()
    match_count = 0
    for year in years:
        headers, season_match_count = _inspect_csv(season_data[year], tour, year)
        available_columns.update(headers)
        match_count += season_match_count

    columns = [column for column in MODEL_COLUMNS if column in available_columns]
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    invalid_stat_matches = 0
    for year in years:
        reader = csv.DictReader(
            io.StringIO(season_data[year].decode("utf-8-sig"), newline="")
        )
        for row in reader:
            if not row or not row.get("winner_name") or not row.get("loser_name"):
                continue
            cleaned, invalid = _validated_model_row(row)
            writer.writerow(cleaned)
            invalid_stat_matches += int(invalid)
    return {
        "csv": output.getvalue(),
        "match_count": match_count,
        "invalid_stat_matches": invalid_stat_matches,
        "years": years,
        "source": SOURCE_REPOSITORY,
        "source_url": f"https://github.com/{SOURCE_REPOSITORY}",
    }
