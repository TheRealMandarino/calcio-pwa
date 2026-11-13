# Modulo tennis

Il tennis è isolato dal predictor calcio: la logica dell'interfaccia e il
calcolo probabilistico sono in `docs/tennis/tennis.js`; markup e shell condivisa
sono in `docs/index.html`; acquisizione dati, calendario e endpoint HTTP sono
nei moduli `backend/tennis/`. Il predictor e l'importazione calcistici sono in
`backend/calcio/` e non elaborano dati tennis.

Quando apri la scheda **Tennis**, il server locale scarica automaticamente i
cinque archivi annuali più recenti ATP o WTA (ad esempio 2022–2026) e il
calendario dei prossimi 14 giorni. Seleziona un incontro e premi **Pronostica
questo match** per compilare giocatori, data e formato e calcolare l'analisi.
I CSV annuali vengono conservati per 24 ore nella cache locale del server (in
`%LOCALAPPDATA%\CalcioPWA\tennis` su Windows); non vengono aggiunti al
repository. Puoi cambiare circuito o aggiornare manualmente il calendario.
Il formato supportato è quello dei CSV di Tennis Abstract / Jeff Sackmann, con
intestazioni come `surface`, `winner_name`, `loser_name`, `minutes`,
`w_svpt`, `w_1stWon`, `w_2ndWon`, `w_bpSaved`, `w_bpFaced` e le rispettive
colonne `l_...`. Le statistiche assenti non sono ricostruite né sostituite con
valori inventati.

Il calendario arriva dallo scoreboard tennis ESPN, che pubblica eventi e
singolari programmati con due giocatori confermati, data, ora e torneo. Le
partite con avversario ancora TBD non vengono presentate come pronosticabili.
Il feed copre gli eventi che ESPN pubblica e può non includere ogni torneo o
qualificazione. I dati sono aggiornati ogni cinque minuti nella cache locale.
L'ora mostrata segue il fuso orario del browser.

Il downloader usa il mirror archivistico
[Aneeshers/tennis-sackmann-archive](https://github.com/Aneeshers/tennis-sackmann-archive),
che conserva i CSV ATP e WTA originali e la documentazione della fonte. Il
mirror attribuisce i dati a Jeff Sackmann / Tennis Abstract e riporta la
licenza CC BY-NC-SA 4.0. Questa licenza richiede
attribuzione, limita l'uso commerciale e impone la condivisione allo stesso
modo: verifica le condizioni della fonte prima di usare o ridistribuire i dati.
Le fonti originali sono [Jeff Sackmann ATP](https://github.com/JeffSackmann/tennis_atp)
e [Jeff Sackmann WTA](https://github.com/JeffSackmann/tennis_wta).
Il servizio non invia CSV manuali a terzi; per scaricare gli archivi e il
calendario il server locale effettua richieste HTTPS alle rispettive fonti.

## Superfici e disponibilità dei dati

- Terra, erba, cemento indoor, cemento outdoor e cemento non specificato sono
  categorie distinte. I dati `Hard` privi di indicazione indoor/outdoor non
  entrano nelle statistiche dei due sottotipi.
- I CSV storici possono riportare solo `Hard`: scegliere la voce cemento non
  specificato è l'unico modo per analizzare quei record senza attribuire loro
  una superficie non documentata.
- Se un giocatore non ha match su una superficie, il pronostico viene fermato e
  l'interfaccia indica le superfici per cui esistono dati. Verificare anche la
  grafia del nome; non vengono trasferite automaticamente statistiche da
  un'altra superficie.
- La copertura delle statistiche aggregate di servizio/break varia per stagione;
  il numero di match storici non coincide necessariamente con il campione utile
  per Hold Rate, Break Rate e Clutch Factor.
- Se la fonte contiene conteggi servizio o break point impossibili, il downloader
  esclude soltanto quei conteggi, conserva il risultato e mostra il numero di
  match interessati.
- La colonna del torneo può contenere la data d'inizio, non quella di ogni
  partita. Perciò il conteggio dei match negli ultimi 21 giorni è indicativo.
  I minuti del torneo in corso si inseriscono manualmente: i CSV storici non
  forniscono in modo affidabile i minuti già giocati nell'evento attuale.
- I dati di Jeff Sackmann / Tennis Abstract sono distribuiti con licenza
  [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/):
  richiedono attribuzione, limitano l'uso commerciale e impongono la
  condivisione allo stesso modo. Prima di importarli, verificarne licenza e
  condizioni d'uso per il proprio caso.
- Tennis-Data.co.uk dichiara che i propri dati sono per uso privato e non
  autorizza scraping automatizzato/uso commerciale; inoltre i suoi risultati
  aggregati non bastano a calcolare le metriche punto-per-punto richieste.
  Non viene usato dal modulo.

Non esiste un pacchetto Python mantenuto equivalente a SoccerData per questa
fonte: il pacchetto PyPI `tennis` è un simulatore di punteggi, non uno scraper.
Il downloader annuale, l'adapter torneo e il client del calendario sono
implementati in Python con la libreria standard. L'adapter manuale legge il
blocco `completedSingles` di una pagina torneo Tennis Abstract.
L'URL della pagina va inserito manualmente. Il server accetta solo URL HTTPS
`www.tennisabstract.com/current/...ATP...html` o `...WTA...html`, limita la
dimensione della risposta e mette in cache i risultati per 10 minuti.

Lo scraper aggiunge match, punteggi, forma, precedenti e tie-break, ma quelle
pagine non forniscono in modo affidabile data del singolo match, superficie
indoor/outdoor o statistiche di servizio e break. Per questo la superficie e la
data d'inizio del torneo vanno confermate manualmente; la data è solo una stima
della recency, non l'effettivo giorno del match. I risultati acquisiti non
vengono usati per Hold Rate, Break Rate, break point o minuti giocati. Queste
metriche usano i CSV annuali scaricati automaticamente o i CSV caricati
manualmente dall'utente.

Il calendario e il download automatico sono disponibili tramite il server
locale (`avvia.bat`), non sul sito statico GitHub Pages senza un backend
configurato. Le fonti possono cambiare
struttura o limitare le richieste; in quel caso il modulo mostra un errore
esplicito. Il modulo calcola una **quota equa** come reciproco della probabilità
di vittoria del modello, senza margine bookmaker; non sono quote offerte o
prezzi di mercato. Le statistiche aggregate di servizio/break sono disponibili negli
archivi annuali solo per i match per cui la fonte le riporta; non sono dati
punto-per-punto. Per distinguere il cemento indoor dall'outdoor serve una
fonte che documenti esplicitamente questa informazione.

## Calcolo

Hold e break sono stime ricavate dai punti vinti al servizio e in risposta,
convertiti in probabilità di tenuta del game tramite la formula del game con
vantaggio. Le percentuali individuali sono ricondotte alla media aggregata
della superficie usando un prior di 150 punti; le due stime (servizio proprio
e risposta avversaria) sono poi combinate. Il clutch factor è la media tra
break point salvati e break point convertiti. Il tasso di tie-break è calcolato
dai set `7-6` / `6-7` nel punteggio storico.

Un modello a stati enumera i game di ogni set, i tie-break a 6-6 e le sequenze
dei set per produrre probabilità vittoria, punteggi per set, game totali attesi,
Over/Under e probabilità di almeno un tie-break. Assume un tie-break standard
in ogni set a 6-6, anche nel set decisivo; non rappresenta le regole speciali
di alcuni tornei.

La fatica combina match e minuti registrati nei 21 giorni precedenti con i
minuti correnti del torneo inseriti dall'utente. Il suo impatto massimo è
limitato a due punti percentuali ed è un'euristica, non calibrata su dati live.
I pronostici non sono garanzie e non usano quote bookmaker.

## Test

Con Node.js installato, eseguire dalla radice del repository:

```powershell
node --test tests/tennis.test.js
```
