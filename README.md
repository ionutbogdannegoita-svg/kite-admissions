# KITE Admissions

CRM locale per gestire le famiglie interessate alla **LATINA INTERNATIONAL SCHOOL IMPRESA SOCIALE S.R.L.**: chi deve venire a conoscere la scuola, cosa sappiamo della famiglia, cosa è stato detto durante e dopo la visita, quale proposta economica è stata comunicata e qual è il prossimo passo.

- Applicazione **locale** sul PC Windows di un solo utilizzatore: Python + Flask + SQLite, pagine nel browser su `127.0.0.1`.
- Google Calendar resta la fonte degli appuntamenti: il flusso è **Google Calendar → CRM, soltanto in lettura**.
- Nessun cloud, nessun login multiutente, nessuna scrittura verso Google.
- **Versione 1.1**: il colloquio di ammissione guidato (Prepara incontro, sezioni A–K, riepilogo e storico), con le stesse sette tabelle della V1.

Documenti: [SPEC.md](SPEC.md) (specifica autorevole, addendum v1.1 al §14) · [specifica operativa v1.1](docs/PROPOSTA_V1.1_INTERVIEW_WORKFLOW.md) · [DECISIONS.md](DECISIONS.md) · [PROJECT_STATE.md](PROJECT_STATE.md) · [piano degli slice](docs/IMPLEMENTATION_PLAN.md).

## Cosa fa

| Vista | A cosa serve |
|---|---|
| **Oggi** | Appuntamenti di oggi (con il link al colloquio) e dei prossimi 7 giorni, famiglie da richiamare (con telefono e pulsante «Fatto»), follow-up scaduti e di oggi con il responsabile, riesami, eventi da collegare, resoconti da completare, pratiche senza prossimo passo, appuntamenti non verificati, stato dell'ultimo aggiornamento Calendar |
| **Appuntamenti** | «Aggiorna da Google Calendar» con anteprima senza preselezioni, elenco per data (prossimi, da collegare, passati, annullati, non verificati), dettaglio con dati Google in sola lettura, collegamento alla famiglia, scheda «Colloquio» con il riepilogo |
| **Colloquio** (v1.1) | «Prepara incontro» calcolato (famiglia, alunni con età, fratelli, incontri precedenti, proposte, follow-up, cosa chiedere) e sezioni A–K nell'ordine della conversazione: presenti e dati dell'alunno, fonte, cosa cercano, profilo e lingue dichiarati, esigenze, cosa è stato presentato, domande, economia, valutazione interna, conclusione con prossimo passo |
| **Famiglie** | Ricerca per cognome/etichetta, adulto, nome del bambino, telefono o email; filtri per anno scolastico, stato e archiviate; prossimo passo di ogni famiglia |
| **Scheda famiglia** | Recapiti, richieste dei figli (una per figlio e anno) con età, scuola attuale e lingue, follow-up con responsabile, «Appuntamenti e colloqui» e «Ultimo colloquio», proposte economiche versionate in tre blocchi, note e cronologia |
| **Dati e Google** | Collegamento Google, percorsi dei dati, backup, ripristino, export, chiusura dell'applicazione |

## Prerequisiti

- Windows 10 o 11 con l'account personale di Ionut.
- Python 3.11 o successivo (sviluppato e testato con Python 3.14) con il launcher `py`.
- Internet solo per l'installazione delle dipendenze e per Google Calendar: schede, offerte e follow-up funzionano offline.

## Installazione (una volta)

1. Clona o copia il repository in una cartella locale **non sincronizzata nel cloud**, ad esempio `C:\Users\ionut\kite-admissions`.
2. Da PowerShell, nella cartella del repository:

   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\install.ps1
   ```

   Lo script crea l'ambiente isolato `.venv`, installa le dipendenze fissate in `requirements.txt` e crea sul Desktop il collegamento **«Avvia KITE Admissions»**. Non crea né tocca dati. Opzioni: `-Dev` installa anche gli strumenti di test, `-NoShortcut` non crea il collegamento, `-ShortcutPath` sceglie dove crearlo.

## Avvio e chiusura

- Doppio clic su **«Avvia KITE Admissions»**: parte il programma (senza finestra di console) e si apre il browser su `http://127.0.0.1:8765/` (se la porta è occupata ne viene scelta un'altra).
- Un secondo doppio clic non avvia un secondo programma: riapre nel browser quello già attivo.
- Per spegnere: **«Chiudi applicazione»** in alto a destra. Chiudere solo la scheda del browser non spegne il programma.
- Da terminale (facoltativo): `.venv\Scripts\python.exe -m kite_admissions [--home CARTELLA] [--port PORTA] [--no-browser]`.

## Primo utilizzo

1. **Dati e Google** → configura Google Calendar (sezione successiva) e scegli il calendario della segreteria.
2. **Appuntamenti** → «Aggiorna da Google Calendar»: gli eventi nuovi compaiono in anteprima **senza preselezioni**; spunta solo gli appuntamenti Admissions e premi «Importa selezionati». «Ignora» evita di riproporre un evento (revocabile).
3. Apri ogni evento «Da collegare»: collega una famiglia esistente con un clic sui suggerimenti (stesso telefono/email o titolo simile) oppure «Crea famiglia dall'evento» con i dati proposti e modificabili.
4. Dall'appuntamento collegato premi **«Prepara e conduci il colloquio»**: prima dell'incontro guarda «Prepara incontro» e scrivi la nota; durante l'incontro compila le sezioni A–K e premi **«Salva»** quando vuoi (nulla si perde, nessun esito viene registrato); alla fine **«Concludi colloquio»** registra l'esito e crea i prossimi passi. Una visita svolta si conclude sempre con un prossimo passo successivo all'incontro (anche «Attendere risposta della famiglia» con la data di riesame).
5. Le proposte economiche si aprono dal colloquio (sezione I) o dalla scheda famiglia: listino, condizione riservata con motivo e «Autorizzata da», importo finale e quota d'iscrizione, sempre scritti da te. La bozza diventa definitiva solo con **«Segna come comunicata»**; per cambiarla si crea una nuova versione.
6. Ogni giorno parti da **Oggi**.

Gli orari, gli spostamenti e gli annullamenti si fanno sempre in Google Calendar («Apri in Google Calendar»), poi si preme di nuovo «Aggiorna». Un incontro senza evento Calendar (per esempio una famiglia arrivata senza appuntamento) si registra facendo creare l'evento alla segreteria, anche a posteriori.

## Aggiornamento dalla V1 alla v1.1

1. **Chiudi applicazione** dall'interfaccia.
2. Aggiorna il checkout usato dal collegamento (dopo l'approvazione della Pull Request): `git -C C:\Users\ionut\kite-admissions pull` su `main`.
3. Avvia dal collegamento: al primo avvio l'app crea la copia **«pre-migrazione»** (`backups\pre-operazione`, schema 1) e poi aggiorna il database allo schema 2. Se la copia non riesce, l'aggiornamento non parte e l'app si apre sulla pagina di ripristino.
4. Controlla nella pagina **Dati e Google** che i conteggi delle tabelle siano quelli di prima.

I backup creati con la V1 restano ripristinabili nella v1.1: vengono verificati e aggiornati allo schema 2. **Ritorno alla V1**, se servisse: chiudi l'app, `git -C C:\Users\ionut\kite-admissions checkout v1.0.0` (o il commit `7452456`), poi ripristina dalla pagina Dati la copia «pre-migrazione» (la V1 accetta solo lo schema 1). Si perdono i dati inseriti dopo l'aggiornamento.

Non avviare mai codice di sviluppo sulla cartella dati reale: per le prove usa `--home` con una cartella separata (vedi «Collaudo senza credenziali Google»).

## Configurazione di Google Calendar (sola lettura)

Serve una sola volta e richiede l'account Google che vede il calendario della segreteria.

1. In [Google Cloud Console](https://console.cloud.google.com/) crea un progetto (per esempio «KITE Admissions») e abilita **Google Calendar API**.
2. **Schermata di consenso OAuth**:
   - con un account Google Workspace della scuola scegli il tipo **Interno**;
   - con un account esterno scegli **Esterno** e aggiungi l'account tra gli utenti di test. In stato «Test» Google fa scadere l'autorizzazione dopo 7 giorni: per non doverla rinnovare, porta l'app «In produzione» (per uso personale basta accettare l'avviso di app non verificata).
   - Ambiti da dichiarare: `https://www.googleapis.com/auth/calendar.events.readonly` e `https://www.googleapis.com/auth/calendar.calendarlist.readonly`. Nessun ambito di scrittura.
3. **Credenziali → Crea credenziali → ID client OAuth → Tipo: App desktop**. Scarica il file JSON.
4. In KITE Admissions, pagina **Dati e Google**:
   1. «Carica» il file JSON del client (viene salvato cifrato);
   2. «Collega Google»: si apre il browser di sistema con il consenso di Google; al termine torna all'app;
   3. «Scegli il calendario»: seleziona il calendario della segreteria (deve essere già condiviso con l'account, almeno in lettura).
5. Verifica eventuali restrizioni dell'amministratore Workspace sulle app di terze parti.

Il client OAuth e l'autorizzazione sono cifrati con **DPAPI di Windows** (legati all'utente Windows) in `%LOCALAPPDATA%\KITEAdmissions\google\`: non sono mai nel repository, nei backup o negli export. Su un altro PC si ricollega Google. «Scollega e revoca» elimina l'autorizzazione locale e la revoca presso Google; nessuna password viene vista o salvata.

## Dove sono i dati

| Cosa | Percorso |
|---|---|
| Database | `%LOCALAPPDATA%\KITEAdmissions\data\admissions.sqlite3` |
| Configurazione non segreta | `%LOCALAPPDATA%\KITEAdmissions\settings.json` |
| Backup | `%LOCALAPPDATA%\KITEAdmissions\backups\` (`automatici`, `manuali`, `pre-operazione`, `preservati`) |
| Export | `%LOCALAPPDATA%\KITEAdmissions\export\` |
| Credenziali Google (cifrate) | `%LOCALAPPDATA%\KITEAdmissions\google\` |
| Log tecnico (senza dati personali) | `%LOCALAPPDATA%\KITEAdmissions\log\` |

Per Ionut `%LOCALAPPDATA%` è `C:\Users\ionut\AppData\Local`. La pagina **Dati e Google** mostra i percorsi effettivi e il pulsante «Apri cartella dati». Codice e dati sono separati: aggiornare il programma non tocca l'archivio. SQLite non cifra il file: la protezione dipende dall'account Windows e dalla cifratura del disco; blocca la sessione quando il PC è incustodito.

## Backup

- **Automatico**: dopo ogni salvataggio e a fine import viene aggiornata la copia del giorno (`backups\automatici`), creata con l'API di backup di SQLite, verificata e sostituita atomicamente. Se la copia non riesce il dato resta salvato e compare l'avviso «Dati salvati, backup non riuscito».
- **Manuale**: «Crea backup ora» nella pagina Dati; il file `.zip` si può scaricare e copiare su un supporto esterno cifrato (consigliato periodicamente: il backup sullo stesso disco non protegge dalla perdita del PC).
- **Pre-operazione**: copia separata e obbligatoria prima di eliminazioni definitive, aggiornamenti dello schema e ripristini di un database sano. Se non riesce, l'operazione non parte.
- Automatici e pre-operazione si conservano 30 giorni, tenendo sempre l'ultima copia valida; i manuali restano finché non li elimini tu.

Ogni backup è un `.zip` autonomo con il database, la configurazione non segreta e un manifest con conteggi e impronte di ogni tabella.

## Ripristino

Pagina **Dati e Google** → Backup → «Ripristina…» sul backup scelto.

1. Il backup viene aperto e verificato (integrità, foreign key, schema, contenuto uguale al manifest): un backup non valido non viene usato.
2. Se il database attuale è sano se ne crea prima una copia; se è **assente** si procede senza copia; se è **danneggiato** viene conservato in `backups\preservati` quando possibile, senza che questo blocchi il ripristino.
3. Dopo la sostituzione si verificano di nuovo integrità, foreign key e contenuto: se la verifica fallisce il ripristino non viene dichiarato riuscito e l'app resta in modalità ripristino. Un backup della V1 (schema 1) viene poi aggiornato allo schema 2, con una copia preventiva; se l'aggiornamento non riesce, anche questo vale come ripristino non riuscito.
4. Una pagina di verifica elenca le famiglie riapparse e quelle mancanti: import e modifiche restano sospesi finché non confermi.

Se all'avvio il database manca (ma esistono backup) o è danneggiato, l'app si apre direttamente sulla pagina di ripristino. Per usare un backup copiato altrove (per esempio su un nuovo PC), mettilo in `backups\manuali` e ricarica la pagina.

## Export ed eliminazione

- **Esporta dati**: crea in `export\` un pacchetto con i CSV delle sei tabelle operative (separatore `;`, UTF-8, formule neutralizzate) e un JSON completo con ID, relazioni, versioni delle offerte, esclusioni Calendar e configurazione non segreta. Nessun invio; credenziali escluse. Per ripristinare l'app si usa un backup, non l'export.
- **Archivia** (azione normale, reversibile): la famiglia esce dai flussi attivi e resta ricercabile.
- **Elimina definitivamente**: mostra cosa verrà eliminato, chiede di digitare l'etichetta, crea la copia pre-operazione e cancella il dossier locale in una sola transazione. Gli eventi Google collegati non vengono più reimportati né riproposti. **Google Calendar non viene modificato**; backup precedenti ed export possono ancora contenere il dossier.

## Test

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -Dev -NoShortcut
.venv\Scripts\python.exe -m pytest
```

La suite (170 test alla chiusura della V1, 304 con la v1.1) usa solo dati sintetici e cartelle temporanee e copre: schema e vincoli, sicurezza locale, avvio reale con Waitress a istanza singola, famiglie e richieste, import Calendar (sorgente simulata e adapter Google a livello HTTP), collegamento, cronologia, offerte, follow-up e Oggi, backup/ripristino/export/eliminazione e un flusso end-to-end completo. Per la v1.1 (`tests/test_v11_*.py`): migrazione da un database V1 e ripristino di backup V1, catalogo, dati dell'alunno ed età, economia in tre blocchi, pagina del colloquio con impronte, conflitti e doppio invio, regola del prossimo passo, riepilogo e storico, e un collaudo end-to-end.

### Collaudo senza credenziali Google

Per provare l'app con eventi sintetici senza collegarsi a Google, avviala con una cartella dati separata e un calendario di prova in JSON (formato Google Calendar v3, vedi `kite_admissions/gcal/fake.py`):

```powershell
$env:KITE_ADMISSIONS_CALENDAR_FIXTURE = "C:\percorso\calendario-prova.json"
.venv\Scripts\python.exe -m kite_admissions --home C:\percorso\cartella-prova --port 8766
```

Ogni pagina mostra un avviso «Sorgente Calendar di prova». Non usare mai questa modalità con la cartella dati reale.

## Sicurezza e privacy (in breve)

- Il server ascolta solo su `127.0.0.1`; richieste con Host inatteso, da pagine esterne (Origin/Referer) o senza token CSRF vengono respinte; nessuna modifica tramite link GET.
- I testi da Google Calendar sono mostrati come testo: nessuno script, immagine remota o collegamento viene eseguito. Una CSP restrittiva blocca comunque script e risorse esterne.
- Nei log tecnici non finiscono dati personali né contenuti degli eventi.
- Raccogliere solo dati utili al contatto e alla visita: niente documenti, codice fiscale, dati bancari, reddito, diagnosi o vicende familiari nelle note.
- Nel colloquio: profilo e lingue sono *dichiarati dalla famiglia*; diagnosi, certificazioni, terapie, allergie, diete, religione e situazioni familiari non si scrivono (per un bisogno educativo c'è solo il flag neutro «Approfondimento» con l'azione da fare). La valutazione interna (sezione J) va scritta in modo professionale: in caso di richiesta di accesso ai dati può dover essere comunicata alla famiglia. L'informativa privacy del processo Admissions va verificata per le nuove categorie (data di nascita, scuola attuale, lingue, esigenze, note del colloquio).

## Struttura del codice

```
kite_admissions/
  app.py, launcher.py        applicazione Flask e avvio a istanza singola (Waitress, loopback)
  schema.py, db.py           le sette tabelle, vincoli, trigger, transazioni, migrazioni (schema 2)
  catalog.py                 liste del colloquio (v1.1): codici ed etichette, regole dell'età
  security.py                Host/Origin/CSRF, intestazioni, gate delle richieste
  gcal/                      Google Calendar in sola lettura, OAuth desktop, DPAPI, sorgenti di prova
  services/                  logica: famiglie, richieste, appuntamenti, import, offerte, follow-up,
                             colloquio (interview.py), Oggi, cronologia, backup, ripristino, export
  web/, templates/, static/  viste HTML, stile, poco JavaScript
scripts/install.ps1          prima installazione e collegamento
tests/                       test automatici su dati sintetici
```
