# KITE Admissions v1.1 — Interview Workflow

Specifica operativa · versione 1.0 · 27 settembre 2026 · base `v1.0.0` (`7452456`, schema 1)

**Stato: approvata dal titolare il 27/09/2026, con tutte le Owner Decision risolte (§9).** È la specifica operativa della Issue #2 «KITE Admissions v1.1 — Interview Workflow». È un upgrade *mirato* della V1: non la riscrive, non ne cambia l'architettura e non aggiunge tabelle.

Storia del documento: bozza 0.1 → revisione interna indipendente (§12) → bozza 0.2 → decisioni del titolare → versione 1.0, dopo il consistency check finale (§13).

---

## 0. In breve

1. **Nessuna nuova tabella.** Restano le 7 tabelle della V1. Lo schema passa alla versione 2 con **sole colonne aggiunte**, due trigger nuovi e un trigger ricreato. La migrazione è stata provata su SQLite 3.50.4, con tabelle `STRICT` e dati sintetici (§8.6).
2. **Il colloquio vive nell'appuntamento.** Nella V1 vale già 1 incontro = 1 evento Calendar = 1 riga `Appointment` = 1 resoconto. Una colonna JSON `interview` contiene la parte strutturata; preparazione, esito, data effettiva, sintesi e nota interna della V1 restano dove sono. Lo **storico** viene da sé: ogni incontro è una riga, nessun colloquio sovrascrive l'altro.
3. **Ogni dato nella sua sede.** I dati dell'alunno (data di nascita, scuola e classe attuali, lingue, profilo, flag di approfondimento) vanno nel suo `StudentLead`; la fonte del contatto nella `Family`; l'economia nell'`Offer` (3 campi); il prossimo passo nel `FollowUp` (2 campi). Il colloquio non ricopia niente.
4. **Una pagina «Colloquio»** con *Prepara incontro* e sezioni A–K nell'ordine della conversazione. È un modulo unico:
   - **«Salva»** registra i contenuti in qualsiasi momento, senza perdere nulla;
   - **«Concludi colloquio»** è l'unico gesto che registra l'esito e crea i prossimi passi.
5. **Economia in tre blocchi**: listino standard, condizione riservata (con motivo e chi l'ha autorizzata), importo finale comunicato. Resta «Segna come comunicata» della V1: offerta congelata e versionata.
6. **Nessuna visita «svolta» senza un prossimo passo *nuovo*** (OD-5). Non valgono l'incontro stesso, gli appuntamenti già passati o i follow-up nati prima della visita. Le scelte rapide precompilano le azioni `FollowUp` esistenti: **nessun nuovo stato**.
7. **Riepilogo** di al massimo 12 righe per colloquio e **storico** dei colloqui nella scheda famiglia.
8. **Liste** in un unico modulo Python, `catalog.py`. Nessun motore di configurazione.
9. **Minimizzazione**: niente diagnosi, salute, diete, religione, reddito, situazione familiare, voti. Solo un flag neutro «Approfondimento educativo/documentale necessario» con una nota operativa breve.
10. **9 Owner Decision risolte** (§9):
    - tutte con l'opzione A;
    - la scuola è paritaria;
    - «Attendere risposta della famiglia» vale come prossimo passo;
    - la Secondaria di I grado è confermata.

    Implementazione in 5 slice con gate, in una sola Issue, **nel worktree separato `C:\Users\ionut\kite-admissions-v1.1`**: il collegamento sul Desktop usa il checkout principale sui dati reali (§10).

---

## 1. Analisi del modello attuale (Fase 1)

### 1.1 Cosa esiste già

| Entità / vista | Cosa c'è nella V1 che riguarda il colloquio |
|---|---|
| **Family** | `display_name`; adulto principale e secondo adulto con telefono/email normalizzati; `contact_source` (testo libero con suggerimenti: Passaparola, Sito web, Social, Open day, Telefonata, Email, Segreteria, Altro); `first_contact_on`; `preliminary_notes`; `archived_at` |
| **StudentLead** | una riga per figlio e anno: `display_name` (anche provvisorio), `school_year` (AAAA/AAAA), `grade` (testo con suggerimenti Nido…Secondaria II grado), `origin` («Provenienza o zona»), `birth_year` (**solo l'anno**, SPEC §3), `notes`; 4 stati con `review_on`, `closed_on`, `closure_reason`, `enrollment_ref` |
| **Appointment** | campi `src_*` di Google, aggiornati solo dall'import; `family_id` e `student_lead_id` (NULL = tutta la famiglia); **dati locali della visita**: `preparation`, `visit_outcome` (SVOLTA / NON_PRESENTATA / ANNULLATA), `visited_at`, `visit_report` («fatti e domande»), `local_observations` («osservazioni personali pertinenti, campo distinto») |
| **Offer** | ambito famiglia o richiesta; `version_no` e `previous_offer_id`; `standard_fee_cents`, `proposed_fee_cents`, `periodicity`; `services` JSON `[{description, amount_cents, periodicity, included}]`; `conditions`; `valid_until`; BOZZA → COMUNICATA → RITIRATA; trigger di congelamento; una sola bozza per ambito |
| **FollowUp** | `action` fra RICHIAMARE, INVIARE_INFORMAZIONI, FISSARE_VISITA, ALTRO (DEC-019); `due_on`; `note`; APERTO/COMPLETATO/ANNULLATO con esito; **responsabile implicito Ionut** |
| **Interaction** | note manuali (NOTA/TELEFONATA/EMAIL/RETTIFICA; per vincolo non si riferiscono mai a un appuntamento) e transizioni B1 immutabili (annullamenti/riattivazioni Calendar, cambi di stato delle richieste) |
| **Pagina appuntamento** | dati Google in sola lettura, collegamento famiglia, «Preparazione dell'incontro», «Resoconto della visita» (esito, data, sintesi, osservazioni, prossimo passo facoltativo nella stessa transazione), cronologia dell'evento |
| **Scheda famiglia** | richieste dei figli, follow-up, «Appuntamenti e visite» con estratto del resoconto, proposte versionate, cronologia; di lato prossimo passo e recapiti |
| **Oggi** | appuntamenti, richiami, follow-up, riesami, da collegare, **resoconti da completare**, pratiche senza prossimo passo |

### 1.2 Cosa si riusa così com'è

- `Appointment` come contenitore dell'incontro. `preparation` diventa la **nota di preparazione**, `visit_outcome` + `visited_at` l'**esito**, `visit_report` la **sintesi libera**, `local_observations` la **nota interna** (J). Il significato di ciascun campo non cambia.
- `StudentLead` per nome, anno, classe richiesta, zona (`origin`) e anno di nascita; `Family.contact_source` per la fonte.
- `Offer` con tutto il ciclo di vita: bozza, «Segna come comunicata» idempotente, congelamento, versioni, ritiro.
- `FollowUp` e le sue quattro azioni come prossimo passo. La V1 sa già creare un follow-up con il resoconto, nella stessa transazione.
- `leads.change_status` e le sue transizioni B1 per «non prosegue».
- Le tecniche esistenti:
  - UUID di creazione contro il doppio invio;
  - contatore di revisione contro due schede aperte;
  - transazioni annidate che si uniscono alla principale;
  - `safe_next` per tornare alla pagina di partenza;
  - `data-show-when` in `app.js`;
  - attributo HTML `form="…"`, già usato in `refresh.html`.

### 1.3 Cosa manca davvero

| Bisogno | Manca nella V1 |
|---|---|
| Età e ammissibilità per Infanzia/Primaria | data di nascita completa (oggi solo l'anno) |
| Situazione scolastica attuale | scuola e classe attuali distinte; `origin` mescola «provenienza» e «zona» |
| Lingue, profilo dichiarato, necessità di approfondimento | nessun campo |
| Presenti, motivazioni, esigenze, cosa è stato presentato, domande, valutazione interna | oggi tutto finisce nel testo libero `visit_report` |
| Tipo di incontro (prima visita, incontro economico…) | nessun campo; il titolo Calendar non è affidabile |
| Condizione riservata motivata, chi l'ha autorizzata, quota d'iscrizione | lo sconto è solo la differenza listino − quota, i motivi finiscono nelle note |
| Responsabile e origine del prossimo passo | responsabile implicito; nessun legame follow-up → incontro |
| Pannello di preparazione, riepilogo, storico dei colloqui | nessuna vista aggregata |
| Liste centralizzate | costanti sparse (`CONTACT_SOURCES`, `CHANNELS`, `GRADE_SUGGESTIONS`) |

### 1.4 Cosa NON va duplicato

- Anagrafica, recapiti, dati dell'alunno, anno e classe: si **leggono e si correggono nella loro sede** (Family, StudentLead) e non si copiano nel colloquio.
- Proposte economiche: il colloquio **mostra** l'Offer e usa i suoi servizi; nessun «importo del colloquio».
- Prossimo passo e materiale da inviare: stanno nel `FollowUp`, non in un campo del colloquio.
- Cronologia: nessuna riga `Interaction` per colloqui, spunte o salvataggi.
- Nessun secondo «resoconto» e nessuna seconda «nota interna»: si riusano quelli della V1.

### 1.5 Vincoli della V1 che guidano la proposta

- **Un resoconto per appuntamento**, per scelta (SPEC §8, DEC-009).
- **L'import tocca solo i campi `src_*`** e non la revisione (DEC-018). Quando cambia l'evento su Google, però, imposta `updated_at`/`updated_by = "Calendar import"` (`calendar_import.py:178-181`). Per questo il colloquio porta la **propria** data di salvataggio.
- **Migrazioni**: `MIGRATIONS[n]` gira in transazione dopo una copia pre-operazione riuscita (`db.migrate`). Il ripristino accetta backup con schema da 1 a N e poi li migra.
- **`TABLES` è fisso a sette** e lo usano `inspect`, `table_counts`, `validate_backup`, `table_digests` e l'export (`OPERATIONAL_TABLES = TABLES[:6]`, per posizione). Con una tabella nuova, il database V1 stesso risulterebbe «tabelle mancanti» e i backup V1 non si ripristinerebbero.
- Nelle tabelle `STRICT`, **`ADD COLUMN` con CHECK funziona**, anche con CHECK che citano altre colonne o `REFERENCES` con default NULL. Provato, §8.6.
- **CSP `script-src 'self'; style-src 'self'`**: niente script o attributi `style` inline.
- **«Cambia collegamento»**: il resoconto segue l'appuntamento, mentre offerte e follow-up restano alla famiglia precedente.
- **«Togli dagli appuntamenti»** (DEC-021) è ammesso solo senza dati locali: dovrà contare anche il colloquio.
- **La route V1 `/resoconto` riscrive sempre tutti e quattro i campi del resoconto** (`parse_report`): un modulo che inviasse solo l'esito cancellerebbe sintesi e nota interna.
- **`today.next_steps` conta come coperto ogni appuntamento non ancora finito** (quindi anche quello in corso) e ogni follow-up aperto, anche se precedente alla visita. Non si può usare così com'è per la regola di chiusura (§8.8).
- **Il collegamento «Avvia KITE Admissions» sul Desktop** avvia il codice del checkout `C:\Users\ionut\kite-admissions` sui dati reali di `%LOCALAPPDATA%\KITEAdmissions` (`install.ps1:53-56`, `paths.py:15-18`).
- **Ripristino di un backup con schema precedente**: se la migrazione dopo la sostituzione fallisce, l'errore non è un `RestoreError` e `restore_run` non entra in modalità ripristino (`restore.py:174-176`, `panel.py:115-136`). Nella V1 il caso non si presentava mai; nella v1.1 sì.

---

## 2. La scaletta del colloquio (Fase 2)

La pagina distingue quattro tipi di contenuto, segnalati con piccole etichette:

- **Domanda**: frasi guida da dire alla famiglia; si leggono e non si salvano.
- **Registra**: informazioni da annotare anche senza formularle come domanda.
- **Economico**: sezione I.
- **Interno**: sezione J, da non mostrare né leggere alla famiglia.

| Sezione | Momento | Tipo | Dove si salva |
|---|---|---|---|
| Prepara incontro | prima | sintesi calcolata + nota | lettura da tutte le tabelle; nota in `Appointment.preparation` |
| A. Presenti e dati dell'alunno | inizio | Registra + conferma | presenti → `interview`; dati alunno → `StudentLead` |
| B. Come ci hanno conosciuto | inizio | Domanda | `Family.contact_source` (+ dettaglio) |
| C. Cosa cercano | conversazione | Domanda | `interview.motivations` |
| D. Profilo scolastico (dichiarato) | conversazione | Domanda | `StudentLead.profile_note` e flag |
| E. Lingue (dichiarate) | conversazione | Domanda | `StudentLead.languages` |
| F. Esigenze organizzative | conversazione | Domanda | `interview.needs` |
| G. Cosa abbiamo presentato | visita / presentazione | Registra | `interview.presented` |
| H. Domande e dubbi della famiglia | durante | Registra | `interview.questions` |
| I. Aspetti economici | fine colloquio | Economico | `Offer` (esistente) |
| J. Valutazione interna KITE | dopo, a famiglia uscita | Interno | `interview.assessment` + `local_observations` |
| K. Conclusione e prossimo passo | congedo | Registra | con «Concludi»: esito, `FollowUp`, eventuali chiusure |

La sezione K si concorda **con la famiglia prima che vada via**; la J si può compilare dopo. L'ordine sullo schermo resta A–K, come richiesto.

### A. Presenti e dati dell'alunno

**Scopo:** partire da dati corretti; sapere chi partecipa alla decisione.
**Domande guida:** «Prima di iniziare confermo qualche dato: nome e cognome di vostro figlio, la data di nascita, per quale anno e quale classe?» · «Che scuola frequenta ora, e che classe?»
**Registra:** chi è presente, senza chiederlo.

| Campo | Tipo | Salvato in | Note |
|---|---|---|---|
| Presenti | chip multipli: Madre · Padre · Nonno/nonna · Altro familiare · L'alunno/a | `interview.attendees` | solo la relazione, niente nomi (quelli degli adulti sono già in Family). Niente «tutore»: rivelerebbe una situazione giuridica familiare (§7.2) |
| Nome e cognome alunno | testo | `StudentLead.display_name` | già in V1 |
| Data di nascita | data (facoltativa) | `StudentLead.birth_date` **(nuovo, OD-2)** | l'anno di nascita V1 si allinea da solo |
| Età | calcolata | — | età oggi e anni compiuti al 31/12 dell'anno di inizio; verifica età (§8.4.6) |
| Anno scolastico, classe richiesta | select, testo con suggerimenti | `school_year`, `grade` | già in V1 |
| Scuola attuale / classe attuale | testo ≤ 200 / testo ≤ 80 | `current_school`, `current_grade` **(nuovi)** | per l'Infanzia anche «nido» o «nessuna» |
| Città/zona di provenienza | testo ≤ 200 | `StudentLead.origin` | campo V1 con etichetta chiarita; contenuto esistente invariato |

Con più figli nell'incontro (appuntamento riferito a tutta la famiglia) compare un blocco per ogni richiesta aperta. Se manca un figlio: «Salva e aggiungi un figlio» (§8.4.3).

### B. Come ci hanno conosciuto

**Domanda guida:** «Come avete conosciuto KITE?»

| Campo | Tipo | Salvato in |
|---|---|---|
| Fonte | lista chiusa (OD-7): Google / sito web · Social network · Passaparola · Altra famiglia KITE · Evento / open day · Territorio · Altro. Un valore V1 fuori lista resta selezionato come «valore precedente» | `Family.contact_source` |
| Dettaglio | testo ≤ 200, facoltativo | `Family.contact_source_detail` **(nuovo)** |

Testo d'aiuto: «Solo se utile, per esempio quale evento o quale famiglia vi ha segnalati, per ringraziare o per ricordarlo. Nessun altro dato della famiglia che vi ha segnalati.»

### C. Cosa cercano

**Domande guida:** «Cosa vi porta a valutare una nuova scuola?» · «Cosa cercate principalmente nel prossimo percorso scolastico?» · «C'è qualcosa che vorreste fosse diverso rispetto all'esperienza attuale?» · «Quali aspetti considerate prioritari nella scelta?»

| Campo | Tipo | Salvato in |
|---|---|---|
| Motivazioni | chip multipli: Approccio didattico · Lingue · Ambiente internazionale · Attenzione individuale · Dimensione delle classi · Continuità educativa · Disciplina e regole · Inclusione · Attività sportive · Attività artistiche · Orari · Servizi · Logistica · Altro | `interview.motivations` |
| Nota | testo ≤ 600 | `interview.motivations_note` |

Testo d'aiuto: «Registra ciò che la famiglia *cerca*, con parole neutre. Niente giudizi sulla scuola attuale, sugli insegnanti o su altre persone.» Si consiglia di segnare al massimo tre priorità, senza limite rigido.

### D. Profilo scolastico (dichiarato dalla famiglia)

**Domande guida:** «Come sta vivendo la scuola quest'anno?» · «Quali attività o materie preferisce?» · «C'è un'area in cui pensate abbia bisogno di più supporto?»

| Campo | Tipo | Salvato in |
|---|---|---|
| Profilo dichiarato | testo ≤ 500 | `StudentLead.profile_note` **(nuovo)** |
| Approfondimento educativo/documentale necessario | casella | `StudentLead.educational_review` **(nuovo)** |
| Nota operativa | testo ≤ 300, visibile solo con la casella | `StudentLead.educational_review_note` **(nuovo)** |

Testi d'aiuto:

- sul profilo: «Riassumi in modo neutro interessi e andamento. Diagnosi, certificazioni, terapie e dettagli sulla salute **non si scrivono, nemmeno se la famiglia li cita**: in quel caso spunta "Approfondimento" e annota solo l'azione. Le valutazioni KITE vanno nella sezione J»;
- sul flag: «Indica solo l'azione, per esempio "colloquio con il coordinamento didattico prima dell'inserimento" o "chiedere la documentazione all'iscrizione"».

Togliendo la casella, la nota si svuota (il vincolo del database lo impone).

### E. Lingue (dichiarate)

**Domande guida:** «Quali lingue parla o sente a casa? Con che livello, indicativamente?» · «Ha già frequentato contesti bilingui o internazionali?»

| Campo | Tipo | Salvato in |
|---|---|---|
| Italiano, Inglese, Francese | livello: — · Nessuna esposizione · Base · Intermedio · Avanzato · Madrelingua/bilingue (dichiarato) | `StudentLead.languages` **(nuovo, JSON)** |
| Altra lingua (fino a 2) | nome ≤ 40 + livello | idem |
| Contesti bilingui/internazionali | Sì · No · Non so | `StudentLead.bilingual_context` **(nuovo)** |

Testo d'aiuto: «Livelli dichiarati dalla famiglia: non sono una valutazione linguistica né una certificazione.»

### F. Esigenze organizzative

**Domande guida:** «Che orari vi servono, in entrata e in uscita?» · «Vi interessano pre-scuola, post-scuola, mensa, trasporto, corsi pomeridiani?» · «Ci sono giorni o orari critici?» · «Da quando vorreste iniziare?» · «Ci sono fratelli o sorelle che frequentano già KITE?»

| Campo | Tipo | Salvato in (`interview.needs`) |
|---|---|---|
| Servizi di interesse | chip: Pre-scuola · Post-scuola · Mensa · Trasporto · Corsi extracurricolari | `services` |
| Attività di interesse | testo ≤ 200 | `activities` |
| Orario desiderato | testo ≤ 200 | `schedule` |
| Giorni/orari critici, logistica | testo ≤ 400 | `constraints` |
| Fratelli/sorelle già frequentanti KITE | casella | `siblings_enrolled` |
| Data auspicata di ingresso | data | `desired_start` |

- «Inserimento in corso d'anno» **si ricava** dalla data (ingresso dopo il 30/09 dell'anno di inizio): nessun campo in più.
- «Altri figli interessati» non è una casella: è il pulsante «Salva e aggiungi un figlio», cioè una nuova richiesta.
- Sopra i campi compaiono in sola lettura i fratelli già presenti nel CRM, con il loro stato.
- Testo d'aiuto sulla mensa: «Allergie, intolleranze o diete non si registrano qui: si raccolgono all'iscrizione con la modulistica della mensa.» Per il trasporto basta la zona (A), non l'indirizzo.

### G. Cosa abbiamo presentato

**Registra**, senza domanda: è la lista di ciò che il referente deve ricordarsi di spiegare.

Chip: Progetto educativo · Impostazione internazionale · Lingue · Orari · Calendario scolastico · Mensa · Divisa · Attività · Servizi · Extracurricolari · Comunicazione scuola-famiglia · Inserimento e ambientamento *(solo Infanzia e Primaria 1ª)* · Costi · Processo di iscrizione → `interview.presented`.

Le spunte **non creano voci in cronologia**. Servono da guida e compaiono nel riepilogo come «non ancora presentato: …», utile per l'incontro successivo.

### H. Domande e dubbi della famiglia

**Registra.** Righe brevi, non un blocco unico: domanda (≤ 300), risposta data (≤ 500), «da verificare» (casella) → `interview.questions`. Si vedono le righe salvate più 3 righe vuote, fino a 12, senza JavaScript. Una risposta senza domanda è un errore di validazione.
Le righe «da verificare» possono diventare un follow-up alla chiusura (K).

### I. Aspetti economici

La sezione **non ha campi propri**: mostra e apre le `Offer` dell'ambito del colloquio (§8.7).

- Per ogni ambito pertinente compaiono la proposta corrente e l'eventuale bozza in blocchi: *Listino standard* · *Condizione riservata* · *Importo finale comunicato* · *Servizi scelti*, con anno e classe dell'ambito in testata. Gli ambiti pertinenti sono la richiesta dell'incontro e la proposta familiare; per un incontro di famiglia, ogni richiesta aperta.
- Tutte le azioni passano dal **modulo del colloquio** (§8.4.3):
  - «Salva e crea la proposta» / «Salva e modifica la bozza»: si salva il colloquio e si apre il modulo dell'offerta, che poi riporta al colloquio;
  - «Salva e crea nuova versione» e «Salva e segna come comunicata» (canale predefinito «Di persona»): il server le esegue nello stesso salvataggio.

  «Segna come comunicata» mantiene il comportamento V1: data unica, congelamento, idempotenza.
- Promemoria neutro: «In famiglia risultano: Anna · Infanzia 2028/2029 · In corso. Nessuna riduzione viene applicata automaticamente.»

### J. Valutazione interna KITE

Sezione con bordo tratteggiato ed etichetta **«Interno KITE — non condividere con la famiglia»**. È breve.

| Campo | Valori | Salvato in |
|---|---|---|
| Interesse percepito | Basso · Medio · Alto · Molto alto (mai «probabilità») | `interview.assessment.interest` |
| Tempistica della decisione | Immediata · Pochi giorni · Alcune settimane · Da definire | `…timing` |
| Principale driver | Didattica · Lingue · Ambiente · Servizi · Logistica · Attività · Prezzo · Altro | `…driver` |
| Principale ostacolo | Nessuno evidente · Costo · Distanza · Trasporto · Orari · Confronto con altre scuole · Disponibilità posti · Dubbio didattico · Altro | `…obstacle` |
| Nota interna | testo | `Appointment.local_observations` (campo V1) |

Testo d'aiuto: «Scrivi in modo professionale e fattuale, come se la famiglia potesse leggerla. Se chiede di accedere ai propri dati (GDPR, art. 15), anche questa nota può doverle essere comunicata.»

### K. Conclusione e prossimo passo

**Domanda guida:** «Allora ci sentiamo… / vi mandiamo… entro…: va bene?»

Tutto ciò che è in K diventa operativo **solo con «Concludi colloquio»**. Fino ad allora «Salva» ne conserva una bozza (`interview.closing_draft`), così nulla si perde.

| Campo | Tipo | Alla conclusione diventa |
|---|---|---|
| Esito dell'incontro | Visita svolta · Famiglia non presentata · Annullata o rinviata | `visit_outcome` (V1) |
| Data e ora effettive | datetime; proposta dall'evento | `visited_at` (V1) |
| Scelta rapida | Richiamo · Seconda visita · Invio proposta · Invio documenti · Attendere risposta · Avvio iscrizione · Non prosegue | **niente**: precompila i campi sotto (JavaScript) |
| Prossimo passo (obbligatorio salvo copertura, §8.8) | azione (le 4 azioni V1 più la voce «Attendere risposta della famiglia») · entro il / riesame il · responsabile · riguarda · nota | `FollowUp` n. 1 |
| Altro passo (facoltativo) | stessi campi | `FollowUp` n. 2 |
| Materiale da inviare | chip: Presentazione/brochure · Rette e servizi · Calendario scolastico · Orari e servizi · Modulo di iscrizione · Regolamento/patto di corresponsabilità · Informativa privacy · Altro (+ nota) | «Materiale: …» nella nota del passo «Inviare informazioni»; se nessun passo ha quell'azione: errore che lo chiede |
| Verifiche | righe H «da verificare» + casella «crea il passo "Verificare e rispondere"» + entro il | `FollowUp` n. 3 (ALTRO), con la nota composta dalle righe H **inviate in quel momento** |
| Follow-up aperti da prima dell'incontro | elenco con casella «completato con questo incontro» (non spuntata di default) | chiusura V1 COMPLETATO («con il colloquio del …») |
| Non prosegue | per ogni richiesta aperta dell'ambito: casella + motivo (precompilato con l'ostacolo) | transizione V1 `NON_PROSEGUE`, una Interaction ciascuna |
| Sintesi libera | testo facoltativo | `visit_report` (V1), salvabile anche con «Salva» |

Le scelte rapide sono scorciatoie verso ciò che esiste, **non stati nuovi**. I giorni sono quelli approvati (OD-7).

| Scelta rapida | Precompila |
|---|---|
| Richiamo | RICHIAMARE, entro 3 giorni |
| Seconda visita | FISSARE_VISITA, entro 2 giorni (l'orario lo fissa la segreteria in Calendar) |
| Invio proposta | INVIARE_INFORMAZIONI «Proposta economica», entro 1 giorno |
| Invio documenti | INVIARE_INFORMAZIONI, entro 2 giorni (il materiale scelto va nella nota) |
| Attendere risposta | «Attendere risposta della famiglia», riesame fra 3 giorni (tempistica in J immediata o pochi giorni), 14 (alcune settimane) o 7 (da definire o non indicata) |
| Avvio iscrizione | INVIARE_INFORMAZIONI «Modulo di iscrizione»: «Iscritto» resta la conferma amministrativa della V1 |
| Non prosegue | nessun passo; spunta la chiusura delle richieste dell'ambito |

**«Attendere risposta della famiglia»** (OD-5) è un prossimo passo valido quando la famiglia valuta e KITE non deve fare nulla fino alla data di riesame: niente attività artificiali.

- Nel menu «Azione» è una voce a sé; il server la registra come follow-up `ALTRO` con la nota predefinita «Attendere risposta della famiglia» (più l'eventuale nota scritta) e con la data di riesame come scadenza.
- In Oggi compare quel giorno fra i follow-up in scadenza.
- Non c'è una nuova azione, perché il vincolo `CHECK` di `FollowUp.action` costringerebbe a ricostruire la tabella.
- Non c'è un cambio di stato della richiesta: «In pausa» resta per le pratiche davvero sospese.

«Interessato», fra gli esiti citati nella richiesta, non è un passo ma un livello di interesse: sta in J.

**Per fascia**, solo dove serve: la verifica dell'età vale per Infanzia e Primaria 1ª; «Inserimento e ambientamento» compare in G solo per Infanzia e Primaria 1ª. Tutto il resto è comune. La fascia si riconosce dal testo della classe richiesta («Infanzia…», «Primaria…», «Secondaria I grado…»); se non si riconosce, nessun aiuto specifico.

---

## 3. Prepara incontro (Fase 3)

È in cima alla pagina del colloquio ed è **quasi tutto calcolato**: l'unico campo scritto è la nota di preparazione, già presente nella V1.

```
PREPARA INCONTRO            Tipo di incontro [Prima visita ▾]    Riguarda [Luca · 2027/2028 ▾]
Famiglia Esempio Rossi · 2 adulti · telefono ✓ email ✓ · fonte: Altra famiglia KITE · dal 20/09/2026
Luca   nato il 14/03/2021 · 5 anni (6 al 31/12/2027: età regolare per la 1ª) · Primaria 1ª 2027/2028
       ora: Infanzia «Esempio» · In corso
Fratelli: Anna · Infanzia 2028/2029 · In corso
Incontri precedenti: nessuno            Proposte: nessuna            Follow-up aperti: nessuno
Note: «preferiscono incontri dopo le 17» (note preliminari) · Telefonata 25/09: «chiesto del trasporto»
DA CHIEDERE O CONFERMARE
  ☐ lingue di Luca   ☐ esigenze organizzative (mai raccolte)   ☐ data di nascita di Anna
Nota di preparazione [ cosa mostrare, cosa chiedere, documenti da preparare…                    ]
```

Contenuto:

- **Famiglia**: etichetta, numero di adulti, recapiti presenti sì/no, fonte e dettaglio, primo contatto, note preliminari.
- **Alunni dell'ambito**: nome, data di nascita ed età con verifica, anno e classe, scuola attuale, stato, badge neutro «Approfondimento» se il flag è attivo.
- **Fratelli**: le altre richieste della famiglia, con anno, classe e stato.
- **Incontri precedenti**: data, tipo, esito, interesse, ostacolo, prossimi passi, con link al riepilogo.
- **Proposte**: la corrente per ambito (importo finale e validità), l'eventuale bozza, il numero di versioni.
- **Follow-up aperti**, con l'eventuale colloquio d'origine, e **ultime 3 note manuali** della cronologia.
- **Da chiedere o confermare**, calcolato: recapito mancante, fonte mancante; per ogni alunno data di nascita, anno, classe, scuola attuale, lingue mancanti; motivazioni o esigenze mai raccolte in nessun colloquio.
- «Tipo di incontro» e «Riguarda» si scelgono qui e si salvano con il colloquio.

Per un appuntamento **non collegato** il pannello chiede prima di collegare o creare la famiglia (flusso V1); la pagina del colloquio non si compila.

---

## 4. Riepilogo colloquio (Fase 4)

Vista in sola lettura, **sempre ricalcolata e mai salvata**. Compare:

- in cima alla pagina dell'appuntamento;
- nella scheda famiglia («Ultimo colloquio»);
- nello storico.

Al massimo **12 righe**, più 2 per ogni alunno in più; lettura in 10–15 secondi.

```
RIEPILOGO · Prima visita · gio 30/09/2026 15:05 · 1° incontro · presenti: madre, padre, alunno
Luca · 5 anni (6 al 31/12/2027, regolare) · Primaria 1ª 2027/2028 · ora: Infanzia «Esempio»
Lingue (dichiarate): IT madrelingua · EN base · FR nessuna esposizione
Cerca (dichiarato): Lingue · Ambiente internazionale · Attenzione individuale — «inglese ogni giorno»
Esigenze: post-scuola, mensa · uscita 16:30 · ingresso a settembre · fratelli a KITE: no
Dubbi: 3 domande, 1 da verificare: «trasporto dalla zona Esempio?»
Proposta v1 comunicata il 30/09 · listino 5.400,00 € annuale · riservata −540,00 € (Fratelli)
         · finale 4.860,00 € annuale · iscrizione 300,00 € · valida fino al 31/10/2026
Interno KITE: interesse ALTO · decisione: alcune settimane · driver: lingue · ostacolo: trasporto
Prossimi passi: Inviare informazioni entro 02/10 · Segreteria · «Materiale: rette, modulo…» [Aperto]
                Verificare e rispondere entro 03/10 · Ionut [Aperto]
Non ancora presentato: calendario, divisa
```

Regole:

- **Dichiarato** e **interno** sono separati ed etichettati.
- **Dati dell'alunno** (righe 2–3), cioè i valori *attuali* della richiesta: compaiono solo nel riepilogo del **colloquio più recente**. Nei colloqui precedenti c'è una riga «Dati dell'alunno: vedi richiesta (valori attuali)». Così un riepilogo vecchio non attribuisce a quell'incontro dati raccolti dopo (§11).
- **Proposta**: per ogni ambito si mostra la versione comunicata il giorno del colloquio (data di Roma di `communicated_at` = giorno di `visited_at`). Se non c'è, il colloquio più recente mostra la proposta corrente con la sua data; i precedenti «nessuna proposta comunicata in questo incontro».
- **Prossimi passi**: i follow-up nati da questo colloquio (`FollowUp.appointment_id`), con il loro stato attuale.
- Le righe vuote non compaiono.
- Un resoconto V1 (senza `interview`) mostra esito, data e sintesi come oggi.

---

## 5. Storico (Fase 5)

**Nessuna nuova entità: il modello attuale basta.** La dimostrazione è nell'OD-1.

- Ogni incontro è un evento Calendar e quindi una riga `Appointment` a sé, con chiave unica `(calendar_id, google_event_id)`. Prima visita, seconda visita, incontro economico e incontro con la Direzione sono **quattro righe, quattro colloqui**: nessuno sovrascrive l'altro.
- **«Tipo di incontro»** (`interview.kind`: Prima visita · Seconda visita · Incontro economico · Incontro con la Direzione · Altro) li rende riconoscibili. Il **numero d'ordine** («2° incontro con la famiglia») si calcola dalle visite svolte ordinate per data.
- Modificare un colloquio cambia solo quel colloquio. «Ultimo salvataggio del colloquio» usa `interview.saved_at`, non `Appointment.updated_*`, che l'import può impostare a «Calendar import». Come nella V1, **non c'è versionamento del singolo resoconto**.
- Nella scheda famiglia la tabella «Appuntamenti e visite» diventa **«Appuntamenti e colloqui»**:

```
Quando            Incontro                     Esito          Interesse   Ostacolo   Prossimi passi
mar 12/10 10:00   Incontro con la Direzione    Visita svolta  Molto alto  —          Modulo iscrizione ✓ completato
mer 06/10 17:00   Incontro economico · Luca    Visita svolta  Alto        Costo      Richiamare entro 09/10 · aperto
gio 30/09 15:00   Prima visita · Luca          Visita svolta  Alto        Trasporto  Inviare informazioni ✓
```

Ogni riga apre il riepilogo; «Resoconto da completare» resta per gli appuntamenti passati senza esito.

Un incontro **non presente in Calendar** (una famiglia che si presenta senza appuntamento) resta fuori perimetro come nella V1: la segreteria crea l'evento, anche a posteriori, e si preme «Aggiorna». Vedi OD-6.

---

## 6. Liste configurabili (Fase 6)

Un solo modulo, **`kite_admissions/catalog.py`**, con tuple ordinate di voci `(codice, etichetta)` e, dove serve, un attributo: `cicli` per le voci legate a una fascia, `richiede_autorizzazione` per i motivi di sconto. Niente tabella, niente pagina di amministrazione, niente JSON modificabile a mano: cambiare una lista è una piccola modifica di codice, testata.

| Lista | Codici (etichette in §2) | Usata da |
|---|---|---|
| `MEETING_KINDS` | PRIMA_VISITA, SECONDA_VISITA, INCONTRO_ECONOMICO, INCONTRO_DIREZIONE, ALTRO | colloquio |
| `ATTENDEE_RELATIONS` | MADRE, PADRE, NONNI, ALTRO_FAMILIARE, ALUNNO | A |
| `CONTACT_SOURCES` | lista chiusa (OD-7); *etichette salvate come testo*, come nella V1 | B, moduli famiglia |
| `MOTIVATIONS` | APPROCCIO_DIDATTICO, LINGUE, AMBIENTE_INTERNAZIONALE, ATTENZIONE_INDIVIDUALE, DIMENSIONE_CLASSI, CONTINUITA, DISCIPLINA, INCLUSIONE, SPORT, ARTE, ORARI, SERVIZI, LOGISTICA, ALTRO | C |
| `LANGUAGES`, `LANGUAGE_LEVELS` | IT, EN, FR (+ «altra»); NESSUNA, BASE, INTERMEDIO, AVANZATO, MADRELINGUA | E |
| `SERVICES_OF_INTEREST` | PRE_SCUOLA, POST_SCUOLA, MENSA, TRASPORTO, EXTRACURRICOLARI | F |
| `PRESENTATION_TOPICS` | PROGETTO_EDUCATIVO, INTERNAZIONALE, LINGUE, ORARI, CALENDARIO, MENSA, DIVISA, ATTIVITA, SERVIZI, EXTRACURRICOLARI, COMUNICAZIONE, INSERIMENTO *(Infanzia, Primaria 1ª)*, COSTI, ISCRIZIONE | G |
| `MATERIALS` | PRESENTAZIONE, LISTINO, CALENDARIO, ORARI, MODULO_ISCRIZIONE, REGOLAMENTO, INFORMATIVA_PRIVACY, ALTRO | K |
| `INTEREST_LEVELS`, `DECISION_TIMINGS` | BASSO…MOLTO_ALTO; IMMEDIATA, POCHI_GIORNI, ALCUNE_SETTIMANE, DA_DEFINIRE | J |
| `DRIVERS`, `OBSTACLES` | DIDATTICA…ALTRO; NESSUNO, COSTO, DISTANZA, TRASPORTO, ORARI, ALTRE_SCUOLE, POSTI, DUBBIO_DIDATTICO, ALTRO | J |
| `REDUCTION_REASONS` | FRATELLI (no autorizz.), PAGAMENTO_ANNUALE (no), PROMOZIONE (sì), ACCORDO_DIREZIONE (sì), ALTRO (sì, nota obbligatoria) | Offer |
| `QUICK_OUTCOMES` | le 7 scelte rapide di K, con azione, giorni e nota; «Attendere risposta» con i giorni legati alla tempistica | K (solo interfaccia) |
| `GRADE_SUGGESTIONS` | classe richiesta: Infanzia · Primaria 1ª–5ª · Secondaria I grado 1ª–3ª (OD-7). Classe attuale: le stesse più «Nido». Il campo resta testo libero con suggerimenti, come nella V1 | A, moduli richiesta |
| Suggerimenti | responsabili (Ionut, Segreteria, Direzione, Coordinamento didattico); autorizzatori (Direzione, Ionut, Amministrazione) | datalist |
| `AGE_RULES` | Infanzia: 3 anni entro il 31/12, anticipo entro il 30/04 successivo; Primaria 1ª: 6 anni, stesse date. Scuola paritaria (OD-2): verifica solo informativa | verifica età |

Regole:

- Si salvano i **codici** (stabili), non le etichette. L'unica eccezione è la fonte, testo come nella V1, per non migrare i dati esistenti.
- **Codice sconosciuto in ingresso** → errore di validazione.
- **Codice già salvato ma tolto dalla lista** → conservato, mostrato come «CODICE (voce non più in elenco)» e rinviato così com'è al salvataggio successivo.
- Cambiare un'etichetta o aggiungere una voce non richiede migrazione.

---

## 7. Privacy e minimizzazione (Fase 7)

### 7.1 Ogni campo nuovo ha una finalità operativa

| Campo | Cosa cambia (decisione · comunicazione · offerta · follow-up) |
|---|---|
| Data di nascita, età | ammissibilità e anticipo (Infanzia/Primaria), classe corretta |
| Scuola/classe attuali | classe di inserimento, eventuale nulla osta per trasferimento in corso d'anno |
| Lingue, contesto bilingue | inserimento nel percorso linguistico, eventuale supporto per l'italiano |
| Profilo dichiarato | preparazione del coordinamento all'inserimento |
| Flag approfondimento + nota | un passo operativo prima dell'inserimento (colloquio, documentazione) |
| Presenti | chi decide; se manca qualcuno, tempistica e nuovo incontro |
| Fonte + dettaglio | ringraziare chi ha segnalato; sapere quali eventi funzionano |
| Motivazioni, driver | cosa mettere in evidenza nella proposta e nel seguito |
| Esigenze e servizi | servizi da inserire nella proposta; fattibilità di orari e trasporto |
| Data di ingresso | posti, inserimento in corso d'anno, eventuale quota parziale |
| Presentato | cosa manca da spiegare nell'incontro successivo |
| Domande/verifiche | risposte da dare (follow-up) |
| Interesse, tempistica, ostacolo | priorità e data del richiamo, argomento da affrontare |
| Riduzioni, autorizzazione | cosa è stato concesso, perché e da chi: coerenza tra famiglie, rinnovi |
| Responsabile, legame con l'incontro | chi fa il passo e da quale incontro nasce |

### 7.2 Campi da NON introdurre (anche se «utili»)

| Non si raccoglie | Perché / alternativa |
|---|---|
| Diagnosi, certificazioni (L. 104, DSA, BES), PEI/PDP, terapie, farmaci | dati sanitari: solo il flag neutro con nota operativa; il profilo lo vieta nel testo d'aiuto |
| Allergie, intolleranze, diete, menù religiosi | dati sanitari o religiosi: si raccolgono all'iscrizione con la modulistica della mensa |
| Religione, scelta dell'insegnamento della religione cattolica | dato religioso, non serve all'ammissione |
| Opinioni politiche, orientamenti, origine etnica, cittadinanza | non pertinenti; per le lingue bastano i livelli dichiarati |
| Reddito, ISEE, professione, datore di lavoro | V1 già esclusi; le esigenze di orario si esprimono in F |
| Situazione familiare (separazioni, affido, tutela, conflitti) | niente «tutore» fra i presenti; la responsabilità genitoriale riguarda l'iscrizione, non Admissions |
| Giudizi su persone, sulla scuola precedente, sugli insegnanti | la nota interna resta professionale e fattuale |
| Voti, pagelle, test d'ingresso | Admissions non è una valutazione formale |
| Indirizzo completo, codice fiscale, documenti, foto | V1 già esclusi; per il trasporto basta la zona |
| Nomi dei presenti e dei fratelli fuori CRM | basta la relazione o la casella |
| Punteggio o «probabilità di iscrizione» | SPEC §12: niente scoring; interesse su 4 livelli |
| Allegati | fuori V1 (SPEC §12) |

### 7.3 Testi d'aiuto e controlli

- Testi d'aiuto nelle sezioni C, D (profilo e flag), E, F (mensa), H e J, con le formule del §2.
- Un test verifica che codici ed etichette del catalogo non contengano i termini: `diagnos`, `certific`, `104`, `DSA`, `BES`, `PEI`, `PDP`, `terapi`, `farmac`, `sanitar`, `salute`, `allerg`, `intoller`, `dieta`, `religi`, `politic`, `etnia`, `cittadinanza`, `reddito`, `ISEE`, `professione`, `separa`, `affido`, `tutore`.
- Nessun dato dei moduli nei log (come nella V1).

### 7.4 Fuori dal software, da verificare prima dell'uso

L'informativa per le famiglie del processo Admissions dovrebbe coprire le nuove categorie: data di nascita del minore, scuola attuale, lingue, esigenze, note del colloquio. È un controllo da fare con chi segue la privacy della scuola. Non blocca l'implementazione. La conservazione segue il riesame manuale della V1 (SPEC §10).

---

## 8. Proposta tecnica (Fase 8)

### 8.1 Mappa dei campi già presenti nella V1

| Requisito | Campo V1 | In v1.1 |
|---|---|---|
| Nome famiglia | `Family.display_name` | invariato, in sola lettura nel colloquio |
| Nome e cognome alunno | `StudentLead.display_name` | modificabile in A |
| Anno scolastico, classe richiesta | `school_year`, `grade` | modificabili in A |
| Città/zona di provenienza | `StudentLead.origin` | etichetta «Città o zona di provenienza»; dati invariati |
| Anno di nascita | `birth_year` | resta; allineato alla data di nascita se presente |
| Fonte del contatto | `Family.contact_source` | lista chiusa (OD-7); valore V1 conservato |
| Primo contatto, note preliminari | `first_contact_on`, `preliminary_notes` | lettura nel pannello Prepara |
| Note di preparazione | `Appointment.preparation` | nel pannello Prepara |
| Esito e data effettiva | `visit_outcome`, `visited_at` | in K, con «Concludi»; regole V1 invariate |
| Sintesi | `visit_report` | «Sintesi libera» in K |
| Nota interna | `local_observations` | in J |
| Proposta economica | `Offer.*` | sezione I + blocchi |
| Prossimo passo | `FollowUp.*` | in K, con responsabile e legame all'incontro |
| Chiusura della richiesta | `StudentLead.status` + Interaction B1 | «Non prosegue» in K; «Iscritto» invariato |

### 8.2 Campi mancanti (da aggiungere)

| Tabella | Colonna | Tipo e vincolo | Scopo |
|---|---|---|---|
| Family | `contact_source_detail` | TEXT | dettaglio della fonte |
| StudentLead | `birth_date` | TEXT data valida; se c'è l'anno, deve coincidere | età e ammissibilità (OD-2) |
| StudentLead | `current_school`, `current_grade` | TEXT | situazione attuale |
| StudentLead | `languages` | TEXT JSON array, default `'[]'` | lingue dichiarate |
| StudentLead | `bilingual_context` | INTEGER NULL/0/1 | contesti bilingui |
| StudentLead | `profile_note` | TEXT | profilo dichiarato |
| StudentLead | `educational_review` | INTEGER 0/1, default 0 | flag neutro |
| StudentLead | `educational_review_note` | TEXT, ammessa solo con flag = 1 | nota operativa |
| Appointment | `interview` | TEXT JSON object, default `'{}'` | colloquio strutturato |
| Offer | `reductions` | TEXT JSON array, default `'[]'` | riduzioni motivate (OD-3a) |
| Offer | `enrollment_fee_cents` | INTEGER ≥ 0 | quota d'iscrizione comunicata (OD-3b) |
| Offer | `authorized_by` | TEXT | chi ha autorizzato la condizione riservata (OD-3c) |
| FollowUp | `appointment_id` | TEXT FK → Appointment, coerente con la famiglia (trigger) | incontro da cui nasce il passo |
| FollowUp | `assignee` | TEXT | responsabile, come etichetta (OD-4) |

**Formato di `Appointment.interview`** (versione 1; chiavi assenti = non compilato):

```json
{
  "format": 1,
  "saved_at": "2026-09-30T13:52:10.000000Z",
  "kind": "PRIMA_VISITA",
  "attendees": ["MADRE", "PADRE", "ALUNNO"],
  "motivations": ["LINGUE", "AMBIENTE_INTERNAZIONALE"],
  "motivations_note": "inglese ogni giorno",
  "needs": {
    "services": ["POST_SCUOLA", "MENSA"],
    "activities": "", "schedule": "uscita 16:30", "constraints": "",
    "siblings_enrolled": false, "desired_start": "2027-09-13"
  },
  "presented": ["PROGETTO_EDUCATIVO", "LINGUE", "COSTI"],
  "questions": [{"q": "C'è il trasporto dalla zona Esempio?", "a": "Da verificare le fermate", "verify": true}],
  "assessment": {"interest": "ALTO", "timing": "ALCUNE_SETTIMANE", "driver": "LINGUE", "obstacle": "TRASPORTO"},
  "closing_draft": {"outcome": "SVOLTA", "visited_at": "…", "steps": [{"action": "INVIARE_INFORMAZIONI", "due_on": "2026-10-02",
                    "assignee": "Segreteria", "lead": null, "note": "…"}], "materials": ["LISTINO"], "verify": true,
                    "verify_due_on": "2026-10-03", "complete_followups": [], "close_leads": [], "closure_reason": ""}
}
```

`closing_draft` esiste solo prima della conclusione e viene rimosso da «Concludi colloquio». `saved_at` è la data dell'ultimo salvataggio del colloquio.

**Formato di `StudentLead.languages`**: `[{"code": "IT", "level": "MADRELINGUA"}, {"code": "EN", "level": "BASE"}, {"code": "ALTRA", "name": "…", "level": "MADRELINGUA"}]`, solo le voci compilate.

**Formato di `Offer.reductions`** (fino a 4 righe, sulla **retta**, stessa periodicità): `[{"reason": "FRATELLI", "amount_cents": 54000, "note": ""}]`.

Tutti i JSON sono validati **nel servizio Python** con le liste del catalogo; il database garantisce che siano JSON validi del tipo giusto. È la stessa impostazione della V1 per `Offer.services` e `Appointment.src_contacts`.

### 8.3 Campi da NON introdurre

Quelli del §7.2, più queste scelte di modello:

- nessuna tabella `Interview`, `Student`, `Contact`, `PriceList`, `Discount` o `Attendee`;
- nessun nuovo stato di `StudentLead`, nessun nuovo `visit_outcome`, nessuna nuova azione `FollowUp`, nessun nuovo tipo di `Interaction`. «Attendere risposta della famiglia» è un follow-up `ALTRO` con nota predefinita (§2 K);
- nessun «esito del colloquio» salvato come stato: le scelte rapide precompilano i follow-up;
- nessun campo «materiale» o «importo» nel colloquio: il materiale finisce nella nota del follow-up, gli importi nell'`Offer`;
- niente «inserimento in corso d'anno» (si ricava dalla data), niente «altri figli interessati» (si crea la richiesta), niente «età tipica per classe»;
- nessun legame `Offer → Appointment` (§8.7);
- niente «condotto da» (persona KITE): la V1 ha un solo utilizzatore (finding differiti);
- nessuna bozza del colloquio nel browser (`localStorage`): si salva sul server.

### 8.4 Proposta UX

#### 8.4.1 Dove

- **Nuova pagina** `GET/POST /appuntamenti/<id>/colloquio`: pannello Prepara + sezioni A–K, a tutta larghezza.
- La **pagina dell'appuntamento** resta per i dati Google e il collegamento. Le schede «Preparazione» e «Resoconto» diventano una sola scheda **«Colloquio»**, **senza moduli propri**: riepilogo (se c'è contenuto) + «Prepara e conduci il colloquio» + «Registra l'esito» (porta a `/colloquio?sezione=k`). Nessun modulo rapido, per non passare dalla route V1 che riscrive tutti i campi.
- Le route V1 `/resoconto` e `/preparazione` **restano**, per compatibilità e per i test. L'interfaccia non le usa più; `/resoconto` applica anch'essa la regola OD-5.
- **Oggi**: link diretto «Colloquio» sugli appuntamenti di oggi; «Resoconti da completare» porta a `/colloquio?sezione=k`. La **scheda famiglia** porta allo stesso modo al colloquio.

#### 8.4.2 Struttura della pagina

```
Colloquio · Famiglia Esempio Rossi · gio 30/09/2026 15:00–16:00          [Apri in Google Calendar]
┌──────────────────────────────────────────────────────────────────────────────────────────────┐
│ A✓  B✓  C✓  D·  E!  F·  G·  H·  I·  J·  K·                                        [ Salva ]  │ ← barra fissa
└──────────────────────────────────────────────────────────────────────────────────────────────┘
PREPARA INCONTRO  (§3)
▸ A. Presenti e dati dell'alunno         Registra   ✓ madre, padre · Luca: dati completi
▸ B. Come ci hanno conosciuto            Domanda    ✓ Altra famiglia KITE
▾ C. Cosa cercano                        Domanda
     «Cosa vi porta a valutare una nuova scuola?» · «Cosa cercate…?» · «Cosa vorreste diverso…?»
     [✓Lingue] [✓Ambiente internazionale] [Attenzione individuale] [Dimensione delle classi] …
     Nota [inglese ogni giorno........................................................]
     Registra ciò che cercano, con parole neutre: niente giudizi su scuole o persone.
                                                                                     [ Salva ]
▸ D. Profilo scolastico · dichiarato     Domanda    ·
▸ E. Lingue · dichiarate                 Domanda    ! lingue di Luca mancanti
▸ F. Esigenze organizzative              Domanda    ·
▸ G. Cosa abbiamo presentato             Registra   ·
▸ H. Domande e dubbi                     Registra   ·
▸ I. Aspetti economici                   Economico  · nessuna proposta
▸ J. Valutazione interna   INTERNO KITE             ·                     (bordo tratteggiato)
▸ K. Conclusione e prossimo passo                   ·          [ Salva ]  [ Concludi colloquio ]
```

- Le sezioni sono `<details>` chiusi, con **una riga di sintesi** nel `<summary>` e un indicatore di stato (✓ compilata, · vuota, ! da completare).
- Dopo un salvataggio la pagina riapre la sezione di provenienza con `?sezione=c`: l'attributo `open` lo mette il server. Non ci si affida alle ancore, che non aprono i `<details>` in modo uniforme.
- La **barra fissa** (CSS `position: sticky`) porta l'indice A–K e un «Salva».
- Le domande guida sono righe brevi in corsivo tenue; le categorie sono **chip** (etichette con casella: un clic, tastiera compresa).

**Economia, sezione I:**

```
I. Aspetti economici                                  Luca · Primaria 1ª · 2027/2028
┌ LISTINO STANDARD ──────────┐┌ CONDIZIONE RISERVATA ───────────┐┌ IMPORTO FINALE COMUNICATO ─┐
│ Retta   5.400,00 € annuale ││ Fratelli −540,00 €              ││ Retta   4.860,00 € annuale │
│                            ││ Autorizzata da: —               ││ Iscrizione 300,00 €        │
│                            ││ (non richiesta per «Fratelli»)  ││ Valida fino al 31/10/2026  │
│                            ││                                 ││ Note: in 10 rate           │
└────────────────────────────┘└─────────────────────────────────┘└────────────────────────────┘
Servizi scelti: mensa 90,00 € mensile (aggiuntivo)
v1 · BOZZA   [Salva e modifica la bozza]   Canale [Di persona ▾]   [Salva e segna come comunicata]
In famiglia: Anna · Infanzia 2028/2029 · In corso. Nessuna riduzione viene applicata automaticamente.
```

**Conclusione, sezione K:**

```
K. Conclusione e prossimo passo                 (diventa operativa solo con «Concludi colloquio»)
Esito  (•) Visita svolta  ( ) Famiglia non presentata  ( ) Annullata o rinviata   il [30/09/2026 15:05]
Scelta rapida  [Richiamo] [Seconda visita] [Invio proposta] [Invio documenti] [Attendere risposta]
               [Avvio iscrizione] [Non prosegue]
Prossimo passo  [Inviare informazioni ▾] entro il [02/10/2026]  Responsabile [Segreteria]  Riguarda [Luca ▾]
                Nota [Proposta economica]
Altro passo     [— ▾]
Materiale da inviare  [✓Rette e servizi] [✓Modulo di iscrizione] [Calendario] [Regolamento] …
Verifiche  1 da verificare: «trasporto dalla zona Esempio?»   [✓] crea il passo entro il [03/10]
Aperti da prima: Far fissare la visita (scaduto il 25/09)      [ ] completato con questo incontro
Copertura: nessun altro passo valido per Luca → per chiudere serve il prossimo passo
Sintesi libera (facoltativa) [.............................................................]
                                                         [ Salva ]  [ Concludi colloquio ]
```

#### 8.4.3 Regole di interazione

1. **Un solo `<form>`** per tutta la pagina. Ogni «Salva» invia tutto (`action=save`, `goto=<sezione>`); «Concludi colloquio» invia `action=conclude`.

2. **«Salva»** registra i contenuti: pannello Prepara, A–J, sintesi libera e la **bozza** di K (`closing_draft`).
   - **Non** registra l'esito, **non** crea follow-up e **non** chiude richieste. Prima della chiusura nessun campo è obbligatorio; i controlli di formato valgono sempre (date, lunghezze, codici).
   - Dopo la conclusione, «Salva» accetta correzioni di esito e data, tranne il *passaggio* a «Visita svolta», che passa sempre da «Concludi».

3. **«Concludi colloquio»**:
   - valida la chiusura (§8.8), poi, **nella stessa transazione** dei contenuti:
     - registra esito e data;
     - crea fino a 3 follow-up, ciascuno con il suo UUID di creazione;
     - completa i follow-up spuntati;
     - chiude le richieste scelte (`leads.change_status`);
   - toglie la bozza e riporta alla pagina dell'appuntamento con il riepilogo;
   - dopo la conclusione, K mostra i follow-up creati in sola lettura, con il loro stato; nuovi passi si aggiungono dalla scheda famiglia (flusso V1).

4. **Più record, un salvataggio atomico.** I record coinvolti sono:
   - `R0`: il colloquio in `Appointment` (JSON, preparazione, sintesi, nota interna, tipo, ambito);
   - `R1…Rn`: i dati di ogni alunno dell'ambito (`StudentLead`);
   - `RF`: la fonte in `Family`.

   Per ognuno il modulo porta `<p>_rev` (revisione) e `<p>_fp`, un'impronta SHA-256 dei valori *come mostrati*. `saved_at` e `closing_draft` sono esclusi dall'impronta di R0. Il server:
   1. valida tutto; se c'è un errore risponde 422 e ripresenta i valori inviati;
   2. per ogni record, se l'impronta dei valori inviati coincide con `<p>_fp` il record **non è stato toccato**: nessuna scrittura, nessun controllo;
   3. se è stato toccato e la revisione è quella corrente → scrive. Le revisioni **si concatenano** dentro `interview.save`: per esempio `change_status` dopo l'aggiornamento dei dati dell'alunno usa la revisione appena incrementata;
   4. se la revisione è cambiata altrove e i valori nel database coincidono con quelli inviati, è un **invio ripetuto** → nessuna scrittura, nessun errore;
   5. altrimenti c'è un **conflitto su quel record**: non si salva niente (409) e il modulo si ripresenta con i valori inviati. Per ogni record in conflitto compaiono i valori attuali e una scelta: «usa i valori attuali» oppure «mantieni i miei». Solo con «mantieni» il nuovo invio porta la revisione corrente di quel record e lo sovrascrive: è una **conferma esplicita**.

   Mai redirect con perdita dell'input. Un doppio «Concludi» ritrova l'UUID del primo follow-up e risponde senza errore (tecnica V1 di `save_report`).

5. **Servizi dedicati**, per non ereditare regole estranee:
   - `leads.update_facts` aggiorna solo le colonne dei dati dell'alunno. Non richiede `review_on` come `update_lead`: la data di riesame si cambia dal suo modulo.
   - `families.update_contact_source` aggiorna solo fonte e dettaglio. Non rifà il controllo dei doppioni di `update_family`: etichetta e recapiti non cambiano.

6. **Salva e vai.** Le uscite che farebbero perdere l'input sono pulsanti di invio con `name="then"`:
   - **destinazioni GET della V1**: nuova proposta (`?ambito=`), modifica bozza, nuovo figlio, modifica famiglia. Il server salva e reindirizza con `next=/appuntamenti/<id>/colloquio?sezione=…`; i moduli di destinazione accettano `next` tramite `safe_next`;
   - **azioni che la V1 fa in POST**: nuova versione (`offers.create_new_version`) e segna come comunicata (`offers.communicate`, con canale e revisione dell'offerta nel modulo). Il server le esegue **nella stessa transazione** del salvataggio: entrambe sono annidabili e idempotenti (UUID di creazione; idempotenza di `communicate`).

   Gli id nelle destinazioni sono verificati contro la famiglia.

7. **Protezione dall'uscita**: in `app.js`, un modulo con `data-guard` segnato come modificato fa scattare l'avviso standard del browser se si lascia la pagina da un link; il proprio invio azzera l'avviso. Tutte le azioni economiche passano dal modulo principale, quindi l'avviso non compare nel momento economico.

8. **Scelte rapide** in `app.js`: i pulsanti portano in attributi `data-*` azione, data (calcolata dal server in Europe/Rome) e nota, e li copiano nei campi. Il motivo di «non prosegue» si precompila con l'ostacolo di J. Senza JavaScript si compilano i campi a mano: il server valida sempre i valori finali.

9. **Mostra/nascondi** (nota del flag, motivo di «non prosegue»): si riusa `data-show-when` della V1, che grazie a `:checked` funziona anche con le caselle.

10. Nessuno script o stile inline (CSP), nessuna nuova libreria.

#### 8.4.4 Moduli esistenti che cambiano

- **Richiesta alunno** (`lead_form.html`): data di nascita (l'anno compare solo se la data è vuota: «se conosci solo l'anno»), scuola e classe attuali, città/zona, lingue, contesto bilingue, profilo (≤ 500), flag + nota. Usa lo stesso parser del colloquio; accetta `next`.
- **Famiglia** (`form.html`, `create_family.html`): fonte (lista chiusa, con il valore precedente conservato) e dettaglio della fonte. `form.html` accetta `next`.
- **Offerta** (`offer_form.html`):
  - blocchi listino, condizione riservata (riduzioni in 4 righe: motivo · importo · nota; «Autorizzata da» con suggerimenti), importo finale (retta finale, quota d'iscrizione, validità, note) e servizi;
  - anno e classe dell'ambito in testata;
  - avviso di coerenza;
  - nella nuova versione, il suggerimento «v1 autorizzata da: …»;
  - accetta `next`.
- **Follow-up**: campo «Responsabile» con suggerimenti, in creazione e modifica; mostrato in scheda e in Oggi.
- **Cambia collegamento**: l'avviso cita anche il colloquio, i follow-up che restano e si staccano, e **i dati degli alunni aggiornati durante il colloquio, che restano nella famiglia precedente**.

#### 8.4.5 Scheda famiglia

- «Richieste dei figli»: età e data di nascita, scuola attuale, lingue in breve, badge neutro «Approfondimento».
- «Appuntamenti e colloqui» (§5) al posto di «Appuntamenti e visite».
- Colonna laterale: **«Ultimo colloquio»**, riepilogo in 4 righe (data e tipo · interesse e tempistica · ostacolo · prossimi passi), sopra «Prossimo passo».
- Proposte in blocchi, con lo stesso partial della sezione I.
- Intestazione: fonte + dettaglio.

#### 8.4.6 Età e verifica dell'età

Funzione pura, testata. Il calcolo avviene sul server, in Europe/Rome.

- **Età oggi** e **anni compiuti al 31/12 dell'anno di inizio** (per il 2027/2028, il 31/12/2027).
- **Infanzia**: 3 anni entro il 31/12 → «età regolare»; entro il 30/04 dell'anno dopo → «anticipo (secondo circolare e disponibilità)»; altrimenti «età da verificare».
- **Primaria 1ª**: 6 anni entro il 31/12 → regolare; entro il 30/04 successivo → anticipo; già 7 al 31/12 → «da verificare».
- Altre classi: nessuna verifica.
- Nato il 29 febbraio: negli anni non bisestili il compleanno si considera raggiunto il 1° marzo. È documentato e testato; non tocca i limiti 31/12 e 30/04.
- **Aiuto informativo, non blocca nulla** (OD-2): nessuna pratica viene fermata o respinta senza intervento umano.
- La scuola è paritaria, quindi valgono le regole generali dell'ordinamento italiano (DPR 89/2009), raccolte in `AGE_RULES`. Ogni anno vanno confrontate con la circolare iscrizioni.

### 8.5 Modifiche minime al database (schema 2)

```sql
ALTER TABLE Family ADD COLUMN contact_source_detail TEXT;

ALTER TABLE StudentLead ADD COLUMN birth_date TEXT CHECK (birth_date IS NULL OR (date(birth_date) IS birth_date
    AND (birth_year IS NULL OR birth_year = CAST(substr(birth_date, 1, 4) AS INTEGER))));
ALTER TABLE StudentLead ADD COLUMN current_school TEXT;
ALTER TABLE StudentLead ADD COLUMN current_grade TEXT;
ALTER TABLE StudentLead ADD COLUMN languages TEXT NOT NULL DEFAULT '[]'
    CHECK (json_valid(languages) AND json_type(languages) = 'array');
ALTER TABLE StudentLead ADD COLUMN bilingual_context INTEGER
    CHECK (bilingual_context IS NULL OR bilingual_context IN (0, 1));
ALTER TABLE StudentLead ADD COLUMN profile_note TEXT;
ALTER TABLE StudentLead ADD COLUMN educational_review INTEGER NOT NULL DEFAULT 0
    CHECK (educational_review IN (0, 1));
ALTER TABLE StudentLead ADD COLUMN educational_review_note TEXT
    CHECK (educational_review_note IS NULL OR educational_review = 1);

ALTER TABLE Appointment ADD COLUMN interview TEXT NOT NULL DEFAULT '{}'
    CHECK (json_valid(interview) AND json_type(interview) = 'object');

ALTER TABLE Offer ADD COLUMN enrollment_fee_cents INTEGER
    CHECK (enrollment_fee_cents IS NULL OR enrollment_fee_cents >= 0);
ALTER TABLE Offer ADD COLUMN reductions TEXT NOT NULL DEFAULT '[]'
    CHECK (json_valid(reductions) AND json_type(reductions) = 'array');
ALTER TABLE Offer ADD COLUMN authorized_by TEXT;
-- trg_offer_frozen ricreato: stesse condizioni della V1 + i tre campi nuovi
DROP TRIGGER trg_offer_frozen;
CREATE TRIGGER trg_offer_frozen BEFORE UPDATE ON Offer
WHEN OLD.status <> 'BOZZA' AND (
       NEW.currency IS NOT OLD.currency
    OR NEW.standard_fee_cents IS NOT OLD.standard_fee_cents
    OR NEW.proposed_fee_cents IS NOT OLD.proposed_fee_cents
    OR NEW.periodicity IS NOT OLD.periodicity
    OR NEW.services IS NOT OLD.services
    OR NEW.conditions IS NOT OLD.conditions
    OR NEW.valid_until IS NOT OLD.valid_until
    OR NEW.enrollment_fee_cents IS NOT OLD.enrollment_fee_cents
    OR NEW.reductions IS NOT OLD.reductions
    OR NEW.authorized_by IS NOT OLD.authorized_by
    OR NEW.communicated_at IS NOT OLD.communicated_at
    OR NEW.communicated_by IS NOT OLD.communicated_by
    OR NEW.communication_channel IS NOT OLD.communication_channel
    OR NEW.created_at IS NOT OLD.created_at
    OR NEW.created_by IS NOT OLD.created_by
    OR (OLD.status = 'RITIRATA' AND (NEW.withdrawn_at IS NOT OLD.withdrawn_at
                                     OR NEW.withdrawal_reason IS NOT OLD.withdrawal_reason)))
BEGIN
    SELECT RAISE(ABORT, 'offer_frozen');
END;

ALTER TABLE FollowUp ADD COLUMN appointment_id TEXT REFERENCES Appointment (id);
ALTER TABLE FollowUp ADD COLUMN assignee TEXT;
CREATE INDEX ix_follow_up_appointment ON FollowUp (appointment_id);
-- Il colloquio d'origine appartiene alla stessa famiglia (come le FK composite V1 per le richieste)
CREATE TRIGGER trg_follow_up_appointment_family BEFORE INSERT ON FollowUp
WHEN NEW.appointment_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM Appointment a WHERE a.id = NEW.appointment_id AND a.family_id = NEW.family_id)
BEGIN
    SELECT RAISE(ABORT, 'follow_up_appointment_family');
END;
-- Si fissa alla creazione; dopo si può solo azzerare («Cambia collegamento»)
CREATE TRIGGER trg_follow_up_appointment_fixed BEFORE UPDATE OF appointment_id, family_id ON FollowUp
WHEN NEW.appointment_id IS NOT NULL AND (NEW.appointment_id IS NOT OLD.appointment_id
     OR NEW.family_id IS NOT OLD.family_id)
BEGIN
    SELECT RAISE(ABORT, 'follow_up_appointment_fixed');
END;
```

In `schema.py`: `SCHEMA_VERSION = 2`, `MIGRATIONS = {1: SCHEMA_V1, 2: MIGRATION_V2}`. `TABLES`, `TABLE_ORDER` e `Interaction` restano invariati. Un database nuovo applica 1 e poi 2 (`initialize` lo fa già): lo schema finale è lo stesso per chi parte da zero e per chi migra.

Tutte le OD sono state risolte con l'opzione A (§9): lo script sopra è quello da implementare, senza varianti.

### 8.6 Migrazione, ripristino e rilascio

- **Quando**: al primo avvio della v1.1, con il meccanismo V1 (`AppState.start` → `db.migrate`). La copia **pre-operazione è obbligatoria**: se fallisce, l'aggiornamento non parte e l'app entra in modalità ripristino, come oggi. Nessuna riga di comando.
- **Dati esistenti**: nessuna trasformazione. Le colonne nuove prendono i default (`'{}'`, `'[]'`, `0`, NULL); i resoconti V1 restano leggibili e modificabili; nessun tentativo di «estrarre» motivazioni dai testi V1.
- **Backup V1**: restano ripristinabili. `validate_backup` accetta lo schema 1, le impronte si confrontano con il manifest del backup e dopo la sostituzione `restore` migra.
- **Correzione minima in `restore.py`**: se la migrazione dopo la sostituzione fallisce, l'errore diventa `RestoreError("Verifica dopo il ripristino fallita: aggiornamento dello schema non riuscito …")`. Così `restore_run` entra in modalità ripristino invece di lasciare l'app attiva su uno schema vecchio. Serve un test del caso di errore.
- **Rilascio e ritorno indietro**:
  - dopo il merge si aggiorna il checkout del collegamento; al primo avvio c'è la copia «pre-migrazione» (schema 1);
  - per tornare alla V1: `git checkout v1.0.0` e ripristino di quella copia (la V1 accetta lo schema 1). Si perdono solo i dati inseriti dopo l'aggiornamento.
- **Prova eseguita**, su un database sintetico creato con lo `SCHEMA_V1` del codice (script nello scratchpad, fuori dal repository): migrazione riuscita, `user_version = 2`, `integrity_check = ok`, 0 violazioni di foreign key, 7 tabelle, default corretti sulle righe V1.
  - Respinti: data di nascita inesistente (`2021-02-30`); anno di nascita incoerente; lingue non array; nota di approfondimento senza flag; colloquio non oggetto; modifica di riduzioni, quota d'iscrizione, autorizzazione o quota proposta di un'offerta comunicata; follow-up verso un appuntamento inesistente; follow-up collegato a un colloquio di un'altra famiglia; collegamento aggiunto dopo la creazione.
  - Accettati: ritiro di un'offerta comunicata; modifica ordinaria di un follow-up; flag con nota; colloquio JSON; «Cambia collegamento» che sposta l'appuntamento e stacca i follow-up.
  - Il revisore indipendente ha riprodotto lo script, in memoria, con lo stesso esito.
- **Ordine delle cancellazioni** (`delete_family`): i FollowUp si cancellano già prima degli Appointment, quindi la nuova FK non cambia nulla.

### 8.7 Impatto su Offer

- **Campi di contenuto**: `enrollment_fee_cents`, `reductions` e `authorized_by` sono congelati dal trigger. `enrollment_fee_cents` e `reductions` entrano in `CONTENT_FIELDS` e «Crea nuova versione» li copia. **`authorized_by` invece non si copia**: la nuova versione ne richiede la conferma, con il suggerimento «v1 autorizzata da: …». Così una riduzione nuova non passa con l'autorizzazione della versione precedente.
- **Validazione** (`parse_offer`): motivi dal catalogo; importi > 0; «Altro» richiede la nota; le riduzioni non superano il listino, se c'è.
- **Completezza per «Segna come comunicata»** (`completeness_problems`): regole V1 + «manca chi ha autorizzato la condizione riservata» se almeno un motivo ha `richiede_autorizzazione` (OD-3c).
- **Coerenza, solo avviso**: con il listino presente, se `listino − Σ riduzioni ≠ retta finale` compare un avviso sulla bozza prima di comunicarla. Non blocca (arrotondamenti e casi particolari si spiegano nelle note), ma resta visibile.
- **Importo finale comunicato**: tutti gli importi sono **scritti**, nessuno calcolato. Retta finale = `proposed_fee_cents`, obbligatoria come nella V1; quota d'iscrizione = `enrollment_fee_cents` (OD-3b A). Una condizione sulla quota d'iscrizione, per esempio una promozione, si descrive nelle note della condizione riservata.
- **Nessun calcolo automatico di sconti**, nessun listino centrale: le cifre le scrive sempre Ionut (SPEC §6, §12).
- **Offerte V1** senza riduzioni: visualizzazione come oggi («sconto» = differenza, se c'è il listino).
- **Nessun legame `Offer → Appointment`**. L'offerta ha un ciclo di vita proprio: una bozza nata al colloquio si comunica anche giorni dopo per email. Il riepilogo usa la data di comunicazione (§4). Una FK richiederebbe congelamento, gestione nelle nuove versioni e in «Cambia collegamento», senza un beneficio reale.
- **Web**: le route delle offerte accettano `next`. Export: colonna aggiuntiva `enrollment_fee_eur`.

### 8.8 Impatto su FollowUp e regola di chiusura

- **`appointment_id`** (FK nullable, coerenza con la famiglia garantita da trigger): si valorizza solo per i follow-up creati da «Concludi colloquio» o dalla route V1 `/resoconto`. Non si modifica dall'interfaccia; lo azzera solo «Cambia collegamento», nella stessa transazione, per i follow-up rimasti alla famiglia precedente.
- **`assignee`** (OD-4): etichetta libera con suggerimenti, prevalorizzata con «Ionut» nel colloquio. **Non** è un utente, non filtra Oggi, non assegna niente a nessuno.
- **Alla conclusione** si creano al massimo 3 follow-up, ciascuno con il suo UUID:
  1. il prossimo passo, che può essere anche «Attendere risposta della famiglia» con la data di riesame;
  2. l'eventuale altro passo;
  3. l'eventuale «Verificare e rispondere» (ALTRO).

  Il materiale scelto si aggiunge come «Materiale: …» alla nota del primo passo con azione «Inviare informazioni»; se non ce n'è, un errore lo chiede. Le note restano entro 2000 caratteri, con «…» se si tagliano.
- **Regola di chiusura** (OD-5 A), calcolata da una funzione dedicata `interview.closure_coverage`: `today.next_steps` non si usa perché conta anche l'incontro in corso e i passi vecchi. Per un appuntamento collegato, il passaggio a «Visita svolta» richiede che **ogni richiesta aperta dell'ambito** abbia una copertura **successiva alla visita**:
  - un follow-up creato in questa conclusione (quelli di famiglia coprono tutte le richieste);
  - oppure un follow-up aperto creato **dopo l'inizio della visita** (`created_at ≥ visited_at`);
  - oppure un **altro** appuntamento della famiglia, non annullato, verificato, senza esito, che inizia dopo la visita;
  - oppure la richiesta chiusa in questa conclusione («non prosegue»), già chiusa (Iscritto/Non prosegue) o in pausa con data di riesame.

  **Non contano**: l'appuntamento stesso, gli appuntamenti con esito, i follow-up nati prima della visita. Questi ultimi la conclusione propone di completarli. Una famiglia senza richieste deve avere un passo di famiglia (follow-up o appuntamento futuro). «Non presentata» e «Annullata» non richiedono copertura: resta l'evidenza V1 in Oggi. Un appuntamento non collegato è fuori dalla regola, come nella V1, e resta fra i «Da collegare».
- Oggi e scheda famiglia mostrano il responsabile. Nessun'altra regola di Oggi cambia e `today.py` resta invariato.

### 8.9 Impatto su Interaction

- **Nessuna modifica** di schema, tipi, vincoli o trigger.
- **Nessuna riga nuova** per colloqui, salvataggi, spunte o follow-up.
- «Non prosegue» dalla conclusione chiama `leads.change_status` dentro la transazione del colloquio: produce la **stessa, unica** Interaction B1 della V1 per ogni richiesta chiusa.
- Le note manuali continuano a non potersi riferire a un appuntamento: il colloquio non ne ha bisogno.

### 8.10 Impatto sulla scheda famiglia e sul resto

| Parte | Cambiamento |
|---|---|
| Scheda famiglia | §8.4.5 |
| Pagina appuntamento | scheda «Colloquio» senza moduli; `has_report` considera anche `interview ≠ '{}'`, così «Togli dagli appuntamenti» e i conteggi di eliminazione non ignorano un colloquio senza esito |
| Oggi | link «Colloquio» sugli appuntamenti di oggi; «Resoconti da completare» porta a `/colloquio?sezione=k`; responsabile nei follow-up |
| Cronologia | la voce resta di tipo «Resoconto»; il titolo diventa «Colloquio · tipo: esito» e il dettaglio «interesse · ostacolo · prossimi passi»; per i resoconti V1 resta l'estratto |
| Export | CSV e JSON includono le colonne nuove (JSON come testo, come già `services`); `enrollment_fee_eur`; LEGGIMI aggiornato |
| Backup/ripristino/eliminazione | correzione minima dell'errore di migrazione dopo il ripristino (§8.6); `dossier_counts` conta come resoconto anche un colloquio con contenuto |
| Calendar | nessun cambiamento: l'import non tocca i campi locali (DEC-018) |

### 8.11 Criteri di accettazione dell'upgrade

Sono prove del prodotto da costruire, **non test già superati**. Dati sempre sintetici.

| ID | Prova | Risultato necessario |
|---|---|---|
| IW01 | Migrazione e compatibilità | Un database schema 1 con dati sintetici in tutte e 7 le tabelle passa allo schema 2 all'avvio, dopo una copia pre-operazione riuscita (se la copia fallisce non migra). Stesse righe e stessi valori V1; integrità e FK OK; default corretti; 7 tabelle; Interaction invariata. Un backup della V1 si ripristina nella v1.1, supera la verifica e viene migrato. Se quella migrazione fallisce, l'app entra in modalità ripristino |
| IW02 | Prepara incontro | Per un appuntamento collegato mostra tutto il §3, compreso l'elenco «Da chiedere» calcolato; per uno non collegato chiede prima il collegamento |
| IW03 | Colloquio durante l'incontro | Sezioni A–K in ordine, chiuse con sintesi. «Salva» senza campi obbligatori non registra esito, follow-up né chiusure. Ogni salvataggio è atomico su colloquio, alunni dell'ambito e fonte. Record non toccati non riscritti (revisione invariata). Conflitto su un record → nulla salvato, valori inviati ripresentati, scelta esplicita «attuali / miei». Doppio invio senza errore. Codici ignoti respinti, codici tolti dal catalogo conservati. Avviso se si esce da un link con modifiche non salvate |
| IW04 | Dichiarato e interno distinti | D ed E etichettate «dichiarato», J «Interno KITE»; nel riepilogo le due parti sono separate |
| IW05 | Domande | Righe domanda/risposta/da verificare; alla conclusione le righe da verificare **inviate in quel momento** generano, se richiesto, un solo follow-up «Verificare e rispondere» nella stessa transazione |
| IW06 | Economia | Blocchi listino, condizione riservata, importo finale, servizi. Riduzioni con motivo, importo e nota. «Segna come comunicata» rifiuta senza «Autorizzata da» quando il motivo la richiede, resta idempotente e congela anche i campi nuovi (anche nel database). La nuova versione copia riduzioni e quota ma **non** l'autorizzazione. Le offerte V1 si vedono come prima. Avviso di incoerenza non bloccante; nessuna riduzione automatica. «Salva e segna come comunicata» e «Salva e crea nuova versione» dal colloquio sono atomici e idempotenti |
| IW07 | Chiusura | Per un appuntamento collegato, il passaggio a «Visita svolta» (colloquio e route V1) richiede data effettiva e una copertura successiva alla visita (§8.8). Test: incontro ancora in corso che non si copre da solo; follow-up precedente alla visita che non conta; altro appuntamento futuro che conta; richiesta in pausa o chiusa; famiglia senza richieste. Le scelte rapide producono solo azioni FollowUp V1; «Attendere risposta della famiglia» è un follow-up ALTRO con nota predefinita e vale come copertura. «Non prosegue» chiude con una sola Interaction per richiesta. I follow-up portano `appointment_id` e responsabile; doppio «Concludi» senza duplicati; regola del materiale rispettata |
| IW08 | Riepilogo | Dopo la chiusura, pagina appuntamento e scheda famiglia mostrano il riepilogo del §4 in al massimo 12 righe, più 2 per alunno in più. Nei colloqui non più recenti niente dati attuali dell'alunno. Ionut lo legge in 10–15 secondi su 3 casi sintetici |
| IW09 | Storico | Tre appuntamenti della stessa famiglia (prima visita, incontro economico, Direzione) mantengono tre colloqui distinti; modificarne uno non tocca gli altri; la scheda li elenca con tipo, esito, interesse, ostacolo e prossimi passi; `saved_at` non cambia con un import. «Cambia collegamento» sposta il colloquio, stacca i follow-up rimasti e lo dichiara. «Togli dagli appuntamenti» rifiutato se il colloquio ha contenuto |
| IW10 | Dati dell'alunno | Data di nascita facoltativa con età; coerenza con l'anno (anche nel database); scuola e classe attuali; lingue e contesto bilingue; profilo ≤ 500; flag con nota ≤ 300 che si svuota togliendolo. Verifica età corretta ai confini 31/12 e 30/04 e per il 29 febbraio. «Altro anno» copia data di nascita e lingue, non scuola attuale, profilo e flag |
| IW11 | Nessuna regressione | Tutti i test V1 verdi; modificati solo quelli elencati nella Issue (§10). Oggi, cronologia, backup, export, eliminazione e ripristino funzionano con i campi nuovi; nessuna riga Interaction per colloqui o spunte |
| IW12 | Privacy | Nessun campo del §7.2; testi d'aiuto del §7.3 presenti (verificati nei template); il test dei termini vietati del §7.3 passa; nessun dato dei moduli nei log |
| IW13 | Liste configurabili | Tutte in `catalog.py`; cambiare un'etichetta o aggiungere una voce non richiede migrazione; togliere una voce non rompe i colloqui passati |
| IW14 | Tempi | Chiusura (esito + prossimo passo con scelta rapida) in 3 azioni al massimo. Ogni salvataggio o conclusione sul dataset sintetico risponde in meno di 2 secondi, soglia già usata in `test_linking.py:277`. Cronometraggio di Ionut al primo uso reale, come per la V1 |

---

## 9. Decisioni del titolare (Owner Decision)

Decisioni del titolare del 27/09/2026. Opzioni e raccomandazioni restano come traccia; fa fede la riga `Owner decision:`.

### OD-1 — BLOCKING — Dove vive il colloquio

Status: RESOLVED

Question: dove si salva il colloquio strutturato?

Options:
- A — Nella riga `Appointment` esistente: una colonna JSON `interview` più i campi V1 (preparazione, esito, data, sintesi, nota interna); dati dell'alunno nel suo `StudentLead`, fonte nella `Family`.
- B — Nuova tabella `Interview`, 1:1 con `Appointment`.
- C — Circa 20 colonne separate in `Appointment` (le liste multiple resterebbero comunque JSON).

Agent recommendation: A

Reason: il rapporto è 1:1 (1 incontro = 1 evento = 1 colloquio), quindi una tabella non aggiunge relazioni. Lo storico c'è già (una riga per incontro) e i campi V1 del resoconto sono già protetti dall'import. Una tabella nuova toccherebbe il riconoscimento del database e dei backup (`TABLES` fisso a 7 in `inspect`, `validate_backup`, `table_digests`: «tabelle mancanti»), l'export (`TABLES[:6]`), l'eliminazione, i conteggi, la DEC-009 e il test «esattamente sette tabelle». Servirebbe solo con più colloqui per lo stesso incontro, colloqui senza evento Calendar (OD-6 B) o versionamento dei resoconti: nessuno di questi è richiesto. JSON rispetto a colonne: le parti multiple (righe domanda, presenti, chip) richiederebbero comunque JSON o tabelle figlie, e la V1 usa già JSON validato in Python (`services`, `src_contacts`).

Owner decision: A.

### OD-2 — BLOCKING — Data di nascita completa e verifica dell'età

Status: RESOLVED

Question: si registra la data di nascita completa, facoltativa, dell'alunno? La SPEC V1 §3 prevede «eventuale anno di nascita soltanto se utile». E la verifica dell'età segue l'ordinamento italiano?

Options:
- A — Sì, facoltativa (`birth_date`), con età e verifica per Infanzia/Primaria 1ª secondo DPR 89/2009; l'anno V1 resta per chi conosce solo quello.
- A2 — Come A, ma senza verifica dell'età: solo età, se la scuola non segue l'ordinamento italiano.
- B — No, solo l'anno: età approssimata e nessuna verifica.

Agent recommendation: A, se Latina segue l'ordinamento italiano (paritaria); altrimenti A2.

Reason: le regole di ammissione (3 o 6 anni entro il 31/12, anticipo entro il 30/04) dipendono da giorno e mese, quindi la data completa cambia una decisione (classe, ammissibilità). Resta facoltativa, senza documenti, nella sola richiesta.

Owner decision: A. LATINA INTERNATIONAL SCHOOL IMPRESA SOCIALE S.R.L. va trattata come scuola paritaria: data di nascita completa facoltativa, età calcolata automaticamente e verifica dei requisiti anagrafici. La verifica è informativa e non blocca mai la pratica senza intervento umano.

### OD-3a — BLOCKING — Condizione riservata: come si registra

Status: RESOLVED

Options:
- A — Righe di riduzione (fino a 4) con motivo dal catalogo, importo sulla retta e nota; avviso non bloccante se `listino − riduzioni ≠ retta finale`.
- B — Solo i motivi come caselle; lo sconto resta la differenza listino − quota della V1, senza scomposizione.

Agent recommendation: A

Reason: la richiesta prevede più condizioni insieme (fratelli, pagamento annuale, altra condizione) e il motivo di ciascuna; con A si sa quanto vale ognuna. È una sola colonna JSON, congelata dal trigger.

Owner decision: A.

### OD-3b — BLOCKING — Quota d'iscrizione

Status: RESOLVED

Options:
- A — Una colonna `enrollment_fee_cents` = quota d'iscrizione **comunicata** (una tantum), nel blocco dell'importo finale; eventuali condizioni sulla quota si descrivono nelle note.
- B — Quota **di listino** + riduzioni anche sulla quota (`applies_to`), con quota finale calcolata.
- C — Nessuna colonna: riga «Quota di iscrizione» fra i servizi V1, con periodicità una tantum.

Agent recommendation: A

Reason: la quota d'iscrizione è chiesta esplicitamente fra i dati da mostrare, e con A si vede in un posto preciso, non come uno dei servizi. Tutti gli importi comunicati restano *scritti*, nessuno calcolato: il revisore aveva segnalato l'asimmetria di B, dove la retta è scritta e la quota calcolata. Costa una colonna. B serve solo se le promozioni sulla quota sono frequenti e vanno misurate.

Owner decision: A.

### OD-3c — BLOCKING — Autorizzazione della condizione riservata

Status: RESOLVED

Options:
- A — `authorized_by` obbligatorio alla comunicazione quando almeno un motivo è marcato «richiede autorizzazione» (proposta: Promozione, Accordo con la Direzione, Altro; non Fratelli né Pagamento annuale); non ereditato dalle nuove versioni.
- B — Sempre facoltativo.

Agent recommendation: A

Reason: registra il fatto («chi ha autorizzato») senza creare un flusso di approvazione fra utenti (DEC-007).

Owner decision: A. «Autorizzata da» è obbligatorio per promozione autorizzata, accordo con la Direzione o altra condizione discrezionale (Altro). Per gli sconti standard già previsti dalle regole KITE (fratelli, pagamento annuale) non serve un'autorizzazione manuale.

### OD-4 — BLOCKING — Responsabile del prossimo passo

Status: RESOLVED

Question: il prossimo passo ha un «responsabile»? SPEC §3 e §8: «Responsabile implicito: Ionut».

Options:
- A — Etichetta libera `FollowUp.assignee` con suggerimenti (Ionut, Segreteria, Direzione, Coordinamento didattico); vuota = Ionut. Nessun utente, filtro o assegnazione.
- B — Resta implicito; chi deve farlo si scrive nella nota.

Agent recommendation: A

Reason: è richiesto esplicitamente per la chiusura. Il passo è spesso della segreteria (invio documenti) o della Direzione. Un'etichetta non introduce multiutenza (DEC-002).

Owner decision: A. Responsabile come semplice etichetta, senza utenti né RBAC.

### OD-5 — BLOCKING — Obbligo del prossimo passo alla chiusura

Status: RESOLVED

Question: segnare come svolta la visita di un appuntamento collegato richiede un prossimo passo?

Options:
- A — Sì, **nel servizio**, per ogni passaggio a SVOLTA (pagina colloquio e route V1 `/resoconto`), con la regola del §8.8: copertura *successiva alla visita*; l'incontro stesso, gli appuntamenti con esito e i follow-up precedenti non contano. «Non presentata» e «annullata» restano senza blocco (evidenza V1 in Oggi). Appuntamenti non collegati fuori regola, come nella V1.
- B — Solo nel pulsante «Concludi colloquio»; la route V1 resta com'è.
- C — Nessun obbligo: resta l'evidenza «senza prossimo passo» della V1.

Agent recommendation: A

Reason: una sola regola, senza scorciatoie: la bozza 0.1 contava anche l'incontro in corso ed è stata corretta. Si applica solo al *passaggio* a svolta: correggere un resoconto già svolto non la riattiva. Esiste sempre una scelta valida: richiamo, attendere risposta della famiglia, non prosegue. Test V1 da aggiornare: vedi §10. È un cambiamento esplicito di comportamento, non un indebolimento dei test.

Owner decision: A. Una visita svolta si chiude con un prossimo passo successivo all'incontro. Il prossimo passo può essere anche «Attendere risposta della famiglia» con una data di riesame, così non si creano attività artificiali (realizzazione: §2 K).

### OD-6 — NON-BLOCKING — Incontri non presenti in Calendar

Status: RESOLVED

Options:
- A — No, come nella V1: la segreteria crea l'evento, anche a posteriori, poi «Aggiorna».
- B — Colloqui locali senza evento: richiederebbe una sorgente locale di appuntamenti, contro DEC-005 e DEC-006.

Agent recommendation: A (i criteri di accettazione lo presuppongono)

Owner decision: A. Nella v1.1 i colloqui restano collegati agli eventi Calendar; nessun colloquio standalone.

### OD-7 — NON-BLOCKING — Liste, fonte e giorni proposti

Status: RESOLVED

Question: le scelte di contenuto che non cambiano l'architettura.

Da decidere o confermare:
1. **Fonte del contatto**: lista chiusa (select; i valori V1 esistenti restano come «valore precedente») oppure testo libero con suggerimenti, come nella V1. Con la lista chiusa, «Sito web», «Open day», «Telefonata», «Email» e «Segreteria» non si possono più scegliere per famiglie nuove: si usano «Google / sito web» ed «Evento / open day», mentre gli ultimi tre sono canali, non fonti. Va aggiornato `test_e2e.py`, che usa «Open day». *Raccomandazione: lista chiusa.*
2. **Fasce**: la Secondaria di I grado, prevista dalla richiesta, resta nelle liste? Nido e Secondaria II grado, presenti nei suggerimenti V1, restano?
3. **Motivi di riduzione**: aggiungere «Convenzione» e «Borsa di studio»? Quali richiedono autorizzazione?
4. **Servizi e materiali** effettivi di Latina (divisa, campus, extracurricolari).
5. **Giorni delle scelte rapide** (3/2/1/2/3-7-14/1) e **suggerimenti** per responsabili e autorizzatori.

Agent recommendation: approvare i valori proposti, con le correzioni di Ionut. Si cambiano anche dopo, senza migrazione.

Owner decision: Secondaria di I grado confermata: le liste prevedono Infanzia, Primaria e Secondaria di I grado. Liste centralizzate come nella proposta, senza un sistema di configurazione generale.

Applicazione: per i punti non citati esplicitamente valgono i valori della proposta.
- Fonte: lista chiusa.
- Motivi di riduzione: FRATELLI, PAGAMENTO_ANNUALE, PROMOZIONE, ACCORDO_DIREZIONE, ALTRO.
- Servizi, materiali, giorni delle scelte rapide e suggerimenti come nel §6.
- Nei suggerimenti della classe richiesta non compaiono più Nido e Secondaria II grado; «Nido» resta fra quelli della classe attuale.

---

## 10. Bozza di Issue e piano di implementazione

**Titolo:** `KITE Admissions v1.1 — Interview Workflow`

**Goal.** La persona KITE conduce il colloquio con una scaletta coerente (prepara, A–K, chiusura) e alla fine ha un riepilogo leggibile in 10–15 secondi e uno storico distinto per incontro, riusando il modello V1 senza nuove tabelle.

**Acceptance Criteria.** IW01–IW14 (§8.11).

**Owner Decisions.** OD-1…OD-7, tutte **RESOLVED** il 27/09/2026 (§9).

**Allowed Surface.**
- `kite_admissions/`: `__init__.py` (versione 1.1.0), `schema.py` (migrazione 2), **`catalog.py` (nuovo)**, `labels.py`, `app.py` (solo le globali Jinja per i nuovi partial).
- `services/`: `appointments.py`, **`interview.py` (nuovo)**, `leads.py`, `families.py`, `offers.py`, `followups.py`, `timeline.py`, `export.py`; `restore.py` **solo** per trasformare in `RestoreError` l'errore di migrazione dopo il ripristino.
- `web/`: `appointments.py`, `families.py`, `offers.py`, `followups.py`, `filters.py` (solo filtri nuovi), `today.py` (solo se serve per il link «Colloquio»).
- `templates/`:
  - `appointments/detail.html`, `change_link.html`, `create_family.html`;
  - **nuovi**: `appointments/interview.html` e i partial `_interview_*.html`, `family/_offer_blocks.html`;
  - `family/detail.html`, `_offers.html`, `offer_form.html`, `lead_form.html`, `form.html`, `_followups.html`, `followup_form.html`;
  - `today/index.html`.
- `static/app.css`, `static/app.js` (solo protezione dall'uscita e scelte rapide).
- `tests/`: file nuovi e questi aggiornamenti dichiarati:
  - `test_foundations.py`: versione dello schema (`SCHEMA_VERSION == 1`); test di migrazione basati su `_patch_v2` (da portare a una versione 3 finta, con `MIGRATIONS` 1–3); versione nel manifest pre-migrazione; `/health` con `1.1.0` e schema 2;
  - `test_linking.py` e `test_timeline.py`: prossimo passo nei resoconti «svolta» su appuntamenti collegati (OD-5);
  - `tests/dataset.py`: stesso motivo;
  - `test_e2e.py`: la fonte «Open day», non più in lista (OD-7.1).
- `SPEC.md` (addendum v1.1), `DECISIONS.md` (DEC-024 e seguenti), `README.md`, `PROJECT_STATE.md`, `pyproject.toml`, `docs/` (questa proposta).

**Forbidden.**
- Nuove tabelle; modifiche a `Interaction` (schema, tipi, trigger); nuovi stati della richiesta, esiti visita o azioni follow-up.
- Modifiche a `gcal/`, `calendar_import.py`, `import_runner.py`, `security.py`, `launcher.py`, `paths.py`, `settings.py`, `backups.py`, `services/today.py`. In `restore.py` è ammessa solo la correzione indicata sopra.
- Nuove dipendenze Python o JS, CDN, script o stili inline, archiviazione nel browser.
- Scritture verso Google, email/WhatsApp, AI esterna, utenti/ruoli/approvazioni, motore listini o sconti automatici, appuntamenti locali, versionamento dei resoconti, audit dei campi.
- **Sviluppare o avviare codice non rilasciato nel checkout `C:\Users\ionut\kite-admissions`**: è quello del collegamento sul Desktop, sui dati reali. Si lavora nel **worktree separato `C:\Users\ionut\kite-admissions-v1.1`** (branch `v1.1-interview-workflow`), con `.venv` propria (`install.ps1 -Dev -NoShortcut`). Il codice di sviluppo si avvia solo con `--home` o `KITE_ADMISSIONS_HOME` su una cartella di prova. Un avvio sulla cartella reale migrerebbe il database a uno schema non definitivo, che la V1 poi rifiuterebbe («too_new»).
- Qualsiasi uso del database reale in test o collaudo.

**Verification.**
1. `.venv\Scripts\python.exe -m pytest` nel worktree: tutto verde. I nuovi test coprono IW01–IW13:
   - servizi, route e vincoli del database;
   - migrazione da schema 1, ripristino di un backup V1 anche nel caso di errore;
   - età ai confini e congelamento dei campi nuovi;
   - regola di chiusura: incontro in corso, follow-up precedenti, appuntamento futuro, pausa/chiusura, famiglia senza richieste;
   - atomicità multi-record, impronte, conflitti, doppio invio;
   - codici fuori catalogo, «Cambia collegamento», trigger dei follow-up;
   - riepilogo e storico, termini vietati.
2. Collaudo su Waitress con cartella dati separata e calendario di prova (come per la V1), con l'elenco dei controlli e il loro esito:
   1. migrazione di un database V1 sintetico;
   2. tre incontri della stessa famiglia;
   3. prepara → colloquio completo;
   4. proposta con riduzione autorizzata, comunicata con doppio clic;
   5. chiusura con materiale, verifiche e completamento di un follow-up vecchio;
   6. riepilogo → storico → «Cambia collegamento»;
   7. backup, ripristino di un backup V1, export.
3. Revisione del diff contro l'Allowed Surface; matrice IW → evidenza nella PR.
4. Dopo il merge (azione di Ionut): aggiornare il checkout del collegamento, controllare al primo avvio la copia «pre-migrazione» e che i conteggi delle tabelle non siano cambiati. Il ritorno indietro è descritto al §8.6.

**Stop Condition.** IW01–IW14 PASS + review PASS + PR mergiata → Issue chiusa. Stop: nessuna funzione ulteriore.

### Slice (una Issue, gate a ogni slice, come nella V1)

| Slice | Contenuto | Gate |
|---|---|---|
| 1 — Fondamenta v1.1 | migrazione 2 con trigger, `catalog.py`, parser e validazioni dei JSON, correzione in `restore.py`, test di migrazione e di ripristino di un backup V1 | IW01, IW13 |
| 2 — Alunno e famiglia | campi `StudentLead` e `Family`, età e verifica, `update_facts` e `update_contact_source`, moduli richiesta/famiglia con `next`, badge e dati nella scheda | IW10, parte di IW12 |
| 3 — Economia | campi Offer, blocchi, riduzioni, autorizzazione non ereditata, trigger, `next`, avviso di coerenza | IW06 (parte offerta) |
| 4 — Colloquio | pagina, Prepara, A–K, «Salva» e «Concludi», salvataggio multi-record con impronte, salva-e-vai (compresi nuova versione e comunicazione), regola di chiusura, follow-up, «Cambia collegamento», JS/CSS | IW02, IW03, IW04, IW05, IW06 (dal colloquio), IW07 |
| 5 — Riepilogo e storico | riepilogo, storico, cronologia, Oggi, export, documentazione, collaudo end-to-end | IW08, IW09, IW11, IW12, IW14 |

---

## 11. Rischi, limiti e finding differiti

**Rischi**

- *Pagina lunga*: mitigata da sezioni chiuse con sintesi, indice fisso e salvataggio ovunque. Da verificare nel collaudo su portatile.
- *Modulo multi-record*: più complesso di un modulo singolo. Mitigato da protocollo esplicito (impronte e revisioni, §8.4.3), servizio unico `interview.save`, parser puri, test per record.
- *Dati dell'alunno aggiornati durante un colloquio collegato alla famiglia sbagliata*: restano nella famiglia precedente dopo «Cambia collegamento» (avviso esplicito). Stesso principio della V1 per offerte e follow-up.
- *Liste*: valori approvati (OD-7), modificabili senza migrazione.
- *Sviluppo sul checkout del collegamento*: vietato nella Issue (worktree separato).

**Limiti accettati**

- Nessun versionamento del singolo colloquio (V1).
- I dati dell'alunno sono solo quelli attuali: il riepilogo di un colloquio vecchio non ricostruisce ciò che si sapeva allora (§4).
- Il JSON del colloquio è poco leggibile in Excel: l'export JSON resta completo.
- La verifica dell'età è indicativa.

**DEFERRED FINDINGS** (emersi nell'analisi e nella revisione, fuori perimetro: non si implementano)

- **V1, modifica di un follow-up** (`web/followups.py:99-111`, `followup_form.html:13`): dopo un conflitto (409) il modulo si ripresenta con la revisione *corrente*, quindi il secondo invio sovrascrive la modifica dell'altra scheda. `family/form.html` invece ripresenta quella vecchia, e il conflitto si ripete fino al ricaricamento. Comportamenti V1 da valutare.
- `StudentLead.origin` mescola «scuola di provenienza» e «zona»: qui si chiarisce l'etichetta, i dati esistenti non vengono riclassificati.
- I moduli V1 con redirect perdono l'input in caso di errore. La nuova pagina lo evita per il colloquio; gli altri moduli restano così.
- `export.OPERATIONAL_TABLES = TABLES[:6]` dipende dall'ordine delle tabelle: fragile ma corretto oggi.
- «Condotto da» (persona KITE che fa il colloquio) servirebbe solo con più referenti: oggi vale DEC-002.
- Stampa del riepilogo e un CSV «colloqui» leggibile: possibili miglioramenti futuri.
- Informativa privacy Admissions da verificare (§7.4): attività non software.

---

## 12. Esito della revisione interna (bozza 0.1 → 0.2)

Revisione indipendente in sola lettura, a contesto pulito. Il revisore ha confrontato la bozza 0.1 con il codice e con i requisiti.

Ha confermato:
- l'impianto: nessuna tabella, migrazione solo additiva;
- le affermazioni sul codice: migrazioni, ripristino, `TABLES`, trigger, `change_link`, `save_report`, `change_status`, CSP, `syncToggles`;
- lo script SQL, riprodotto in memoria.

Verdetto sulla 0.1: *da correggere prima*. Correzioni applicate:

| Punto | Correzione |
|---|---|
| Regola di chiusura aggirabile: l'incontro in corso e i follow-up vecchi contavano come copertura | copertura *successiva alla visita* con `interview.closure_coverage`; esclusioni esplicite; test dedicati (OD-5, §8.8, IW07) |
| «Salva» e chiusura non distinti; follow-up duplicabili | «Salva» non registra mai esito né follow-up; tutto passa da «Concludi»; bozza di K nel JSON (§8.4.3) |
| «Registra solo l'esito» avrebbe cancellato sintesi e nota interna | modulo tolto; la pagina appuntamento non ha più moduli (§8.4.1) |
| Salvataggio multi-record non implementabile così | protocollo con impronte, revisioni concatenate, conflitti per record con scelta esplicita, servizi dedicati (§8.4.3) |
| Storico con dati attuali dell'alunno | dati dell'alunno solo nel riepilogo più recente (§4, §11) |
| Autorizzazione copiata nelle nuove versioni | non si copia (§8.7, IW06) |
| OD economica che accorpava tre scelte | divisa in OD-3a, 3b, 3c; tutti gli importi comunicati sono scritti |
| Profilo che invitava a scrivere dati sanitari | testo d'aiuto esplicito, limite di 500 caratteri, test dei termini (§2 D, §7.3) |
| Rischio per il database reale durante lo sviluppo | worktree separato obbligatorio (§10 Forbidden) |
| Ripristino di un backup V1 che fallisce a metà | correzione minima in `restore.py` + test (§8.6) |
| Elenco dei test V1 incompleto | elenco esplicito (§10) |
| Scelte dell'agente che spettano al titolare (fonte a lista chiusa, giorni, ordinamento per l'età) | spostate in OD-7 e OD-2 |
| Dati non necessari | tolti «tutore», nota sui presenti, «in corso d'anno», «altri figli interessati», «età tipica» |
| Materiale in due posti, tre follow-up automatici | materiale solo nella nota del follow-up; al massimo 3 follow-up tutti visibili prima di concludere; verifiche dalle righe inviate |
| «Segna come comunicata» in un modulo separato | passa dal modulo principale (`then=communicate`) |
| Ancore contro `?sezione=` | ovunque `?sezione=` |
| Criteri non misurabili | soglie: 12 righe, 3 azioni, 2 secondi; elenco dei termini vietati |
| Coerenza `FollowUp.appointment_id` solo nel servizio | due trigger nel database, provati (§8.5, §8.6) |
| Data di modifica del colloquio falsata dall'import | `interview.saved_at` (§5) |

Un punto non è stato accolto: il revisore riteneva la quota d'iscrizione assente dalla richiesta. La richiesta la elenca fra i dati da mostrare, quindi resta (OD-3b).

## 13. Consistency check finale e decisioni applicate (versione 1.0)

Ultimo controllo di coerenza, fatto su tutto il documento dopo le decisioni del titolare.

- **Decisioni riportate:**
  - OD-1…OD-7 con la riga `Owner decision:` e lo stato RESOLVED;
  - nessuna variante residua nel §8.5;
  - raccomandazioni ripulite dai riferimenti ormai superati.
- **«Attendere risposta della famiglia»** (OD-5):
  - realizzata con un follow-up `ALTRO` con nota predefinita e data di riesame;
  - nessuna nuova azione né stato, perché la `CHECK` su `FollowUp.action` richiederebbe la ricostruzione della tabella;
  - sostituisce la scelta rapida «Famiglia valuta»;
  - i giorni restano quelli approvati (3/7/14 secondo la tempistica).
- **Fasce** (OD-7):
  - classe richiesta: Infanzia, Primaria 1ª–5ª, Secondaria I grado 1ª–3ª;
  - classe attuale: in più «Nido»;
  - il campo resta testo libero con suggerimenti, come nella V1;
  - per la scuola paritaria la verifica dell'età è solo informativa (OD-2).
- **Allowed Surface completata**:
  - `app.py`, solo globali Jinja;
  - `web/filters.py`, solo filtri nuovi.

  Servono ai partial condivisi (blocchi dell'offerta, riepilogo). È un completamento tecnico, non uno scope nuovo.
- **Coerenza interna verificata**:
  - «Salva» e «Concludi» (§2 K, §8.4.3, IW03, IW07);
  - regola di chiusura (§8.8, OD-5, IW07);
  - massimo 3 follow-up (§2 K, §8.4.3, §8.8);
  - autorizzazione non ereditata (§8.4.4, §8.7, IW06);
  - `?sezione=` al posto delle ancore (§8.4.1, §8.4.2, §8.10);
  - elenco dei test V1 da aggiornare (§10);
  - worktree e divieto di usare i dati reali (§10).

## Appendice A — Tracciabilità: dalla richiesta alla proposta

| Alla fine del colloquio devo capire… | Dove |
|---|---|
| chi è la famiglia | Family (A, Prepara, riepilogo riga 1) |
| chi è il bambino, quanti anni ha | StudentLead + età calcolata (A, §8.4.6) |
| anno e classe richiesti | `school_year`, `grade` |
| situazione scolastica attuale | `current_school`, `current_grade`, `profile_note` |
| perché valutano KITE / cosa cercano | `interview.motivations` (+ nota), driver in J |
| dubbi e necessità | `interview.questions`, `interview.needs`, flag |
| servizi di interesse | `interview.needs.services`; servizi scelti nell'Offer |
| cosa KITE ha spiegato | `interview.presented` |
| retta/listino presentato | Offer: listino standard |
| sconto o condizione riservata | Offer: `reductions` con motivo |
| importo finale comunicato | Offer: retta finale + quota d'iscrizione, «Segna come comunicata» |
| chi ha autorizzato | Offer: `authorized_by` |
| interesse | `interview.assessment.interest` |
| obiezioni e ostacoli | `assessment.obstacle` + nota interna + verifiche aperte |
| prossimo passo concordato | FollowUp con `appointment_id` e `assignee` |

## Appendice B — Prove della migrazione

Script nello scratchpad della sessione (fuori dal repository), eseguiti con `.venv` del progetto: Python 3.14.4, SQLite 3.50.4. Creano lo schema V1 importando `SCHEMA_V1` dal codice, inseriscono righe sintetiche, applicano lo script del §8.5 nella stessa forma di `db._run_script` (transazione `BEGIN IMMEDIATE`) e verificano i vincoli. Esiti al §8.6.
