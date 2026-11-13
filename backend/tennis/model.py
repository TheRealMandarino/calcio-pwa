import csv
import io
import math
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone


SHRINKAGE_POINTS = 150
MAX_FATIGUE_SHIFT = 0.02
SURFACE_DISPLAY_NAMES = {
    "clay": "terra battuta",
    "grass": "erba",
    "hard_indoor": "cemento indoor",
    "hard_outdoor": "cemento outdoor",
    "hard_unspecified": "cemento non specificato",
}


class TennisModelError(ValueError):
    pass


def normalize_name(value):
    stripped = unicodedata.normalize("NFD", str(value or ""))
    stripped = "".join(
        character for character in stripped
        if unicodedata.category(character) != "Mn"
    )
    return re.sub(r"[^a-z0-9]", "", stripped.lower())


def normalize_surface(value):
    surface = str(value or "").strip().lower()
    if surface in {"clay", "terra", "terra battuta"}:
        return "clay"
    if surface in {"grass", "erba"}:
        return "grass"
    if re.search(r"^(hard|cement|cemento)\s*(indoor|in)\b", surface) or re.search(
        r"^(indoor)\s*(hard|cement|cemento)\b", surface
    ):
        return "hard_indoor"
    if re.search(r"^(hard|cement|cemento)\s*(outdoor|out)\b", surface) or re.search(
        r"^(outdoor)\s*(hard|cement|cemento)\b", surface
    ):
        return "hard_outdoor"
    if surface in {"hard", "cement", "cemento"}:
        return "hard_unspecified"
    return None


def surface_display_name(surface):
    return SURFACE_DISPLAY_NAMES.get(surface, surface)


def parse_date(value):
    raw = str(value or "").strip()
    if re.fullmatch(r"\d{8}", raw):
        try:
            return datetime.strptime(raw, "%Y%m%d").date()
        except ValueError:
            return None
    match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", raw)
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def _normalize_header(header, index):
    if index == 0:
        header = header.replace("\ufeff", "")
    return re.sub(r"[^a-z0-9]", "", header.strip().lower())


def parse_csv(text):
    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        headers = next(reader)
    except StopIteration as error:
        raise TennisModelError("Il CSV non contiene righe di match.") from error
    normalized = [
        _normalize_header(header, index)
        for index, header in enumerate(headers)
    ]
    if len(set(normalized)) != len(normalized):
        raise TennisModelError(
            "Il CSV contiene intestazioni duplicate dopo la normalizzazione."
        )
    rows = []
    for values in reader:
        if not any(str(value).strip() for value in values):
            continue
        record = {}
        for index, header in enumerate(normalized):
            record[header] = values[index].strip() if index < len(values) else ""
        rows.append(record)
    if not rows:
        raise TennisModelError("Il CSV non contiene righe di match.")
    return rows


def read_number(record, key):
    raw = record.get(key)
    if raw is None or raw == "":
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value >= 0 else None


def _service_stats(record, side, strict=True):
    service_points = read_number(record, f"{side}svpt")
    first_won = read_number(record, f"{side}1stwon")
    second_won = read_number(record, f"{side}2ndwon")
    service_points_won = (
        None if first_won is None or second_won is None else first_won + second_won
    )
    break_points_saved = read_number(record, f"{side}bpsaved")
    break_points_faced = read_number(record, f"{side}bpfaced")

    if (
        service_points_won is not None
        and service_points is not None
        and service_points_won > service_points
    ):
        if strict:
            raise TennisModelError(
                "Il CSV contiene punti vinti al servizio superiori ai punti giocati."
            )
        service_points = None
        service_points_won = None
    if (
        break_points_saved is not None
        and break_points_faced is not None
        and break_points_saved > break_points_faced
    ):
        if strict:
            raise TennisModelError(
                "Il CSV contiene break point salvati superiori a quelli affrontati."
            )
        break_points_saved = None
        break_points_faced = None

    return {
        "service_points": service_points,
        "service_points_won": service_points_won,
        "break_points_saved": break_points_saved,
        "break_points_faced": break_points_faced,
    }


def _side_stats(player_service, opponent_service, tie_breaks, tie_breaks_won):
    return_points = None
    return_points_total = opponent_service["service_points"]
    if (
        opponent_service["service_points"] is not None
        and opponent_service["service_points_won"] is not None
    ):
        return_points = (
            opponent_service["service_points"] - opponent_service["service_points_won"]
        )
    return {
        "service_points": player_service["service_points"],
        "service_points_won": player_service["service_points_won"],
        "return_points": return_points,
        "return_points_total": return_points_total,
        "break_points_saved": player_service["break_points_saved"],
        "break_points_faced": player_service["break_points_faced"],
        "tie_breaks": tie_breaks,
        "tie_breaks_won": tie_breaks_won,
    }


def parse_matches(text, strict=True):
    rows = parse_csv(text)
    required = ["surface", "winnername", "losername"]
    headers = set(rows[0])
    missing = [header for header in required if header not in headers]
    if missing:
        raise TennisModelError(
            f"Colonne obbligatorie mancanti: {', '.join(missing)}."
        )

    matches = []
    for row in rows:
        winner_name = row.get("winnername", "")
        loser_name = row.get("losername", "")
        if (
            not winner_name
            or not loser_name
            or normalize_name(winner_name) == normalize_name(loser_name)
        ):
            continue
        winner_service = _service_stats(row, "w", strict=strict)
        loser_service = _service_stats(row, "l", strict=strict)
        score = str(row.get("score") or "")
        set_matches = re.findall(r"(\d+)\s*-\s*(\d+)(?:\(\d+\))?", score)
        tie_breaks = [
            int(games_a) == 7
            for games_a, games_b in set_matches
            if (int(games_a) == 7 and int(games_b) == 6)
            or (int(games_a) == 6 and int(games_b) == 7)
        ]
        matches.append({
            "winner_name": winner_name,
            "loser_name": loser_name,
            "winner_key": normalize_name(winner_name),
            "loser_key": normalize_name(loser_name),
            "best_of": read_number(row, "bestof"),
            "surface": normalize_surface(row.get("surface")),
            "date": parse_date(row.get("tourneydate") or row.get("date")),
            "minutes": read_number(row, "minutes"),
            "tournament": row.get("tourneyname") or "",
            "score": score,
            "winner": _side_stats(
                winner_service,
                loser_service,
                len(tie_breaks),
                sum(1 for won in tie_breaks if won),
            ),
            "loser": _side_stats(
                loser_service,
                winner_service,
                len(tie_breaks),
                sum(1 for won in tie_breaks if not won),
            ),
        })
    if not matches:
        raise TennisModelError("Non sono stati trovati match validi nel CSV.")
    return matches


def empty_stats():
    return {
        "matches": 0,
        "wins": 0,
        "service_points": 0,
        "service_points_won": 0,
        "return_points": 0,
        "return_points_total": 0,
        "break_points_saved": 0,
        "break_points_faced": 0,
        "break_points_converted": 0,
        "break_points_conversion_chances": 0,
        "tie_breaks": 0,
        "tie_breaks_won": 0,
    }


def add_player_match(stats, player, opponent):
    stats["matches"] += 1
    if player["service_points"] is not None and player["service_points_won"] is not None:
        stats["service_points"] += player["service_points"]
        stats["service_points_won"] += player["service_points_won"]
    if player["return_points"] is not None and player["return_points_total"] is not None:
        stats["return_points"] += player["return_points"]
        stats["return_points_total"] += player["return_points_total"]
    if (
        player["break_points_saved"] is not None
        and player["break_points_faced"] is not None
    ):
        stats["break_points_saved"] += player["break_points_saved"]
        stats["break_points_faced"] += player["break_points_faced"]
    if (
        opponent["break_points_saved"] is not None
        and opponent["break_points_faced"] is not None
    ):
        stats["break_points_converted"] += max(
            0, opponent["break_points_faced"] - opponent["break_points_saved"]
        )
        stats["break_points_conversion_chances"] += opponent["break_points_faced"]
    stats["tie_breaks"] += player["tie_breaks"]
    stats["tie_breaks_won"] += player["tie_breaks_won"]


def hold_probability(point_win_probability):
    p = min(0.999999, max(0.000001, point_win_probability))
    q = 1 - p
    return (
        p ** 4 * (1 + 4 * q + 10 * q ** 2)
        + 20 * p ** 3 * q ** 3 * (p ** 2 / (p ** 2 + q ** 2))
    )


def _finalize_player(player, global_serve, global_return):
    player["serve_point_rate"] = (
        None
        if global_serve is None or not player["service_points"]
        else (player["service_points_won"] + SHRINKAGE_POINTS * global_serve)
        / (player["service_points"] + SHRINKAGE_POINTS)
    )
    player["return_point_rate"] = (
        None
        if global_return is None or not player["return_points_total"]
        else (player["return_points"] + SHRINKAGE_POINTS * global_return)
        / (player["return_points_total"] + SHRINKAGE_POINTS)
    )
    player["hold_rate"] = (
        None
        if player["serve_point_rate"] is None
        else hold_probability(player["serve_point_rate"])
    )
    player["break_rate"] = (
        None
        if player["return_point_rate"] is None
        else 1 - hold_probability(1 - player["return_point_rate"])
    )
    player["win_rate"] = (
        player["wins"] / player["matches"] if player["matches"] else None
    )
    player["break_point_save_rate"] = (
        player["break_points_saved"] / player["break_points_faced"]
        if player["break_points_faced"]
        else None
    )
    player["break_point_conversion_rate"] = (
        player["break_points_converted"] / player["break_points_conversion_chances"]
        if player["break_points_conversion_chances"]
        else None
    )
    player["clutch_factor"] = (
        None
        if player["break_point_save_rate"] is None
        or player["break_point_conversion_rate"] is None
        else (player["break_point_save_rate"] + player["break_point_conversion_rate"]) / 2
    )
    player["tie_break_win_rate"] = (
        player["tie_breaks_won"] / player["tie_breaks"] if player["tie_breaks"] else None
    )
    return player


def summarize_surface(matches, surface):
    selected = [match for match in matches if match["surface"] == surface]
    players = {}
    total = {
        "service_points": 0,
        "service_points_won": 0,
        "return_points": 0,
        "return_points_total": 0,
    }

    def get_player(name, key):
        if key not in players:
            players[key] = {"name": name, "key": key, **empty_stats()}
        return players[key]

    for match in selected:
        winner = get_player(match["winner_name"], match["winner_key"])
        loser = get_player(match["loser_name"], match["loser_key"])
        winner["wins"] += 1
        add_player_match(winner, match["winner"], match["loser"])
        add_player_match(loser, match["loser"], match["winner"])
        for player in (match["winner"], match["loser"]):
            if (
                player["service_points"] is not None
                and player["service_points_won"] is not None
            ):
                total["service_points"] += player["service_points"]
                total["service_points_won"] += player["service_points_won"]
            if (
                player["return_points"] is not None
                and player["return_points_total"] is not None
            ):
                total["return_points"] += player["return_points"]
                total["return_points_total"] += player["return_points_total"]

    global_serve = (
        total["service_points_won"] / total["service_points"]
        if total["service_points"]
        else None
    )
    global_return = (
        total["return_points"] / total["return_points_total"]
        if total["return_points_total"]
        else None
    )
    for player in players.values():
        _finalize_player(player, global_serve, global_return)
    return {
        "matches": selected,
        "players": players,
        "global_serve": global_serve,
        "global_return": global_return,
    }


def player_surface_counts(matches, player_name):
    player_key = normalize_name(player_name)
    counts = {}
    for match in matches:
        if match["winner_key"] != player_key and match["loser_key"] != player_key:
            continue
        if not match["surface"]:
            continue
        counts[match["surface"]] = counts.get(match["surface"], 0) + 1
    return counts


def recent_load(matches, player_key, reference_date):
    start = reference_date - timedelta(days=21)
    recent = [
        match for match in matches
        if (match["winner_key"] == player_key or match["loser_key"] == player_key)
        and match["date"] is not None
        and start <= match["date"] < reference_date
    ]
    return {
        "matches": len(recent),
        "minutes": sum(match["minutes"] or 0 for match in recent),
    }


def head_to_head(surface_matches, player_a, player_b):
    h2h = [
        match for match in surface_matches
        if (
            match["winner_key"] == player_a["key"]
            and match["loser_key"] == player_b["key"]
        )
        or (
            match["winner_key"] == player_b["key"]
            and match["loser_key"] == player_a["key"]
        )
    ]
    return {
        "matches": len(h2h),
        "wins_a": sum(1 for match in h2h if match["winner_key"] == player_a["key"]),
        "wins_b": sum(1 for match in h2h if match["winner_key"] == player_b["key"]),
    }


def tie_break_win_probability(point_a, point_b, first_server_a):
    def point_win(a_serves):
        return point_a if a_serves else 1 - point_b

    probability_a_on_two = point_win(not first_server_a) * point_win(first_server_a)
    probability_b_on_two = (1 - point_win(not first_server_a)) * (
        1 - point_win(first_server_a)
    )
    deuce_win_a = probability_a_on_two / (probability_a_on_two + probability_b_on_two)
    memo = {}

    def visit(score_a, score_b, point):
        if score_a >= 7 and score_a - score_b >= 2:
            return 1
        if score_b >= 7 and score_b - score_a >= 2:
            return 0
        if score_a >= 6 and score_b >= 6:
            return deuce_win_a
        key = (score_a, score_b, point)
        if key in memo:
            return memo[key]
        if point == 0:
            server_a = first_server_a
        else:
            server_a = (
                not first_server_a
                if ((point - 1) // 2) % 2 == 0
                else first_server_a
            )
        win_point_a = point_win(server_a)
        result = (
            win_point_a * visit(score_a + 1, score_b, point + 1)
            + (1 - win_point_a) * visit(score_a, score_b + 1, point + 1)
        )
        memo[key] = result
        return result

    return visit(0, 0, 0)


def get_set_distribution(point_a, point_b, first_server_a):
    hold_a = hold_probability(point_a)
    hold_b = hold_probability(point_b)
    memo = {}

    def play(games_a, games_b, server_a):
        if games_a == 6 and games_b == 6:
            tie_break_a = tie_break_win_probability(point_a, point_b, first_server_a)
            return [
                {"winner_a": True, "games": 13, "tie_break": True, "probability": tie_break_a},
                {
                    "winner_a": False,
                    "games": 13,
                    "tie_break": True,
                    "probability": 1 - tie_break_a,
                },
            ]
        if (games_a >= 6 or games_b >= 6) and abs(games_a - games_b) >= 2:
            return [{
                "winner_a": games_a > games_b,
                "games": games_a + games_b,
                "tie_break": False,
                "probability": 1,
            }]
        key = (games_a, games_b, server_a)
        if key in memo:
            return memo[key]
        game_a = hold_a if server_a else 1 - hold_b
        outcomes = []
        for outcome in play(games_a + 1, games_b, not server_a):
            copied = dict(outcome)
            copied["probability"] *= game_a
            outcomes.append(copied)
        for outcome in play(games_a, games_b + 1, not server_a):
            copied = dict(outcome)
            copied["probability"] *= 1 - game_a
            outcomes.append(copied)
        memo[key] = outcomes
        return outcomes

    return play(0, 0, first_server_a)


def simulate_match(point_a, point_b, best_of, line):
    sets_to_win = math.ceil(best_of / 2)
    states = {
        (0, 0, 0, 0): {
            "sets_a": 0,
            "sets_b": 0,
            "games": 0,
            "tie_break": False,
            "probability": 1,
        }
    }
    finals = []
    set_distributions = {}

    for set_index in range(best_of):
        nxt = {}
        for state in states.values():
            if state["sets_a"] == sets_to_win or state["sets_b"] == sets_to_win:
                finals.append(state)
                continue
            first_server_a = set_index % 2 == 0
            set_key = "A" if first_server_a else "B"
            if set_key not in set_distributions:
                set_distributions[set_key] = get_set_distribution(
                    point_a, point_b, first_server_a
                )
            for set_outcome in set_distributions[set_key]:
                updated = {
                    "sets_a": state["sets_a"] + (1 if set_outcome["winner_a"] else 0),
                    "sets_b": state["sets_b"] + (0 if set_outcome["winner_a"] else 1),
                    "games": state["games"] + set_outcome["games"],
                    "tie_break": state["tie_break"] or set_outcome["tie_break"],
                    "probability": state["probability"] * set_outcome["probability"],
                }
                key = (
                    updated["sets_a"],
                    updated["sets_b"],
                    updated["games"],
                    int(updated["tie_break"]),
                )
                previous = nxt.get(key)
                if previous:
                    previous["probability"] += updated["probability"]
                else:
                    nxt[key] = updated
        states = nxt
    finals.extend(states.values())

    result = {
        "win_a": 0,
        "win_b": 0,
        "expected_games": 0,
        "over": 0,
        "under": 0,
        "push": 0,
        "at_least_one_tie_break": 0,
        "set_scores": {},
        "total_probability": 0,
    }
    for state in finals:
        winner_a = state["sets_a"] > state["sets_b"]
        probability = state["probability"]
        score = f"{state['sets_a']}-{state['sets_b']}"
        result["total_probability"] += probability
        result["win_a" if winner_a else "win_b"] += probability
        result["expected_games"] += state["games"] * probability
        if state["games"] > line:
            result["over"] += probability
        elif state["games"] < line:
            result["under"] += probability
        else:
            result["push"] += probability
        if state["tie_break"]:
            result["at_least_one_tie_break"] += probability
        result["set_scores"][score] = result["set_scores"].get(score, 0) + probability
    return result


def _missing_player_detail(matches, name, player):
    if player:
        return None
    counts = player_surface_counts(matches, name)
    if not counts:
        return f"{name}: nessun match nello storico caricato o nome non riconosciuto"
    available = ", ".join(
        f"{surface_display_name(surface)} ({count})"
        for surface, count in sorted(counts.items(), key=lambda item: item[1], reverse=True)
    )
    return f"{name}: storico disponibile su {available}"


def analyze_match(matches, options):
    if options.get("best_of") not in {3, 5, "3", "5"}:
        raise TennisModelError(
            "Il formato del match deve essere al meglio dei 3 o dei 5 set."
        )
    surface = summarize_surface(matches, options["surface"])
    player_a = surface["players"].get(normalize_name(options["player_a"]))
    player_b = surface["players"].get(normalize_name(options["player_b"]))
    if not player_a or not player_b:
        details = [
            detail for detail in (
                _missing_player_detail(matches, options["player_a"], player_a),
                _missing_player_detail(matches, options["player_b"], player_b),
            )
            if detail
        ]
        raise TennisModelError(
            "Nessun match storico per uno o entrambi i giocatori su "
            f"{surface_display_name(options['surface'])}. {'; '.join(details)}. "
            "Controlla i nomi e seleziona la superficie reale del match; "
            "le superfici non vengono sostituite automaticamente."
        )
    if (
        player_a["serve_point_rate"] is None
        or player_b["serve_point_rate"] is None
        or player_a["return_point_rate"] is None
        or player_b["return_point_rate"] is None
    ):
        raise TennisModelError(
            "Servono statistiche aggregate dei punti al servizio per entrambi "
            "i giocatori su questa superficie."
        )

    reference_date = parse_date(options.get("match_date"))
    if not reference_date:
        raise TennisModelError("Inserisci una data del match valida.")
    recent_a = recent_load(matches, player_a["key"], reference_date)
    recent_b = recent_load(matches, player_b["key"], reference_date)
    tournament_minutes_a = float(options.get("tournament_minutes_a") or 0)
    tournament_minutes_b = float(options.get("tournament_minutes_b") or 0)
    burden_a = recent_a["minutes"] + recent_a["matches"] * 45 + tournament_minutes_a
    burden_b = recent_b["minutes"] + recent_b["matches"] * 45 + tournament_minutes_b
    fatigue_shift = MAX_FATIGUE_SHIFT * math.tanh((burden_b - burden_a) / 360)

    point_a = (player_a["serve_point_rate"] + (1 - player_b["return_point_rate"])) / 2
    point_b = (player_b["serve_point_rate"] + (1 - player_a["return_point_rate"])) / 2
    point_a = min(0.9, max(0.1, point_a - fatigue_shift))
    point_b = min(0.9, max(0.1, point_b + fatigue_shift))

    line = float(options.get("game_line", 21.5))
    if not math.isfinite(line) or line < 0 or line > 200:
        raise TennisModelError("La linea game deve essere un numero valido tra 0 e 200.")
    prediction = simulate_match(point_a, point_b, int(options["best_of"]), line)
    return {
        "surface": surface,
        "player_a": player_a,
        "player_b": player_b,
        "recent_a": recent_a,
        "recent_b": recent_b,
        "tournament_minutes_a": tournament_minutes_a,
        "tournament_minutes_b": tournament_minutes_b,
        "fatigue_shift": fatigue_shift,
        "point_a": point_a,
        "point_b": point_b,
        "line": line,
        "prediction": prediction,
        "h2h": head_to_head(surface["matches"], player_a, player_b),
        "best_of": int(options["best_of"]),
    }
