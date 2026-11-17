import json
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


MOTOGP_BASE_URL = "https://api.motogp.pulselive.com/motogp/v1"
REQUEST_TIMEOUT = 30
MAX_RESPONSE_BYTES = 32 * 1024 * 1024


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
            "Origin": "https://www.motogp.com",
            "Referer": "https://www.motogp.com/",
            "User-Agent": "CalcioPWA-MotoGP/1.0 (race archive)",
        },
    )
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            data = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as error:
        raise DataSourceError(
            f"MotoGP API returned HTTP {error.code} for {url}.",
            status_code=error.code,
            retry_after=error.headers.get("Retry-After"),
        ) from error
    except (URLError, TimeoutError) as error:
        raise DataSourceError(f"Unable to reach the MotoGP API: {url}") from error

    if len(data) > MAX_RESPONSE_BYTES:
        raise DataSourceError(f"MotoGP API response exceeds the 32 MB limit: {url}")
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DataSourceError(f"MotoGP API returned invalid JSON: {url}") from error


class MotoGPClient:
    def __init__(
        self,
        minimum_request_interval=0.25,
        sleep=time.sleep,
        clock=time.monotonic,
        max_retries=4,
    ):
        self.minimum_request_interval = minimum_request_interval
        self.sleep = sleep
        self.clock = clock
        self.max_retries = max_retries
        self.last_request = None

    def _get_json(self, path, params=None):
        url = f"{MOTOGP_BASE_URL}/{path}"
        for attempt in range(self.max_retries + 1):
            if self.last_request is not None:
                delay = self.minimum_request_interval - (self.clock() - self.last_request)
                if delay > 0:
                    self.sleep(delay)
            self.last_request = self.clock()
            try:
                return fetch_json(url, params)
            except DataSourceError as error:
                if error.status_code not in (429, 500, 502, 503, 504) or attempt == self.max_retries:
                    raise
                try:
                    retry_delay = float(error.retry_after)
                except (TypeError, ValueError):
                    retry_delay = min(0.5 * (2 ** attempt), 15)
                self.sleep(max(retry_delay, self.minimum_request_interval))
                self.last_request = self.clock()
        raise DataSourceError(f"Unable to fetch {url} after rate-limit retries.")

    def fetch_seasons(self):
        seasons = self._get_json("results/seasons")
        if not isinstance(seasons, list):
            raise DataSourceError("MotoGP API returned an invalid season list.")
        return seasons

    def fetch_events(self, season_id):
        events = self._get_json(
            "results/events",
            {"seasonUuid": season_id},
        )
        if not isinstance(events, list):
            raise DataSourceError(f"MotoGP API returned an invalid calendar for season {season_id}.")
        return events

    def fetch_categories(self, season_id):
        categories = self._get_json(
            "results/categories",
            {"seasonUuid": season_id},
        )
        if not isinstance(categories, list):
            raise DataSourceError(f"MotoGP API returned invalid categories for season {season_id}.")
        return categories

    def fetch_sessions(self, event_id, category_id):
        sessions = self._get_json(
            "results/sessions",
            {"eventUuid": event_id, "categoryUuid": category_id},
        )
        if not isinstance(sessions, list):
            raise DataSourceError(f"MotoGP API returned invalid sessions for event {event_id}.")
        return sessions

    def fetch_classification(self, session_id):
        payload = self._get_json(
            f"results/session/{session_id}/classification",
            {"test": "false"},
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("classification"), list):
            raise DataSourceError(f"MotoGP API returned an invalid classification for session {session_id}.")
        return payload

    def fetch_standings(self, season_id, category_id):
        payload = self._get_json(
            "results/standings",
            {"seasonUuid": season_id, "categoryUuid": category_id},
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("classification"), list):
            raise DataSourceError(f"MotoGP API returned invalid standings for season {season_id}.")
        return payload
