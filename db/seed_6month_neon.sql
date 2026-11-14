-- HealthFlow Clinic — 6-Month Historic + 14-Day Future Synthetic Dataset (Zero PHI/PII)
-- Run this directly in the Neon SQL Editor after db/schema.sql.

TRUNCATE TABLE visit_checkins, provider_delays, staff_consents, demo_personas,
               audit_logs, idempotency_keys, reminders, appointments, slots,
               patients, providers, appointment_types, locations RESTART IDENTITY CASCADE;

INSERT INTO locations (location_id, name, suite, time_zone) VALUES
  ('LOC-MAIN', 'Main Clinic', 'Suite 200', 'America/New_York'),
  ('LOC-NORTH', 'North Wing', 'Suite 110', 'America/New_York'),
  ('LOC-DOWNTOWN', 'Downtown Specialty Center', 'Floor 4', 'America/New_York');

INSERT INTO appointment_types (type_id, label, label_es, duration_minutes) VALUES
  ('TYPE-CARD', 'Specialty Cardiology', 'Cardiología Especializada', 30),
  ('TYPE-PRIM', 'Primary Care Follow-Up', 'Seguimiento de Atención Primaria', 30),
  ('TYPE-WELL', 'Annual Wellness Exam', 'Examen Anual de Bienestar', 30),
  ('TYPE-LAB',  'Diagnostic Consult', 'Consulta de Diagnóstico', 30);

INSERT INTO providers (provider_id, display_name, specialty, specialty_es, location_id, active) VALUES
  ('PROV-01', 'Dr. A. Chen',   'Specialty Cardiology', 'Cardiología Especializada', 'LOC-MAIN', TRUE),
  ('PROV-02', 'Dr. M. Patel',  'Specialty Cardiology', 'Cardiología Especializada', 'LOC-MAIN', TRUE),
  ('PROV-03', 'Dr. S. Rivera', 'Primary Care',         'Atención Primaria',         'LOC-NORTH', TRUE),
  ('PROV-04', 'Dr. E. Brooks', 'Primary Care',         'Atención Primaria',         'LOC-NORTH', TRUE),
  ('PROV-05', 'Dr. K. Nair',   'Diagnostic Consult',   'Consulta de Diagnóstico',   'LOC-DOWNTOWN', TRUE),
  ('PROV-06', 'Dr. L. Gomez',  'Annual Wellness',      'Bienestar Anual',           'LOC-DOWNTOWN', TRUE);

-- 40 Synthetic Patients (Zero real PHI/PII — synthetic initials & opaque IDs)
INSERT INTO patients (patient_id, monogram, display_alias, consent_c1_version, consent_c1_granted_at, consent_c2_reminders, consent_c2_updated_at)
SELECT
  'PAT-' || LPAD(gs::text, 3, '0'),
  CHR(65 + (gs % 26)) || CHR(65 + ((gs * 7) % 26)),
  'Patient #' || LPAD(gs::text, 3, '0'),
  'v1.0',
  NOW() - INTERVAL '180 days',
  (gs % 3 <> 0),
  NOW() - INTERVAL '180 days'
FROM generate_series(1, 40) AS gs;

-- Generate 6 months (180 days) of historic slots + 14 days of future slots (weekdays, 9:00 AM - 3:30 PM EST)
WITH days AS (
  SELECT (DATE_TRUNC('day', NOW() AT TIME ZONE 'America/New_York') + (d || ' days')::interval) AS day_est,
         d AS day_offset
  FROM generate_series(-180, 14) AS d
  WHERE EXTRACT(ISODOW FROM (DATE_TRUNC('day', NOW() AT TIME ZONE 'America/New_York') + (d || ' days')::interval)) <= 5
),
hours AS (
  SELECT m AS min_offset
  FROM generate_series(540, 900, 30) AS m -- 09:00 to 15:00 EST
),
grid AS (
  SELECT
    p.provider_id,
    CASE
      WHEN p.provider_id IN ('PROV-01','PROV-02') THEN 'TYPE-CARD'
      WHEN p.provider_id IN ('PROV-03','PROV-04') THEN 'TYPE-PRIM'
      WHEN p.provider_id = 'PROV-05' THEN 'TYPE-LAB'
      ELSE 'TYPE-WELL'
    END AS type_id,
    ((d.day_est + (h.min_offset || ' minutes')::interval) AT TIME ZONE 'America/New_York') AS start_ts,
    d.day_offset,
    h.min_offset,
    ROW_NUMBER() OVER (ORDER BY d.day_offset, p.provider_id, h.min_offset) AS rn
  FROM days d
  CROSS JOIN providers p
  CROSS JOIN hours h
  -- Keep compact density so Neon free tier stays super fast (< 3,500 rows)
  WHERE (d.day_offset >= -7 OR (ABS(d.day_offset) % 3 = 0 AND h.min_offset IN (540, 600, 660, 780, 840)))
)
INSERT INTO slots (slot_id, provider_id, type_id, start_time, end_time, status)
SELECT
  'SLOT-' || LPAD(rn::text, 5, '0'),
  provider_id,
  type_id,
  start_ts,
  start_ts + INTERVAL '30 minutes',
  CASE
    WHEN day_offset < 0 THEN 'Booked'
    WHEN day_offset = 0 AND min_offset <= 780 THEN 'Booked'
    WHEN provider_id = 'PROV-01' AND (rn % 10) <> 0 THEN 'Booked' -- >90% utilized flag
    WHEN provider_id = 'PROV-03' AND (rn % 2) = 0 THEN 'Available' -- <60% utilized flag
    WHEN (rn % 3) = 0 THEN 'Booked'
    ELSE 'Available'
  END
FROM grid;

-- Populate historic (6-month) and upcoming appointments covering all standard flows
WITH booked_slots AS (
  SELECT slot_id, provider_id, type_id, start_time,
         ROW_NUMBER() OVER (ORDER BY start_time, slot_id) AS appt_rn
  FROM slots
  WHERE status = 'Booked'
)
INSERT INTO appointments (
  appt_id, patient_id, slot_id, type_id, status, reschedule_count, late_cancellation, access_code_hash, created_at, updated_at
)
SELECT
  'APT-' || (2026000 + appt_rn)::text,
  'PAT-' || LPAD(((appt_rn % 40) + 1)::text, 3, '0'),
  slot_id,
  type_id,
  CASE
    WHEN start_time < NOW() - INTERVAL '2 hours' THEN
      CASE
        WHEN appt_rn % 17 = 0 THEN 'No-show'
        WHEN appt_rn % 11 = 0 THEN 'Cancelled'
        WHEN appt_rn % 7 = 0  THEN 'Completed'
        ELSE 'Completed'
      END
    ELSE
      CASE
        WHEN appt_rn % 5 = 0 THEN 'Rescheduled'
        WHEN appt_rn % 2 = 0 THEN 'Confirmed'
        ELSE 'Scheduled'
      END
  END,
  CASE WHEN appt_rn % 5 = 0 THEN 1 ELSE 0 END,
  (start_time < NOW() AND appt_rn % 22 = 0),
  'pbkdf2_sha256$100000$demosalt$8f9e7d6c5b4a3f2e1d0c9b8a7f6e5d4c3b2a1f0e9d8c7b6a5f4e3d2c1b0a9f8e',
  start_time - INTERVAL '7 days',
  start_time - INTERVAL '1 day'
FROM booked_slots;

-- Ensure 5 signature showcase scenarios exist for today/upcoming days
-- 1) Standard Scheduled Visit (0/2 reschedules)
UPDATE appointments SET patient_id = 'PAT-001', status = 'Scheduled', reschedule_count = 0
WHERE appt_id = (SELECT appt_id FROM appointments WHERE status IN ('Scheduled','Confirmed') AND appt_id NOT IN ('APT-2026001') ORDER BY created_at DESC LIMIT 1 OFFSET 0);

-- 2) Multi-appointment patient (PAT-001 has 2 active future visits to test conflict detection BR-06)
UPDATE appointments SET patient_id = 'PAT-001', status = 'Confirmed', reschedule_count = 1
WHERE appt_id = (SELECT appt_id FROM appointments WHERE status IN ('Scheduled','Confirmed') AND patient_id <> 'PAT-001' ORDER BY created_at DESC LIMIT 1 OFFSET 1);

-- 3) Max Reschedules Reached (PAT-002 has 2/2 used -> tests BR-08 lock)
UPDATE appointments SET patient_id = 'PAT-002', status = 'Rescheduled', reschedule_count = 2
WHERE appt_id = (SELECT appt_id FROM appointments WHERE status IN ('Scheduled','Confirmed','Rescheduled') AND patient_id NOT IN ('PAT-001') ORDER BY created_at DESC LIMIT 1 OFFSET 2);

-- 4) Inside 24-hour Lock Window (PAT-003 -> tests BR-07 lock)
UPDATE appointments SET patient_id = 'PAT-003', status = 'Confirmed', reschedule_count = 0
WHERE appt_id = (
  SELECT a.appt_id FROM appointments a JOIN slots s ON a.slot_id = s.slot_id
  WHERE s.start_time BETWEEN NOW() + INTERVAL '2 hours' AND NOW() + INTERVAL '20 hours'
  LIMIT 1
);

-- Active clinic delay for Dr. M. Patel (+20 min, BR-13)
INSERT INTO provider_delays (provider_id, delay_minutes, reason_code, active, logged_by_role)
VALUES ('PROV-02', 20, 'EXTENDED_CONSULT', TRUE, 'Clinician');

-- Check-in states for today's visits
INSERT INTO visit_checkins (appt_id, room_status, updated_by_role)
SELECT a.appt_id,
       CASE WHEN ROW_NUMBER() OVER (ORDER BY s.start_time) % 3 = 1 THEN 'Checked In'
            WHEN ROW_NUMBER() OVER (ORDER BY s.start_time) % 3 = 2 THEN 'Roomed'
            ELSE 'Not Arrived' END,
       'FrontDesk'
FROM appointments a
JOIN slots s ON a.slot_id = s.slot_id
WHERE s.start_time::date = (NOW() AT TIME ZONE 'America/New_York')::date
  AND a.status IN ('Scheduled', 'Confirmed', 'Rescheduled');

-- Populate realistic 6-month audit log history (Zero PHI/PII)
INSERT INTO audit_logs (actor_role, actor_alias, action, appt_id, old_slot_id, new_slot_id, detail, idempotency_prefix, created_at)
SELECT
  CASE WHEN gs % 4 = 0 THEN 'FrontDesk' WHEN gs % 4 = 1 THEN 'Patient' WHEN gs % 4 = 2 THEN 'Clinician' ELSE 'Manager' END,
  CASE WHEN gs % 4 = 0 THEN 'Main Desk' WHEN gs % 4 = 1 THEN 'Patient Self-Service' WHEN gs % 4 = 2 THEN 'Dr. M. Patel' ELSE 'Ops Lead' END,
  CASE WHEN gs % 3 = 0 THEN 'RESCHEDULED' WHEN gs % 3 = 1 THEN 'CONFIRMED' ELSE 'DELAY_LOGGED' END,
  'APT-' || (2026010 + gs)::text,
  'SLOT-' || LPAD((gs * 3)::text, 5, '0'),
  'SLOT-' || LPAD((gs * 3 + 2)::text, 5, '0'),
  CASE WHEN gs % 3 = 0 THEN 'Atomic slot transfer completed (BR-09/BR-10)'
       WHEN gs % 3 = 1 THEN 'Visit attendance confirmed'
       ELSE 'Clinic delay +20m recorded (BR-13)' END,
  SUBSTRING(MD5(gs::text), 1, 10),
  NOW() - ((gs * 3) || ' days')::interval
FROM generate_series(1, 45) AS gs;

-- Seed One-Click Demo Personas across all 4 roles
INSERT INTO demo_personas (persona_key, role, display_label, display_label_es, scenario_tag, scenario_tag_es, target_id) VALUES
  ('pat-multi',   'Patient',   'Patient #001 (2 Active Visits)',      'Paciente #001 (2 Citas Activas)',      'Standard + Multi-Visit Conflict Check (BR-06)', 'Estándar + Verificación de Conflicto (BR-06)', 'PAT-001'),
  ('pat-max',     'Patient',   'Patient #002 (2/2 Reschedules Used)', 'Paciente #002 (2/2 Reprogramaciones)',   'Max Reschedule Guardrail (BR-08)',               'Límite de Reprogramación (BR-08)',            'PAT-002'),
  ('pat-lock',    'Patient',   'Patient #003 (Visit < 24h)',          'Paciente #003 (Cita < 24h)',           '24-Hour Lock Window Guardrail (BR-07)',          'Bloqueo de 24 Horas (BR-07)',                 'PAT-003'),
  ('pat-std',     'Patient',   'Patient #004 (Confirmed Visit)',      'Paciente #004 (Cita Confirmada)',      'Self-Service Confirm / Cancel Flow',             'Flujo de Confirmación / Cancelación',          'PAT-004'),
  ('staff-all',   'FrontDesk', 'Main Clinic Triage Coordinator',      'Coordinador de Triaje — Clínica Central', 'All 3 Locations · Drag & Drop Roster',         '3 Ubicaciones · Tablero Arrastrar y Soltar',   'ALL'),
  ('staff-north', 'FrontDesk', 'North Wing Front Desk',               'Recepción Ala Norte',                  'North Wing Queue & Check-In',                    'Cola y Registro Ala Norte',                    'LOC-NORTH'),
  ('clin-chen',   'Clinician', 'Dr. A. Chen (Cardiology · >90% Load)','Dra. A. Chen (Cardiología · >90%)',     'High-Volume Schedule (BR-13 Flag)',              'Agenda de Alta Demanda (Alerta BR-13)',         'PROV-01'),
  ('clin-patel',  'Clinician', 'Dr. M. Patel (Cardiology · +20m Delay)','Dr. M. Patel (Cardiología · +20m)',   'Active Clinic Delay Broadcast',                  'Alerta Activa de Retraso Clínico',             'PROV-02'),
  ('clin-rivera', 'Clinician', 'Dr. S. Rivera (Primary Care · Open)', 'Dra. S. Rivera (Primaria · Disponible)', 'Open Capacity (<60% Utilization)',               'Capacidad Disponible (<60% Uso)',              'PROV-03'),
  ('mgr-all',     'Manager',   'Regional Operations Director',        'Director Regional de Operaciones',     '6-Month KPIs & Immutable Audit Trail',           'KPIs de 6 Meses y Registro de Auditoría',      'ALL');
