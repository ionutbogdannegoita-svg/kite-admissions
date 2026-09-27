# KITE Admissions — Project State

## Status

`V1_COMPLETE`

Verificato il **2026-09-27 (Europe/Rome)** con la revisione indipendente finale del branch `v1-implementation` rispetto a [SPEC.md](SPEC.md): codice, test automatici, collaudo end-to-end e criteri AC01–AC10. La V1 è in una Pull Request da `v1-implementation` verso `main`, in attesa di revisione e merge da parte di Ionut (nessun merge automatico).

[SPEC.md](SPEC.md) resta la fonte autorevole (versione 0.2 del 25 settembre 2026, invariata). Le diciture della sua intestazione («da validare», «Nessuna implementazione avviata») sono storiche e restano come scritte: lo stato corrente è quello di questa pagina. Le scelte tecniche emerse durante lo sviluppo sono in [DECISIONS.md](DECISIONS.md) (DEC-013 e seguenti).

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

## Revisione indipendente finale (2026-09-27)

- Rilettura critica di tutto il codice rispetto alla SPEC: database e vincoli, famiglie e richieste, Google Calendar, appuntamenti, cronologia, offerte, follow-up e Oggi, backup e ripristino, sicurezza locale.
- **Un difetto trovato e corretto.** L'estrazione deterministica dei recapiti dal testo degli eventi scambiava date (`12/03/2019`, `25.09.2026`) e anni scolastici (`2026/2027`) per numeri di telefono, e il primo «telefono» trovato precompilava il modulo «Crea famiglia dall'evento»: un recapito inventato (SPEC §4.3, §5). Ora date e anni scolastici sono separati dal testo prima della ricerca dei numeri; i numeri veri restano riconosciuti.
- Test aggiunti: regressione del difetto; annullamento e riattivazione di un evento spostato fuori finestra, letti per ID; aggiornamento completo attraverso l'adapter Google su HTTP simulato (tutte le pagine, sole richieste GET, 404 che non vale come annullamento); ripristino rifiutato mentre è in corso un aggiornamento Calendar.

## Test

- `.venv\Scripts\python.exe -m pytest`: **170 test verdi** (166 della sessione di sviluppo + 4 della revisione), su dati sintetici e cartelle temporanee.
- Collaudo end-to-end su server Waitress reale, con cartella dati separata, calendario di prova da file e soli dati sintetici: **42/42 controlli superati**. Coperti: database nuovo, avvio a istanza singola solo su `127.0.0.1` (porta irraggiungibile dagli indirizzi di rete del PC), famiglie e richieste, import selettivo, cinque collegamenti o creazioni dall'evento, visita, offerta comunicata con doppio clic e immutabile anche nel database, nuova versione, follow-up, Oggi con annullamento e riattivazione, cronologia, backup → modifiche → ripristino → verifica dei valori, export, chiusura dall'interfaccia.

## Criteri di accettazione AC01–AC10

Tutti **PASS**; la matrice con le evidenze è nella Pull Request della V1. Precisazioni:

- **AC02/AC03** sono verificati con la sorgente simulata e con l'adapter Google a livello HTTP (sole richieste GET verso l'API Calendar, paginazione completa, mappatura degli errori). La prova con l'account Google reale richiede le credenziali del proprietario.
- **AC04/AC05**: i tempi (circa 60 secondi per collegare o creare la famiglia, resoconto in 1–2 minuti) sono verificati come numero di passi, cioè due azioni con i campi già proposti, e come tempi di risposta del server. Resta consigliato il cronometraggio di Ionut al primo uso reale.

## Limitazioni note

- Nessuna prova ancora eseguita contro l'API Google reale: servono progetto Google Cloud, client OAuth «App desktop» e account con accesso al calendario della segreteria (vedi README). In stato OAuth «Test» Google fa scadere l'autorizzazione dopo 7 giorni.
- Il database non è cifrato da SQLite: protezione affidata all'account Windows e alla cifratura del disco (SPEC §9.1).
- Il backup su un supporto esterno è manuale: download del backup dalla pagina Dati.
- Due processi aperti sullo stesso database da programmi esterni (per esempio un visualizzatore SQLite) impediscono il ripristino finché non vengono chiusi; l'app lo segnala senza toccare i dati.
- I recapiti estratti dal testo degli eventi sono suggerimenti prudenti da verificare: un numero attaccato ad altre lettere o cifre può non essere riconosciuto.

## NEXT ACTION (proprietario)

1. Rivedere e unire su `main` la Pull Request della V1.
2. Su questo PC l'installazione è presente: `.venv` nel repository e collegamento «Avvia KITE Admissions» sul Desktop (`OneDrive\Desktop`); il database reale è stato creato al primo avvio in `%LOCALAPPDATA%\KITEAdmissions` ed è ancora vuoto al 2026-09-27. Un'istanza avviata prima della correzione va chiusa («Chiudi applicazione») e riaperta dal collegamento per usare il codice corretto. Su un altro PC: `scripts\install.ps1`.
3. Configurare Google Calendar (README, sezione «Configurazione di Google Calendar») e fare il primo aggiornamento reale: verificare che gli eventi della segreteria compaiano in anteprima e che un import selezionato funzioni.
4. Scegliere la destinazione della copia esterna periodica dei backup.

## Vincoli che restano validi

- Nessuna funzionalità V2 (SPEC §12), nessun cloud o accesso LAN, nessuna scrittura verso Google.
- Nessun dato reale delle famiglie, database, backup, export, token o credenziale in Git.
