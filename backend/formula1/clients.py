import json
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


JOLPICA_BASE_URL = "https://api.jolpi.ca/ergast/f1"
OPENF1_BASE_URL = "https://api.openf1.org/v1"
REQUEST_TIMEOUT = 30
MAX_RESPONSE_BYTES = 32 * 1024 * 1024
JOLPICA_PAGE_SIZE = 100


class DataSourceError(RuntimeError):
    def __init__(self, message, status_code=None, retry_after=None):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


def fetch_json(url, params=None):
    if params:
        url = f"{url}?{urlencode(params)}"
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "CalcioPWA-Formula1/1.0 (historical race archive)",
        },
    )
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            data = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as error:
        retry_after = error.headers.get("Retry-After")
        raise DataSourceError(
            f"{url} returned HTTP {error.code}.",
            status_code=error.code,
            retry_after=retry_after,
        ) from error
    except (URLError, TimeoutError) as error:
        raise DataSourceError(f"Unable to reach data source: {url}") from error

    if len(data) > MAX_RESPONSE_BYTES:
        raise DataSourceError(f"Response from {url} exceeds the 32 MB limit.")
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DataSourceError(f"Data source returned invalid JSON: {url}") from error


def _record_identity(item):
    if not isinstance(item, dict):
        return None
    for key in (
        "round",
        "number",
        "driverId",
        "constructorId",
        "lap",
        "position",
    ):
        if key in item:
            return key, str(item[key])
    return None


def _merge_list(current, incoming):
    merged = list(current)
    index = {
        identity: position
        for position, item in enumerate(merged)
        if (identity := _record_identity(item)) is not None
    }
    for item in incoming:
        identity = _record_identity(item)
        if identity is not None and identity in index:
            position = index[identity]
            merged[position] = _merge_value(merged[position], item)
        elif identity is not None:
            index[identity] = len(merged)
            merged.append(item)
        elif item not in merged:
            merged.append(item)
    return merged


def _merge_value(current, incoming):
    if isinstance(current, dict) and isinstance(incoming, dict):
        merged = dict(current)
        for key, value in incoming.items():
            if key not in merged:
                merged[key] = value
            else:
                merged[key] = _merge_value(merged[key], value)
        return merged
    if isinstance(current, list) and isinstance(incoming, list):
        return _merge_list(current, incoming)
    return current


class JolpicaClient:
    def __init__(
        self,
        minimum_request_interval=0.25,
        sleep=time.sleep,
        clock=time.monotonic,
        max_retries=5,
    ):
        self.minimum_request_interval = minimum_request_interval
        self.sleep = sleep
        self.clock = clock
        self.max_retries = max_retries
        self.last_request = None

    def _get_json(self, path, params=None):
        url = f"{JOLPICA_BASE_URL}/{path}"
        for attempt in range(self.max_retries + 1):
            if self.last_request is not None:
                elapsed = self.clock() - self.last_request
                delay = self.minimum_request_interval - elapsed
                if delay > 0:
                    self.sleep(delay)
            self.last_request = self.clock()
            try:
                return fetch_json(url, params)
            except DataSourceError as error:
                if error.status_code != 429 or attempt == self.max_retries:
                    raise
                try:
                    retry_delay = float(error.retry_after)
                except (TypeError, ValueError):
                    retry_delay = 0.5 * (2 ** attempt)
                self.sleep(max(retry_delay, self.minimum_request_interval))
                self.last_request = self.clock()
        raise DataSourceError(f"Unable to fetch {url} after rate-limit retries.")

    def fetch_schedule(self, year):
        payload = self._get_json(f"{year}.json")
        races = payload.get("MRData", {}).get("RaceTable", {}).get("Races")
        if not isinstance(races, list):
            raise DataSourceError(f"Jolpica returned an invalid {year} calendar.")
        return races

    def fetch_races(self, year, collection):
        if collection not in {"results", "qualifying", "sprint"}:
            raise ValueError(f"Unsupported race collection: {collection}")

        offset = 0
        total = None
        merged_races = []
        while total is None or offset < total:
            payload = self._get_json(
                f"{year}/{collection}.json",
                {"limit": JOLPICA_PAGE_SIZE, "offset": offset},
            )
            response = payload.get("MRData", {})
            race_table = response.get("RaceTable", {})
            races = race_table.get("Races")
            if not isinstance(races, list):
                raise DataSourceError(
                    f"Jolpica returned invalid {collection} data for {year}."
                )
            try:
                total = int(response.get("total", len(races)))
                page_size = int(response.get("limit", JOLPICA_PAGE_SIZE))
            except (TypeError, ValueError) as error:
                raise DataSourceError(
                    f"Jolpica returned invalid pagination for {year} {collection}."
                ) from error
            if page_size <= 0:
                raise DataSourceError(
                    f"Jolpica returned an invalid page size for {year} {collection}."
                )
            merged_races = _merge_list(merged_races, races)
            if total == 0 or offset + page_size >= total:
                break
            if not races:
                raise DataSourceError(
                    f"Jolpica pagination stopped early for {year} {collection}."
                )
            offset += page_size
        return merged_races

    def fetch_standings(self, year, standings_type):
        if standings_type not in {"driver", "constructor"}:
            raise ValueError(f"Unsupported standings type: {standings_type}")
        payload = self._get_json(f"{year}/{standings_type}Standings.json")
        standings_table = payload.get("MRData", {}).get("StandingsTable", {})
        lists = standings_table.get("StandingsLists")
        if not isinstance(lists, list):
            raise DataSourceError(
                f"Jolpica returned invalid {standings_type} standings for {year}."
            )
        return lists


class OpenF1Client:
    def __init__(self, minimum_request_interval=2.1, sleep=time.sleep, clock=time.monotonic):
        self.minimum_request_interval = minimum_request_interval
        self.sleep = sleep
        self.clock = clock
        self.last_request = None

    def _get_json(self, endpoint, params=None):
        if self.last_request is not None:
            elapsed = self.clock() - self.last_request
            delay = self.minimum_request_interval - elapsed
            if delay > 0:
                self.sleep(delay)

        url = f"{OPENF1_BASE_URL}/{endpoint}"
        self.last_request = self.clock()
        try:
            return fetch_json(url, params)
        except DataSourceError as error:
            if error.status_code != 429:
                raise
            try:
                retry_delay = float(error.retry_after)
            except (TypeError, ValueError):
                retry_delay = 60.0
            self.sleep(max(retry_delay, self.minimum_request_interval))
            self.last_request = self.clock()
            return fetch_json(url, params)

    def fetch_sessions(self, year):
        sessions = self._get_json("sessions", {"year": year})
        if not isinstance(sessions, list):
            raise DataSourceError(f"OpenF1 returned an invalid session list for {year}.")
        return sessions

    def fetch_session_data(self, session_key, include_details=False):
        endpoints = ["laps", "drivers"]
        if include_details:
            endpoints.extend(["stints", "pit", "weather", "race_control"])

        data = {}
        unavailable = []
        for endpoint in endpoints:
            try:
                rows = self._get_json(endpoint, {"session_key": session_key})
            except DataSourceError as error:
                if error.status_code != 404:
                    raise
                unavailable.append({"endpoint": endpoint, "reason": str(error)})
                continue
            if not isinstance(rows, list):
                raise DataSourceError(
                    f"OpenF1 returned invalid {endpoint} data for session {session_key}."
                )
            data[endpoint] = rows
            if not rows:
                unavailable.append({"endpoint": endpoint, "reason": "No records."})

        return data, unavailable
