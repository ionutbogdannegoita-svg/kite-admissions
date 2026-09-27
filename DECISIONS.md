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

## Decisioni v1.1 — Interview Workflow (27 settembre 2026)

Decisioni del titolare (Owner Decision OD-1…OD-7, tutte risolte il 27/09/2026) e scelte tecniche dell'implementazione, entro la specifica operativa [docs/PROPOSTA_V1.1_INTERVIEW_WORKFLOW.md](docs/PROPOSTA_V1.1_INTERVIEW_WORKFLOW.md) e l'addendum [SPEC §14](SPEC.md). Issue #2.

## DEC-024 — Il colloquio vive nell'appuntamento (OD-1)

Un incontro = un evento Calendar = una riga `Appointment` = un colloquio. La parte strutturata è la colonna JSON `Appointment.interview`; preparazione, esito, data effettiva, sintesi e nota interna restano i campi V1. I dati dell'alunno stanno nel suo `StudentLead`, la fonte nella `Family`, l'economia nell'`Offer`, il prossimo passo nel `FollowUp`: il colloquio non copia nulla. Nessuna nuova tabella: restano le sette tabelle (DEC-009) e lo storico nasce da sé, una riga per incontro. Nessun colloquio senza evento Calendar (OD-6).

## DEC-025 — Data di nascita facoltativa e verifica dell'età informativa (OD-2)

La scuola è paritaria: `StudentLead.birth_date` facoltativa, età calcolata sul server in Europe/Rome, anni compiuti al 31/12 dell'anno di inizio e verifica per Infanzia e Primaria 1ª secondo le regole generali (DPR 89/2009, anticipo entro il 30/04). La verifica è solo un'etichetta: non blocca mai la pratica. L'anno di nascita V1 resta per chi conosce solo quello e segue la data quando c'è (vincolo anche nel database).

## DEC-026 — Liste centralizzate in `catalog.py` (OD-7)

Tutte le liste del colloquio sono tuple di codici ed etichette in `kite_admissions/catalog.py`, senza tabelle né pagine di configurazione. Nei dati si salvano i codici (eccezione: la fonte, testo come nella V1). Un codice sconosciuto in ingresso è un errore; un codice già salvato e poi tolto dall'elenco si conserva e si mostra come «voce non più in elenco». Fonte a lista chiusa: un valore V1 fuori lista resta come «valore precedente». Fasce: Infanzia, Primaria, Secondaria di I grado.

## DEC-027 — Economia in tre blocchi, nessuno sconto calcolato (OD-3a, 3b, 3c)

L'offerta si mostra e si compila come listino standard, condizione riservata e importo finale comunicato. Le riduzioni sono righe (fino a 4) con motivo del catalogo, importo sulla retta e nota; `enrollment_fee_cents` è la quota d'iscrizione comunicata; `authorized_by` è obbligatorio alla comunicazione per promozione, accordo con la Direzione e altra condizione discrezionale, non per fratelli e pagamento annuale. Tutti gli importi sono scritti da Ionut: nessun calcolo, solo un avviso non bloccante se listino − riduzioni ≠ retta finale. I tre campi sono congelati dal trigger; «Crea nuova versione» copia riduzioni e quota ma non l'autorizzazione, che va confermata (con il suggerimento della versione precedente).

## DEC-028 — Responsabile come etichetta e legame con l'incontro (OD-4)

`FollowUp.assignee` è un'etichetta libera con suggerimenti (vuota = Ionut): nessun utente, filtro o assegnazione (DEC-002). `FollowUp.appointment_id` indica l'incontro da cui nasce il passo: si valorizza solo alla conclusione del colloquio o dalla route V1 del resoconto, due trigger garantiscono che sia della stessa famiglia e che non cambi; «Cambia collegamento» lo azzera per i passi rimasti alla famiglia precedente (con aggiornamento della loro revisione).

## DEC-029 — «Salva» e «Concludi colloquio»; prossimo passo obbligatorio (OD-5)

«Salva» registra i contenuti in qualsiasi momento, compresa la bozza della conclusione (`interview.closing_draft`), e non registra mai esito, follow-up o chiusure. «Concludi colloquio» è l'unico gesto che registra l'esito e, nella stessa transazione, crea fino a tre follow-up, completa quelli precedenti spuntati e chiude le richieste «non prosegue» (una Interaction B1 ciascuna). Il passaggio a «Visita svolta» di un appuntamento collegato richiede una copertura successiva alla visita (`interview.closure_coverage`): passo creato ora, follow-up aperto creato dopo l'inizio della visita e non completato in questa stessa conclusione, altro appuntamento verificato e successivo, oppure richiesta chiusa o in pausa. Non contano l'incontro stesso, gli appuntamenti con esito e i follow-up precedenti. La stessa regola vale per la route V1 `/resoconto`. «Attendere risposta della famiglia» è un follow-up ALTRO con nota predefinita e data di riesame: vale come prossimo passo senza attività artificiali; nessuna nuova azione, stato o esito.

Stati della sezione K: finché l'esito non è «Visita svolta» la conclusione resta compilabile (un esito «non presentata/annullata» si cambia con «Concludi», oppure, se registrato per errore, torna «non ancora registrato» con una spunta esplicita e «Salva»); dopo «Visita svolta» «Salva» accetta solo correzioni (anche il ritorno a «non ancora registrato»), mai il passaggio a svolta. Un doppio «Concludi» è riconosciuto dall'identificativo della conclusione salvato nel colloquio e dagli UUID dei passi.

## DEC-030 — Salvataggio multi-record con impronte e revisioni

Il modulo del colloquio porta, per il colloquio, per ogni alunno mostrato e per la fonte della famiglia, la revisione e un'impronta SHA-256 dei valori normalizzati come mostrati. Record intatto → nessuna scrittura; revisione invariata → scrittura; valori attuali uguali agli inviati → invio ripetuto, nessuna scrittura; valori attuali ancora uguali a quelli mostrati (cambiati altrove solo campi non mostrati, per esempio note o stato della richiesta) → scrittura; altrimenti conflitto: niente si salva (409), si ripresentano i valori inviati con le differenze e la scelta esplicita «mantieni i miei» / «usa gli attuali». Rispetto alla bozza della specifica l'impronta del colloquio comprende anche la bozza della sezione K: una modifica della sola bozza deve salvarsi, mai perdersi. La bozza si salva solo se diversa dalla proposta, e una data effettiva vuota vale «usa la proposta dell'evento»: una pagina aperta prima dell'inizio dell'incontro non fissa una data vuota. Lingue, livelli e codici tolti dal catalogo si ripresentano come «voce non più in elenco», così un record intatto non diventa mai «toccato» né invalido; una richiesta non più della famiglia (dopo «Cambia collegamento») non si ripresenta nella bozza. Servizi dedicati (`leads.update_facts`, `families.update_contact_source`) aggiornano solo i propri campi; le revisioni si concatenano nella transazione. Il modulo usa `novalidate`: tutti i controlli sono sul server, così un campo in una sezione chiusa non blocca «Salva».

## DEC-031 — Riepilogo e storico ricalcolati, mai salvati

Il riepilogo (massimo 12 righe, più 2 per ogni alunno in più) si calcola a ogni apertura da colloquio, richieste, offerte e follow-up; dichiarato e interno sono separati ed etichettati. I dati dell'alunno, che sono quelli attuali, compaiono solo nel colloquio più recente: è l'ultimo colloquio *avvenuto* (con esito, oppure con contenuto e già iniziato), così un incontro futuro soltanto preparato non rende «precedenti» quelli svolti. La proposta mostrata è quella comunicata nel giorno del colloquio, oppure per il più recente quella corrente (o la bozza da comunicare). La scheda famiglia elenca gli incontri in «Appuntamenti e colloqui» e mostra «Ultimo colloquio» in 4 righe; la cronologia titola «Colloquio · tipo: esito».

## DEC-032 — Schema 2 additivo; backup V1 ripristinabili

La versione 2 dello schema aggiunge solo colonne, un indice, due trigger e ricrea il trigger di congelamento delle offerte; un database nuovo e uno migrato hanno lo stesso schema. La migrazione parte al primo avvio dopo la copia pre-operazione obbligatoria. Un backup della V1 si ripristina, supera la verifica delle impronte e viene migrato; se quella migrazione fallisce il ripristino diventa un `RestoreError` e l'app resta in modalità ripristino. Ritorno alla V1: `git checkout v1.0.0` e ripristino della copia «pre-migrazione» (schema 1).

## DEC-033 — Appuntamenti non collegati: resta il flusso V1

Il colloquio si conduce solo su un appuntamento collegato a una famiglia: altrimenti la pagina chiede prima il collegamento. Per gli appuntamenti non collegati la pagina dell'appuntamento conserva i moduli V1 di preparazione e resoconto, fuori dalla regola del prossimo passo come nella V1. Per quelli collegati la scheda «Colloquio» non ha moduli propri: riepilogo, «Prepara e conduci il colloquio», «Registra l'esito».

## DEC-034 — Sviluppo e collaudo isolati dai dati reali

La v1.1 si sviluppa nel worktree separato `C:\Users\ionut\kite-admissions-v1.1` con la propria `.venv`; il checkout `C:\Users\ionut\kite-admissions`, usato dal collegamento sul Desktop sui dati reali, resta su `main` fino al rilascio. Test e collaudo usano solo cartelle temporanee o di prova (`--home`) e dati sintetici; il database reale non viene mai aperto dal codice di sviluppo.
