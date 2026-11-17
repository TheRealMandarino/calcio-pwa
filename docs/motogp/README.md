# Modulo MotoGP

Il package `backend/motogp/` mantiene un archivio separato da calcio e Formula 1.
Scarica calendario, classifiche e classificazioni delle gare della classe MotoGP
dal 2016 alla stagione corrente; dal 2023 include anche le Sprint. Le feature
pre-gara sono salvate separatamente dal risultato (`target`) per non usare
informazioni future come input.

## Aggiornamento

```powershell
python -m pip install -r requirements.txt
python -m backend.motogp --update
```

Il comando accetta `--start-year`, `--end-year` e `--data-dir`. I dati sono
salvati fuori dal repository in `%LOCALAPPDATA%\CalcioPWA\motogp` (Windows) o
`~/.cache/CalcioPWA/motogp`; `CALCIOPWA_MOTOGP_DATA` consente di impostare una
directory diversa. Per l'aggiornamento completo insieme agli altri sport, fare
doppio clic su `aggiorna_database.bat`. Il comando rigenera anche i file statici
in `docs/motogp/`.

## Archivio e modello

- `seasons/<anno>.json`: calendario e classificazioni ufficiali per gara e
  Sprint, più la classifica mondiale.
- `features/<anno>.json`: una riga per pilota e sessione, con forma recente,
  punti accumulati e risultati precedenti sul circuito. Il risultato della
  sessione è salvato in `target`, separato dagli input.
- `manifest.json`: fonti, copertura e classifiche non disponibili.

Le probabilità di vittoria della prossima gara sono stime euristiche basate
sulla classifica corrente, sulle vittorie e sui podi; non sono quote bookmaker
né un modello calibrato e non garantiscono l'esito.

## Fonte e limiti

I dati provengono dall'API pubblica non documentata usata dal sito MotoGP:
`https://api.motogp.pulselive.com/motogp/v1`. L'API può cambiare o limitare le
richieste; classifiche non reperibili vengono annotate nel manifest e non
inventate. Il modulo non acquisisce live timing o dati telemetrici.

Il progetto è indipendente e non affiliato a MotoGP. Marchi e diritti sui dati
appartengono ai rispettivi titolari; rispettare disponibilità e termini d'uso
della fonte.
