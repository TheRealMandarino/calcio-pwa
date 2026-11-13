# Calcio PWA

Applicazione web per consultare risultati, calendario e previsioni dei campionati.

## Struttura

- `backend/calcio/`: predictor, raccolta dati e test calcistici.
- `backend/tennis/`: API, calendario, storico e scraper tennis; indipendenti dal
  predictor calcio.
- `backend/formula1/`: acquisizione, archivio e feature Formula 1, separati da
  calcio e tennis.
- `backend/server.py`: server HTTP condiviso che instrada le API di calcio,
  tennis e Formula 1.
- `docs/tennis/`: JavaScript, modello di previsione e documentazione tennis,
  indipendenti dal predictor 1X2 del calcio.
- `docs/index.html`: shell dell'app con schede separate per calcio, tennis e Formula 1.
- `data/database.db`: archivio locale usato per risultati, statistiche e backtest.
- `docs/`: applicazione pubblicata con GitHub Pages e i relativi dati JSON generati.
- `backup_config/`: copia storica del codice e dei dati, non usata dall'applicazione attuale.
- `avvia.bat`: avvia il server locale e apre l'app nel browser.
- `requirements.txt`: dipendenze Python.

I JSON in `docs/` sono generati dal processo di aggiornamento: non modificarli a mano.

## Avvio e aggiornamento locale

Per avviare l'applicazione, eseguire `avvia.bat`.

Per aggiornare risultati, calendari, previsioni e quote:

```powershell
python -m pip install -r requirements.txt
python -m backend.calcio.daily_refresh
```

Per eseguire il backtest walk-forward della sola Serie A:

```powershell
python -m backend.calcio.backtest_primary_leagues --league "ITA-Serie A"
```

Il report mostra accuratezza per stagione e turno, oltre alla massima sequenza
di pronostici 1X2 errati. I turni sono ricostruiti da date e squadre perché il
database non contiene il numero ufficiale della giornata.

Le quote richiedono `ODDS_API_KEY` nel file locale `backend/.env`. Non aggiungere
quel file a Git. L'aggiornamento automatico e la pubblicazione sono descritti in
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## Modulo tennis

La scheda **Tennis** carica automaticamente gli ultimi cinque anni di dati ATP/WTA
e il calendario dei prossimi 14 giorni; seleziona un match per ottenere
probabilità e quota equa stimata dal modello, senza quote bookmaker. I dati
restano nella cache locale del server. Per metodo, fonti, licenza e limiti,
consulta
[docs/tennis/README.md](docs/tennis/README.md).

## Formula 1

Il modulo dedicato usa l'API open source Jolpica-F1 per lo storico dal 2016 e
OpenF1 per i tempi sul giro e i dettagli di gara dal 2023. Per installare e
aggiornare lo storico:

```powershell
python -m pip install -r requirements.txt
python -m backend.formula1 --update
```

I dati scaricati sono salvati localmente fuori dal repository. Il report
dell'aggiornamento elenca le sessioni non disponibili; non vengono inventati
giri o classifiche. Per fonti, copertura e feature senza leakage consulta
[docs/formula1/README.md](docs/formula1/README.md).
