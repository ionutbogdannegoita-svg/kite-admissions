# KITE Admissions — Sequenza di implementazione

Sequenza degli slice approvata nella richiesta di creazione del repository. Obiettivi, inclusioni, esclusioni e gate riportano soltanto requisiti della [SPEC.md](../SPEC.md), che rimane autorevole. Questo documento non implementa gli slice e non modifica la progettazione.

I riferimenti AC indicano le prove pertinenti della specifica, non risultati già ottenuti. Ogni slice richiede un perimetro approvato secondo il protocollo Ionut; al suo gate si arresta il lavoro, senza iniziare automaticamente il successivo. Si usano dati sintetici. Le protezioni previste dalla specifica si applicano appena viene introdotto il comportamento interessato: la posizione dello Slice 7 non autorizza eliminazioni o migrazioni senza le copie preventive richieste.

## Slice 0 — Foundations

- **Obiettivo:** predisporre le fondamenta dell'applicazione locale approvata.
- **Cosa entra:** Flask + SQLite e avvio Windows/browser secondo SPEC §2; una sola istanza, loopback e arresto locale; separazione codice/dati/configurazione; schema delle sette tabelle, relazioni, transazioni e versione schema del §8; protezioni locali pertinenti del §10.
- **Cosa NON entra:** workflow di famiglie, appuntamenti, offerte e follow-up; collegamento/import Google; implementazione degli slice successivi; cloud, multiutenza o nuovo modello dati.
- **Gate:** verificare le fondamenta di AC01 e AC10 applicabili allo slice: avvio/arresto locale, singola istanza, assenza di esposizione LAN, database identificabile, sette tabelle e vincoli coerenti, protezioni locali pertinenti. Non dichiarare completati i flussi ancora assenti.

## Slice 1 — Family + StudentLead

- **Obiettivo:** gestire famiglia e richieste distinte per figlio/anno.
- **Cosa entra:** campi minimi, creazione/modifica e ricerca; recapiti mancanti ammessi; segnalazione dei possibili duplicati; stati IN_CORSO, IN_PAUSA, ISCRITTO, NON_PROSEGUE e prossimo riesame; relazioni e transazioni coerenti. Le transizioni introdotte persistono già in Interaction secondo B1, anche se la vista timeline arriva nello Slice 4 (SPEC §§3, 5, 7, 8).
- **Cosa NON entra:** import Calendar, collegamento eventi, offerte, workflow FollowUp/Oggi, fusione automatica di dossier, anagrafica scolastica completa.
- **Gate:** prove pertinenti di AC04, AC05 e AC10 su dati sintetici: due fratelli con richieste/esiti distinti, dati mancanti ammessi, doppioni segnalati, nessuna creazione parziale o relazione incoerente, transizioni atomiche senza duplicati e salvataggi obsoleti segnalati.

## Slice 2 — Google Calendar import

- **Obiettivo:** importare e aggiornare selettivamente gli appuntamenti in sola lettura.
- **Cosa entra:** collegamento OAuth desktop read-only e scelta di un calendario; import manuale con anteprima senza preselezioni, paginazione e verifica degli ID già importati; identità calendario/evento, esclusioni, ricorrenze/all-day, errori e ultimo aggiornamento; separazione dati remoti/locali. Cancellazioni e riattivazioni registrano già le Interaction di B1 anche senza famiglia (SPEC §§4, 7, 8).
- **Cosa NON entra:** scritture Calendar, scheduler/webhook/code, import indiscriminato, classificazione AI, famiglie fittizie o collegamento automatico.
- **Gate:** AC02 e parti pertinenti di AC03/AC10: soltanto eventi selezionati, tutte le pagine lette, import ripetuto senza duplicati, spostamenti fuori finestra, cancellazioni confermate distinte dagli errori, riattivazioni persistenti, all-day/ricorrenze corretti e testo sorgente sicuro. Verificare l'assenza di chiamate Google di scrittura.

## Slice 3 — Appointment + family linking

- **Obiettivo:** collegare ogni appuntamento alla famiglia corretta.
- **Cosa entra:** elenco/dettaglio Appuntamenti, stato Da collegare, ricerca e suggerimenti con provenienza, collegamento a famiglia esistente o creazione transazionale da dati confermati, riferimento al figlio facoltativo e coerente, cambio collegamento con le avvertenze previste, apertura in Google Calendar (SPEC §§3–5, 8).
- **Cosa NON entra:** modifica locale dei campi Google, fusione di dossier, spostamento automatico di offerte/follow-up al cambio famiglia, tabella molti-a-molti per i fratelli.
- **Gate:** AC04 e parti pertinenti di AC10: cinque casi sintetici ordinari collegati/creati entro circa 60 secondi ciascuno, esclusa la rete; controllo doppioni, dati mancanti ammessi, nessun salvataggio a metà e nessun collegamento famiglia/figlio incoerente.

## Slice 4 — Family detail + timeline

- **Obiettivo:** rendere consultabili dossier, resoconto e cronologia persistente.
- **Cosa entra:** Scheda famiglia, preparazione/esito visita e resoconto in Appointment, fatti distinti dalle osservazioni locali, note/comunicazioni manuali, timeline dalle registrazioni esistenti e dalle Interaction B1; visibilità delle transizioni di un appuntamento dopo il collegamento, senza ricrearle (SPEC §§3, 4.3, 7, 8).
- **Cosa NON entra:** nuova tabella AuditEvent, audit di ogni campo, versionamento completo dei resoconti, integrazione email o offerte/follow-up non ancora realizzati.
- **Gate:** AC05 e casi B1 di AC03: resoconto in 1–2 minuti, esiti dei fratelli distinti, import che preserva anagrafica e note locali, transizioni consultabili dopo riavvio con tipo/oggetto/timestamp/origine/stati, nessun duplicato o evento per apertura pagina/import invariato.

## Slice 5 — Offers

- **Obiettivo:** conservare le condizioni offerte e la loro storia.
- **Cosa entra:** offerte per famiglia o richiesta, importi in centesimi EUR e periodicità, servizi/condizioni; BOZZA → COMUNICATA, ritiro e nuova versione; contenuto comunicato congelato, data/ora idempotente e proposta corrente secondo SPEC §6; esposizione in scheda/timeline.
- **Cosa NON entra:** secondo approvatore, invio email/messaggi, motore listini/sconti, fatturazione, sblocco di offerte comunicate o ripristino automatico di vecchie condizioni.
- **Gate:** AC06 e vincoli pertinenti di AC10: doppio clic non duplica né cambia la data; nuova bozza non sostituisce la comunicata; nuova comunicazione conserva lo storico; ambito e predecessore coerenti; ritiro senza riattivazioni implicite.

## Slice 6 — FollowUp + Oggi

- **Obiettivo:** rendere visibile il prossimo passo delle pratiche.
- **Cosa entra:** FollowUp APERTO/COMPLETATO/ANNULLATO, scadenza e richiamo; vista Oggi con appuntamenti, Da collegare, scaduti/odierni, resoconti mancanti e pratiche senza prossimo passo; copertura famiglia/figlio, riesame e ultima verifica Calendar secondo SPEC §§3, 4.4, 7, 8.
- **Cosa NON entra:** notifiche esterne, assegnazioni a colleghi, BI/scoring, nuovi stati del funnel o completamenti inventati delle telefonate.
- **Gate:** AC07: completati esclusi dagli aperti; cancellazione dell'unica visita evidenzia la mancanza del prossimo passo; evento non verificato non conta come copertura certa; regole famiglia/figlio e scadenze Europe/Rome corrette; errori e ultima verifica visibili.

## Slice 7 — Backup/export/restore

- **Obiettivo:** completare gestione e recupero locali dei dati secondo SPEC §9.
- **Cosa entra:** copie SQLite coerenti automatiche/manuali e pre-operazione, configurazione non segreta, errori e conservazione prevista; restore verificato incluso B2; export CSV/JSON senza token e con protezione formule; archiviazione reversibile ed eliminazione esplicita del dossier con esclusioni Calendar; pannello tecnico.
- **Cosa NON entra:** backup cloud imposto, sincronizzazione fra PC, importatore generico di CSV, motore retention/GDPR, promessa di cancellazione forense o modifiche a Google.
- **Gate:** AC08 e AC09: backup con database in uso; errore della copia preventiva che blocca l'operazione quando richiesta; restore sia da database sano sia assente/corrotto/non leggibile; integrità, foreign key, valori e relazioni attesi confrontati su dati sintetici; avvisi sui dati/cancellazioni successivi alla copia; nessuna ripresa con restore fallito; export senza token; eliminazione atomica e mancata reimportazione degli eventi esclusi.

## Final acceptance criteria gate

- **Obiettivo:** verificare l'intera V1 rispetto ai dieci criteri concordati.
- **Cosa entra:** prove AC01–AC10 di SPEC §11 su un dataset sintetico piccolo e rappresentativo, inclusi B1 e B2 e i flussi integrati fra gli slice.
- **Cosa NON entra:** nuovi requisiti, funzionalità V2, ottimizzazioni speculative o avvio di attività successive.
- **Gate:** tutti i dieci criteri verificati con evidenze; nessuna dichiarazione di successo per controlli non eseguiti. A criteri soddisfatti, chiudere la V1 secondo il protocollo Ionut e fermarsi.
