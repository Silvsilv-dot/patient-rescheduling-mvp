// HealthFlow Clinic — Bilingual (EN/ES), WCAG 2.2 AA, Zero-PHI Interactive Client
const I18N = {
  en: {
    skip_to_main: "Skip to main content",
    emergency_text: "Not for medical emergencies. If you have urgent or life-threatening symptoms, call 911 or go to the nearest emergency room immediately.",
    demo_pill: "DEMO · Synthetic Data Only (Zero PHI/PII)",
    brand_sub: "Care Coordination & Scheduling",
    tz_local: "My Local Time",
    tz_est: "Clinic Base (EST/EDT)",
    theme_dark: "Dark",
    theme_light: "Light",
    privacy_btn: "Privacy & Consent",
    tab_patient: "1. Patient Rescheduling",
    tab_staff: "2. Front-Desk Triage",
    tab_clinician: "3. Clinician Day View",
    tab_manager: "4. Manager Analytics (6-Mo)",
    drawer_title: "Choose a New Appointment Time",
    footer_compliance: "HIPAA Minimum-Necessary & WCAG 2.2 AA Compliant Prototype · Base Clinic Time: EST/EDT (America/New_York)",
    patient_title: "Your Upcoming & Past Appointments",
    patient_sub: "Confirm, move, or cancel eligible visits in plain English. Changes update the clinic schedule right away.",
    confirm_visit: "Confirm Visit",
    reschedule_visit: "Move / Reschedule",
    cancel_visit: "Cancel Visit",
    lock_24h_msg: "Visits starting within 24 hours cannot be moved online. Please call the front desk for help.",
    lock_max_msg: "You have used both online reschedules (2 of 2). Please call the clinic desk to make another change.",
    staff_title: "Staff Coordination & Triage",
    staff_sub: "Drag any appointment card onto an open slot below (or click Reschedule) to move visits atomically.",
    clinician_title: "Clinician Daily Timeline",
    clinician_sub: "Real-time room readiness, visit progression, and clinic delay broadcasts.",
    manager_title: "Clinic Operations & 6-Month Historic KPIs",
    manager_sub: "Aggregated capacity utilization, late-cancellation metrics, and immutable audit trail (BR-13 / BR-16).",
  },
  es: {
    skip_to_main: "Saltar al contenido principal",
    emergency_text: "No es para emergencias médicas. Si tiene síntomas urgentes o que pongan en peligro su vida, llame al 911 o acuda a la sala de emergencias más cercana de inmediato.",
    demo_pill: "DEMO · Solo Datos Sintéticos (Cero PHI/PII)",
    brand_sub: "Coordinación de Atención y Citas",
    tz_local: "Mi Hora Local",
    tz_est: "Hora Base Clínica (EST/EDT)",
    theme_dark: "Oscuro",
    theme_light: "Claro",
    privacy_btn: "Privacidad y Consentimiento",
    tab_patient: "1. Reprogramación Paciente",
    tab_staff: "2. Triaje Recepción",
    tab_clinician: "3. Vista del Médico",
    tab_manager: "4. Analítica Gerencial (6 Meses)",
    drawer_title: "Elija un Nuevo Horario de Cita",
    footer_compliance: "Prototipo Cumplidor HIPAA Mínimo Necesario y WCAG 2.2 AA · Hora Base: EST/EDT (America/New_York)",
    patient_title: "Sus Citas Próximas y Anteriores",
    patient_sub: "Confirme, cambie o cancele citas elegibles de forma sencilla. Los cambios actualizan la agenda al instante.",
    confirm_visit: "Confirmar Cita",
    reschedule_visit: "Cambiar Horario",
    cancel_visit: "Cancelar Cita",
    lock_24h_msg: "Las citas dentro de las próximas 24 horas no se pueden cambiar en línea. Llame a recepción.",
    lock_max_msg: "Ha usado sus 2 cambios en línea (2 de 2). Llame a la clínica para más ayuda.",
    staff_title: "Coordinación y Triaje de Personal",
    staff_sub: "Arrastre cualquier cita a un horario libre abajo (o pulse Reprogramar) para moverla de forma segura.",
    clinician_title: "Agenda Diaria del Médico",
    clinician_sub: "Estado de sala en tiempo real, progreso de consultas y alertas de retraso.",
    manager_title: "Operaciones y KPIs Históricos de 6 Meses",
    manager_sub: "Utilización de capacidad, cancelaciones tardías y registro de auditoría inmutable (BR-13 / BR-16).",
  }
};

const state = {
  lang: "en",
  theme: "light",
  tzMode: "LOCAL",
  userTz: Intl.DateTimeFormat().resolvedOptions().timeZone || "America/New_York",
  clinicTz: "America/New_York",
  activeRole: "Patient",
  personas: [],
  selectedPersonaByRole: {
    Patient: "PAT-001",
    FrontDesk: "ALL",
    Clinician: "PROV-01",
    Manager: "ALL",
  },
  staffFilter: "ALL",
  managerWindow: "180d",
  draggedApptId: null,
};

function t(key) {
  return (I18N[state.lang] && I18N[state.lang][key]) || I18N.en[key] || key;
}

function makeIdempotencyKey() {
  return "demo-" + crypto.randomUUID().replace(/-/g, "").slice(0, 24);
}

function formatDualTime(isoString) {
  const dt = new Date(isoString);
  const locale = state.lang === "es" ? "es-US" : "en-US";
  const activeTz = state.tzMode === "LOCAL" ? state.userTz : state.clinicTz;

  const primary = new Intl.DateTimeFormat(locale, {
    timeZone: activeTz,
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  }).format(dt);

  if (activeTz === state.clinicTz) {
    return primary;
  }
  const estStr = new Intl.DateTimeFormat(locale, {
    timeZone: state.clinicTz,
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  }).format(dt);
  return `${primary} (${estStr} Clinic)`;
}

function showBanner(message) {
  const el = document.getElementById("status-banner");
  el.textContent = message;
  el.classList.remove("hidden");
  setTimeout(() => el.classList.add("hidden"), 6000);
}

function applyTranslations() {
  document.documentElement.lang = state.lang;
  document.querySelectorAll("[data-i18n]").forEach((el) => {
    const key = el.getAttribute("data-i18n");
    if (key) el.textContent = t(key);
  });
  document.getElementById("theme-label").textContent =
    state.theme === "light" ? t("theme_dark") : t("theme_light");
}

function updateFooterClock() {
  const now = new Date();
  const est = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit",
    timeZoneName: "short",
  }).format(now);
  const local = new Intl.DateTimeFormat("en-US", {
    timeZone: state.userTz,
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  }).format(now);
  document.getElementById("live-clock-readout").textContent =
    state.userTz === "America/New_York" ? `Clinic Clock: ${est}` : `Local: ${local} · Clinic: ${est}`;
}

async function apiFetch(url, options = {}) {
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const data = await res.json();
  if (!res.ok) {
    throw new Error(data.detail || (data.error && data.error.message) || "Request failed");
  }
  return data;
}

function clearAndAppend(parent, nodes) {
  parent.replaceChildren(...(Array.isArray(nodes) ? nodes : [nodes]));
}

function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "className") node.className = v;
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2).toLowerCase(), v);
    else if (v !== null && v !== undefined) node.setAttribute(k, String(v));
  }
  for (const child of Array.isArray(children) ? children : [children]) {
    if (typeof child === "string") node.appendChild(document.createTextNode(child));
    else if (child instanceof Node) node.appendChild(child);
  }
  return node;
}

// --- One-Click Persona Switcher ---
async function loadPersonas() {
  const data = await apiFetch("/api/v1/demo/personas");
  state.personas = data.personas || [];
  syncPersonaDropdown();
}

function syncPersonaDropdown() {
  const sel = document.getElementById("persona-select");
  const rolePersonas = state.personas.filter((p) => p.role === state.activeRole);
  const options = rolePersonas.map((p) => {
    const label = state.lang === "es" ? `${p.display_label_es} — ${p.scenario_tag_es}` : `${p.display_label} — ${p.scenario_tag}`;
    const opt = el("option", { value: p.target_id }, label);
    if (p.target_id === state.selectedPersonaByRole[state.activeRole]) opt.selected = true;
    return opt;
  });
  clearAndAppend(sel, options);
}

// --- Modal & Drawer Helpers ---
function openModal(titleText, bodyNodes, footerNodes) {
  document.getElementById("modal-title").textContent = titleText;
  clearAndAppend(document.getElementById("modal-body"), bodyNodes);
  clearAndAppend(document.getElementById("modal-footer"), footerNodes);
  document.getElementById("modal-backdrop").classList.remove("hidden");
  document.getElementById("confirm-modal").classList.remove("hidden");
}

function closeModal() {
  document.getElementById("modal-backdrop").classList.add("hidden");
  document.getElementById("confirm-modal").classList.add("hidden");
}

function closeDrawer() {
  document.getElementById("drawer-backdrop").classList.add("hidden");
  document.getElementById("reschedule-drawer").classList.add("hidden");
}

// --- Consent & Privacy Notice Modal (BR-18, BR-19, BR-24) ---
function openPrivacyModal() {
  const patientId = state.selectedPersonaByRole.Patient;
  const optInCheckbox = el("input", { type: "checkbox", id: "c2-optin", checked: "checked" });

  const body = [
    el("p", {}, state.lang === "es"
      ? "Este portal utiliza datos sintéticos educativos. Nunca se recopilan ni muestran números de seguro social, direcciones ni diagnósticos clínicos."
      : "This portal uses educational synthetic records only. Social Security numbers, street addresses, and clinical diagnoses are never collected or displayed."),
    el("p", {}, state.lang === "es"
      ? "Al continuar, acepta el Aviso de Privacidad (C1 v1.0) y los Términos de Uso. Todas las acciones se registran de forma segura."
      : "By continuing, you acknowledge the Privacy Notice (C1 v1.0) and Terms of Use. Every schedule change is recorded in the audit log."),
    el("label", { style: "display:flex;align-items:center;gap:10px;margin-top:12px;min-height:44px;" }, [
      optInCheckbox,
      el("span", {}, state.lang === "es"
        ? "Acepto recibir recordatorios simulados de cita (48h y 24h antes) — Consentimiento C2"
        : "Opt in to simulated visit reminders (48h and 24h prior) — C2 Consent")
    ])
  ];

  const acceptBtn = el("button", {
    type: "button",
    className: "btn btn-primary",
    onClick: async () => {
      await apiFetch("/api/v1/consent/c1", {
        method: "POST",
        body: JSON.stringify({
          patient_id: patientId,
          accept_privacy_terms: true,
          opt_in_reminders: optInCheckbox.checked,
        }),
      });
      closeModal();
      showBanner(state.lang === "es" ? "Preferencias de privacidad y recordatorios guardadas." : "Privacy consent and reminder preferences saved.");
      renderActiveView();
    }
  }, state.lang === "es" ? "Aceptar y Guardar" : "Accept & Save Preferences");

  openModal(t("privacy_btn"), body, [
    el("button", { type: "button", className: "btn", onClick: closeModal }, state.lang === "es" ? "Cerrar" : "Close"),
    acceptBtn,
  ]);
}

// --- Reschedule Drawer (Shared by Patient & Front Desk) ---
async function openRescheduleDrawer(appt, isStaff = false) {
  const drawer = document.getElementById("reschedule-drawer");
  const backdrop = document.getElementById("drawer-backdrop");
  document.getElementById("drawer-subtitle").textContent =
    `${appt.appt_id} · ${formatDualTime(appt.start_time)}`;

  const bodyEl = document.getElementById("drawer-body");
  clearAndAppend(bodyEl, el("p", {}, "Loading open 30-minute slots..."));
  backdrop.classList.remove("hidden");
  drawer.classList.remove("hidden");

  const data = await apiFetch(`/api/v1/appointments/${appt.appt_id}/slots?staff_override=${isStaff}`);
  const nodes = [];

  if (data.low_availability_fallback) {
    nodes.push(el("div", { className: "status-banner", style: "margin-bottom:12px;" },
      state.lang === "es"
        ? "Quedan menos de 3 horarios disponibles en los próximos 14 días. También puede llamar a recepción al (555) 010-2026."
        : "Fewer than 3 slots remain in the next 14 days. You may also call the clinic front desk at (555) 010-2026 for priority assistance."
    ));
  }

  // Wait-Time Comparison Cards (BR-12)
  if (data.wait_time_comparison && data.wait_time_comparison.length > 0) {
    nodes.push(el("h3", { style: "margin:0 0 8px;font-size:1rem;" },
      state.lang === "es" ? "Próxima Disponibilidad por Opción" : "Earliest Opening Comparison"
    ));
    const waitList = el("div", { className: "kpi-grid", style: "margin-bottom:16px;" },
      data.wait_time_comparison.map((w) =>
        el("div", { className: "panel-card", style: "padding:12px;" }, [
          el("div", { className: "kpi-label" }, w.option_label),
          el("div", { style: "font-weight:700;margin-top:4px;" }, `${w.wait_days} ${state.lang === "es" ? "días de espera" : "days wait"}`),
          el("div", { className: "kpi-sub" }, formatDualTime(w.earliest_start)),
        ])
      )
    );
    nodes.push(waitList);
  }

  nodes.push(el("h3", { style: "margin:0 0 6px;font-size:1rem;" },
    state.lang === "es" ? "Seleccione un Nuevo Horario (30 min)" : "Select a New 30-Minute Slot"
  ));

  const grid = el("div", { className: "slot-grid" },
    data.slots.slice(0, 24).map((slot) => {
      const reasonLabels = {
        booked: state.lang === "es" ? "Ocupado" : "Booked",
        blocked: state.lang === "es" ? "Bloqueado" : "Blocked",
        past: state.lang === "es" ? "Pasado" : "Past",
        current: state.lang === "es" ? "Actual" : "Current Slot",
        conflict: state.lang === "es" ? "Conflicto de horario" : "Schedule Conflict",
      };
      const btn = el("button", {
        type: "button",
        className: "slot-btn",
        disabled: slot.available ? null : "disabled",
        onClick: () => promptRescheduleConfirm(appt, slot, isStaff),
      }, [
        el("strong", {}, formatDualTime(slot.start_time)),
        el("span", { className: "kpi-sub" }, slot.available ? slot.provider_label : `${slot.provider_label} · ${reasonLabels[slot.unavailable_reason] || "Unavailable"}`),
      ]);
      return btn;
    })
  );
  nodes.push(grid);
  clearAndAppend(bodyEl, nodes);
}

function promptRescheduleConfirm(appt, slot, isStaff) {
  const msg = state.lang === "es"
    ? `¿Desea mover su cita ${appt.appt_id} al ${formatDualTime(slot.start_time)} en ${slot.location_name}?`
    : `Would you like to move appointment ${appt.appt_id} to ${formatDualTime(slot.start_time)} at ${slot.location_name}?`;

  openModal(
    state.lang === "es" ? "Confirmar Cambio de Cita" : "Confirm Appointment Move",
    [el("p", {}, msg)],
    [
      el("button", { type: "button", className: "btn", onClick: closeModal }, state.lang === "es" ? "Volver" : "Go Back"),
      el("button", {
        type: "button",
        className: "btn btn-primary",
        onClick: async () => {
          await apiFetch(`/api/v1/appointments/${appt.appt_id}/reschedule`, {
            method: "POST",
            headers: { "Idempotency-Key": makeIdempotencyKey() },
            body: JSON.stringify({
              new_slot_id: slot.slot_id,
              actor_role: isStaff ? "FrontDesk" : "Patient",
              actor_alias: isStaff ? "Front-Desk Coordinator" : "Patient Self-Service",
            }),
          });
          closeModal();
          closeDrawer();
          showBanner(state.lang === "es"
            ? `¡Listo! La cita ${appt.appt_id} se movió a ${formatDualTime(slot.start_time)}.`
            : `Done! Appointment ${appt.appt_id} has been moved to ${formatDualTime(slot.start_time)}.`
          );
          renderActiveView();
        },
      }, state.lang === "es" ? "Sí, Mover Cita" : "Yes, Move My Appointment"),
    ]
  );
}

function promptCancelConfirm(appt) {
  const warnText = appt.allowed_actions.is_late_cancel_window
    ? (state.lang === "es"
      ? "Aviso: Esta cita comienza en menos de 24 horas y se registrará como cancelación tardía."
      : "Please note: This visit starts in less than 24 hours and will be recorded as a late cancellation.")
    : (state.lang === "es"
      ? "El horario quedará libre de inmediato para otros pacientes."
      : "Your slot will be released immediately so another patient can book it.");

  openModal(
    state.lang === "es" ? "Confirmar Cancelación" : "Confirm Visit Cancellation",
    [
      el("p", {}, state.lang === "es"
        ? `¿Está seguro de que desea cancelar la cita ${appt.appt_id} (${formatDualTime(appt.start_time)})?`
        : `Are you sure you want to cancel appointment ${appt.appt_id} on ${formatDualTime(appt.start_time)}?`),
      el("p", { className: "muted-text" }, warnText),
    ],
    [
      el("button", { type: "button", className: "btn", onClick: closeModal }, state.lang === "es" ? "Mantener Cita" : "Keep My Visit"),
      el("button", {
        type: "button",
        className: "btn btn-danger",
        onClick: async () => {
          await apiFetch(`/api/v1/appointments/${appt.appt_id}/cancel`, {
            method: "POST",
            headers: { "Idempotency-Key": makeIdempotencyKey() },
            body: JSON.stringify({ confirm: true, actor_role: "Patient" }),
          });
          closeModal();
          showBanner(state.lang === "es"
            ? `La cita ${appt.appt_id} ha sido cancelada.`
            : `Appointment ${appt.appt_id} has been cancelled.`
          );
          renderActiveView();
        },
      }, state.lang === "es" ? "Sí, Cancelar Cita" : "Yes, Cancel Visit"),
    ]
  );
}

// --- VIEW 1: PATIENT RESCHEDULING PORTAL ---
async function renderPatientView(container) {
  const patientId = state.selectedPersonaByRole.Patient;
  const data = await apiFetch(`/api/v1/patient/${patientId}/overview`);

  const header = el("div", { className: "page-header" }, [
    el("div", {}, [
      el("h1", { className: "page-title" }, t("patient_title")),
      el("p", { className: "muted-text" }, `${data.patient.display_alias} · ${t("patient_sub")}`),
    ]),
    el("div", { className: "pill-group" }, [
      el("span", { className: "status-pill status-ok" },
        `✓ C1 Privacy ${data.patient.consent_c1_valid ? "Active" : "Required"}`),
      el("span", { className: "status-pill status-info" },
        `● C2 Reminders ${data.patient.consent_c2_reminders ? "ON (48h/24h)" : "OFF"}`),
    ]),
  ]);

  const upcomingCards = data.upcoming_appointments.length === 0
    ? [el("div", { className: "panel-card" }, state.lang === "es" ? "No tiene citas próximas activas." : "You have no active upcoming appointments.")]
    : data.upcoming_appointments.map((appt) => {
        const label = state.lang === "es" ? appt.visit_label_es : appt.visit_label;
        const statusClass = appt.status === "Confirmed" ? "status-ok" : appt.status === "Rescheduled" ? "status-info" : "status-warn";
        const statusPrefix = appt.status === "Confirmed" ? "✓" : appt.status === "Rescheduled" ? "↻" : "○";

        const lockNotice = appt.allowed_actions.lock_reason_code === "within_24h_lock"
          ? el("p", { className: "status-pill status-warn", style: "margin-top:10px;" }, `▲ ${t("lock_24h_msg")}`)
          : appt.allowed_actions.lock_reason_code === "max_reschedules_reached"
            ? el("p", { className: "status-pill status-warn", style: "margin-top:10px;" }, `▲ ${t("lock_max_msg")}`)
            : null;

        const delayNotice = appt.active_delay_minutes > 0
          ? el("p", { className: "status-pill status-danger", style: "margin-top:8px;" },
              `⏱ Clinic Notice: Care team is running ~${appt.active_delay_minutes} minutes behind schedule.`)
          : null;

        return el("article", { className: "panel-card", style: "margin-bottom:14px;" }, [
          el("div", { className: "page-header", style: "margin-bottom:8px;" }, [
            el("div", {}, [
              el("span", { className: `status-pill ${statusClass}` }, `${statusPrefix} ${appt.status}`),
              el("h2", { style: "margin:8px 0 2px;font-size:1.2rem;" }, label),
              el("div", { className: "muted-text" },
                `${appt.appt_id} · ${formatDualTime(appt.start_time)} · ${appt.location_name} (${appt.location_suite})`),
            ]),
            el("span", { className: "tag-pill" },
              `${state.lang === "es" ? "Cambios usados" : "Reschedules used"}: ${appt.reschedule_count} / ${appt.max_reschedules}`),
          ]),
          delayNotice,
          lockNotice,
          el("div", { className: "action-group", style: "margin-top:14px;" }, [
            el("button", {
              type: "button",
              className: "btn btn-primary",
              disabled: appt.allowed_actions.can_confirm ? null : "disabled",
              onClick: async () => {
                await apiFetch(`/api/v1/appointments/${appt.appt_id}/confirm`, {
                  method: "POST",
                  headers: { "Idempotency-Key": makeIdempotencyKey() },
                });
                showBanner(state.lang === "es"
                  ? `¡Gracias! Su visita ${appt.appt_id} está confirmada.`
                  : `Thank you! Your visit ${appt.appt_id} is now confirmed.`);
                renderActiveView();
              },
            }, t("confirm_visit")),
            el("button", {
              type: "button",
              className: "btn",
              disabled: appt.allowed_actions.can_reschedule ? null : "disabled",
              onClick: () => openRescheduleDrawer(appt, false),
            }, t("reschedule_visit")),
            el("button", {
              type: "button",
              className: "btn btn-danger",
              disabled: appt.allowed_actions.can_cancel ? null : "disabled",
              onClick: () => promptCancelConfirm(appt),
            }, t("cancel_visit")),
          ]),
        ]);
      });

  const pastSection = data.past_appointments.length > 0
    ? el("section", { className: "panel-card", style: "margin-top:20px;" }, [
        el("h2", { style: "margin:0 0 10px;font-size:1.1rem;" },
          state.lang === "es" ? "Historial Reciente de Visitas" : "Your Recent Visit History"),
        ...data.past_appointments.map((p) =>
          el("div", { className: "roster-row" }, [
            el("div", {}, [
              el("strong", {}, state.lang === "es" ? p.visit_label_es : p.visit_label),
              el("div", { className: "kpi-sub" }, `${p.appt_id} · ${formatDualTime(p.start_time)} · ${p.location_name}`),
            ]),
            el("span", { className: "tag-pill" }, p.status),
          ])
        ),
      ])
    : null;

  clearAndAppend(container, [header, ...upcomingCards, pastSection].filter(Boolean));
}

// --- VIEW 2: FRONT-DESK STAFF COORDINATION & TRIAGE (Matching Screenshot + Drag & Drop) ---
async function renderStaffView(container) {
  const locId = state.selectedPersonaByRole.FrontDesk;
  const data = await apiFetch(`/api/v1/staff/triage?location_id=${locId}`);

  const header = el("div", { className: "page-header" }, [
    el("div", {}, [
      el("h1", { className: "page-title" }, t("staff_title")),
      el("p", { className: "muted-text" }, t("staff_sub")),
    ]),
    el("button", {
      type: "button",
      className: "btn",
      onClick: () => renderActiveView(),
    }, state.lang === "es" ? "↻ Actualizar Datos en Vivo" : "↻ Refresh Live Data"),
  ]);

  const kpis = el("div", { className: "kpi-grid" }, [
    el("div", { className: "kpi-card" }, [
      el("div", { className: "kpi-label" }, state.lang === "es" ? "Reservas Totales" : "Total Bookings"),
      el("div", { className: "kpi-value" }, String(data.kpis.total_bookings)),
    ]),
    el("div", { className: "kpi-card" }, [
      el("div", { className: "kpi-label" }, state.lang === "es" ? "Visitas Confirmadas" : "Confirmed Visits"),
      el("div", { className: "kpi-value" }, String(data.kpis.confirmed_visits)),
    ]),
    el("div", { className: "kpi-card" }, [
      el("div", { className: "kpi-label" }, state.lang === "es" ? "Acción Pendiente" : "Pending Action"),
      el("div", { className: "kpi-value" }, String(data.kpis.pending_action)),
    ]),
    el("div", { className: "kpi-card" }, [
      el("div", { className: "kpi-label" }, state.lang === "es" ? "Carga Clínica" : "Clinician Load"),
      el("div", { className: "kpi-value" }, `${data.kpis.clinician_load_pct}%`),
    ]),
  ]);

  // Drag-and-Drop Dropzones for Open Slots
  const dropZonePanel = el("section", { className: "panel-card", style: "margin-bottom:18px;" }, [
    el("strong", {}, state.lang === "es"
      ? "Horarios Abiertos — Arrastre una cita aquí para moverla al instante:"
      : "Quick Open Slots — Drag any appointment row below onto an open slot to move immediately:"),
    el("div", { className: "dropzone-grid" },
      data.quick_open_slots.slice(0, 6).map((slot) => {
        const dz = el("div", {
          className: "dropzone-slot",
          tabindex: "0",
          onDragover: (e) => { e.preventDefault(); dz.classList.add("drag-over"); },
          onDragleave: () => dz.classList.remove("drag-over"),
          onDrop: async (e) => {
            e.preventDefault();
            dz.classList.remove("drag-over");
            if (!state.draggedApptId) return;
            await apiFetch(`/api/v1/appointments/${state.draggedApptId}/reschedule`, {
              method: "POST",
              headers: { "Idempotency-Key": makeIdempotencyKey() },
              body: JSON.stringify({
                new_slot_id: slot.slot_id,
                actor_role: "FrontDesk",
                actor_alias: "Staff Drag-and-Drop Board",
              }),
            });
            showBanner(`Moved ${state.draggedApptId} to ${slot.provider_name} (${formatDualTime(slot.start_time)})`);
            state.draggedApptId = null;
            renderActiveView();
          },
        }, [
          el("div", { style: "font-weight:700;font-size:0.9rem;" }, formatDualTime(slot.start_time)),
          el("div", { className: "kpi-sub" }, `${slot.provider_name} · ${slot.location_name}`),
        ]);
        return dz;
      })
    ),
  ]);

  // Filter Bar + Triage Roster
  const filteredRoster = data.roster.filter((r) => {
    if (state.staffFilter === "Confirmed") return r.status === "Confirmed";
    if (state.staffFilter === "Pending") return r.status === "Scheduled" || r.status === "Rescheduled";
    return true;
  });

  const filterBar = el("div", { className: "toolbar-row" }, [
    el("strong", {}, state.lang === "es" ? `Lista de Coordinación (${filteredRoster.length})` : `Coordination Roster (${filteredRoster.length})`),
    el("div", { className: "segmented-group", role: "group", "aria-label": "Filter roster status" },
      ["ALL", "Confirmed", "Pending"].map((f) =>
        el("button", {
          type: "button",
          className: `seg-btn ${state.staffFilter === f ? "active" : ""}`,
          onClick: () => { state.staffFilter = f; renderActiveView(); },
        }, f === "ALL" ? (state.lang === "es" ? "Todos" : "All") : f)
      )
    ),
  ]);

  const rosterCard = el("section", { className: "panel-card", style: "padding:0;overflow:hidden;" },
    filteredRoster.map((row) => {
      const statusClass = row.status === "Confirmed" ? "status-ok" : row.status === "Rescheduled" ? "status-info" : "status-warn";
      const rowEl = el("div", {
        className: "roster-row",
        draggable: "true",
        onDragstart: () => { state.draggedApptId = row.appt_id; rowEl.classList.add("dragging"); },
        onDragend: () => { rowEl.classList.remove("dragging"); },
      }, [
        el("div", { className: "row-identity" }, [
          el("div", { className: "monogram-badge", "aria-hidden": "true" }, row.monogram),
          el("div", {}, [
            el("strong", {}, `${row.display_alias} · ${row.provider_name}`),
            el("div", { className: "kpi-sub" }, `${row.appt_id} · ${formatDualTime(row.start_time)}`),
          ]),
        ]),
        el("div", { className: "pill-group" }, [
          el("span", { className: "tag-pill" }, state.lang === "es" ? row.specialty_label_es : row.specialty_label),
          el("span", { className: "tag-pill" }, row.location_name),
          el("span", { className: `status-pill ${statusClass}` }, `● ${row.status}`),
          el("span", { className: "tag-pill" }, row.room_status),
        ]),
        el("div", { className: "action-group" }, [
          el("button", {
            type: "button",
            className: "btn btn-primary",
            onClick: async () => {
              await apiFetch(`/api/v1/staff/appointments/${row.appt_id}/checkin`, {
                method: "POST",
                body: JSON.stringify({ room_status: "Checked In", actor_role: "FrontDesk" }),
              });
              showBanner(`${row.appt_id} checked in at front desk.`);
              renderActiveView();
            },
          }, state.lang === "es" ? "Registrar Llegada" : "Check In"),
          el("button", {
            type: "button",
            className: "btn",
            onClick: () => openRescheduleDrawer({
              appt_id: row.appt_id,
              start_time: row.start_time,
            }, true),
          }, state.lang === "es" ? "Reprogramar" : "Reschedule"),
        ]),
      ]);
      return rowEl;
    })
  );

  clearAndAppend(container, [header, kpis, dropZonePanel, filterBar, rosterCard]);
}

// --- VIEW 3: CLINICIAN DAY TIMELINE ---
async function renderClinicianView(container) {
  const providerId = state.selectedPersonaByRole.Clinician;
  const data = await apiFetch(`/api/v1/clinician/${providerId}/schedule`);

  const header = el("div", { className: "page-header" }, [
    el("div", {}, [
      el("h1", { className: "page-title" }, `${data.clinician.display_name} — ${t("clinician_title")}`),
      el("p", { className: "muted-text" }, `${data.clinician.specialty} · ${data.clinician.location_name} (${data.clinician.location_suite})`),
    ]),
    el("div", { className: "action-group" }, [
      el("span", { className: `status-pill ${data.active_delay_minutes > 0 ? "status-danger" : "status-ok"}` },
        data.active_delay_minutes > 0 ? `▲ Active Delay: +${data.active_delay_minutes} min` : "✓ Running On Time"),
      el("button", {
        type: "button",
        className: "btn btn-danger",
        onClick: async () => {
          await apiFetch("/api/v1/clinician/delays", {
            method: "POST",
            body: JSON.stringify({ provider_id: providerId, delay_minutes: 20, actor_role: "Clinician" }),
          });
          showBanner(`Broadcasted +20 min delay alert for ${data.clinician.display_name} (BR-13).`);
          renderActiveView();
        },
      }, "+20m Delay"),
      el("button", {
        type: "button",
        className: "btn",
        onClick: async () => {
          await apiFetch("/api/v1/clinician/delays", {
            method: "POST",
            body: JSON.stringify({ provider_id: providerId, delay_minutes: 0, actor_role: "Clinician" }),
          });
          showBanner(`Cleared delay for ${data.clinician.display_name}.`);
          renderActiveView();
        },
      }, "Clear Delay"),
    ]),
  ]);

  const timelineCard = el("section", { className: "panel-card", style: "padding:0;overflow:hidden;" },
    data.timeline.map((item) => {
      const hasAppt = Boolean(item.appt_id);
      return el("div", { className: "roster-row" }, [
        el("div", { className: "row-identity" }, [
          el("div", { className: "monogram-badge" }, hasAppt ? item.monogram : "—"),
          el("div", {}, [
            el("strong", {}, formatDualTime(item.start_time)),
            el("div", { className: "kpi-sub" }, hasAppt
              ? `${item.appt_id} · ${item.display_alias} · ${state.lang === "es" ? item.visit_label_es : item.visit_label}`
              : (state.lang === "es" ? "Horario abierto (30 min)" : "Open 30-minute slot")),
          ]),
        ]),
        hasAppt
          ? el("div", { className: "action-group" }, [
              el("span", { className: "status-pill status-info" }, `● ${item.room_status}`),
              el("button", {
                type: "button",
                className: "btn btn-primary",
                onClick: async () => {
                  await apiFetch(`/api/v1/staff/appointments/${item.appt_id}/checkin`, {
                    method: "POST",
                    body: JSON.stringify({ room_status: "Completed", actor_role: "Clinician" }),
                  });
                  showBanner(`Visit ${item.appt_id} marked Completed.`);
                  renderActiveView();
                },
              }, state.lang === "es" ? "Completar Visita" : "Complete Visit"),
            ])
          : el("span", { className: "tag-pill" }, "Available"),
      ]);
    })
  );

  clearAndAppend(container, [header, timelineCard]);
}

// --- VIEW 4: CLINIC MANAGER 6-MONTH ANALYTICS & AUDIT TRAIL ---
async function renderManagerView(container) {
  const data = await apiFetch(`/api/v1/manager/analytics?window=${state.managerWindow}`);
  const s = data.summary;

  const header = el("div", { className: "page-header" }, [
    el("div", {}, [
      el("h1", { className: "page-title" }, t("manager_title")),
      el("p", { className: "muted-text" }, t("manager_sub")),
    ]),
    el("div", { className: "segmented-group", role: "group", "aria-label": "Analytics Time Window" },
      [
        { key: "7d", label: "7 Days" },
        { key: "30d", label: "30 Days" },
        { key: "180d", label: "6 Months (Full History)" },
        { key: "future14d", label: "Next 14 Days" },
      ].map((w) =>
        el("button", {
          type: "button",
          className: `seg-btn ${state.managerWindow === w.key ? "active" : ""}`,
          onClick: () => { state.managerWindow = w.key; renderActiveView(); },
        }, w.label)
      )
    ),
  ]);

  const kpis = el("div", { className: "kpi-grid" }, [
    el("div", { className: "kpi-card" }, [
      el("div", { className: "kpi-label" }, "Clinic Utilization Rate"),
      el("div", { className: "kpi-value" }, `${s.utilization_pct}%`),
      el("div", { className: "kpi-sub" }, `${s.total_booked} booked · ${s.completed_count} completed`),
    ]),
    el("div", { className: "kpi-card" }, [
      el("div", { className: "kpi-label" }, "Reschedule Rate"),
      el("div", { className: "kpi-value" }, `${s.reschedule_rate_pct}%`),
      el("div", { className: "kpi-sub" }, `${s.rescheduled_count} total moves logged`),
    ]),
    el("div", { className: "kpi-card" }, [
      el("div", { className: "kpi-label" }, "Late Cancellations (<24h · BR-11)"),
      el("div", { className: "kpi-value" }, `${s.late_cancel_rate_pct}%`),
      el("div", { className: "kpi-sub" }, `${s.late_cancel_count} late · ${s.noshow_count} no-shows (${s.noshow_rate_pct}%)`),
    ]),
    el("div", { className: "kpi-card" }, [
      el("div", { className: "kpi-label" }, "Consent Compliance (BR-18/19)"),
      el("div", { className: "kpi-value" }, `${s.c1_compliance_pct}%`),
      el("div", { className: "kpi-sub" }, `C1 Privacy: ${s.c1_compliance_pct}% · C2 Reminders: ${s.c2_optin_pct}%`),
    ]),
  ]);

  // Provider Utilization Bars (BR-13)
  const providerPanel = el("section", { className: "panel-card", style: "margin-bottom:18px;" }, [
    el("h2", { style: "margin:0 0 12px;font-size:1.1rem;" }, "Provider Utilization & Capacity Flags (BR-13: <60% Low / >90% Overbooked)"),
    ...data.providers.map((p) => {
      const badgeClass = p.flag === "overbooked" ? "status-danger" : p.flag === "underutilized" ? "status-warn" : "status-ok";
      const fillClass = p.flag === "overbooked" ? "over" : p.flag === "underutilized" ? "low" : "";
      return el("div", { style: "margin-bottom:14px;" }, [
        el("div", { className: "page-header", style: "margin-bottom:2px;" }, [
          el("strong", {}, `${p.display_name} (${p.specialty} · ${p.location_name})`),
          el("span", { className: `status-pill ${badgeClass}` }, `${p.utilization_pct}% · ${p.flag.toUpperCase()}`),
        ]),
        el("div", { className: "bar-track" }, [
          el("div", { className: `bar-fill ${fillClass}`, style: `width:${Math.min(100, p.utilization_pct)}%;` }),
        ]),
      ]);
    }),
  ]);

  // 6-Month Historic Trend Table
  const trendPanel = el("section", { className: "panel-card", style: "margin-bottom:18px;" }, [
    el("h2", { style: "margin:0 0 10px;font-size:1.1rem;" }, "6-Month Historic Flow Summary (Completed vs. Rescheduled vs. Cancelled/No-Show)"),
    el("div", { className: "table-wrap" }, [
      el("table", { className: "audit-table" }, [
        el("thead", {}, el("tr", {}, [
          el("th", {}, "Month"),
          el("th", {}, "Completed"),
          el("th", {}, "Rescheduled"),
          el("th", {}, "Cancelled / No-Show"),
        ])),
        el("tbody", {}, data.monthly_trend.map((m) =>
          el("tr", {}, [
            el("td", {}, m.month_key),
            el("td", {}, String(m.completed)),
            el("td", {}, String(m.rescheduled)),
            el("td", {}, String(m.missed_or_cancelled)),
          ])
        )),
      ]),
    ]),
  ]);

  // Immutable Audit Trail (BR-16 — Zero PHI/PII)
  const auditPanel = el("section", { className: "panel-card" }, [
    el("h2", { style: "margin:0 0 10px;font-size:1.1rem;" }, "Immutable Operational Audit Trail (BR-16 · Zero PHI/PII)"),
    el("div", { className: "table-wrap" }, [
      el("table", { className: "audit-table" }, [
        el("thead", {}, el("tr", {}, [
          el("th", {}, "Timestamp (EST / Local)"),
          el("th", {}, "Actor"),
          el("th", {}, "Action"),
          el("th", {}, "Appt ID"),
          el("th", {}, "Details"),
        ])),
        el("tbody", {}, data.audit_logs.map((a) =>
          el("tr", {}, [
            el("td", {}, formatDualTime(a.created_at)),
            el("td", {}, `${a.actor_role} (${a.actor_alias})`),
            el("td", {}, el("span", { className: "tag-pill" }, a.action)),
            el("td", {}, a.appt_id || "—"),
            el("td", {}, a.detail),
          ])
        )),
      ]),
    ]),
  ]);

  clearAndAppend(container, [header, kpis, providerPanel, trendPanel, auditPanel]);
}

async function renderActiveView() {
  const container = document.getElementById("view-container");
  try {
    if (state.activeRole === "Patient") await renderPatientView(container);
    else if (state.activeRole === "FrontDesk") await renderStaffView(container);
    else if (state.activeRole === "Clinician") await renderClinicianView(container);
    else if (state.activeRole === "Manager") await renderManagerView(container);
  } catch (err) {
    clearAndAppend(container, el("div", { className: "panel-card" }, [
      el("strong", {}, "Unable to load live data: "),
      el("span", {}, err.message),
    ]));
  }
}

function initEvents() {
  document.querySelectorAll(".role-tab").forEach((tab) => {
    tab.addEventListener("click", async () => {
      document.querySelectorAll(".role-tab").forEach((b) => {
        b.classList.remove("active");
        b.setAttribute("aria-selected", "false");
      });
      tab.classList.add("active");
      tab.setAttribute("aria-selected", "true");
      state.activeRole = tab.getAttribute("data-role");
      syncPersonaDropdown();

      // Record C3 confidentiality acknowledgment for non-patient roles (BR-24)
      if (state.activeRole !== "Patient") {
        apiFetch("/api/v1/consent/c3", {
          method: "POST",
          body: JSON.stringify({
            role: state.activeRole,
            persona_key: state.selectedPersonaByRole[state.activeRole],
          }),
        }).catch(() => {});
      }
      renderActiveView();
    });
  });

  document.getElementById("persona-select").addEventListener("change", (e) => {
    state.selectedPersonaByRole[state.activeRole] = e.target.value;
    renderActiveView();
  });

  document.getElementById("tz-select").addEventListener("change", (e) => {
    state.tzMode = e.target.value;
    renderActiveView();
  });

  document.getElementById("lang-toggle").addEventListener("click", () => {
    state.lang = state.lang === "en" ? "es" : "en";
    applyTranslations();
    syncPersonaDropdown();
    renderActiveView();
  });

  document.getElementById("theme-toggle").addEventListener("click", (e) => {
    state.theme = state.theme === "light" ? "dark" : "light";
    document.documentElement.setAttribute("data-theme", state.theme);
    e.currentTarget.setAttribute("aria-pressed", state.theme === "dark" ? "true" : "false");
    applyTranslations();
  });

  document.getElementById("privacy-open-btn").addEventListener("click", openPrivacyModal);
  document.getElementById("drawer-close").addEventListener("click", closeDrawer);
  document.getElementById("drawer-backdrop").addEventListener("click", closeDrawer);
  document.getElementById("modal-close").addEventListener("click", closeModal);
  document.getElementById("modal-backdrop").addEventListener("click", closeModal);

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      closeDrawer();
      closeModal();
    }
  });
}

(async function boot() {
  initEvents();
  applyTranslations();
  updateFooterClock();
  setInterval(updateFooterClock, 1000);
  await loadPersonas();
  await renderActiveView();
})();
