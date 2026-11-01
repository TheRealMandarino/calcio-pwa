import csv
import io
import sqlite3
from datetime import datetime, timezone
from urllib.error import URLError
from urllib.request import urlopen
from zoneinfo import ZoneInfo


BASE_URL = "https://www.football-data.co.uk/mmz4281/{season}/{division}.csv"
LEAGUES = {
    "I2": "ITA-Serie B",
    "E1": "ENG-Championship",
}
def current_season_start(today=None):
    today = today or datetime.now().date()
    return today.year if today.month >= 7 else today.year - 1


def seasons_to_import(today=None):
    current_start = current_season_start(today)
    return [
        (f"{start % 100:02d}{(start + 1) % 100:02d}", f"{start}/{start + 1}")
        for start in range(current_start - 2, current_start + 1)
    ]


def fetch_csv(url):
    try:
        with urlopen(url, timeout=30) as response:
            return response.read()
    except (OSError, URLError, TimeoutError) as error:
        raise RuntimeError(f"Impossibile scaricare il CSV da {url}: {error}") from error


def parse_number(value):
    if value is None or str(value).strip() == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def parse_integer(value):
    number = parse_number(value)
    return int(number) if number is not None else None


def parse_match_date(date_value, time_value):
    parsed_date_value = date_value.strip()
    for date_format in ("%d/%m/%y", "%d/%m/%Y"):
        try:
            parsed_date = datetime.strptime(parsed_date_value, date_format).date()
            break
        except ValueError:
            continue
    else:
        raise ValueError(f"Formato data non supportato: {date_value}")
    parsed_time = None
    if time_value and time_value.strip():
        try:
            parsed_time = datetime.strptime(time_value.strip(), "%H:%M").time()
        except ValueError:
            parsed_time = None
    return datetime.combine(parsed_date, parsed_time or datetime.min.time()).strftime("%Y-%m-%d %H:%M:%S")


def parse_season_csv(content, division, league, season_name):
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("latin-1")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or not {"Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG"}.issubset(reader.fieldnames):
        raise ValueError(f"Formato CSV non valido per {league} {season_name}.")

    matches = []
    details = []
    for row_number, row in enumerate(reader, start=2):
        if not row.get("Date") or not row.get("HomeTeam") or not row.get("AwayTeam"):
            continue
        try:
            match_date = parse_match_date(row["Date"], row.get("Time", ""))
        except ValueError as error:
            raise ValueError(
                f"Data non valida nel CSV {league} {season_name}, riga {row_number}: {row['Date']}"
            ) from error

        home_goals = parse_integer(row.get("FTHG"))
        away_goals = parse_integer(row.get("FTAG"))
        completed = home_goals is not None and away_goals is not None
        matches.append((
            match_date,
            league,
            row["HomeTeam"].strip(),
            row["AwayTeam"].strip(),
            home_goals,
            away_goals,
            parse_number(row.get("HxG")),
            parse_number(row.get("AxG")),
            "finished" if completed else "scheduled",
            season_name,
        ))
        details.append((
            league,
            match_date,
            row["HomeTeam"].strip(),
            row["AwayTeam"].strip(),
            parse_integer(row.get("HS")),
            parse_integer(row.get("AS")),
            parse_integer(row.get("HST")),
            parse_integer(row.get("AST")),
            parse_integer(row.get("HC")),
            parse_integer(row.get("AC")),
            parse_number(row.get("AvgCH")),
            parse_number(row.get("AvgCD")),
            parse_number(row.get("AvgCA")),
        ))

    return matches, details


def download_lower_league_data(today=None, fetcher=fetch_csv):
    matches = []
    details = []
    for season_code, season_name in seasons_to_import(today):
        for division, league in LEAGUES.items():
            url = BASE_URL.format(season=season_code, division=division)
            content = fetcher(url)
            season_matches, season_details = parse_season_csv(content, division, league, season_name)
            if not season_matches:
                raise ValueError(f"Nessuna partita trovata per {league} {season_name} ({url}).")
            matches.extend(season_matches)
            details.extend(season_details)
    return matches, details


def update_lower_leagues(db_path, today=None, fetcher=fetch_csv):
    matches, details = download_lower_league_data(today=today, fetcher=fetcher)
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS lower_league_details (
                    league TEXT NOT NULL,
                    date TEXT NOT NULL,
                    home_team TEXT NOT NULL,
                    away_team TEXT NOT NULL,
                    home_shots INTEGER,
                    away_shots INTEGER,
                    home_shots_on_target INTEGER,
                    away_shots_on_target INTEGER,
                    home_corners INTEGER,
                    away_corners INTEGER,
                    odds_home REAL,
                    odds_draw REAL,
                    odds_away REAL,
                    PRIMARY KEY (league, date, home_team, away_team)
                )
            """)
            league_placeholders = ",".join("?" for _ in LEAGUES.values())
            conn.execute(
                f"DELETE FROM matches WHERE league IN ({league_placeholders})",
                tuple(LEAGUES.values()),
            )
            conn.execute(
                f"DELETE FROM lower_league_details WHERE league IN ({league_placeholders})",
                tuple(LEAGUES.values()),
            )
            conn.executemany("""
                INSERT INTO matches (
                    date, league, home_team, away_team, home_goals, away_goals,
                    home_xg, away_xg, status, season
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, matches)
            conn.executemany("""
                INSERT INTO lower_league_details (
                    league, date, home_team, away_team, home_shots, away_shots,
                    home_shots_on_target, away_shots_on_target, home_corners, away_corners,
                    odds_home, odds_draw, odds_away
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, details)
    finally:
        conn.close()

    print(
        "Aggiornate da football-data.co.uk: "
        f"{sum(match[1] == 'ITA-Serie B' for match in matches)} partite di Serie B e "
        f"{sum(match[1] == 'ENG-Championship' for match in matches)} di Championship."
    )


def update_scheduled_fixtures(db_path, league, events, now=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    fixtures = []
    for event in events:
        commence_time = event.get("commence_time")
        if not commence_time or not event.get("home_team") or not event.get("away_team"):
            continue
        try:
            kickoff = datetime.fromisoformat(commence_time.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError(f"Orario non valido per una partita Championship: {commence_time}") from error
        if kickoff.tzinfo is None:
            kickoff = kickoff.replace(tzinfo=timezone.utc)
        if kickoff <= now:
            continue

        local_kickoff = kickoff.astimezone(ZoneInfo("Europe/Rome"))
        season_start = local_kickoff.year if local_kickoff.month >= 7 else local_kickoff.year - 1
        fixtures.append((
            local_kickoff.strftime("%Y-%m-%d %H:%M:%S"),
            league,
            event["home_team"],
            event["away_team"],
            None,
            None,
            None,
            None,
            "scheduled",
            f"{season_start}/{season_start + 1}",
        ))

    if not fixtures:
        print(f"Nessuna nuova partita futura per {league}; mantengo il calendario già salvato.")
        return

    conn = sqlite3.connect(db_path)
    try:
        with conn:
            conn.execute(
                "DELETE FROM matches WHERE league = ? AND home_goals IS NULL",
                (league,),
            )
            conn.executemany("""
                INSERT INTO matches (
                    date, league, home_team, away_team, home_goals, away_goals,
                    home_xg, away_xg, status, season
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, fixtures)
    finally:
        conn.close()
    print(f"Aggiornate da The Odds API: {len(fixtures)} partite future di {league}.")


def update_championship_fixtures(db_path, events, now=None):
    update_scheduled_fixtures(db_path, "ENG-Championship", events, now)


if __name__ == "__main__":
    from backend.scraper import DEFAULT_DB_PATH, init_db

    init_db(DEFAULT_DB_PATH)
    update_lower_leagues(DEFAULT_DB_PATH)
