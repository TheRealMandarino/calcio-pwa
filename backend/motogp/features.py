from collections import defaultdict


def _number(value):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _points(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _sessions(season):
    rows = []
    for event in season.get("events", []):
        for session in event.get("sessions", []):
            if session.get("type") not in {"RAC", "SPR"}:
                continue
            classification = session.get("classification", [])
            if not isinstance(classification, list):
                continue
            rows.append((
                str(session.get("date") or event.get("date_start") or ""),
                0 if session.get("type") == "SPR" else 1,
                event,
                session,
                classification,
            ))
    return sorted(rows, key=lambda row: (row[0], row[1]))


def build_pre_race_features(season, prior_seasons=()):
    target_year = int(season["year"])
    seasons = sorted(
        [*prior_seasons, season],
        key=lambda item: int(item.get("year", 0)),
    )
    rider_history = defaultdict(list)
    team_history = defaultdict(list)
    current_year_points = defaultdict(float)
    current_team_points = defaultdict(float)
    active_year = None
    feature_rows = []

    for historical_season in seasons:
        year = int(historical_season.get("year", 0))
        if year != active_year:
            current_year_points.clear()
            current_team_points.clear()
            active_year = year

        for _, _, event, session, classification in _sessions(historical_season):
            circuit = event.get("circuit") or {}
            circuit_id = circuit.get("id") or circuit.get("name")
            for result in classification:
                rider = result.get("rider") or {}
                rider_id = rider.get("id")
                if not rider_id:
                    continue
                team = result.get("team") or {}
                team_id = team.get("id") or team.get("name") or ""
                finish_position = _number(result.get("position"))

                if year == target_year:
                    previous_races = rider_history[rider_id]
                    recent_finishes = [
                        item["finish_position"]
                        for item in previous_races[-3:]
                        if item["finish_position"] is not None
                    ]
                    circuit_finishes = [
                        item["finish_position"]
                        for item in previous_races
                        if item["circuit_id"] == circuit_id
                        and item["finish_position"] is not None
                    ]
                    recent_team_results = [
                        item["finish_position"]
                        for item in team_history[team_id][-3:]
                        if item["finish_position"] is not None
                    ] if team_id else []
                    feature_rows.append({
                        "year": target_year,
                        "event_id": event.get("id"),
                        "event_name": event.get("name", ""),
                        "circuit": circuit.get("name", ""),
                        "session_id": session.get("id"),
                        "session_type": session.get("type"),
                        "session_date": session.get("date"),
                        "rider_id": rider_id,
                        "rider_name": rider.get("full_name", ""),
                        "team": team.get("name", ""),
                        "features": {
                            "rider_sessions_before": len(previous_races),
                            "rider_points_before": current_year_points[rider_id],
                            "team_points_before": current_team_points[team_id] if team_id else 0,
                            "rider_avg_finish_last_3": (
                                sum(recent_finishes) / len(recent_finishes)
                                if recent_finishes else None
                            ),
                            "team_avg_finish_last_3": (
                                sum(recent_team_results) / len(recent_team_results)
                                if recent_team_results else None
                            ),
                            "circuit_sessions_before": len(circuit_finishes),
                            "circuit_avg_finish_before": (
                                sum(circuit_finishes) / len(circuit_finishes)
                                if circuit_finishes else None
                            ),
                        },
                        "target": {
                            "finish_position": finish_position,
                            "points": _points(result.get("points")),
                            "status": result.get("status", ""),
                        },
                    })

                history_item = {
                    "finish_position": finish_position,
                    "circuit_id": circuit_id,
                }
                rider_history[rider_id].append(history_item)
                if team_id:
                    team_history[team_id].append(history_item)
                current_year_points[rider_id] += _points(result.get("points"))
                if team_id:
                    current_team_points[team_id] += _points(result.get("points"))

    return feature_rows
