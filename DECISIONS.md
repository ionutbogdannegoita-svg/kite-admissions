# KITE Admissions — Decisioni approvate

Registro delle sole decisioni già contenute nella [specifica autorevole](SPEC.md) e confermate nella richiesta di creazione del repository. Non introduce nuove scelte progettuali.

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
