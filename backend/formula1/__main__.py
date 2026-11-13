import argparse
import sys

from .updater import MIN_HISTORY_YEAR, ArchiveUpdater, default_data_directory


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Scarica lo storico Formula 1 e crea feature pre-gara."
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="aggiorna le stagioni e i dati disponibili",
    )
    parser.add_argument("--start-year", type=int, default=MIN_HISTORY_YEAR)
    parser.add_argument("--end-year", type=int)
    parser.add_argument("--data-dir", help="directory locale per i dati scaricati")
    parser.add_argument(
        "--no-details",
        action="store_true",
        help="non scaricare stint, pit stop, meteo e race control dell'anno corrente",
    )
    args = parser.parse_args(argv)
    if not args.update:
        parser.error("specificare --update per avviare l'aggiornamento")

    updater = ArchiveUpdater(data_directory=args.data_dir)
    try:
        manifest = updater.update(
            start_year=args.start_year,
            end_year=args.end_year,
            include_details=not args.no_details,
        )
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Aggiornamento Formula 1 non riuscito: {error}", file=sys.stderr)
        return 1

    print(f"Dati Formula 1 salvati in: {updater.data_directory}")
    for season in manifest["seasons"]:
        print(
            f"{season['year']}: {season['completed_races']} gare concluse, "
            f"{season['pre_race_feature_rows']} righe pre-gara."
        )
    if manifest["unavailable"]:
        print(
            f"Avviso: {len(manifest['unavailable'])} sessioni o dati non disponibili; "
            "dettagli in manifest.json."
        )

    try:
        import json
        from pathlib import Path
        from .predictor import build_formula1_view_payload
        docs_export = Path(__file__).resolve().parent.parent.parent / "docs" / "formula1" / "formula1-data.json"
        docs_js_export = Path(__file__).resolve().parent.parent.parent / "docs" / "formula1" / "formula1-data.js"
        if docs_export.parent.is_dir():
            payload = build_formula1_view_payload(updater.data_directory)
            with docs_export.open("w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            with docs_js_export.open("w", encoding="utf-8") as f:
                f.write("window.__FORMULA1_DATA__ = " + json.dumps(payload, ensure_ascii=False) + ";\n")
            print(f"Esportazione statica per web app aggiornata in: {docs_export}")
    except Exception as exc:
        print(f"Nota: esportazione statica non riuscita: {exc}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
