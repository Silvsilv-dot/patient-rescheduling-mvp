-- Patient Rescheduling MVP schema (Postgres / Neon).
-- Synthetic demo data only. Safe to run more than once (idempotent).
-- Prohibited by design: contact details, record numbers, clinical or
-- billing data. The privacy test scans this file's column names.

CREATE TABLE IF NOT EXISTS locations (
    location_id  TEXT PRIMARY KEY,
    name         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS providers (
    provider_id  TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    location_id  TEXT NOT NULL REFERENCES locations (location_id)
);

CREATE TABLE IF NOT EXISTS appointment_types (
    type_id      TEXT PRIMARY KEY,
    label        TEXT NOT NULL,
    duration_min INTEGER NOT NULL CHECK (duration_min > 0)
);

CREATE TABLE IF NOT EXISTS patients (
    patient_id   TEXT PRIMARY KEY,
    display_name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS slots (
    slot_id      TEXT PRIMARY KEY,
    provider_id  TEXT NOT NULL REFERENCES providers (provider_id),
    location_id  TEXT NOT NULL REFERENCES locations (location_id),
    type_id      TEXT NOT NULL REFERENCES appointment_types (type_id),
    start_time   TIMESTAMPTZ NOT NULL,
    end_time     TIMESTAMPTZ NOT NULL,
    status       TEXT NOT NULL CHECK (status IN ('Available', 'Booked', 'Blocked')),
    CHECK (end_time > start_time)
);

CREATE TABLE IF NOT EXISTS appointments (
    appt_id            TEXT PRIMARY KEY,
    patient_id         TEXT NOT NULL REFERENCES patients (patient_id),
    slot_id            TEXT NOT NULL REFERENCES slots (slot_id),
    type_id            TEXT NOT NULL REFERENCES appointment_types (type_id),
    status             TEXT NOT NULL CHECK (status IN (
                           'Scheduled', 'Confirmed', 'Rescheduled',
                           'Cancelled', 'Completed', 'No-show')),
    delay_minutes      INTEGER NOT NULL DEFAULT 0 CHECK (delay_minutes >= 0),
    reschedule_count   INTEGER NOT NULL DEFAULT 0 CHECK (reschedule_count >= 0),
    late_cancellation  BOOLEAN NOT NULL DEFAULT FALSE,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- BR-10: a slot can never hold two active appointments. The database itself
-- refuses a second active appointment on the same slot, even under a race.
CREATE UNIQUE INDEX IF NOT EXISTS uq_active_appointment_per_slot
    ON appointments (slot_id)
    WHERE status IN ('Scheduled', 'Confirmed', 'Rescheduled');

CREATE TABLE IF NOT EXISTS patient_access (
    appt_id          TEXT PRIMARY KEY REFERENCES appointments (appt_id),
    access_code_hash TEXT NOT NULL,
    failed_attempts  INTEGER NOT NULL DEFAULT 0,
    locked_until     TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS reschedule_history (
    history_id  BIGSERIAL PRIMARY KEY,
    appt_id     TEXT NOT NULL REFERENCES appointments (appt_id),
    old_slot_id TEXT NOT NULL REFERENCES slots (slot_id),
    new_slot_id TEXT NOT NULL REFERENCES slots (slot_id),
    old_start   TIMESTAMPTZ NOT NULL,
    new_start   TIMESTAMPTZ NOT NULL,
    changed_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS reminders (
    reminder_id BIGSERIAL PRIMARY KEY,
    appt_id     TEXT NOT NULL REFERENCES appointments (appt_id),
    due_at      TIMESTAMPTZ NOT NULL,
    status      TEXT NOT NULL DEFAULT 'Simulated' CHECK (status = 'Simulated'),
    suppressed  BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS notice_versions (
    version        TEXT PRIMARY KEY,
    notice_text    TEXT NOT NULL,
    effective_from TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS consents (
    consent_id     BIGSERIAL PRIMARY KEY,
    subject_id     TEXT NOT NULL,
    subject_role   TEXT NOT NULL CHECK (subject_role IN ('Patient', 'FrontDesk', 'Manager')),
    consent_type   TEXT NOT NULL CHECK (consent_type IN ('C1', 'C2', 'C3')),
    notice_version TEXT NOT NULL,
    status         TEXT NOT NULL CHECK (status IN ('Granted', 'Declined', 'Withdrawn')),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS idempotency_keys (
    idem_key      TEXT NOT NULL,
    endpoint      TEXT NOT NULL,
    subject_id    TEXT NOT NULL,
    request_hash  TEXT NOT NULL,
    status_code   INTEGER NOT NULL,
    response_body JSONB NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (idem_key, endpoint, subject_id)
);

CREATE TABLE IF NOT EXISTS audit_log (
    audit_id   BIGSERIAL PRIMARY KEY,
    actor_role TEXT NOT NULL,
    action     TEXT NOT NULL,
    appt_id    TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Indexes for the main read paths
CREATE INDEX IF NOT EXISTS ix_slots_type_status_start
    ON slots (type_id, status, start_time);
CREATE INDEX IF NOT EXISTS ix_slots_provider_start
    ON slots (provider_id, start_time);
CREATE INDEX IF NOT EXISTS ix_appointments_patient_status
    ON appointments (patient_id, status);
CREATE INDEX IF NOT EXISTS ix_consents_subject
    ON consents (subject_id, consent_type, created_at);
CREATE INDEX IF NOT EXISTS ix_audit_created
    ON audit_log (created_at);
CREATE INDEX IF NOT EXISTS ix_slots_start
    ON slots (start_time);
CREATE INDEX IF NOT EXISTS ix_appointments_slot
    ON appointments (slot_id);
CREATE INDEX IF NOT EXISTS ix_reminders_appt
    ON reminders (appt_id);
CREATE INDEX IF NOT EXISTS ix_history_appt
    ON reschedule_history (appt_id);
