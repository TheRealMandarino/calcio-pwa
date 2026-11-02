# Aggiornamenti automatici

Il workflow GitHub Actions `Update match data` aggiorna ogni giorno i calendari,
i risultati e le previsioni, poi pubblica i JSON usati dalla PWA su GitHub Pages.
Il computer locale non deve essere acceso. Gli aggiornamenti programmati possono
partire con ritardo rispetto all'orario previsto; il workflow si può avviare anche
manualmente da **GitHub → Actions → Update match data → Run workflow**.

Per abilitare anche le quote bookmaker:

1. Aprire **Settings → Secrets and variables → Actions** nel repository.
2. Creare un repository secret chiamato `ODDS_API_KEY` con la chiave The Odds API.
3. Avviare il workflow manualmente per la prima pubblicazione dei dati.

La chiave viene letta come secret durante l'esecuzione e non è scritta nei file
pubblici. Ogni esecuzione con la chiave interroga sette campionati e può consumare
quota API. Senza chiave, calendari, risultati e previsioni vengono comunque
aggiornati; le quote bookmaker non sono disponibili.

La pubblicazione automatica richiede che GitHub Actions abbia il permesso di
scrivere nel repository e che GitHub Pages pubblichi la cartella `docs` del branch
`main`.
