# KITE Admissions

CRM locale per gestire contatti Admissions, appuntamenti, richieste degli alunni, offerte e follow-up di KITE United Schools, inizialmente per **LATINA INTERNATIONAL SCHOOL IMPRESA SOCIALE S.R.L.**

Applicazione sul PC Windows di un solo utilizzatore. Stack approvato: **Python + Flask + SQLite + browser locale**. Google Calendar resta la fonte degli appuntamenti: il flusso è **Google Calendar → CRM, soltanto in lettura**.

**Stato: DESIGN COMPLETE / READY FOR IMPLEMENTATION.** Nessun codice applicativo è stato implementato; lo Slice 0 non è iniziato.

- [SPEC.md](SPEC.md): specifica autorevole del progetto, copiata integralmente da `KITE-Admissions-Specifica-MVP.md`.
- [PROJECT_STATE.md](PROJECT_STATE.md): punto di sospensione, vincoli e prossima attività.
- [DECISIONS.md](DECISIONS.md): decisioni già approvate.
- [Piano di implementazione](docs/IMPLEMENTATION_PLAN.md): sequenza degli slice e relativi gate.

La specifica conserva le diciture storiche «da validare» e «validazione richiesta» per rispettare il divieto di riscriverla. La richiesta esplicita di creazione del repository del 25 settembre 2026 la identifica come finale e validata: il suo stato è `READY_FOR_IMPLEMENTATION`.

## Resume

1. Apri o clona questo repository sul PC.
2. Leggi prima [SPEC.md](SPEC.md), poi [PROJECT_STATE.md](PROJECT_STATE.md) e il [piano](docs/IMPLEMENTATION_PLAN.md).
3. Chiedi di riprendere **Slice 0 — Foundations**, mantenendo la progettazione approvata. Prima di scrivere codice, delimita e approva una sola Issue o equivalente completo secondo il protocollo Ionut.

Prompt minimo: «Riprendi KITE Admissions da `design-v1`. Leggi SPEC.md e PROJECT_STATE.md; prepara il perimetro dello Slice 0 senza riprogettare l'applicazione.»

Il tag annotato `design-v1` identifica la chiusura della progettazione prima dello sviluppo. I dieci criteri di accettazione della specifica sono prove del prodotto futuro, non test già superati.

## Dati locali

Repository privato, dedicato alla documentazione e al futuro codice. Database, backup, export, dati reali delle famiglie, token OAuth, credenziali e configurazioni del PC devono restare fuori da Git. Il `.gitignore` esclude i percorsi e i formati previsti; prima di ogni push resta necessario controllare i file staged.
