# KITE Admissions — Decisioni approvate

DEC-001…DEC-012: decisioni già contenute nella [specifica autorevole](SPEC.md) e confermate nella richiesta di creazione del repository. DEC-013 in poi: scelte tecniche emerse durante l'implementazione della V1, entro il perimetro della specifica.

## DEC-001 — Una società nella V1

LATINA INTERNATIONAL SCHOOL IMPRESA SOCIALE S.R.L., contesto Latina. Nessuna separazione multi-tenant o tabella società/plessi. Fonte: SPEC §1.

## DEC-002 — Un utilizzatore

Un solo utilizzatore sul proprio PC; nessun account applicativo multiutente, ruolo, RBAC, assegnazione a colleghi o secondo approvatore. Fonte: SPEC §§1, 10.

## DEC-003 — Esecuzione locale sul PC

Browser locale su Windows, unico processo backend, ascolto soltanto su `127.0.0.1`, dati locali e dossier utilizzabili offline. Nessun hosting cloud applicativo o accesso remoto/LAN. Fonte: SPEC §§2, 9.1, 10.

## DEC-004 — Flask + SQLite

Python, Flask, SQLite, HTML generato dal backend e poco JavaScript. Accesso SQLite con libreria standard e query predefinite parametrizzate; Waitress per l'uso quotidiano su Windows, debug disattivato. Fonte: SPEC §2.

## DEC-005 — Calendar fonte degli appuntamenti

Gli appuntamenti sono creati e gestiti in Google Calendar. Il CRM conserva una copia identificata da `calendar_id + google_event_id`; campi remoti e informazioni locali restano separati. Fonte: SPEC §§1, 4.3.

## DEC-006 — Flusso Calendar → CRM soltanto

Un calendario sorgente, OAuth in sola lettura, import manuale ripetibile e selettivo, nessun evento preselezionato. Nessuna scrittura verso Google, scheduler, webhook o sincronizzazione bidirezionale. Fonte: SPEC §§4, 12.

## DEC-007 — Offerte versionate

BOZZA, COMUNICATA e RITIRATA. «Segna come comunicata» congela il contenuto e registra una sola data/ora anche con ritentativi; una nuova bozza conserva la precedente comunicata. Nessun invio esterno o approvazione fra utenti. Fonte: SPEC §6.

## DEC-008 — Transizioni importanti persistenti

Interaction registra atomicamente cancellazioni/riattivazioni Calendar e cambi di stato StudentLead, comprese iscrizione, non prosecuzione e riapertura. Nessun evento duplicato per stato invariato, nessun audit completo dei campi, nessuna tabella AuditEvent. Fonte: SPEC §7, B1.

## DEC-009 — Sette tabelle

Family, StudentLead, Appointment, Offer, FollowUp, Interaction e CalendarExclusion. StudentLead incorpora la pratica di ammissione; Appointment incorpora il resoconto della visita. Fonte: SPEC §8.

## DEC-010 — Quattro viste operative

Oggi, Appuntamenti, Famiglie, Scheda famiglia. Una piccola pagina tecnica «Dati e collegamento Google» raccoglie connessione, dati, backup, export e ripristino. Fonte: SPEC §3.

## DEC-011 — Backup, export e restore locali

Backup coerenti tramite API SQLite, configurazione non segreta inclusa e token esclusi; export locale CSV/JSON. Copia preventiva obbligatoria per eliminazioni/migrazioni e restore con database corrente sano. Con database assente o corrotto/non leggibile, un backup verificato deve poter essere ripristinato secondo B2, preservando l'esistente quando possibile e verificando integrità, valori e relazioni prima della ripresa. Fonte: SPEC §9.

## DEC-012 — Scope V2 escluso

Nessuna estensione automatica ad altri plessi, multiutenza, cloud, ulteriori integrazioni, fatturazione, BI, scoring o AI esterna sui dati delle famiglie. Il perimetro completo delle esclusioni resta quello di SPEC §12; le estensioni richiedono una decisione esplicita successiva. Fonte: SPEC §§10–12.

## Decisioni implementative (V1, 26 settembre 2026)

Scelte tecniche prese durante lo sviluppo entro il perimetro della SPEC; non cambiano il prodotto.

## DEC-013 — Versione schema nel file SQLite

La versione dello schema è `PRAGMA user_version`, il file è riconosciuto da `PRAGMA application_id`: nessuna ottava tabella per le migrazioni. Le tabelle sono `STRICT`; vincoli essenziali anche nel database: FK composite (figlio, famiglia), indici univoci per versioni e bozza unica delle offerte anche con richiesta nulla, trigger che congelano le offerte comunicate e le transizioni in Interaction. Ogni migrazione gira in transazione dopo una copia pre-operazione riuscita. `settings.json` riporta la versione solo a scopo informativo.

## DEC-014 — Credenziali Google protette con DPAPI e accesso solo GET

Client OAuth «App desktop» e autorizzazione sono cifrati con DPAPI dell'utente Windows (via `ctypes`, nessuna dipendenza aggiuntiva) in `google\`, fuori da backup ed export. L'adapter usa `google-auth` con una sessione `requests` e un'unica funzione di lettura (GET) con timeout espliciti: niente client generato da discovery. Il consenso usa il redirect su loopback del flusso desktop.

## DEC-015 — Backup come pacchetto autonomo con impronte

Ogni backup è un `.zip` con il database copiato tramite API di backup SQLite, la configurazione non segreta e un manifest con conteggio e SHA-256 di ogni tabella. Le impronte servono a verificare il backup prima dell'uso e il database dopo il ripristino. La sostituzione sposta prima da parte il file attuale: se un altro programma lo tiene aperto il ripristino si ferma senza toccarlo.

## DEC-016 — Aggiornamento Calendar in un thread del pulsante

«Aggiorna da Google Calendar» avvia un thread che vive solo per quell'operazione, con pagina di avanzamento che si ricarica da sola e una sola operazione Calendar alla volta; nessun servizio in background né ritentativi automatici. L'anteprima dei nuovi eventi resta solo in memoria (valida 60 minuti) e non entra mai nel database o nei log.

## DEC-017 — Transizioni Calendar legate all'appuntamento

Le Interaction di annullamento/riattivazione hanno sempre `family_id` nullo e la famiglia si ricava dall'appuntamento: dopo un «Cambia collegamento» la cronologia segue l'appuntamento senza riferimenti superati. Le transizioni delle richieste portano famiglia e richiesta, coerenti per FK composita.

## DEC-018 — Idempotenza e salvataggi obsoleti

I moduli di creazione portano un UUID generato al rendering: un doppio invio trova la riga già esistente e non la duplica. Ogni modifica locale controlla il contatore di revisione. L'import tocca solo i campi `src_*` e le informazioni di verifica, quindi non incrementa la revisione e non invalida un resoconto aperto in un'altra scheda.

## DEC-019 — Azione del follow-up come elenco controllato

`FollowUp.action` è uno tra Richiamare, Inviare informazioni, Far fissare la visita, Altro (con nota obbligatoria): così «Da richiamare» in Oggi si individua senza interpretare testo libero. Il resoconto della visita può creare il prossimo follow-up nella stessa transazione.

## DEC-020 — Verifica obbligatoria dopo il ripristino

Dopo un ripristino import e modifiche restano sospesi, salvo eliminazione, archiviazione, backup ed export, finché Ionut conferma la verifica; la pagina elenca le famiglie riapparse e quelle mancanti rispetto al database sostituito. Lo stato Calendar va ricontrollato con un nuovo aggiornamento.

## DEC-021 — Evento importato per errore

Un appuntamento senza collegamento, dati locali o cronologia si può togliere: la riga viene rimossa e l'evento entra in CalendarExclusion come «Ignorato», revocabile dall'anteprima. È l'«Ignora» di SPEC §4.2 applicato dopo un import sbagliato.

## DEC-022 — Export leggibile in Excel italiano

CSV con separatore `;` e UTF-8 con BOM; le celle che iniziano con `= + - @` sono precedute da un apostrofo. Gli importi restano in centesimi con colonne aggiuntive in euro per le offerte.

## DEC-023 — Sorgente Calendar di prova solo esplicita

Per test e collaudo senza credenziali esistono una sorgente simulata in memoria e una sorgente da file JSON, attivabile soltanto con la variabile `KITE_ADMISSIONS_CALENDAR_FIXTURE`, segnalata in ogni pagina. Mai attiva di default.
