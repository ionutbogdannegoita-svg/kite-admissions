"""Schema SQLite V1: sei tabelle operative e una tecnica (SPEC §8, DEC-009).

La versione dello schema è in `PRAGMA user_version`; `PRAGMA application_id` identifica il file.
I vincoli essenziali sono nel database oltre che nel server: foreign key composite
(figlio, famiglia) impediscono collegamenti incoerenti, i trigger congelano le offerte
comunicate e le transizioni registrate in Interaction.
"""

from __future__ import annotations

SCHEMA_VERSION = 1
APPLICATION_ID = int.from_bytes(b"KITE", "big")

TABLES = (
    "Family",
    "StudentLead",
    "Appointment",
    "Offer",
    "FollowUp",
    "Interaction",
    "CalendarExclusion",
)

AUTHORS = ("Ionut", "Calendar import")
LEAD_STATUSES = ("IN_CORSO", "IN_PAUSA", "ISCRITTO", "NON_PROSEGUE")
SOURCE_STATUSES = ("CONFIRMED", "TENTATIVE", "CANCELLED_SOURCE")
VERIFICATION_STATES = ("VERIFIED", "NOT_VERIFIED")
VISIT_OUTCOMES = ("SVOLTA", "NON_PRESENTATA", "ANNULLATA")
OFFER_STATUSES = ("BOZZA", "COMUNICATA", "RITIRATA")
PERIODICITIES = ("ANNUALE", "MENSILE", "UNA_TANTUM")
FOLLOWUP_ACTIONS = ("RICHIAMARE", "INVIARE_INFORMAZIONI", "FISSARE_VISITA", "ALTRO")
FOLLOWUP_STATUSES = ("APERTO", "COMPLETATO", "ANNULLATO")
EXCLUSION_REASONS = ("IGNORED", "FAMILY_DELETED")
MANUAL_INTERACTIONS = ("NOTA", "TELEFONATA", "EMAIL", "RETTIFICA")
CALENDAR_TRANSITIONS = ("CALENDAR_CANCELLED", "CALENDAR_REACTIVATED")
LEAD_TRANSITIONS = ("LEAD_STATUS_CHANGED", "LEAD_ENROLLED", "LEAD_NOT_CONTINUING", "LEAD_REOPENED")
TRANSITIONS = CALENDAR_TRANSITIONS + LEAD_TRANSITIONS
INTERACTION_TYPES = MANUAL_INTERACTIONS + TRANSITIONS


def _in(values: tuple[str, ...]) -> str:
    return "(" + ", ".join(f"'{value}'" for value in values) + ")"


_TS = "LIKE '____-__-__T__:__:__.______Z'"


def _date(column: str) -> str:
    return f"{column} IS NULL OR date({column}) IS {column}"


def _ts(column: str) -> str:
    return f"{column} IS NULL OR {column} {_TS}"


_COMMON = f"""
    created_at TEXT NOT NULL CHECK (created_at {_TS}),
    updated_at TEXT NOT NULL CHECK (updated_at {_TS}),
    created_by TEXT NOT NULL CHECK (created_by IN {_in(AUTHORS)}),
    updated_by TEXT NOT NULL CHECK (updated_by IN {_in(AUTHORS)}),
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1)"""

_UUID = "id TEXT NOT NULL PRIMARY KEY CHECK (length(id) = 36)"
_LABEL = "CHECK (length(trim({0})) BETWEEN 1 AND 200)"

SCHEMA_V1 = f"""
CREATE TABLE Family (
    {_UUID},
    display_name TEXT NOT NULL {_LABEL.format('display_name')},
    primary_adult_name TEXT,
    primary_phone TEXT,
    primary_phone_norm TEXT,
    primary_email TEXT,
    primary_email_norm TEXT,
    secondary_adult_name TEXT,
    secondary_phone TEXT,
    secondary_phone_norm TEXT,
    secondary_email TEXT,
    secondary_email_norm TEXT,
    contact_source TEXT,
    first_contact_on TEXT CHECK ({_date('first_contact_on')}),
    preliminary_notes TEXT,
    archived_at TEXT CHECK ({_ts('archived_at')}),{_COMMON},
    CHECK ((primary_phone IS NULL) = (primary_phone_norm IS NULL)),
    CHECK ((primary_email IS NULL) = (primary_email_norm IS NULL)),
    CHECK ((secondary_phone IS NULL) = (secondary_phone_norm IS NULL)),
    CHECK ((secondary_email IS NULL) = (secondary_email_norm IS NULL))
) STRICT;

CREATE INDEX ix_family_display_name ON Family (display_name);
CREATE INDEX ix_family_primary_phone ON Family (primary_phone_norm);
CREATE INDEX ix_family_secondary_phone ON Family (secondary_phone_norm);
CREATE INDEX ix_family_primary_email ON Family (primary_email_norm);
CREATE INDEX ix_family_secondary_email ON Family (secondary_email_norm);

CREATE TABLE StudentLead (
    {_UUID},
    family_id TEXT NOT NULL REFERENCES Family (id),
    display_name TEXT NOT NULL {_LABEL.format('display_name')},
    school_year TEXT CHECK (school_year IS NULL OR (
        school_year GLOB '[0-9][0-9][0-9][0-9]/[0-9][0-9][0-9][0-9]'
        AND CAST(substr(school_year, 6, 4) AS INTEGER) = CAST(substr(school_year, 1, 4) AS INTEGER) + 1)),
    grade TEXT,
    origin TEXT,
    birth_year INTEGER CHECK (birth_year IS NULL OR birth_year BETWEEN 1990 AND 2100),
    notes TEXT,
    status TEXT NOT NULL DEFAULT 'IN_CORSO' CHECK (status IN {_in(LEAD_STATUSES)}),
    review_on TEXT CHECK ({_date('review_on')}),
    closed_on TEXT CHECK ({_date('closed_on')}),
    closure_reason TEXT,
    enrollment_ref TEXT,{_COMMON},
    UNIQUE (id, family_id),
    CHECK ((status = 'IN_PAUSA') = (review_on IS NOT NULL)),
    CHECK ((status IN ('ISCRITTO', 'NON_PROSEGUE')) = (closed_on IS NOT NULL)),
    CHECK ((status = 'ISCRITTO') = (enrollment_ref IS NOT NULL)),
    CHECK (status IN ('ISCRITTO', 'NON_PROSEGUE') OR closure_reason IS NULL)
) STRICT;

CREATE INDEX ix_student_lead_family ON StudentLead (family_id);

CREATE TRIGGER trg_student_lead_family_fixed BEFORE UPDATE OF family_id ON StudentLead
WHEN NEW.family_id IS NOT OLD.family_id
BEGIN
    SELECT RAISE(ABORT, 'student_lead_family_fixed');
END;

CREATE TABLE Appointment (
    {_UUID},
    calendar_id TEXT NOT NULL CHECK (length(calendar_id) > 0),
    google_event_id TEXT NOT NULL CHECK (length(google_event_id) > 0),
    src_title TEXT,
    src_start_at TEXT CHECK ({_ts('src_start_at')}),
    src_end_at TEXT CHECK ({_ts('src_end_at')}),
    src_start_date TEXT CHECK ({_date('src_start_date')}),
    src_end_date TEXT CHECK ({_date('src_end_date')}),
    src_time_zone TEXT,
    src_location TEXT,
    src_description TEXT,
    src_contacts TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(src_contacts) AND json_type(src_contacts) = 'array'),
    src_status TEXT NOT NULL CHECK (src_status IN {_in(SOURCE_STATUSES)}),
    src_updated_at TEXT,
    src_html_link TEXT,
    src_ical_uid TEXT,
    src_recurring_event_id TEXT,
    src_original_start TEXT,
    verification_state TEXT NOT NULL CHECK (verification_state IN {_in(VERIFICATION_STATES)}),
    verification_error TEXT,
    last_synced_at TEXT NOT NULL CHECK (last_synced_at {_TS}),
    last_checked_at TEXT NOT NULL CHECK (last_checked_at {_TS}),
    family_id TEXT REFERENCES Family (id),
    student_lead_id TEXT,
    preparation TEXT,
    visit_outcome TEXT CHECK (visit_outcome IS NULL OR visit_outcome IN {_in(VISIT_OUTCOMES)}),
    visited_at TEXT CHECK ({_ts('visited_at')}),
    visit_report TEXT,
    local_observations TEXT,{_COMMON},
    UNIQUE (calendar_id, google_event_id),
    FOREIGN KEY (student_lead_id, family_id) REFERENCES StudentLead (id, family_id),
    CHECK (student_lead_id IS NULL OR family_id IS NOT NULL),
    CHECK ((src_start_at IS NOT NULL AND src_end_at IS NOT NULL AND src_start_date IS NULL
            AND src_end_date IS NULL AND src_end_at >= src_start_at)
        OR (src_start_at IS NULL AND src_end_at IS NULL AND src_start_date IS NOT NULL
            AND src_end_date IS NOT NULL AND src_end_date > src_start_date)),
    CHECK ((verification_state = 'VERIFIED') = (verification_error IS NULL)),
    CHECK (visit_outcome IS NOT 'SVOLTA' OR visited_at IS NOT NULL)
) STRICT;

CREATE INDEX ix_appointment_family ON Appointment (family_id);
CREATE INDEX ix_appointment_lead ON Appointment (student_lead_id, family_id);
CREATE INDEX ix_appointment_start_at ON Appointment (src_start_at);
CREATE INDEX ix_appointment_start_date ON Appointment (src_start_date);

CREATE TRIGGER trg_appointment_identity_fixed BEFORE UPDATE OF calendar_id, google_event_id ON Appointment
WHEN NEW.calendar_id IS NOT OLD.calendar_id OR NEW.google_event_id IS NOT OLD.google_event_id
BEGIN
    SELECT RAISE(ABORT, 'appointment_identity_fixed');
END;

CREATE TABLE Offer (
    {_UUID},
    family_id TEXT NOT NULL REFERENCES Family (id),
    student_lead_id TEXT,
    version_no INTEGER NOT NULL CHECK (version_no >= 1),
    previous_offer_id TEXT,
    currency TEXT NOT NULL DEFAULT 'EUR' CHECK (currency = 'EUR'),
    standard_fee_cents INTEGER CHECK (standard_fee_cents IS NULL OR standard_fee_cents >= 0),
    proposed_fee_cents INTEGER CHECK (proposed_fee_cents IS NULL OR proposed_fee_cents >= 0),
    periodicity TEXT CHECK (periodicity IS NULL OR periodicity IN {_in(PERIODICITIES)}),
    services TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(services) AND json_type(services) = 'array'),
    conditions TEXT,
    valid_until TEXT CHECK ({_date('valid_until')}),
    status TEXT NOT NULL DEFAULT 'BOZZA' CHECK (status IN {_in(OFFER_STATUSES)}),
    communicated_at TEXT CHECK ({_ts('communicated_at')}),
    communicated_by TEXT CHECK (communicated_by IS NULL OR communicated_by = 'Ionut'),
    communication_channel TEXT,
    withdrawn_at TEXT CHECK ({_ts('withdrawn_at')}),
    withdrawal_reason TEXT,{_COMMON},
    UNIQUE (id, family_id),
    FOREIGN KEY (student_lead_id, family_id) REFERENCES StudentLead (id, family_id),
    FOREIGN KEY (previous_offer_id, family_id) REFERENCES Offer (id, family_id),
    CHECK (previous_offer_id IS NULL OR previous_offer_id <> id),
    CHECK ((status = 'BOZZA') = (communicated_at IS NULL)),
    CHECK ((status = 'BOZZA') = (communicated_by IS NULL)),
    CHECK (status = 'BOZZA' OR (proposed_fee_cents IS NOT NULL AND periodicity IS NOT NULL)),
    CHECK ((status = 'RITIRATA') = (withdrawn_at IS NOT NULL)),
    CHECK ((status = 'RITIRATA') = (withdrawal_reason IS NOT NULL)),
    CHECK (status = 'BOZZA' OR communication_channel IS NULL OR length(communication_channel) > 0)
) STRICT;

CREATE INDEX ix_offer_family ON Offer (family_id);
CREATE INDEX ix_offer_lead ON Offer (student_lead_id, family_id);
CREATE INDEX ix_offer_previous ON Offer (previous_offer_id, family_id);
-- Versioni univoche nello stesso ambito famiglia/richiesta, inclusa la richiesta nulla.
CREATE UNIQUE INDEX ux_offer_scope_version ON Offer (family_id, ifnull(student_lead_id, ''), version_no);
-- Una sola bozza corrente per ambito.
CREATE UNIQUE INDEX ux_offer_scope_draft ON Offer (family_id, ifnull(student_lead_id, '')) WHERE status = 'BOZZA';

CREATE TRIGGER trg_offer_predecessor_scope BEFORE INSERT ON Offer
WHEN NEW.previous_offer_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM Offer AS p
    WHERE p.id = NEW.previous_offer_id
      AND p.family_id = NEW.family_id
      AND p.student_lead_id IS NEW.student_lead_id
      AND p.version_no < NEW.version_no)
BEGIN
    SELECT RAISE(ABORT, 'offer_predecessor_scope');
END;

CREATE TRIGGER trg_offer_scope_fixed BEFORE UPDATE OF family_id, student_lead_id, version_no, previous_offer_id ON Offer
WHEN NEW.family_id IS NOT OLD.family_id OR NEW.student_lead_id IS NOT OLD.student_lead_id
  OR NEW.version_no IS NOT OLD.version_no OR NEW.previous_offer_id IS NOT OLD.previous_offer_id
BEGIN
    SELECT RAISE(ABORT, 'offer_scope_fixed');
END;

CREATE TRIGGER trg_offer_status_flow BEFORE UPDATE OF status ON Offer
WHEN NOT (NEW.status IS OLD.status
          OR (OLD.status = 'BOZZA' AND NEW.status = 'COMUNICATA')
          OR (OLD.status = 'COMUNICATA' AND NEW.status = 'RITIRATA'))
BEGIN
    SELECT RAISE(ABORT, 'offer_status_flow');
END;

-- Contenuto economico e data di comunicazione congelati da COMUNICATA (SPEC §6).
CREATE TRIGGER trg_offer_frozen BEFORE UPDATE ON Offer
WHEN OLD.status <> 'BOZZA' AND (
       NEW.currency IS NOT OLD.currency
    OR NEW.standard_fee_cents IS NOT OLD.standard_fee_cents
    OR NEW.proposed_fee_cents IS NOT OLD.proposed_fee_cents
    OR NEW.periodicity IS NOT OLD.periodicity
    OR NEW.services IS NOT OLD.services
    OR NEW.conditions IS NOT OLD.conditions
    OR NEW.valid_until IS NOT OLD.valid_until
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

CREATE TABLE FollowUp (
    {_UUID},
    family_id TEXT NOT NULL REFERENCES Family (id),
    student_lead_id TEXT,
    action TEXT NOT NULL CHECK (action IN {_in(FOLLOWUP_ACTIONS)}),
    due_on TEXT NOT NULL CHECK (date(due_on) IS due_on),
    note TEXT,
    status TEXT NOT NULL DEFAULT 'APERTO' CHECK (status IN {_in(FOLLOWUP_STATUSES)}),
    closed_on TEXT CHECK ({_date('closed_on')}),
    outcome TEXT,{_COMMON},
    FOREIGN KEY (student_lead_id, family_id) REFERENCES StudentLead (id, family_id),
    CHECK ((status = 'APERTO') = (closed_on IS NULL)),
    CHECK (status <> 'APERTO' OR outcome IS NULL),
    CHECK (action <> 'ALTRO' OR length(trim(ifnull(note, ''))) > 0)
) STRICT;

CREATE INDEX ix_follow_up_family ON FollowUp (family_id);
CREATE INDEX ix_follow_up_lead ON FollowUp (student_lead_id, family_id);
CREATE INDEX ix_follow_up_open ON FollowUp (status, due_on);

CREATE TABLE Interaction (
    {_UUID},
    family_id TEXT REFERENCES Family (id),
    student_lead_id TEXT,
    appointment_id TEXT REFERENCES Appointment (id),
    offer_id TEXT,
    type TEXT NOT NULL CHECK (type IN {_in(INTERACTION_TYPES)}),
    occurred_at TEXT NOT NULL CHECK (occurred_at {_TS}),
    text TEXT NOT NULL CHECK (length(trim(text)) > 0),
    origin TEXT NOT NULL CHECK (origin IN {_in(AUTHORS)}),
    previous_state TEXT,
    next_state TEXT,{_COMMON},
    FOREIGN KEY (student_lead_id, family_id) REFERENCES StudentLead (id, family_id),
    FOREIGN KEY (offer_id, family_id) REFERENCES Offer (id, family_id),
    CHECK (family_id IS NOT NULL OR appointment_id IS NOT NULL),
    CHECK (student_lead_id IS NULL OR family_id IS NOT NULL),
    CHECK (offer_id IS NULL OR family_id IS NOT NULL),
    CHECK (type IN {_in(MANUAL_INTERACTIONS)} OR (previous_state IS NOT NULL AND next_state IS NOT NULL)),
    CHECK (CASE type
        WHEN 'CALENDAR_CANCELLED' THEN previous_state IN ('CONFIRMED', 'TENTATIVE') AND next_state = 'CANCELLED_SOURCE'
        WHEN 'CALENDAR_REACTIVATED' THEN previous_state = 'CANCELLED_SOURCE' AND next_state IN ('CONFIRMED', 'TENTATIVE')
        WHEN 'LEAD_ENROLLED' THEN previous_state IN ('IN_CORSO', 'IN_PAUSA', 'NON_PROSEGUE') AND next_state = 'ISCRITTO'
        WHEN 'LEAD_NOT_CONTINUING' THEN previous_state IN ('IN_CORSO', 'IN_PAUSA', 'ISCRITTO') AND next_state = 'NON_PROSEGUE'
        WHEN 'LEAD_REOPENED' THEN previous_state IN ('ISCRITTO', 'NON_PROSEGUE') AND next_state IN ('IN_CORSO', 'IN_PAUSA')
        WHEN 'LEAD_STATUS_CHANGED' THEN previous_state IN ('IN_CORSO', 'IN_PAUSA')
            AND next_state IN ('IN_CORSO', 'IN_PAUSA') AND previous_state <> next_state
        ELSE previous_state IS NULL AND next_state IS NULL
    END),
    CHECK (type NOT IN {_in(CALENDAR_TRANSITIONS)} OR (appointment_id IS NOT NULL AND family_id IS NULL
        AND student_lead_id IS NULL AND offer_id IS NULL AND origin = 'Calendar import')),
    CHECK (type NOT IN {_in(LEAD_TRANSITIONS)} OR (student_lead_id IS NOT NULL AND appointment_id IS NULL
        AND offer_id IS NULL AND origin = 'Ionut')),
    CHECK (type NOT IN {_in(MANUAL_INTERACTIONS)} OR (family_id IS NOT NULL AND appointment_id IS NULL
        AND origin = 'Ionut'))
) STRICT;

CREATE INDEX ix_interaction_family ON Interaction (family_id);
CREATE INDEX ix_interaction_lead ON Interaction (student_lead_id, family_id);
CREATE INDEX ix_interaction_appointment ON Interaction (appointment_id);
CREATE INDEX ix_interaction_offer ON Interaction (offer_id, family_id);

-- Le transizioni registrate restano immutate nelle operazioni ordinarie (SPEC §7).
CREATE TRIGGER trg_interaction_transition_fixed BEFORE UPDATE ON Interaction
WHEN OLD.type IN {_in(TRANSITIONS)}
BEGIN
    SELECT RAISE(ABORT, 'interaction_transition_fixed');
END;

CREATE TABLE CalendarExclusion (
    calendar_id TEXT NOT NULL CHECK (length(calendar_id) > 0),
    google_event_id TEXT NOT NULL CHECK (length(google_event_id) > 0),
    reason TEXT NOT NULL CHECK (reason IN {_in(EXCLUSION_REASONS)}),
    excluded_at TEXT NOT NULL CHECK (excluded_at {_TS}),
    PRIMARY KEY (calendar_id, google_event_id)
) STRICT, WITHOUT ROWID;
"""

# Script per portare lo schema da (versione - 1) a versione. Ogni voce gira in una transazione.
MIGRATIONS: dict[int, str] = {1: SCHEMA_V1}

# Ordine stabile delle righe per confronti e impronte (backup, ripristino, export).
TABLE_ORDER = {
    "Family": "id",
    "StudentLead": "id",
    "Appointment": "id",
    "Offer": "id",
    "FollowUp": "id",
    "Interaction": "id",
    "CalendarExclusion": "calendar_id, google_event_id",
}
