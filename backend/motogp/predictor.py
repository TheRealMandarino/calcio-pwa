import json
import math
from datetime import date
from pathlib import Path

from .updater import default_data_directory


def _integer(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _race_result(event):
    return _session_result(event, "RAC")


def _session_result(event, session_type):
    for session in event.get("sessions", []):
        if session.get("type") != session_type:
            continue
        classification = session.get("classification", [])
        if not classification:
            continue
        ranked = [
            (position, item)
            for item in classification
            if (position := _integer(item.get("position"))) is not None and position > 0
        ]
        if ranked:
            return min(ranked, key=lambda item: item[0])[1]
    return None


def _normalize_standings(standings):
    classification = standings.get("classification", [])
    if not isinstance(classification, list):
        return []
    normalized = []
    for row in classification:
        rider = row.get("rider") or {}
        if not rider.get("id"):
            continue
        normalized.append({
            "position": _integer(row.get("position")) or len(normalized) + 1,
            "rider_id": rider.get("id"),
            "name": rider.get("full_name") or "Pilota",
            "number": rider.get("number"),
            "country": (rider.get("country") or {}).get("name", ""),
            "team": (row.get("team") or {}).get("name", ""),
            "constructor": (row.get("constructor") or {}).get("name", ""),
            "points": _number(row.get("points")),
            "race_wins": _integer(row.get("race_wins")) or 0,
            "sprint_wins": _integer(row.get("sprint_wins")) or 0,
            "podiums": _integer(row.get("podiums")) or 0,
        })
    return sorted(normalized, key=lambda item: item["position"])


def _lap_time_seconds(value):
    if isinstance(value, dict):
        for key in ("lap", "time", "best", "best_lap"):
            parsed = _lap_time_seconds(value.get(key))
            if parsed is not None:
                return parsed
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if 30.0 <= number <= 300.0 else None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if ":" in text:
        minutes, seconds = text.split(":", 1)
        try:
            return float(minutes) * 60.0 + float(seconds)
        except (TypeError, ValueError):
            return None
    try:
        number = float(text)
        return number if 30.0 <= number <= 300.0 else None
    except (TypeError, ValueError):
        return None


def _result_time(result):
    for key in ("best_lap", "lap_time", "fastest_lap", "time", "lap"):
        parsed = _lap_time_seconds(result.get(key))
        if parsed is not None:
            return parsed
    return None


def _recent_form(history, rider_id, session_type=None, limit=5):
    positions = [
        row["position"]
        for row in history
        if row["rider_id"] == rider_id
        and (session_type is None or row["session_type"] == session_type)
        and row["position"] is not None
    ][-limit:]
    if not positions:
        return 0.35
    strengths = [max(0.05, (22.0 - position) / 21.0) for position in positions]
    return sum(strengths) / len(strengths)


def _circuit_form(history, rider_id, circuit_id, circuit_name, session_type=None):
    rows = [
        row for row in history
        if row["rider_id"] == rider_id
        and (row["circuit_id"] == circuit_id if circuit_id else row["circuit_name"] == circuit_name)
        and (session_type is None or row["session_type"] == session_type)
        and row["position"] is not None
    ][-8:]
    if not rows:
        return 0.35
    strengths = [max(0.05, (22.0 - row["position"]) / 21.0) for row in rows]
    return sum(strengths) / len(strengths)


def _build_rider_history(seasons):
    history = []
    for season in sorted(seasons, key=lambda item: int(item.get("year", 0))):
        for event in sorted(season.get("events", []), key=lambda item: str(item.get("date_start") or "")):
            circuit = event.get("circuit") or {}
            circuit_id = circuit.get("id")
            circuit_name = circuit.get("name") or ""
            for session in event.get("sessions", []):
                session_type = session.get("type")
                if session_type not in {"RAC", "SPR"}:
                    continue
                for result in session.get("classification", []):
                    rider = result.get("rider") or {}
                    position = _integer(result.get("position"))
                    if not rider.get("id") or position is None:
                        continue
                    history.append({
                        "rider_id": rider["id"],
                        "position": position,
                        "session_type": session_type,
                        "circuit_id": circuit_id,
                        "circuit_name": circuit_name,
                    })
    return history


def _build_predictions(standings, history, next_event, session_type):
    if not standings:
        return []

    circuit = next_event.get("circuit") or {}
    circuit_id = circuit.get("id")
    circuit_name = circuit.get("name") or ""

    pace_times = {}
    qualifying_positions = {}
    for session in next_event.get("sessions", []):
        session_code = str(session.get("type") or "").upper()
        if session_code not in {"FP1", "FP2", "FP3", "PRA", "PR", "Q1", "Q2"}:
            continue
        for result in session.get("classification", []):
            rider = result.get("rider") or {}
            rider_id = rider.get("id")
            if not rider_id:
                continue
            lap_time = _result_time(result)
            if lap_time is not None:
                pace_times.setdefault(rider_id, []).append(lap_time)
            if session_code in {"Q1", "Q2"}:
                position = _integer(result.get("position"))
                if position is not None:
                    qualifying_positions[rider_id] = min(
                        position,
                        qualifying_positions.get(rider_id, position),
                    )

    fastest_pace = min(
        (time for rider_times in pace_times.values() for time in rider_times),
        default=None,
    )
    max_points = max((rider["points"] for rider in standings), default=0.0)

    scored = []
    for rider in standings:
        rider_id = rider["rider_id"]
        points_strength = rider["points"] / max_points if max_points else 0.35
        wins = rider["race_wins"] if session_type == "RAC" else rider["sprint_wins"]
        wins_strength = min(1.0, wins / 6.0)
        podium_strength = min(1.0, rider["podiums"] / 10.0)
        standing_strength = 0.55 * points_strength + 0.30 * wins_strength + 0.15 * podium_strength

        recent_strength = _recent_form(history, rider_id, session_type, limit=5)
        if recent_strength == 0.35:
            recent_strength = _recent_form(history, rider_id, None, limit=5)
        circuit_strength = _circuit_form(
            history, rider_id, circuit_id, circuit_name, session_type
        )
        if circuit_strength == 0.35:
            circuit_strength = _circuit_form(
                history, rider_id, circuit_id, circuit_name, None
            )

        pace_strength = 0.35
        rider_times = pace_times.get(rider_id, [])
        if rider_times and fastest_pace:
            pace_strength = max(0.05, fastest_pace / min(rider_times))
        if rider_id in qualifying_positions:
            qualifying_strength = max(
                0.05,
                (22.0 - qualifying_positions[rider_id]) / 21.0,
            )
            pace_strength = qualifying_strength if pace_strength == 0.35 else 0.6 * pace_strength + 0.4 * qualifying_strength

        if session_type == "SPR":
            raw_score = (
                0.25 * standing_strength
                + 0.35 * recent_strength
                + 0.15 * circuit_strength
                + 0.20 * pace_strength
                + 0.05 * podium_strength
            )
        else:
            raw_score = (
                0.35 * standing_strength
                + 0.25 * recent_strength
                + 0.20 * circuit_strength
                + 0.15 * pace_strength
                + 0.05 * podium_strength
            )
        scored.append((rider, max(raw_score, 0.01) ** 1.25))

    total_score = sum(score for _, score in scored)
    predictions = []
    for rider, score in scored:
        probability = score / total_score if total_score else 1.0 / len(scored)
        predictions.append({
            **rider,
            "win_probability": round(probability * 100, 1),
            "odds": min(round(1 / probability, 2), 999.0),
        })
    predictions.sort(key=lambda item: item["win_probability"], reverse=True)
    for rank, rider in enumerate(predictions, start=1):
        rider["rank"] = rank
    return predictions


def build_motogp_view_payload(data_directory=None, today=None):
    base_dir = Path(data_directory or default_data_directory())
    manifest_path = base_dir / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"MotoGP manifest not found at {manifest_path}")
    with manifest_path.open(encoding="utf-8") as file:
        manifest = json.load(file)

    year = _integer(manifest.get("end_year"))
    if year is None:
        raise ValueError("MotoGP manifest has no current season.")
    season_path = base_dir / "seasons" / f"{year}.json"
    if not season_path.is_file():
        raise FileNotFoundError(f"MotoGP season {year} not found at {season_path}")
    with season_path.open(encoding="utf-8") as file:
        season = json.load(file)

    today = today or date.today().isoformat()
    events = sorted(
        season.get("events", []),
        key=lambda event: str(event.get("date_start") or ""),
    )
    standings = _normalize_standings(season.get("standings") or {})
    completed_event_ids = set()
    next_event = None
    calendar = []

    for event in events:
        event_status = str(event.get("status", "")).upper()
        winner = _race_result(event)
        sprint_winner = _session_result(event, "SPR")
        has_result = winner is not None
        is_completed = has_result
        is_cancelled = event_status in {"CANCELLED", "CANCELED"}
        is_unavailable = event_status == "FINISHED" and not has_result
        if is_completed:
            completed_event_ids.add(event.get("id"))
        elif not is_cancelled and not is_unavailable and next_event is None:
            event_end = str(event.get("date_end") or event.get("date_start") or "")
            if not event_end or event_end >= today or event_status not in {"", "SCHEDULED"}:
                next_event = event

        status = (
            "completed" if is_completed
            else "cancelled" if is_cancelled
            else "unavailable" if is_unavailable
            else "upcoming"
        )
        winner_rider = (winner or {}).get("rider") or {}
        winner_team = (winner or {}).get("team") or {}
        sprint_winner_rider = (sprint_winner or {}).get("rider") or {}
        sprint_winner_team = (sprint_winner or {}).get("team") or {}
        circuit = event.get("circuit") or {}
        calendar.append({
            "id": event.get("id"),
            "name": event.get("name") or event.get("sponsored_name") or "",
            "date": event.get("date_start"),
            "circuit": circuit.get("name", ""),
            "place": circuit.get("place", ""),
            "country": event.get("country", ""),
            "status": status,
            "winner": winner_rider.get("full_name") if winner else None,
            "winner_team": winner_team.get("name", "") if winner else "",
            "sprint_winner": sprint_winner_rider.get("full_name") if sprint_winner else None,
            "sprint_winner_team": sprint_winner_team.get("name", "") if sprint_winner else "",
        })

    leader = standings[0] if standings else None
    seasons_for_history = [season]
    for previous_year in range(manifest.get("start_year", year), year):
        previous_path = base_dir / "seasons" / f"{previous_year}.json"
        if previous_path.is_file():
            with previous_path.open(encoding="utf-8") as file:
                seasons_for_history.append(json.load(file))
    history = _build_rider_history(seasons_for_history)
    race_predictions = []
    sprint_predictions = []
    if next_event and standings:
        race_predictions = _build_predictions(
            standings, history, next_event, "RAC"
        )
        sprint_predictions = _build_predictions(
            standings, history, next_event, "SPR"
        )

    next_race = None
    if next_event:
        circuit = next_event.get("circuit") or {}
        next_race = {
            "name": next_event.get("name") or next_event.get("sponsored_name") or "",
            "date": next_event.get("date_start"),
            "date_end": next_event.get("date_end"),
            "circuit": circuit.get("name", ""),
            "place": circuit.get("place", ""),
            "country": next_event.get("country", ""),
            "predictions": race_predictions,
            "race_predictions": race_predictions,
            "sprint_predictions": sprint_predictions,
        }

    return {
        "season": year,
        "generated_at": manifest.get("generated_at"),
        "total_races": len(calendar),
        "completed_races": len(completed_event_ids),
        "remaining_races": max(0, len(calendar) - len(completed_event_ids)),
        "leader": leader,
        "next_race": next_race,
        "standings": standings,
        "calendar": calendar,
    }
