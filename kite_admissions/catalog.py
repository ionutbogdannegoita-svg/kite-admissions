"""Liste centralizzate del colloquio di ammissione (v1.1, SPEC §14, DEC-026).

Nessuna configurazione a runtime: cambiare una lista è una piccola modifica di codice, testata.
Nei dati si salvano i codici, mai le etichette (eccezione: la fonte del contatto, testo come nella
V1). Un codice tolto dall'elenco resta leggibile nei dati già salvati: «voce non più in elenco».
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable

from .db import fold
from .services.common import ValidationError


@dataclass(frozen=True)
class Entry:
    code: str
    label: str
    cycles: tuple[str, ...] = ()  # voce legata a una fascia (vuoto = tutte)
    needs_authorization: bool = False  # solo motivi di riduzione


def _entries(*pairs: tuple[str, str]) -> tuple[Entry, ...]:
    return tuple(Entry(code, label) for code, label in pairs)


MEETING_KINDS = _entries(
    ("PRIMA_VISITA", "Prima visita"),
    ("SECONDA_VISITA", "Seconda visita"),
    ("INCONTRO_ECONOMICO", "Incontro economico"),
    ("INCONTRO_DIREZIONE", "Incontro con la Direzione"),
    ("ALTRO", "Altro incontro"),
)

# Solo la relazione con l'alunno: niente nomi, niente situazioni giuridiche familiari.
ATTENDEE_RELATIONS = _entries(
    ("MADRE", "Madre"),
    ("PADRE", "Padre"),
    ("NONNI", "Nonno/nonna"),
    ("ALTRO_FAMILIARE", "Altro familiare"),
    ("ALUNNO", "L'alunno/a"),
)

# Fonte del contatto: lista chiusa (OD-7), salvata come testo come nella V1.
CONTACT_SOURCES = (
    "Google / sito web",
    "Social network",
    "Passaparola",
    "Altra famiglia KITE",
    "Evento / open day",
    "Territorio",
    "Altro",
)

MOTIVATIONS = _entries(
    ("APPROCCIO_DIDATTICO", "Approccio didattico"),
    ("LINGUE", "Lingue"),
    ("AMBIENTE_INTERNAZIONALE", "Ambiente internazionale"),
    ("ATTENZIONE_INDIVIDUALE", "Attenzione individuale"),
    ("DIMENSIONE_CLASSI", "Dimensione delle classi"),
    ("CONTINUITA", "Continuità educativa"),
    ("DISCIPLINA", "Disciplina e regole"),
    ("INCLUSIONE", "Inclusione"),
    ("SPORT", "Attività sportive"),
    ("ARTE", "Attività artistiche"),
    ("ORARI", "Orari"),
    ("SERVIZI", "Servizi"),
    ("LOGISTICA", "Logistica"),
    ("ALTRO", "Altro"),
)

LANGUAGES = _entries(("IT", "Italiano"), ("EN", "Inglese"), ("FR", "Francese"))
OTHER_LANGUAGE = "ALTRA"
MAX_OTHER_LANGUAGES = 2
LANGUAGE_LEVELS = _entries(
    ("NESSUNA", "Nessuna esposizione"),
    ("BASE", "Base"),
    ("INTERMEDIO", "Intermedio"),
    ("AVANZATO", "Avanzato"),
    ("MADRELINGUA", "Madrelingua/bilingue (dichiarato)"),
)

SERVICES_OF_INTEREST = _entries(
    ("PRE_SCUOLA", "Pre-scuola"),
    ("POST_SCUOLA", "Post-scuola"),
    ("MENSA", "Mensa"),
    ("TRASPORTO", "Trasporto"),
    ("EXTRACURRICOLARI", "Corsi extracurricolari"),
)

PRESENTATION_TOPICS = (
    Entry("PROGETTO_EDUCATIVO", "Progetto educativo"),
    Entry("INTERNAZIONALE", "Impostazione internazionale"),
    Entry("LINGUE", "Lingue"),
    Entry("ORARI", "Orari"),
    Entry("CALENDARIO", "Calendario scolastico"),
    Entry("MENSA", "Mensa"),
    Entry("DIVISA", "Divisa"),
    Entry("ATTIVITA", "Attività"),
    Entry("SERVIZI", "Servizi"),
    Entry("EXTRACURRICOLARI", "Extracurricolari"),
    Entry("COMUNICAZIONE", "Comunicazione scuola-famiglia"),
    Entry("INSERIMENTO", "Inserimento e ambientamento", cycles=("INFANZIA", "PRIMARIA_1")),
    Entry("COSTI", "Costi"),
    Entry("ISCRIZIONE", "Processo di iscrizione"),
)

MATERIALS = _entries(
    ("PRESENTAZIONE", "Presentazione della scuola"),
    ("LISTINO", "Rette e servizi"),
    ("CALENDARIO", "Calendario scolastico"),
    ("ORARI", "Orari e servizi"),
    ("MODULO_ISCRIZIONE", "Modulo di iscrizione"),
    ("REGOLAMENTO", "Regolamento e patto di corresponsabilità"),
    ("INFORMATIVA_PRIVACY", "Informativa privacy"),
    ("ALTRO", "Altro"),
)

INTEREST_LEVELS = _entries(("BASSO", "Basso"), ("MEDIO", "Medio"), ("ALTO", "Alto"), ("MOLTO_ALTO", "Molto alto"))
DECISION_TIMINGS = _entries(
    ("IMMEDIATA", "Immediata"),
    ("POCHI_GIORNI", "Pochi giorni"),
    ("ALCUNE_SETTIMANE", "Alcune settimane"),
    ("DA_DEFINIRE", "Da definire"),
)
DRIVERS = _entries(
    ("DIDATTICA", "Didattica"),
    ("LINGUE", "Lingue"),
    ("AMBIENTE", "Ambiente"),
    ("SERVIZI", "Servizi"),
    ("LOGISTICA", "Logistica"),
    ("ATTIVITA", "Attività"),
    ("PREZZO", "Prezzo"),
    ("ALTRO", "Altro"),
)
OBSTACLES = _entries(
    ("NESSUNO", "Nessuno evidente"),
    ("COSTO", "Costo"),
    ("DISTANZA", "Distanza"),
    ("TRASPORTO", "Trasporto"),
    ("ORARI", "Orari"),
    ("ALTRE_SCUOLE", "Confronto con altre scuole"),
    ("POSTI", "Disponibilità posti"),
    ("DUBBIO_DIDATTICO", "Dubbio didattico"),
    ("ALTRO", "Altro"),
)

# Condizione riservata (OD-3a/3c): gli sconti standard KITE non richiedono un'autorizzazione manuale.
REDUCTION_REASONS = (
    Entry("FRATELLI", "Fratelli"),
    Entry("PAGAMENTO_ANNUALE", "Pagamento annuale"),
    Entry("PROMOZIONE", "Promozione autorizzata", needs_authorization=True),
    Entry("ACCORDO_DIREZIONE", "Accordo con la Direzione", needs_authorization=True),
    Entry("ALTRO", "Altra condizione discrezionale", needs_authorization=True),
)
MAX_REDUCTIONS = 4

# «Attendere risposta della famiglia» (OD-5): voce del menu Azione, registrata come follow-up ALTRO.
WAIT_ACTION = "ATTESA"
WAIT_NOTE = "Attendere risposta della famiglia"
WAIT_DAYS = {"IMMEDIATA": 3, "POCHI_GIORNI": 3, "ALCUNE_SETTIMANE": 14, "DA_DEFINIRE": 7}
WAIT_DEFAULT_DAYS = 7
# Passo creato alla conclusione dalle domande «da verificare» (sezione H): follow-up ALTRO.
VERIFY_NOTE = "Verificare e rispondere"


@dataclass(frozen=True)
class QuickOutcome:
    code: str
    label: str
    action: str | None  # azione FollowUp (o WAIT_ACTION); None = nessun passo
    days: int = 0
    note: str = ""


# Scelte rapide della conclusione: precompilano il prossimo passo, non sono stati.
QUICK_OUTCOMES = (
    QuickOutcome("RICHIAMO", "Richiamo", "RICHIAMARE", 3),
    QuickOutcome("SECONDA_VISITA", "Seconda visita", "FISSARE_VISITA", 2, "Seconda visita"),
    QuickOutcome("INVIO_PROPOSTA", "Invio proposta", "INVIARE_INFORMAZIONI", 1, "Proposta economica"),
    QuickOutcome("INVIO_DOCUMENTI", "Invio documenti", "INVIARE_INFORMAZIONI", 2),
    QuickOutcome("ATTENDERE_RISPOSTA", "Attendere risposta", WAIT_ACTION, WAIT_DEFAULT_DAYS),
    QuickOutcome("AVVIO_ISCRIZIONE", "Avvio iscrizione", "INVIARE_INFORMAZIONI", 1, "Modulo di iscrizione"),
    QuickOutcome("NON_PROSEGUE", "Non prosegue", None),
)
MAX_STEPS = 2  # prossimo passo + un altro passo facoltativo

ASSIGNEE_SUGGESTIONS = ("Ionut", "Segreteria", "Direzione", "Coordinamento didattico")
DEFAULT_ASSIGNEE = "Ionut"
AUTHORIZER_SUGGESTIONS = ("Direzione", "Ionut", "Amministrazione")

# Fasce di Latina (OD-7): Infanzia, Primaria, Secondaria di I grado. Il campo resta testo libero.
GRADE_SUGGESTIONS = (
    "Infanzia",
    "Primaria 1ª", "Primaria 2ª", "Primaria 3ª", "Primaria 4ª", "Primaria 5ª",
    "Secondaria I grado 1ª", "Secondaria I grado 2ª", "Secondaria I grado 3ª",
)
CURRENT_GRADE_SUGGESTIONS = ("Nido",) + GRADE_SUGGESTIONS

MAX_QUESTIONS = 12
EMPTY_QUESTION_ROWS = 3


@dataclass(frozen=True)
class AgeRule:
    years: int
    deadline: tuple[int, int] = (12, 31)  # compiuti entro il 31/12 dell'anno di inizio
    early: tuple[int, int] = (4, 30)  # anticipo: entro il 30/04 dell'anno successivo


# Scuola paritaria (OD-2): regole generali dell'ordinamento (DPR 89/2009); verifica solo informativa.
AGE_RULES = {"INFANZIA": AgeRule(3), "PRIMARIA_1": AgeRule(6)}


def codes(entries: tuple[Entry, ...]) -> tuple[str, ...]:
    return tuple(entry.code for entry in entries)


def find(entries: tuple[Entry, ...], code: str | None) -> Entry | None:
    return next((entry for entry in entries if entry.code == code), None)


def label(entries: tuple[Entry, ...], code: str | None) -> str:
    """Etichetta di un codice; un codice non più in elenco resta leggibile."""
    if not code:
        return ""
    entry = find(entries, code)
    return entry.label if entry else f"{code} (voce non più in elenco)"


def clean_codes(values: Iterable[Any] | None, entries: tuple[Entry, ...], *, field: str, what: str,
                previous: Iterable[str] | None = ()) -> list[str]:
    """Codici scelti, nell'ordine dell'elenco. Ammessi solo codici dell'elenco o già salvati prima."""
    known = codes(entries)
    legacy = set(previous or ())
    chosen: list[str] = []
    for value in values or ():
        code = str(value).strip()
        if not code or code in chosen:
            continue
        if code not in known and code not in legacy:
            raise ValidationError(f"{what}: voce non valida, ricarica la pagina.", field)
        chosen.append(code)
    order = {code: index for index, code in enumerate(known)}
    return sorted(chosen, key=lambda code: order.get(code, len(order)))


def clean_code(value: Any, entries: tuple[Entry, ...], *, field: str, what: str,
               previous: str | None = None) -> str | None:
    chosen = clean_codes([value] if value not in (None, "") else [], entries, field=field, what=what,
                         previous=[previous] if previous else [])
    return chosen[0] if chosen else None


def for_cycle(entries: tuple[Entry, ...], cycles: set[str] | None) -> tuple[Entry, ...]:
    """Voci valide per le fasce indicate; senza fascia nota si mostrano solo le voci comuni."""
    return tuple(entry for entry in entries if not entry.cycles or (cycles and set(entry.cycles) & cycles))


def grade_cycle(grade: str | None) -> str | None:
    """Fascia dalla classe richiesta (testo libero): INFANZIA, PRIMARIA_1, PRIMARIA, SECONDARIA_1 o None."""
    text = " ".join((fold(grade) or "").replace("ª", "a").split())
    if text.startswith("infanzia"):
        return "INFANZIA"
    if text.startswith("primaria"):
        rest = text[len("primaria"):].strip()
        return "PRIMARIA_1" if rest[:1] == "1" and not rest[1:2].isdigit() else "PRIMARIA"
    for prefix in ("secondaria i grado", "secondaria di i grado", "secondaria di primo grado", "secondaria 1"):
        if text.startswith(prefix):
            return "SECONDARIA_1"
    return None


def wait_days(timing: str | None) -> int:
    return WAIT_DAYS.get(timing or "", WAIT_DEFAULT_DAYS)


def school_start_year(school_year: str | None) -> int | None:
    if not school_year or len(school_year) != 9:
        return None
    try:
        return int(school_year[:4])
    except ValueError:
        return None


def rule_dates(rule: AgeRule, start_year: int) -> tuple[date, date]:
    """Date limite della regola per l'anno scolastico che inizia in `start_year`."""
    deadline = date(start_year, *rule.deadline)
    early = date(start_year + 1, *rule.early)
    return deadline, early
