# KITE Admissions — Project State

## Status

`READY_FOR_IMPLEMENTATION`

Data e ora della sospensione documentata: **2026-09-25 23:09:05 +02:00 (Europe/Rome)**.

Punto di ripresa: tag annotato `design-v1`.

## Last completed phase

Product/design specification completed and validated.

[SPEC.md](SPEC.md) è la fonte autorevole. È la copia integrale di `KITE-Admissions-Specifica-MVP.md`, versione 0.2 del 25 settembre 2026. L'approvazione e lo stato sopra derivano dalla richiesta esplicita di creazione del repository; le diciture storiche «da validare» nel documento sono conservate senza riscriverlo.

## Architecture approved

- Flask.
- SQLite.
- Applicazione locale nel browser, sul PC Windows.
- Un solo utilizzatore.
- Import Google Calendar in sola lettura.
- Nessun hosting cloud applicativo.
- Nessun RBAC o login multiutente.
- Nessuna scrittura verso Calendar.

Unica società V1: **LATINA INTERNATIONAL SCHOOL IMPRESA SOCIALE S.R.L.**

## Approved tables

1. Family.
2. StudentLead.
3. Appointment.
4. Offer.
5. FollowUp.
6. Interaction.
7. CalendarExclusion.

Sei tabelle operative e una tabella tecnica di esclusioni. Nessuna tabella aggiuntiva AuditEvent.

## Approved operational views

1. Oggi.
2. Appuntamenti.
3. Famiglie.
4. Scheda famiglia.

Pannello tecnico «Dati e collegamento Google»: connessione Google, dati, backup, export e restore.

## Final design blockers

None.

- **B1 persistent transitions: resolved.** Le transizioni importanti sono registrate in Interaction nella stessa transazione del cambiamento, senza duplicazioni; vale anche per appuntamenti non ancora collegati a una famiglia (SPEC §7).
- **B2 restore with corrupted/missing current DB: resolved.** Il ripristino da backup valido è possibile con database corrente assente, corrotto o non leggibile; la copia preventiva rimane obbligatoria quando il database corrente è sano (SPEC §9.2).

Account/calendario sorgente e destinazione dell'eventuale seconda copia di backup saranno forniti in fase di configurazione: non sono blocchi di progettazione e non vengono inventati qui (SPEC §13).

## Acceptance criteria

I **10 criteri finali AC01–AC10** sono in [SPEC.md, §11](SPEC.md#11-criteri-di-accettazione-v1--dieci-prove-essenziali). Nessuno è dichiarato superato: l'applicazione non è ancora stata costruita.

## Implementation status

No application code has been written.

Slice 0 non avviato. Nessun database applicativo, collegamento Google o ambiente Flask predisposto da questo task. Nessuna Issue o Pull Request aperta per lo sviluppo.

## NEXT ACTION

`SLICE 0 — Foundations`

Leggere prima `SPEC.md`. **Non riprogettare l'applicazione prima dello Slice 0.**

Prima dell'implementazione, delimitare e approvare una singola Issue o equivalente completo con Goal, Acceptance Criteria, Allowed Surface, Forbidden, Verification e Stop Condition, esplicitando eventuali Owner Decisions. Questo punto di ripresa non autorizza da solo l'avvio dello sviluppo.

## Suggested implementation sequence

- Slice 0 — Foundations.
- Slice 1 — Family + StudentLead.
- Slice 2 — Google Calendar import.
- Slice 3 — Appointment + family linking.
- Slice 4 — Family detail + timeline.
- Slice 5 — Offers.
- Slice 6 — FollowUp + Oggi.
- Slice 7 — Backup/export/restore.
- Final acceptance criteria gate.

Obiettivi, confini e gate sono nel [piano](docs/IMPLEMENTATION_PLAN.md); non sostituiscono la specifica.

## Important constraints

- Mantenere lo scope minimo.
- Nessuna funzionalità V2.
- Nessun supporto multiutente.
- Nessun deployment cloud o accesso LAN/remoto.
- Nessuna sincronizzazione Calendar bidirezionale.
- Nessun dato reale delle famiglie in Git; usare dati sintetici per le prove.
- Nessuna credenziale, token, database, backup o export in Git.
- Fermarsi ai criteri della singola attività autorizzata; nessun avvio automatico dello slice successivo.
