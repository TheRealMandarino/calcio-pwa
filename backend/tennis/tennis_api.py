import logging
from urllib.parse import parse_qs, urlsplit

if __package__:
    from .tennis_scraper import TennisScraperError, fetch_tournament_results
    from .tennis_calendar import (
        TennisCalendarError,
        fetch_calendar as fetch_tennis_calendar,
        validate_request as validate_tennis_calendar_request,
    )
    from .tennis_history import (
        TennisHistoryError,
        fetch_historical_archive,
        validate_request as validate_tennis_history_request,
    )
else:
    from tennis.tennis_scraper import TennisScraperError, fetch_tournament_results
    from tennis.tennis_calendar import (
        TennisCalendarError,
        fetch_calendar as fetch_tennis_calendar,
        validate_request as validate_tennis_calendar_request,
    )
    from tennis.tennis_history import (
        TennisHistoryError,
        fetch_historical_archive,
        validate_request as validate_tennis_history_request,
    )


class TennisApiHandlerMixin:
    def handle_tennis_request(self, request_path):
        handlers = {
            "/api/tennis-results": self.handle_tennis_results_request,
            "/api/tennis-history": self.handle_tennis_history_request,
            "/api/tennis-calendar": self.handle_tennis_calendar_request,
        }
        handler = handlers.get(request_path)
        if handler is None:
            return False
        handler()
        return True

    def handle_tennis_results_request(self):
        parameters = parse_qs(urlsplit(self.path).query)
        url = parameters.get("url", [""])[0]
        surface = parameters.get("surface", [""])[0]
        tournament_date = parameters.get("date", [""])[0]
        try:
            results = fetch_tournament_results(url, surface, tournament_date)
        except TennisScraperError as error:
            self.send_json(400, {"error": str(error)})
            return
        except (OSError, TimeoutError) as error:
            logging.exception("Impossibile acquisire i risultati tennis.")
            self.send_json(
                502,
                {"error": f"Impossibile acquisire i risultati tennis: {error}"},
            )
            return

        self.send_json(200, results)

    def handle_tennis_history_request(self):
        parameters = parse_qs(urlsplit(self.path).query)
        tour = parameters.get("tour", [""])[0]
        try:
            start_year = int(parameters.get("start", [""])[0])
            end_year = int(parameters.get("end", [""])[0])
            validate_tennis_history_request(tour, start_year, end_year)
        except (TennisHistoryError, ValueError) as error:
            self.send_json(400, {"error": str(error)})
            return

        try:
            archive = fetch_historical_archive(tour, start_year, end_year)
        except TennisHistoryError as error:
            logging.warning("Impossibile acquisire lo storico tennis: %s", error)
            self.send_json(502, {"error": str(error)})
            return
        except OSError as error:
            logging.exception("Impossibile acquisire lo storico tennis.")
            self.send_json(
                502,
                {"error": f"Impossibile acquisire lo storico tennis: {error}"},
            )
            return

        body = archive["csv"].encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Tennis-Match-Count", str(archive["match_count"]))
        self.send_header(
            "X-Tennis-Invalid-Stat-Matches",
            str(archive["invalid_stat_matches"]),
        )
        self.send_header("X-Tennis-Years", f"{start_year}-{end_year}")
        self.send_header("X-Tennis-Source", archive["source"])
        self.send_header("X-Tennis-Source-Url", archive["source_url"])
        self.end_headers()
        self.wfile.write(body)

    def handle_tennis_calendar_request(self):
        parameters = parse_qs(urlsplit(self.path).query)
        tour = parameters.get("tour", [""])[0]
        try:
            days = int(parameters.get("days", ["14"])[0])
            validate_tennis_calendar_request(tour, days)
        except (TennisCalendarError, ValueError) as error:
            self.send_json(400, {"error": str(error)})
            return

        try:
            calendar = fetch_tennis_calendar(tour, days)
        except TennisCalendarError as error:
            logging.warning("Impossibile acquisire il calendario tennis: %s", error)
            self.send_json(502, {"error": str(error)})
            return
        except OSError as error:
            logging.exception("Impossibile acquisire il calendario tennis.")
            self.send_json(
                502,
                {"error": f"Impossibile acquisire il calendario tennis: {error}"},
            )
            return

        self.send_json(200, calendar)
