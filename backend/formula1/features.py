import math
from collections import defaultdict


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _integer(value):
    number = _number(value)
    return int(number) if number is not None and number.is_integer() else None


def _time_seconds(value):
    if not value:
        return None
    try:
        parts = [float(part) for part in str(value).split(":")]
    except ValueError:
        return None
    if not parts or any(not math.isfinite(part) or part < 0 for part in parts):
        return None
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + part
    return seconds


def _mean(values):
    valid_values = [value for value in values if value is not None]
    return sum(valid_values) / len(valid_values) if valid_values else None


def _race_telemetry_summary(race_data):
    data = race_data.get("data", {})
    drivers_by_number = {
        str(driver.get("driver_number")): driver
        for driver in data.get("drivers", [])
        if driver.get("driver_number") is not None
    }
    laps_by_code = defaultdict(list)
    for lap in data.get("laps", []):
        driver = drivers_by_number.get(str(lap.get("driver_number")), {})
        code = driver.get("name_acronym")
        duration = _number(lap.get("lap_duration"))
        if (
            code
            and duration is not None
            and not lap.get("is_pit_out_lap", False)
        ):
            laps_by_code[code].append(duration)

    pit_stops = defaultdict(int)
    for pit_stop in data.get("pit", []):
        driver = drivers_by_number.get(str(pit_stop.get("driver_number")), {})
        if driver.get("name_acronym"):
            pit_stops[driver["name_acronym"]] += 1

    compounds = defaultdict(list)
    for stint in data.get("stints", []):
        driver = drivers_by_number.get(str(stint.get("driver_number")), {})
        code = driver.get("name_acronym")
        compound = stint.get("compound")
        if code and compound and compound not in compounds[code]:
            compounds[code].append(compound)

    summaries = {}
    for code in set(laps_by_code) | set(pit_stops) | set(compounds):
        durations = laps_by_code.get(code, [])
        summaries[code] = {
            "mean_non_pit_out_lap_seconds": _mean(durations),
            "best_non_pit_out_lap_seconds": min(durations) if durations else None,
            "pit_stops": pit_stops.get(code, 0),
            "compounds": compounds.get(code, []),
        }
    return summaries


def _empty_telemetry_summary():
    return {
        "mean_non_pit_out_lap_seconds": None,
        "best_non_pit_out_lap_seconds": None,
        "pit_stops": None,
        "compounds": [],
    }


def _append_season_history(
    season,
    race_data_by_round,
    driver_history,
    constructor_history,
    circuit_history,
    telemetry_history,
):
    telemetry_by_round = {
        str(round_number): _race_telemetry_summary(race_data)
        for round_number, race_data in race_data_by_round.items()
    }
    for race in sorted(
        season.get("results", []),
        key=lambda item: _integer(item.get("round")) or 0,
    ):
        circuit_id = race.get("Circuit", {}).get("circuitId", "")
        round_telemetry = telemetry_by_round.get(str(race.get("round")), {})
        constructor_positions = defaultdict(list)

        for result in race.get("Results", []):
            driver = result.get("Driver", {})
            constructor_id = result.get("Constructor", {}).get("constructorId")
            driver_id = driver.get("driverId")
            if not driver_id:
                continue
            position = _integer(result.get("position"))
            driver_history[driver_id].append({"position": position})
            circuit_history[(driver_id, circuit_id)].append(position)
            constructor_positions[constructor_id].append(position)
            driver_telemetry = round_telemetry.get(driver.get("code"))
            telemetry_history[driver_id].append(
                driver_telemetry or _empty_telemetry_summary()
            )

        for constructor_id, positions in constructor_positions.items():
            constructor_history[constructor_id].append({
                "position": _mean(positions),
            })


def build_pre_race_features(
    season,
    race_data_by_round=None,
    prior_seasons=None,
    prior_race_data_by_season=None,
):
    """Build per-driver rows using only information available before each race."""
    race_data_by_round = race_data_by_round or {}
    prior_seasons = prior_seasons or []
    prior_race_data_by_season = prior_race_data_by_season or {}
    qualifying_by_round = {
        str(race.get("round")): {
            result.get("Driver", {}).get("driverId"): result
            for result in race.get("QualifyingResults", [])
            if result.get("Driver", {}).get("driverId")
        }
        for race in season.get("qualifying", [])
    }
    sprint_by_round = {
        str(race.get("round")): {
            result.get("Driver", {}).get("driverId"): result
            for result in race.get("SprintResults", [])
            if result.get("Driver", {}).get("driverId")
        }
        for race in season.get("sprints", [])
    }
    telemetry_by_round = {
        str(round_number): _race_telemetry_summary(race_data)
        for round_number, race_data in race_data_by_round.items()
    }

    driver_history = defaultdict(list)
    constructor_points = defaultdict(float)
    driver_points = defaultdict(float)
    constructor_history = defaultdict(list)
    circuit_history = defaultdict(list)
    telemetry_history = defaultdict(list)
    feature_rows = []

    for prior_season in sorted(prior_seasons, key=lambda item: item.get("year", 0)):
        prior_year = prior_season.get("year")
        _append_season_history(
            prior_season,
            prior_race_data_by_season.get(prior_year, {}),
            driver_history,
            constructor_history,
            circuit_history,
            telemetry_history,
        )

    for race in sorted(
        season.get("results", []),
        key=lambda item: _integer(item.get("round")) or 0,
    ):
        round_number = str(race.get("round", ""))
        qualifying = qualifying_by_round.get(round_number, {})
        sprint_results = sprint_by_round.get(round_number, {})
        circuit = race.get("Circuit", {})
        circuit_id = circuit.get("circuitId", "")
        telemetry = telemetry_by_round.get(round_number, {})
        constructor_positions_this_race = defaultdict(list)
        driver_points_this_race = defaultdict(float)
        constructor_points_this_race = defaultdict(float)

        for result in race.get("Results", []):
            driver = result.get("Driver", {})
            constructor = result.get("Constructor", {})
            driver_id = driver.get("driverId")
            constructor_id = constructor.get("constructorId")
            if not driver_id:
                continue

            prior_driver_results = driver_history[driver_id]
            prior_constructor_results = constructor_history[constructor_id]
            prior_circuit_results = circuit_history[(driver_id, circuit_id)]
            qualifying_result = qualifying.get(driver_id, {})
            sprint_result = sprint_results.get(driver_id, {})
            qualifying_times = [
                _time_seconds(qualifying_result.get(key))
                for key in ("Q1", "Q2", "Q3")
            ]
            valid_qualifying_times = [value for value in qualifying_times if value is not None]
            code = driver.get("code")
            prior_telemetry = telemetry_history[driver_id][-3:]
            current_result_telemetry = telemetry.get(code, {})

            feature_rows.append({
                "season": season.get("year"),
                "round": _integer(race.get("round")),
                "race_name": race.get("raceName"),
                "date": race.get("date"),
                "circuit_id": circuit_id,
                "driver_id": driver_id,
                "driver_code": code,
                "constructor_id": constructor_id,
                "features": {
                    "qualifying_position": _integer(qualifying_result.get("position")),
                    "qualifying_best_lap_seconds": (
                        min(valid_qualifying_times) if valid_qualifying_times else None
                    ),
                    "sprint_position_before_race": _integer(
                        sprint_result.get("position")
                    ),
                    "sprint_points_before_race": (
                        _number(sprint_result.get("points")) or 0.0
                    ),
                    "grid_position": _integer(result.get("grid")),
                    "driver_races_before": len(prior_driver_results),
                    "driver_points_before": (
                        driver_points[driver_id]
                        + (_number(sprint_result.get("points")) or 0.0)
                    ),
                    "constructor_points_before": (
                        constructor_points[constructor_id]
                        + sum(
                            _number(item.get("points")) or 0.0
                            for item in sprint_results.values()
                            if item.get("Constructor", {}).get("constructorId")
                            == constructor_id
                        )
                    ),
                    "driver_avg_finish_last_3": _mean(
                        [item["position"] for item in prior_driver_results[-3:]]
                    ),
                    "driver_avg_finish_last_5": _mean(
                        [item["position"] for item in prior_driver_results[-5:]]
                    ),
                    "constructor_avg_finish_last_3": _mean(
                        [item["position"] for item in prior_constructor_results[-3:]]
                    ),
                    "circuit_races_before": len(prior_circuit_results),
                    "circuit_avg_finish_before": _mean(prior_circuit_results),
                    "prior_non_pit_out_lap_mean_seconds_last_3": _mean(
                        [item["mean_non_pit_out_lap_seconds"] for item in prior_telemetry]
                    ),
                    "prior_best_non_pit_out_lap_seconds_last_3": _mean(
                        [item["best_non_pit_out_lap_seconds"] for item in prior_telemetry]
                    ),
                    "prior_pit_stops_avg_last_3": _mean(
                        [item["pit_stops"] for item in prior_telemetry]
                    ),
                    "prior_compounds_last_3": [
                        item["compounds"] for item in prior_telemetry if item["compounds"]
                    ],
                },
                "target": {
                    "finish_position": _integer(result.get("position")),
                    "points": _number(result.get("points")),
                    "status": result.get("status"),
                    "fastest_lap_seconds": _time_seconds(
                        result.get("FastestLap", {}).get("Time", {}).get("time")
                    ),
                },
            })

            position = _integer(result.get("position"))
            points = _number(result.get("points")) or 0.0
            constructor_positions_this_race[constructor_id].append(position)
            driver_history[driver_id].append({"position": position})
            circuit_history[(driver_id, circuit_id)].append(position)
            driver_points_this_race[driver_id] += points
            constructor_points_this_race[constructor_id] += points
            telemetry_history[driver_id].append(
                current_result_telemetry or _empty_telemetry_summary()
            )

        for driver_id, points in driver_points_this_race.items():
            driver_points[driver_id] += points
        for constructor_id, points in constructor_points_this_race.items():
            constructor_points[constructor_id] += points
        for driver_id, sprint_result in sprint_results.items():
            points = _number(sprint_result.get("points")) or 0.0
            driver_points[driver_id] += points
            sprint_constructor_id = sprint_result.get("Constructor", {}).get(
                "constructorId"
            )
            if sprint_constructor_id:
                constructor_points[sprint_constructor_id] += points

        for constructor_id, positions in constructor_positions_this_race.items():
            constructor_history[constructor_id].append({
                "position": _mean(positions),
            })

    return feature_rows
