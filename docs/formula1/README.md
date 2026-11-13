# Modulo Formula 1

Il package `backend/formula1/` è separato dai moduli calcio e tennis. Scarica
calendario, risultati completi, qualifiche e classifiche per almeno
le stagioni 2016–corrente; costruisce inoltre righe di feature per ogni pilota
e gara, con obiettivo finale esplicitamente separato dagli input pre-gara.
La scheda Formula 1 in `docs/index.html` legge la copertura del manifest dal
server locale; la pagina statica non espone l'archivio privato sul computer.

## Aggiornamento

```powershell
python -m pip install -r requirements.txt
python -m backend.formula1 --update
```

Il comando accetta `--start-year`, `--end-year` e `--data-dir`; `--no-details`
salta stint, pit stop, meteo e race control. Per impostazione predefinita i
dati sono salvati fuori dal repository in `%LOCALAPPDATA%\CalcioPWA\formula1`
(Windows) o `~/.cache/CalcioPWA/formula1` (altri sistemi). È possibile
impostare `CALCIOPWA_FORMULA1_DATA`.

L'archivio contiene:

- `seasons/<anno>.json`: calendario, risultati di gara e sprint, qualifiche e
  standings piloti/costruttori.
- `races/<anno>-<round>.json`: giri, settori e speed trap da OpenF1; per
  l'anno corrente anche stint/pneumatici, pit stop, meteo e race control.
- `features/<anno>.json`: una riga per pilota/gara, con forma recente,
  prestazioni sul circuito, posizione di qualifica e ritmo precedente.
- `manifest.json`: fonti, copertura e sessioni non disponibili.

## Fonti e copertura

- [Jolpica-F1](https://github.com/jolpica/jolpica-f1), API compatibile Ergast:
  calendario, risultati, qualifiche e classifiche.
- [OpenF1](https://openf1.org/), API open source: tempi sul giro e dettagli di
  sessione dal 2023. L'API limita le richieste; l'aggiornamento le scaglionerà.
- FastF1 è stato provato, ma l'accesso ai feed timing nel runtime richiede
  autenticazione Formula 1. Per questo il modulo usa Jolpica e OpenF1 e non
  dichiara come disponibili i dati che le fonti non restituiscono.

Le stagioni 2016–2022 comprendono i risultati, le qualifiche e le classifiche;
il timing giro per giro viene importato da OpenF1 dal 2023. Se un endpoint o
una sessione non è disponibile, l'archivio lo registra nel manifest anziché
simulare i dati.

## Feature e validità del backtest

Le feature di ogni riga sono calcolate usando soltanto gare precedenti anche
tra stagioni: media delle posizioni recenti, punti accumulati nella stagione,
risultati precedenti sul circuito, qualifica della gara corrente e, quando
disponibile, il risultato sprint precedente alla gara principale e
ritmo/pit stop/pneumatici delle gare già disputate. Il risultato finale è
salvato separatamente in `target`; non deve essere passato al modello come
input. Le classifiche ufficiali sono snapshot della fonte e non sono una
feature storica point-in-time. I punti pre-gara derivati includono i risultati
dei GP e degli sprint già disputati.

I dati dei servizi esterni restano soggetti alla loro disponibilità, ai
termini d'uso e a eventuali ritardi di pubblicazione.
