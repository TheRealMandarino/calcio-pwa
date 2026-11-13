import json
import os
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

from .clients import DataSourceError, JolpicaClient, OpenF1Client
from .features import build_pre_race_features


MIN_HISTORY_YEAR = 2016
OPENF1_START_YEAR = 2023


def default_data_directory():
    configured = os.environ.get("CALCIOPWA_FORMULA1_DATA")
    if configured:
        return Path(configured)
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / ".cache"
    return base / "CalcioPWA" / "formula1"


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f"{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as temporary_file:
        temporary_path = Path(temporary_file.name)
        json.dump(data, temporary_file, ensure_ascii=False, separators=(",", ":"))
        temporary_file.write("\n")
    temporary_path.replace(path)


def _read_json(path):
    with path.open(encoding="utf-8") as file:
        return json.load(file)


def _event_round_for_session(session, schedule):
    session_start = session.get("date_start")
    if not session_start:
        return None
    try:
        session_date = datetime.fromisoformat(session_start.replace("Z", "+00:00")).date()
    except ValueError:
        return None

    candidates = []
    for race in schedule:
        try:
            race_date = date.fromisoformat(race.get("date", ""))
            round_number = int(race.get("round", ""))
        except (TypeError, ValueError):
            continue
        day_difference = abs((session_date - race_date).days)
        if day_difference <= 1:
            candidates.append((day_difference, round_number))
    if not candidates:
        return None
    return min(candidates)[1]


class ArchiveUpdater:
    def __init__(
        self,
        data_directory=None,
        jolpica=None,
        openf1=None,
        now=None,
    ):
        self.data_directory = Path(data_directory or default_data_directory())
        self.jolpica = jolpica or JolpicaClient()
        self.openf1 = openf1 or OpenF1Client()
        self.now = now or (lambda: datetime.now(timezone.utc))

    def _load_or_fetch_season(self, year, current_year):
        path = self.data_directory / "seasons" / f"{year}.json"
        if path.is_file() and year != current_year:
            return _read_json(path)

        schedule = self.jolpica.fetch_schedule(year)
        results = self.jolpica.fetch_races(year, "results")
        qualifying = self.jolpica.fetch_races(year, "qualifying")
        sprints = self.jolpica.fetch_races(year, "sprint")
        driver_standings = self.jolpica.fetch_standings(year, "driver")
        constructor_standings = self.jolpica.fetch_standings(year, "constructor")
        season = {
            "schema_version": 1,
            "year": year,
            "source": "Jolpica-F1 (Ergast-compatible API)",
            "schedule": schedule,
            "results": results,
            "qualifying": qualifying,
            "sprints": sprints,
            "standings": {
                "drivers": driver_standings,
                "constructors": constructor_standings,
            },
        }
        _write_json(path, season)
        return season

    def _load_or_fetch_sessions(self, year, current_year):
        path = self.data_directory / "sessions" / f"{year}.json"
        if path.is_file() and year != current_year:
            return _read_json(path)
        sessions = self.openf1.fetch_sessions(year)
        _write_json(path, sessions)
        return sessions

    def _collect_lap_data(self, year, season, current_year, include_details):
        if year < OPENF1_START_YEAR:
            return 0, []

        sessions = self._load_or_fetch_sessions(year, current_year)
        completed_sessions = [
            session
            for session in sessions
            if session.get("session_type") == "Race"
            and session.get("session_name") == "Race"
            and not session.get("is_cancelled", False)
            and self._session_has_ended(session)
        ]
        existing_rounds = {str(race.get("round")) for race in season["schedule"]}
        downloaded = 0
        unavailable = []

        for session in completed_sessions:
            round_number = _event_round_for_session(session, season["schedule"])
            if round_number is None or str(round_number) not in existing_rounds:
                unavailable.append({
                    "session_key": session.get("session_key"),
                    "reason": "No matching Jolpica calendar round.",
                })
                continue

            path = (
                self.data_directory
                / "races"
                / f"{year}-{round_number:02d}.json"
            )
            required_endpoints = ["laps", "drivers"]
            if include_details and year == current_year:
                required_endpoints.extend(
                    ["stints", "pit", "weather", "race_control"]
                )
            if path.is_file():
                cached = _read_json(path)
                cached_data = cached.get("data", {})
                if (
                    cached.get("session", {}).get("session_key") == session.get("session_key")
                    and cached_data.get("laps")
                    and cached_data.get("drivers")
                    and all(endpoint in cached_data for endpoint in required_endpoints)
                ):
                    continue

            session_key = session.get("session_key")
            if session_key is None:
                unavailable.append({
                    "round": round_number,
                    "reason": "OpenF1 session key is missing.",
                })
                continue

            try:
                data, missing = self.openf1.fetch_session_data(
                    session_key,
                    include_details=include_details and year == current_year,
                )
            except DataSourceError:
                raise

            event = {
                "schema_version": 1,
                "year": year,
                "round": round_number,
                "session": session,
                "source": "OpenF1",
                "data": data,
                "unavailable": missing,
            }
            _write_json(path, event)
            downloaded += 1
            if not data.get("laps"):
                unavailable.append({
                    "round": round_number,
                    "session_key": session_key,
                    "reason": "Lap data is unavailable.",
                })

        return downloaded, unavailable

    def _session_has_ended(self, session):
        session_end = session.get("date_end")
        if not session_end:
            return False
        try:
            end_time = datetime.fromisoformat(session_end.replace("Z", "+00:00"))
        except ValueError:
            return False
        if end_time.tzinfo is None:
            end_time = end_time.replace(tzinfo=timezone.utc)
        return end_time <= self.now()

    def _write_features(
        self,
        year,
        season,
        prior_seasons,
        prior_race_data_by_season,
        race_data_by_round,
    ):
        features = build_pre_race_features(
            season,
            race_data_by_round,
            prior_seasons,
            prior_race_data_by_season,
        )
        path = self.data_directory / "features" / f"{year}.json"
        _write_json(path, {
            "schema_version": 1,
            "year": year,
            "feature_timing": "All predictors use data available before each target race.",
            "rows": features,
        })
        return len(features)

    def update(self, start_year=MIN_HISTORY_YEAR, end_year=None, include_details=True):
        current_year = self.now().year
        end_year = current_year if end_year is None else end_year
        if type(start_year) is not int or type(end_year) is not int:
            raise ValueError("Season years must be integers.")
        if not MIN_HISTORY_YEAR <= start_year <= end_year <= current_year:
            raise ValueError(
                f"Choose seasons between {MIN_HISTORY_YEAR} and {current_year}."
            )

        self.data_directory.mkdir(parents=True, exist_ok=True)
        season_summaries = []
        all_unavailable = []
        total_lap_events = 0
        seasons_by_year = {}

        for prior_year in range(MIN_HISTORY_YEAR, start_year):
            path = self.data_directory / "seasons" / f"{prior_year}.json"
            if path.is_file():
                seasons_by_year[prior_year] = _read_json(path)

        for year in range(start_year, end_year + 1):
            season = self._load_or_fetch_season(year, current_year)
            seasons_by_year[year] = season
            lap_events, unavailable = self._collect_lap_data(
                year,
                season,
                current_year,
                include_details,
            )
            total_lap_events += lap_events
            all_unavailable.extend(
                {"year": year, **item} for item in unavailable
            )
            season_summaries.append({
                "year": year,
                "scheduled_races": len(season.get("schedule", [])),
                "completed_races": len(season.get("results", [])),
                "qualifying_records": sum(
                    len(race.get("QualifyingResults", []))
                    for race in season.get("qualifying", [])
                ),
                "sprint_records": sum(
                    len(race.get("SprintResults", []))
                    for race in season.get("sprints", [])
                ),
                "driver_standings": sum(
                    len(item.get("DriverStandings", []))
                    for item in season.get("standings", {}).get("drivers", [])
                ),
                "constructor_standings": sum(
                    len(item.get("ConstructorStandings", []))
                    for item in season.get("standings", {}).get("constructors", [])
                ),
                "pre_race_feature_rows": 0,
            })

        race_data_by_year = {}
        races_directory = self.data_directory / "races"
        for year in seasons_by_year:
            race_data_by_year[year] = {}
            if races_directory.is_dir():
                for path in races_directory.glob(f"{year}-*.json"):
                    event = _read_json(path)
                    race_data_by_year[year][str(event.get("round"))] = event

        for summary in season_summaries:
            year_events = race_data_by_year.get(summary["year"], {}).values()
            summary["lap_events_available"] = sum(
                bool(event.get("data", {}).get("laps")) for event in year_events
            )

        for index, year in enumerate(range(start_year, end_year + 1)):
            prior_years = [
                prior_year
                for prior_year in sorted(seasons_by_year)
                if prior_year < year
            ]
            season_summaries[index]["pre_race_feature_rows"] = self._write_features(
                year,
                seasons_by_year[year],
                [seasons_by_year[prior_year] for prior_year in prior_years],
                {
                    prior_year: race_data_by_year.get(prior_year, {})
                    for prior_year in prior_years
                },
                race_data_by_year.get(year, {}),
            )

        manifest = {
            "schema_version": 1,
            "generated_at": self.now().isoformat(),
            "start_year": start_year,
            "end_year": end_year,
            "sources": {
                "results_qualifying_standings": "https://api.jolpi.ca/ergast/f1/",
                "lap_timing_and_race_features": "https://api.openf1.org/v1/",
            },
            "lap_coverage": (
                f"OpenF1 race lap timing is collected from {OPENF1_START_YEAR} onward; "
                "Jolpica results, qualifying and standings cover every requested season."
            ),
            "seasons": season_summaries,
            "lap_events_fetched_this_update": total_lap_events,
            "lap_events_available": sum(
                summary["lap_events_available"] for summary in season_summaries
            ),
            "unavailable": all_unavailable,
        }
        _write_json(self.data_directory / "manifest.json", manifest)
        return manifest
