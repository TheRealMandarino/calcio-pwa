import json
import threading
import time
from datetime import date, datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen


CALENDAR_BASE_URL = "https://site.api.espn.com/apis/site/v2/sports/tennis"
CALENDAR_TIMEOUT = 15
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_CALENDAR_DAYS = 30
CACHE_SECONDS = 300
CALENDAR_CACHE = {}
CACHE_LOCK = threading.Lock()


class TennisCalendarError(ValueError):
    pass


def validate_request(tour, days):
    if tour not in {"atp", "wta"}:
        raise TennisCalendarError("Seleziona un circuito ATP o WTA valido.")
    if type(days) is not int or not 1 <= days <= MAX_CALENDAR_DAYS:
        raise TennisCalendarError(
            f"L'intervallo calendario deve essere compreso tra 1 e {MAX_CALENDAR_DAYS} giorni."
        )


def parse_scoreboard(payload, tour, now=None, end_time=None):
    if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
        raise TennisCalendarError("Il calendario ricevuto non ha il formato atteso.")

    current_time = now or datetime.now(timezone.utc)
    matches = {}
    for event in payload["events"]:
        tournament = event.get("name")
        if not isinstance(tournament, str) or not tournament.strip():
            continue
        venue = event.get("venue") or {}
        venue_name = venue.get("displayName", "") if isinstance(venue, dict) else ""

        for grouping in event.get("groupings") or []:
            grouping_info = grouping.get("grouping") or {}
            grouping_slug = str(grouping_info.get("slug", "")).lower()
            grouping_name = str(grouping_info.get("displayName", "")).lower()
            is_singles = "singles" in grouping_slug or "singles" in grouping_name
            is_doubles = "doubles" in grouping_slug or "doubles" in grouping_name
            if not is_singles or is_doubles:
                continue

            for competition in grouping.get("competitions") or []:
                status = (competition.get("status") or {}).get("type") or {}
                if status.get("state") != "pre":
                    continue
                try:
                    start_time = datetime.fromisoformat(
                        competition.get("date", "").replace("Z", "+00:00")
                    )
                except (TypeError, ValueError):
                    continue
                if start_time < current_time - timedelta(minutes=30):
                    continue
                if end_time and start_time >= end_time:
                    continue

                competitors = competition.get("competitors") or []
                players = []
                for competitor in competitors:
                    athlete = competitor.get("athlete") or {}
                    name = athlete.get("displayName")
                    if not isinstance(name, str) or not name.strip():
                        continue
                    if name.strip().lower() in {"tbd", "to be determined"}:
                        continue
                    players.append(name.strip())
                if len(players) != 2 or players[0].casefold() == players[1].casefold():
                    continue

                competition_venue = competition.get("venue") or {}
                court = competition_venue.get("court", "") if isinstance(
                    competition_venue, dict
                ) else ""
                competition_id = str(competition.get("id") or "")
                if not competition_id:
                    continue
                matches[competition_id] = {
                    "id": competition_id,
                    "tour": tour.upper(),
                    "tournament": tournament.strip(),
                    "venue": venue_name,
                    "court": court,
                    "date": start_time.astimezone(timezone.utc).isoformat(),
                    "playerA": players[0],
                    "playerB": players[1],
                    "bestOf": 5 if _is_mens_grand_slam(tour, tournament) else 3,
                    "source": "ESPN",
                }

    return sorted(matches.values(), key=lambda match: (match["date"], match["tournament"]))


def _is_mens_grand_slam(tour, tournament):
    if tour != "atp":
        return False
    title = tournament.casefold()
    return any(name in title for name in (
        "australian open",
        "roland garros",
        "french open",
        "wimbledon",
        "us open",
    ))


def fetch_tour_calendar(tour, start_date, days):
    validate_request(tour, days)
    end_date = start_date + timedelta(days=days - 1)
    query = urlencode({
        "dates": f"{start_date:%Y%m%d}-{end_date:%Y%m%d}",
        "limit": "1000",
    })
    url = f"{CALENDAR_BASE_URL}/{tour}/scoreboard?{query}"
    request = Request(
        url,
        headers={
            "User-Agent": "CalcioPWA-Tennis/1.0 (local match calendar)",
            "Accept": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=CALENDAR_TIMEOUT) as response:
            final_url = urlsplit(response.geturl())
            if (
                final_url.scheme != "https"
                or final_url.hostname != "site.api.espn.com"
            ):
                raise TennisCalendarError(
                    "La fonte ha reindirizzato a un host non previsto."
                )
            data = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as error:
        raise TennisCalendarError(
            f"Il calendario ESPN ha restituito un errore HTTP {error.code} per {tour.upper()}."
        ) from error
    except (URLError, TimeoutError) as error:
        raise TennisCalendarError(
            f"Impossibile raggiungere il calendario ESPN per {tour.upper()}."
        ) from error

    if len(data) > MAX_RESPONSE_BYTES:
        raise TennisCalendarError("La risposta del calendario supera il limite di 8 MB.")
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TennisCalendarError("ESPN ha restituito un calendario non valido.") from error

    window_end = datetime.combine(
        end_date + timedelta(days=1),
        datetime.min.time(),
        tzinfo=timezone.utc,
    )
    return parse_scoreboard(payload, tour, end_time=window_end)


def fetch_calendar(tour, days=14):
    validate_request(tour, days)
    today = date.today()
    cache_key = (tour, today, days)
    now = time.monotonic()
    with CACHE_LOCK:
        cached = CALENDAR_CACHE.get(cache_key)
        if cached and now - cached[0] < CACHE_SECONDS:
            return cached[1]

    result = fetch_tour_calendar(tour, today, days)
    response = {
        "tour": tour.upper(),
        "days": days,
        "start_date": today.isoformat(),
        "matches": result,
        "source": "ESPN",
        "source_url": "https://www.espn.com/tennis/",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    with CACHE_LOCK:
        CALENDAR_CACHE[cache_key] = (time.monotonic(), response)
    return response
