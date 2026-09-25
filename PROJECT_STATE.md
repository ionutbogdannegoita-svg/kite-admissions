# KITE Admissions — Project State

## Status

`V1_COMPLETE`

Aggiornato il **2026-09-26 (Europe/Rome)**. Implementazione completa della V1 sul branch `v1-implementation`, in attesa di revisione e merge su `main` da parte di Ionut.

[SPEC.md](SPEC.md) resta la fonte autorevole (versione 0.2 del 25 settembre 2026, invariata). Le scelte tecniche emerse durante lo sviluppo sono in [DECISIONS.md](DECISIONS.md) (DEC-013 e seguenti).

## Architettura (come da SPEC)

- Python 3.14 + Flask + SQLite, pagine HTML generate dal server, poco JavaScript.
- Waitress in un solo processo su `127.0.0.1`, istanza singola, avvio da collegamento senza console.
- Un solo utilizzatore, nessun login, nessun RBAC, nessun cloud.
- Google Calendar → CRM in sola lettura (scope `calendar.events.readonly` e `calendar.calendarlist.readonly`).
- Sette tabelle: Family, StudentLead, Appointment, Offer, FollowUp, Interaction, CalendarExclusion.
- Viste: Oggi, Appuntamenti, Famiglie, Scheda famiglia + pannello «Dati e collegamento Google».

## Implementato

| Slice | Contenuto |
|---|---|
| 0 — Foundations | Schema V1 `STRICT` con FK su ogni connessione, FK composite figlio/famiglia, trigger di congelamento; versione in `PRAGMA user_version`, migrazioni con copia preventiva; ispezione del database e modalità ripristino; protezioni locali (loopback, Host, Origin/Referer, CSRF, CSP); launcher a istanza singola; `scripts/install.ps1` |
| 1 — Family + StudentLead | Famiglie con recapiti facoltativi, doppioni per telefono/email/etichetta/nome bambino, ricerca e filtri, archiviazione; richieste per figlio e anno, quattro stati con transizioni atomiche in Interaction |
| 2 — Google Calendar import | Adapter solo GET, OAuth desktop, credenziali DPAPI; aggiornamento con anteprima senza preselezioni, verifica per ID, annullamenti e riattivazioni con una Interaction ciascuno, errori → «Non verificato», ricorrenze, giorni interi, esclusioni |
| 3 — Appuntamenti | Elenco e dettaglio, suggerimenti e ricerca, collegamento, creazione famiglia dall'evento in transazione, cambio collegamento, preparazione e resoconto separati dai dati Google |
| 4 — Scheda + cronologia | Scheda completa, note e comunicazioni manuali, cronologia ricavata dalle registrazioni senza copie |
| 5 — Offerte | Bozza, «Segna come comunicata» idempotente, contenuto congelato (server e database), versioni, proposta corrente, ritiro |
| 6 — FollowUp + Oggi | Follow-up con esito, regole del prossimo passo, vista Oggi operativa, resoconto con prossimo passo |
| 7 — Backup/export/restore | Backup automatico giornaliero, manuale e pre-operazione con conservazione; ripristino B2 verificato anche con database assente o danneggiato; verifica post-ripristino; export CSV/JSON; eliminazione definitiva con esclusione degli eventi |

## Test

- `.venv\Scripts\python.exe -m pytest`: **165 test verdi** su dati sintetici e cartelle temporanee (schema e vincoli, sicurezza, avvio reale con Waitress, flussi di ogni slice, flusso end-to-end completo).
- Collaudo manuale sul server reale con calendario di prova da file: import misto, collegamento e creazione dall'evento, visita, offerte con doppio clic, spostamento/annullamento/riattivazione/errore 404, Oggi, backup → modifica → ripristino → verifica dei valori, export, chiusura dall'interfaccia.
- Avvio da collegamento Windows con `pythonw` (senza console): un'istanza sola in ascolto su `127.0.0.1`, il secondo avvio riapre la stessa, chiusura pulita.

## Criteri di accettazione AC01–AC10

Tutti **PASS** con le evidenze elencate nella PR. Due precisazioni:

- **AC02/AC03** sono verificati con la sorgente simulata e con il contratto HTTP dell'adapter Google (sole richieste GET, paginazione completa, mappatura degli errori). La prova con l'account Google reale richiede le credenziali del proprietario.
- **AC04** (circa 60 secondi per caso) è misurato come numero di passi: due azioni per collegare o creare la famiglia dall'evento. Il cronometraggio umano resta consigliato.

## Limitazioni note

- Nessuna prova ancora eseguita contro l'API Google reale: servono progetto Google Cloud, client OAuth «App desktop» e account con accesso al calendario della segreteria (vedi README). In stato OAuth «Test» Google fa scadere l'autorizzazione dopo 7 giorni.
- Il database non è cifrato da SQLite: protezione affidata all'account Windows e alla cifratura del disco (SPEC §9.1).
- Il backup su un supporto esterno è manuale: download del backup dalla pagina Dati.
- Due processi aperti sullo stesso database da programmi esterni (per esempio un visualizzatore SQLite) impediscono il ripristino finché non vengono chiusi; l'app lo segnala senza toccare i dati.

## NEXT ACTION (proprietario)

1. Rivedere e unire la PR del branch `v1-implementation` su `main`.
2. Sul PC di Ionut l'installazione è già pronta: `.venv` nella cartella del repository e collegamento «Avvia KITE Admissions» sul Desktop (`OneDrive\Desktop`). Il database reale nasce al primo avvio in `%LOCALAPPDATA%\KITEAdmissions`. Su un altro PC: `scripts\install.ps1`.
3. Configurare Google Calendar (README, sezione «Configurazione di Google Calendar») e fare il primo aggiornamento reale: verificare che gli eventi della segreteria compaiano in anteprima e che un import selezionato funzioni.
4. Scegliere la destinazione della copia esterna periodica dei backup.

## Vincoli che restano validi

- Nessuna funzionalità V2 (SPEC §12), nessun cloud o accesso LAN, nessuna scrittura verso Google.
- Nessun dato reale delle famiglie, database, backup, export, token o credenziale in Git.
