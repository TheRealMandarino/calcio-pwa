import json
import os
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

from .clients import DataSourceError, MotoGPClient
from .features import build_pre_race_features


MIN_HISTORY_YEAR = 2016
MOTOGP_SOURCE = "https://api.motogp.pulselive.com/motogp/v1"


def default_data_directory():
    configured = os.environ.get("CALCIOPWA_MOTOGP_DATA")
    if configured:
        return Path(configured)
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / ".cache"
    return base / "CalcioPWA" / "motogp"


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


def _integer(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _has_ranked_result(event, session_type):
    return any(
        session.get("type") == session_type
        and any(
            (_integer(result.get("position")) or 0) > 0
            for result in session.get("classification", [])
        )
        for session in event.get("sessions", [])
    )


def _normalized_event(event):
    circuit = event.get("circuit") or {}
    country = event.get("country")
    if isinstance(country, dict):
        country = country.get("name") or country.get("iso")
    return {
        "id": event.get("id"),
        "name": event.get("name") or event.get("sponsored_name") or "",
        "sponsored_name": event.get("sponsored_name") or "",
        "short_name": event.get("short_name") or "",
        "date_start": event.get("date_start"),
        "date_end": event.get("date_end"),
        "status": event.get("status") or "",
        "test": bool(event.get("test")),
        "country": country or "",
        "circuit": {
            "id": circuit.get("id"),
            "name": circuit.get("name") or "",
            "place": circuit.get("place") or "",
            "nation": circuit.get("nation") or "",
        },
        "sessions": [],
    }


def _normalized_session(session, classification):
    return {
        "id": session.get("id"),
        "type": session.get("type") or "",
        "number": session.get("number"),
        "date": session.get("date"),
        "status": session.get("status") or "",
        "condition": session.get("condition") or {},
        "classification": classification,
    }


class ArchiveUpdater:
    def __init__(self, data_directory=None, client=None, now=None):
        self.data_directory = Path(data_directory or default_data_directory())
        self.client = client or MotoGPClient()
        self.now = now or date.today

    def _load_or_fetch_season(self, year, season_id, current_year):
        path = self.data_directory / "seasons" / f"{year}.json"
        if path.is_file() and year != current_year:
            return _read_json(path)

        categories = self.client.fetch_categories(season_id)
        category = next(
            (
                item for item in categories
                if str(item.get("name", "")).replace("™", "").strip().casefold() == "motogp"
            ),
            None,
        )
        if category is None or not category.get("id"):
            raise DataSourceError(f"MotoGP category is unavailable for season {year}.")

        events = []
        unavailable = []
        for source_event in self.client.fetch_events(season_id):
            event = _normalized_event(source_event)
            if (
                event["test"]
                or not event["id"]
                or not event["short_name"]
                or not event["circuit"]["id"]
            ):
                continue

            try:
                sessions = self.client.fetch_sessions(event["id"], category["id"])
            except DataSourceError as error:
                if error.status_code != 404:
                    raise
                unavailable.append({
                    "event": event["name"],
                    "session": "all",
                    "reason": str(error),
                })
                events.append(event)
                continue

            for source_session in sessions:
                session_type = source_session.get("type")
                if session_type not in {
                    "RAC",
                    "SPR",
                    "FP1",
                    "FP2",
                    "FP3",
                    "PRA",
                    "PR",
                    "Q1",
                    "Q2",
                    "WUP",
                }:
                    continue
                if str(source_session.get("status", "")).upper() != "FINISHED":
                    continue
                session_id = source_session.get("id")
                if not session_id:
                    continue
                try:
                    payload = self.client.fetch_classification(session_id)
                except DataSourceError as error:
                    if error.status_code != 404:
                        raise
                    unavailable.append({
                        "event": event["name"],
                        "session": session_type,
                        "reason": str(error),
                    })
                    continue
                event["sessions"].append(
                    _normalized_session(
                        source_session,
                        payload.get("classification", []),
                    )
                )
            events.append(event)

        standings = self.client.fetch_standings(season_id, category["id"])
        return {
            "schema_version": 1,
            "year": year,
            "source": "MotoGP public results API",
            "category": {"id": category["id"], "name": category.get("name", "MotoGP")},
            "schedule": [
                {key: value for key, value in event.items() if key != "sessions"}
                for event in events
            ],
            "events": events,
            "standings": standings,
            "unavailable": unavailable,
        }

    def update(self, start_year=MIN_HISTORY_YEAR, end_year=None):
        current_year = self.now().year
        end_year = current_year if end_year is None else end_year
        if type(start_year) is not int or type(end_year) is not int:
            raise ValueError("Season years must be integers.")
        if not MIN_HISTORY_YEAR <= start_year <= end_year <= current_year:
            raise ValueError(
                f"Choose seasons between {MIN_HISTORY_YEAR} and {current_year}."
            )

        self.data_directory.mkdir(parents=True, exist_ok=True)
        seasons = self.client.fetch_seasons()
        season_ids = {
            _integer(item.get("year")): item.get("id")
            for item in seasons
            if isinstance(item, dict) and _integer(item.get("year")) is not None and item.get("id")
        }
        seasons_by_year = {}
        summaries = []
        unavailable = []

        for year in range(MIN_HISTORY_YEAR, start_year):
            cached_path = self.data_directory / "seasons" / f"{year}.json"
            if cached_path.is_file():
                seasons_by_year[year] = _read_json(cached_path)

        for year in range(start_year, end_year + 1):
            season_id = season_ids.get(year)
            if not season_id:
                raise DataSourceError(f"MotoGP season {year} is missing from the season list.")
            season = self._load_or_fetch_season(year, season_id, current_year)
            _write_json(self.data_directory / "seasons" / f"{year}.json", season)
            seasons_by_year[year] = season
            unavailable.extend(
                {"year": year, **item} for item in season.get("unavailable", [])
            )
            completed_races = sum(
                _has_ranked_result(event, "RAC")
                for event in season.get("events", [])
            )
            completed_sprints = sum(
                _has_ranked_result(event, "SPR")
                for event in season.get("events", [])
            )
            summaries.append({
                "year": year,
                "scheduled_events": len(season.get("schedule", [])),
                "completed_races": completed_races,
                "completed_sprints": completed_sprints,
                "standings_riders": len(
                    season.get("standings", {}).get("classification", [])
                ),
                "pre_race_feature_rows": 0,
            })

        for index, year in enumerate(range(start_year, end_year + 1)):
            previous_seasons = [
                seasons_by_year[previous_year]
                for previous_year in sorted(seasons_by_year)
                if previous_year < year
            ]
            features = build_pre_race_features(
                seasons_by_year[year],
                previous_seasons,
            )
            _write_json(self.data_directory / "features" / f"{year}.json", {
                "schema_version": 1,
                "year": year,
                "feature_timing": "All predictors use sessions completed before the target session.",
                "rows": features,
            })
            summaries[index]["pre_race_feature_rows"] = len(features)

        manifest = {
            "schema_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "start_year": start_year,
            "end_year": end_year,
            "sources": {"results": MOTOGP_SOURCE},
            "coverage": "MotoGP class race and sprint classifications; sprint data from 2023 onward.",
            "seasons": summaries,
            "unavailable": unavailable,
        }
        _write_json(self.data_directory / "manifest.json", manifest)
        return manifest


def export_static_data(data_directory=None, docs_directory=None):
    from .predictor import build_motogp_view_payload

    base = (
        Path(docs_directory)
        if docs_directory
        else Path(__file__).resolve().parents[2] / "docs" / "motogp"
    )
    payload = build_motogp_view_payload(data_directory)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    _write_json(base / "motogp-data.json", payload)
    (base / "motogp-data.js").write_text(
        "window.__MOTOGP_DATA__ = " + encoded + ";\n",
        encoding="utf-8",
    )
    return payload
