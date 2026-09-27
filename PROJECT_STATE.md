# KITE Admissions — Project State

## Status

`V1_1_COMPLETE` — in Pull Request verso `main`, in attesa di revisione e merge da parte di Ionut (nessun merge automatico).

- **V1.0.0** (`7452456`, schema 1): su `main`, in uso reale dal collegamento «Avvia KITE Admissions».
- **v1.1 — Interview Workflow** (Issue #2, branch `v1.1-interview-workflow`, schema 2): completata il **2026-09-27 (Europe/Rome)** nel worktree separato `C:\Users\ionut\kite-admissions-v1.1`, con test automatici, collaudo su Waitress con dati sintetici e revisione indipendente contro la specifica.

Fonti: [SPEC.md](SPEC.md) (V1, con l'addendum v1.1 al §14), [specifica operativa v1.1](docs/PROPOSTA_V1.1_INTERVIEW_WORKFLOW.md) (versione 1.0, approvata dal titolare il 27/09/2026, OD-1…OD-7 risolte), [DECISIONS.md](DECISIONS.md) (DEC-024…DEC-034 per la v1.1). Le diciture storiche nell'intestazione della SPEC («da validare», «Nessuna implementazione avviata») restano come scritte: lo stato corrente è questa pagina.

## Architettura (invariata)

- Python 3.14 + Flask + SQLite, pagine HTML generate dal server, poco JavaScript; Waitress su `127.0.0.1`, istanza singola.
- Un solo utilizzatore, nessun login, nessun RBAC, nessun cloud; Google Calendar → CRM in sola lettura.
- **Sette tabelle**: Family, StudentLead, Appointment, Offer, FollowUp, Interaction, CalendarExclusion. Nessuna tabella nuova nella v1.1.
- Viste: Oggi, Appuntamenti (con la pagina **Colloquio**), Famiglie, Scheda famiglia + pannello «Dati e collegamento Google».

## v1.1 — implementato

| Slice | Contenuto | Commit |
|---|---|---|
| 1 — Fondamenta | Schema 2 solo additivo (colonne, indice, trigger dei follow-up, trigger di congelamento ricreato), `catalog.py`, validazione dei JSON, ripristino di backup V1 con migrazione e `RestoreError` se fallisce | `30b2c4f` |
| 2 — Alunno e famiglia | Data di nascita facoltativa, età e verifica informativa (31/12, 30/04, 29 febbraio), scuola e classe attuali, lingue dichiarate, profilo, flag neutro di approfondimento; fonte a lista chiusa con dettaglio | `3c30266` |
| 3 — Economia | Offerta in tre blocchi, riduzioni a righe con motivo, quota d'iscrizione, «Autorizzata da» obbligatoria per le condizioni discrezionali e non ereditata dalle nuove versioni, avviso di coerenza, nessuno sconto calcolato | `389a84b` |
| 4 — Colloquio | Pagina Colloquio (Prepara incontro + sezioni A–K), «Salva» atomico multi-record con impronte e revisioni, conflitti con scelta esplicita, «Concludi colloquio» con fino a tre follow-up (responsabile, legame con l'incontro), «Attendere risposta della famiglia», materiale, verifiche, «non prosegue», regola del prossimo passo anche per la route V1, azioni economiche dal colloquio, scheda «Colloquio» nella pagina appuntamento | `2455c7a` |
| 5 — Riepilogo e integrazione | Riepilogo (≤ 12 righe + 2 per alunno), «Appuntamenti e colloqui» e «Ultimo colloquio» nella scheda, cronologia, Oggi (link al colloquio, responsabile), export con le colonne nuove, conteggi dell'eliminazione, documentazione, collaudo | vedi `git log` |

## Test

- `.venv\Scripts\python.exe -m pytest` nel worktree: **304 test verdi** (i 170 della V1, con gli aggiornamenti dichiarati nella Issue, e 134 nuovi della v1.1 in `tests/test_v11_*.py`), su dati sintetici e cartelle temporanee.
- Test V1 aggiornati, come previsto dalla specifica (§10): `test_foundations.py` (versione dello schema e migrazioni su una versione successiva finta), `test_e2e.py` (fonte «Evento / open day»), `test_linking.py`, `test_timeline.py` e `tests/dataset.py` (prossimo passo per le visite svolte di appuntamenti collegati, OD-5).

## Collaudo v1.1 (2026-09-27)

Su server Waitress reale, cartelle dati di prova nello scratchpad, calendario di prova da file, soli dati sintetici:

- **51/51 controlli** dello scenario completo: avvio v1.1, import selettivo, tre incontri della stessa famiglia, Prepara incontro con «Da chiedere», sezioni A–K con salvataggi intermedi, record non toccati non riscritti, conflitto fra due schede e scelta «usa gli attuali», doppio invio, nessuna riga di cronologia per i salvataggi, proposta con riduzione promozionale rifiutata senza autorizzazione e comunicata con doppio clic (una sola data, congelata anche nel database), chiusura con materiale, verifiche, «Attendere risposta» e completamento di un follow-up vecchio, doppio «Concludi», riepilogo ≤ 12 righe, storico, cronologia, Oggi, «Cambia collegamento», backup, export, tempi < 2 s, CSP.
- **Compatibilità con la V1 autentica** (codice `7452456` estratto con `git archive` e avviato su una cartella di prova): dati e backup creati dalla 1.0.0; la v1.1 avviata su quei dati crea la copia «pre-migrazione» (schema 1), migra allo schema 2 con valori V1 identici, integrità e foreign key OK, 7 tabelle; il backup V1 si ripristina nella v1.1, viene migrato, segnala famiglie mancanti e riapparse, e il colloquio funziona sui dati ripristinati (23/23 controlli, dopo la correzione di un'attesa errata dello script).
- `app.js`: sintassi verificata con Node e comportamento provato su un DOM simulato (scelte rapide, data di «Attendere risposta» secondo la tempistica, «non prosegue», protezione dall'uscita). Struttura HTML bilanciata sulle pagine principali.
- **Un difetto trovato nel collaudo e corretto**: un incontro futuro soltanto preparato veniva considerato «colloquio più recente» e toglieva i dati attuali dell'alunno dal riepilogo della visita già svolta. Ora conta l'ultimo colloquio avvenuto; test di regressione aggiunto.

## Revisione indipendente v1.1 (2026-09-27)

Revisione in sola lettura, a contesto pulito, del diff `main...v1.1-interview-workflow` contro la specifica operativa e l'addendum (con prove su database temporanei sintetici). Esito: **nessun difetto critico, 2 major e 3 minor, tutti corretti** con un test di regressione ciascuno (i test falliscono sul codice precedente e passano sul nuovo):

1. *Major* — un follow-up spuntato «completato con questo incontro» valeva ancora come prossimo passo, aggirando OD-5. Ora i follow-up completati nella stessa conclusione non contano; l'elenco si chiama «Follow-up già aperti».
2. *Major* — aprendo il colloquio prima dell'inizio dell'incontro, un «Salva» successivo fissava nella bozza una data effettiva vuota e la chiusura in 3 azioni falliva. Ora una data vuota vale «usa la proposta dell'evento» e la bozza si salva solo se diversa dalla proposta.
3. *Minor* — un esito «non presentata/annullata» registrato per errore non poteva tornare «non ancora registrato». Ora c'è una spunta esplicita, valida con «Salva» (mai verso «svolta»).
4. *Minor* — una lingua o un livello tolti dal catalogo rompevano i salvataggi o sparivano. Ora restano come «voce non più in elenco» e si possono solo togliere.
5. *Minor* — dopo «Cambia collegamento» con una bozza K, l'impronta del colloquio si rompeva una volta. Ora la richiesta non più della famiglia non si ripresenta.

Confermati dalla revisione: protocollo multi-record (ogni campo mostrato è letto e viceversa), regola OD-5, offerte (§8.7), migrazione e ripristino, riepilogo e storico, privacy, Allowed Surface, rendering dei rami rari dei template.

## Limitazioni note

- Il controllo visivo nel browser (impaginazione della pagina Colloquio su portatile) e il cronometraggio reale di Ionut (chiusura in 3 azioni, riepilogo letto in 10–15 secondi) restano da fare al primo uso: l'estensione del browser non era collegata durante il collaudo.
- Nessuna prova ancora eseguita contro l'API Google reale (vedi README); in stato OAuth «Test» l'autorizzazione scade dopo 7 giorni.
- Il database non è cifrato da SQLite; il backup esterno è manuale.
- I dati dell'alunno nel riepilogo sono quelli attuali: un riepilogo vecchio non ricostruisce ciò che si sapeva allora (limite accettato nella specifica).
- Nessun versionamento del singolo colloquio (come per il resoconto V1).

## NEXT ACTION (proprietario)

1. Rivedere e unire su `main` la Pull Request della v1.1.
2. Dopo il merge: «Chiudi applicazione», `git -C C:\Users\ionut\kite-admissions pull`, riavvio dal collegamento; controllare in «Dati e Google» la copia «pre-migrazione» e che i conteggi delle tabelle non siano cambiati. Ritorno alla V1 descritto nel README.
3. Verificare con chi segue la privacy della scuola che l'informativa Admissions copra le nuove categorie (data di nascita del minore, scuola attuale, lingue, esigenze, note del colloquio).
4. Al primo uso reale: provare il colloquio su un portatile e cronometrare chiusura e lettura del riepilogo; confrontare ogni anno le regole dell'età con la circolare iscrizioni.
5. Restano validi i passi V1 non ancora fatti: configurazione di Google Calendar e destinazione della copia esterna dei backup.

## Vincoli che restano validi

- Nessuna funzionalità V2 (SPEC §12), nessun cloud o accesso LAN, nessuna scrittura verso Google.
- Nessun dato reale delle famiglie, database, backup, export, token o credenziale in Git.
- Codice di sviluppo mai avviato sulla cartella dati reale (DEC-034).
