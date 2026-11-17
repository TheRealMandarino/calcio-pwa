import argparse
import sys

from .clients import DataSourceError
from .updater import MIN_HISTORY_YEAR, ArchiveUpdater, export_static_data


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Scarica lo storico MotoGP e crea feature pre-gara."
    )
    parser.add_argument("--update", action="store_true", help="aggiorna l'archivio MotoGP")
    parser.add_argument("--start-year", type=int, default=MIN_HISTORY_YEAR)
    parser.add_argument("--end-year", type=int)
    parser.add_argument("--data-dir", help="directory locale per i dati scaricati")
    args = parser.parse_args(argv)
    if not args.update:
        parser.error("specificare --update per avviare l'aggiornamento")

    updater = ArchiveUpdater(data_directory=args.data_dir)
    try:
        manifest = updater.update(
            start_year=args.start_year,
            end_year=args.end_year,
        )
        export_static_data(updater.data_directory)
    except (DataSourceError, OSError, ValueError) as error:
        print(f"Aggiornamento MotoGP non riuscito: {error}", file=sys.stderr)
        return 1

    print(f"Dati MotoGP salvati in: {updater.data_directory}")
    for season in manifest["seasons"]:
        print(
            f"{season['year']}: {season['completed_races']} gare, "
            f"{season['completed_sprints']} sprint, "
            f"{season['pre_race_feature_rows']} righe feature pre-gara."
        )
    if manifest["unavailable"]:
        print(
            f"Avviso: {len(manifest['unavailable'])} classifiche non disponibili; "
            "dettagli in manifest.json."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
