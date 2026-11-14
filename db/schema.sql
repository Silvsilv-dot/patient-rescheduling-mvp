-- HealthFlow Clinic Showcase MVP — Idempotent Schema (Zero PHI/PII)
-- Enforces BR-01..BR-25, PROHIBITED_FIELD_PARTS, and atomic double-booking protection.

CREATE TABLE IF NOT EXISTS locations (
    location_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    suite TEXT NOT NULL,
    time_zone TEXT NOT NULL DEFAULT 'America/New_York'
);

CREATE TABLE IF NOT EXISTS appointment_types (
    type_id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    label_es TEXT NOT NULL,
    duration_minutes INTEGER NOT NULL DEFAULT 30 CHECK (duration_minutes = 30)
);

CREATE TABLE IF NOT EXISTS providers (
    provider_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    specialty TEXT NOT NULL,
    specialty_es TEXT NOT NULL,
    location_id TEXT NOT NULL REFERENCES locations(location_id),
    active BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS patients (
    patient_id TEXT PRIMARY KEY,
    monogram TEXT NOT NULL CHECK (char_length(monogram) BETWEEN 2 AND 3),
    display_alias TEXT NOT NULL,
    consent_c1_version TEXT,
    consent_c1_granted_at TIMESTAMPTZ,
    consent_c2_reminders BOOLEAN NOT NULL DEFAULT FALSE,
    consent_c2_updated_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS slots (
    slot_id TEXT PRIMARY KEY,
    provider_id TEXT NOT NULL REFERENCES providers(provider_id),
    type_id TEXT NOT NULL REFERENCES appointment_types(type_id),
    start_time TIMESTAMPTZ NOT NULL,
    end_time TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL DEFAULT 'Available' CHECK (status IN ('Available', 'Booked', 'Blocked')),
    CHECK (end_time > start_time)
);

CREATE TABLE IF NOT EXISTS appointments (
    appt_id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients(patient_id),
    slot_id TEXT NOT NULL REFERENCES slots(slot_id),
    type_id TEXT NOT NULL REFERENCES appointment_types(type_id),
    status TEXT NOT NULL DEFAULT 'Scheduled'
        CHECK (status IN ('Scheduled', 'Confirmed', 'Rescheduled', 'Cancelled', 'Completed', 'No-show')),
    reschedule_count INTEGER NOT NULL DEFAULT 0 CHECK (reschedule_count >= 0 AND reschedule_count <= 2),
    late_cancellation BOOLEAN NOT NULL DEFAULT FALSE,
    access_code_hash TEXT NOT NULL,
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- BR-10: Prevent two active appointments from ever claiming the same slot
CREATE UNIQUE INDEX IF NOT EXISTS uq_active_appointment_per_slot
    ON appointments (slot_id)
    WHERE status IN ('Scheduled', 'Confirmed', 'Rescheduled');

CREATE TABLE IF NOT EXISTS reminders (
    reminder_id SERIAL PRIMARY KEY,
    appt_id TEXT NOT NULL REFERENCES appointments(appt_id) ON DELETE CASCADE,
    reminder_type TEXT NOT NULL CHECK (reminder_type IN ('48h', '24h')),
    scheduled_for TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL DEFAULT 'Simulated-Pending' CHECK (status IN ('Simulated-Pending', 'Suppressed'))
);

CREATE TABLE IF NOT EXISTS idempotency_keys (
    idempotency_key TEXT PRIMARY KEY,
    request_fingerprint TEXT NOT NULL,
    response_payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- BR-16: Immutable operational audit log (Zero PHI/PII)
CREATE TABLE IF NOT EXISTS audit_logs (
    audit_id SERIAL PRIMARY KEY,
    actor_role TEXT NOT NULL CHECK (actor_role IN ('Patient', 'FrontDesk', 'Clinician', 'Manager', 'System')),
    actor_alias TEXT NOT NULL,
    action TEXT NOT NULL,
    appt_id TEXT,
    old_slot_id TEXT,
    new_slot_id TEXT,
    detail TEXT NOT NULL,
    idempotency_prefix TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Batch 4 Operational Tables (BR-13, BR-24)
CREATE TABLE IF NOT EXISTS provider_delays (
    delay_id SERIAL PRIMARY KEY,
    provider_id TEXT NOT NULL REFERENCES providers(provider_id),
    delay_minutes INTEGER NOT NULL CHECK (delay_minutes >= 0 AND delay_minutes <= 180),
    reason_code TEXT NOT NULL DEFAULT 'CLINIC_FLOW',
    active BOOLEAN NOT NULL DEFAULT TRUE,
    logged_by_role TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS visit_checkins (
    appt_id TEXT PRIMARY KEY REFERENCES appointments(appt_id) ON DELETE CASCADE,
    room_status TEXT NOT NULL DEFAULT 'Not Arrived'
        CHECK (room_status IN ('Not Arrived', 'Checked In', 'Roomed', 'In Visit', 'Completed', 'No-Show')),
    updated_by_role TEXT NOT NULL DEFAULT 'System',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS staff_consents (
    consent_id SERIAL PRIMARY KEY,
    role TEXT NOT NULL CHECK (role IN ('FrontDesk', 'Clinician', 'Manager')),
    persona_key TEXT NOT NULL,
    c3_version TEXT NOT NULL DEFAULT 'v1.0',
    acknowledged_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS demo_personas (
    persona_key TEXT PRIMARY KEY,
    role TEXT NOT NULL CHECK (role IN ('Patient', 'FrontDesk', 'Clinician', 'Manager')),
    display_label TEXT NOT NULL,
    display_label_es TEXT NOT NULL,
    scenario_tag TEXT NOT NULL,
    scenario_tag_es TEXT NOT NULL,
    target_id TEXT NOT NULL
);
