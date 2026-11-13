import re
import threading
import time
from datetime import date
from html import unescape
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


MAX_PAGE_BYTES = 2 * 1024 * 1024
PAGE_TIMEOUT = 15
CACHE_SECONDS = 600
SURFACES = {"clay", "grass", "hard_indoor", "hard_outdoor", "hard_unspecified"}
TOURNAMENT_PATH = re.compile(r"^/current/\d{4}(?:ATP|WTA)[A-Za-z0-9-]+\.html$")
COMPLETED_MATCHES = re.compile(
    r"""var\s+completedSingles\s*=\s*'(.*?)'\s*;""",
    re.DOTALL,
)
MATCH_LINE = re.compile(
    r"^(?P<round>R\d+|QF|SF|F|RR|BR):\s*"
    r"(?:\([^)]+\))?\s*"
    r"(?P<winner>.+?)\s+\([A-Z]{3}\)\s+d\.\s*"
    r"(?:\([^)]+\))?\s*"
    r"(?P<loser>.+?)\s+\([A-Z]{3}\)\s+"
    r"(?P<score>.+)$"
)
RESULT_CACHE = {}
RESULT_CACHE_LOCK = threading.Lock()


class TennisScraperError(ValueError):
    pass


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def extract_text(fragment):
    parser = TextExtractor()
    parser.feed(fragment)
    parser.close()
    return unescape("".join(parser.parts)).replace("\xa0", " ").strip()


def validate_request(url, surface, tournament_date):
    parsed = urlsplit(url)
    try:
        invalid_host = (
            parsed.scheme != "https"
            or parsed.hostname != "www.tennisabstract.com"
            or parsed.port is not None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or not TOURNAMENT_PATH.fullmatch(parsed.path)
        )
    except ValueError:
        invalid_host = True
    if invalid_host:
        raise TennisScraperError(
            "Inserisci una pagina torneo HTTPS di tennisabstract.com/current/."
        )
    if surface not in SURFACES:
        raise TennisScraperError("Seleziona una superficie valida per il torneo.")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", tournament_date or ""):
        raise TennisScraperError("Inserisci la data d'inizio del torneo in formato valido.")
    try:
        date.fromisoformat(tournament_date)
    except ValueError as error:
        raise TennisScraperError("La data d'inizio del torneo non è valida.") from error
    return parsed


def parse_tournament_page(page, url, surface, tournament_date):
    parsed = validate_request(url, surface, tournament_date)
    title_match = re.search(r"<title[^>]*>(.*?)</title>", page, re.IGNORECASE | re.DOTALL)
    title = extract_text(title_match.group(1)) if title_match else parsed.path.rsplit("/", 1)[-1]
    tournament_name = re.sub(
        r"^Tennis Abstract:\s*|\s+Results and Forecast\s*$",
        "",
        title,
        flags=re.IGNORECASE,
    ).strip()

    matches_block = COMPLETED_MATCHES.search(page)
    if not matches_block:
        raise TennisScraperError(
            "La pagina non espone il blocco completedSingles atteso; "
            "il formato della fonte potrebbe essere cambiato."
        )

    body = matches_block.group(1)
    lines = re.split(r"<br\s*/?>", body, flags=re.IGNORECASE)
    matches = []
    for line in lines:
        text = extract_text(line)
        match = MATCH_LINE.match(text)
        if not match:
            continue
        winner = match.group("winner").strip()
        loser = match.group("loser").strip()
        if not winner or not loser or len(matches) >= 300:
            continue
        matches.append({
            "round": match.group("round"),
            "winner": winner,
            "loser": loser,
            "score": match.group("score").strip(),
            "tournament": tournament_name,
            "surface": surface,
            "date": tournament_date,
        })

    if not matches:
        raise TennisScraperError(
            "Nessun match completato riconosciuto nella pagina torneo."
        )
    return {
        "source": "Tennis Abstract",
        "source_url": url,
        "tournament": tournament_name,
        "surface": surface,
        "tournament_date": tournament_date,
        "matches": matches,
    }


def fetch_tournament_results(url, surface, tournament_date):
    validate_request(url, surface, tournament_date)
    cache_key = (url, surface, tournament_date)
    now = time.monotonic()
    with RESULT_CACHE_LOCK:
        cached = RESULT_CACHE.get(cache_key)
        if cached and now - cached[0] < CACHE_SECONDS:
            return cached[1]

    request = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (compatible; CalcioPWA-Tennis/1.0; "
                "+https://github.com/TheRealMandarino/calcio-pwa)"
            ),
            "Accept": "text/html",
        },
    )
    try:
        with urlopen(request, timeout=PAGE_TIMEOUT) as response:
            final_url = response.geturl()
            validate_request(final_url, surface, tournament_date)
            page = response.read(MAX_PAGE_BYTES + 1)
    except HTTPError as error:
        raise TennisScraperError(
            f"Tennis Abstract ha restituito un errore HTTP {error.code}."
        ) from error
    except (URLError, TimeoutError) as error:
        raise TennisScraperError(
            "Tennis Abstract non è raggiungibile o non ha risposto in tempo."
        ) from error

    if len(page) > MAX_PAGE_BYTES:
        raise TennisScraperError("La pagina torneo supera il limite di 2 MB.")
    results = parse_tournament_page(
        page.decode("utf-8", errors="replace"),
        url,
        surface,
        tournament_date,
    )
    with RESULT_CACHE_LOCK:
        RESULT_CACHE[cache_key] = (time.monotonic(), results)
    return results
