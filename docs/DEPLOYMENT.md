# Aggiornamenti automatici

Il workflow GitHub Actions `Update match data` aggiorna ogni giorno i calendari,
i risultati e le previsioni, poi pubblica i JSON usati dalla PWA su GitHub Pages.
Il computer locale non deve essere acceso. Gli aggiornamenti programmati possono
partire con ritardo rispetto all'orario previsto; il workflow si può avviare anche
manualmente da **GitHub → Actions → Update match data → Run workflow**.

I risultati e lo storico dei sette campionati sono importati dai CSV di
football-data.co.uk; così l'aggiornamento non dipende più dallo scraping di FBref,
che può bloccare le richieste automatiche con un CAPTCHA. La fonte dichiara i dati
destinati all'uso personale non commerciale.

Per abilitare anche le quote bookmaker:

1. Aprire **Settings → Secrets and variables → Actions** nel repository.
2. Creare un repository secret chiamato `ODDS_API_KEY` con la chiave The Odds API.
3. Avviare il workflow manualmente per la prima pubblicazione dei dati.

La chiave viene letta come secret durante l'esecuzione e non è scritta nei file
pubblici. Ogni esecuzione con la chiave interroga sette campionati, aggiornando
quote e prossime partite, e può consumare quota API. Senza chiave, risultati,
storico e previsioni vengono comunque aggiornati dai CSV; le quote e il calendario
completo delle partite future non sono garantiti. La chiave è quindi necessaria
per pubblicare tutte le prossime partite anche nei campionati principali.

La pubblicazione automatica richiede che GitHub Actions abbia il permesso di
scrivere nel repository e che GitHub Pages pubblichi la cartella `docs` del branch
`main`.
