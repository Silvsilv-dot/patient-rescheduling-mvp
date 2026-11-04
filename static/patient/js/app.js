/* The patient page. Every piece of data from the server is shown with textContent, never
 * as HTML, so a hostile value can only ever appear as plain text.
 *
 * Flow: privacy notice -> sign in -> appointment (confirm / cancel / reminders)
 *       -> reschedule (wait comparison, choose a time, review, confirm).
 */
(function () {
  "use strict";

  var SCREENS = ["notice", "declined", "signin", "appointment", "reschedule"];
  var REFRESH_MS = Number(document.body.getAttribute("data-refresh-ms")) || 10000;

  var FULL = {
    weekday: "short", month: "short", day: "numeric", year: "numeric",
    hour: "numeric", minute: "2-digit", timeZoneName: "short",
  };
  var SHORT = { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" };
  var TIME = { hour: "numeric", minute: "2-digit" };
  var DAY = { weekday: "long", month: "long", day: "numeric" };

  var BADGE = {
    Scheduled: "info", Rescheduled: "info", Confirmed: "ok",
    Completed: "ok", Cancelled: "bad", "No-show": "bad",
  };
  var REASONS = {
    booked: "Booked",
    blocked: "Not available",
    past: "Time has passed",
    current: "Your current time",
    conflict: "Overlaps another appointment of yours",
    unavailable: "Not available",
  };

  var state = freshState();

  function freshState() {
    return {
      noticeVersion: null, accepted: false, reconsent: false,
      apptId: null, appt: null, tz: "America/New_York",
      slots: null, waits: null, selected: null, pendingKey: null,
      timer: null, refreshing: false, slotsSignature: "", waitsSignature: "", dayOpen: {},
    };
  }

  // ---------------------------------------------------------------- small helpers
  function $(id) {
    return document.getElementById(id);
  }

  function make(tag, className, text) {
    var node = document.createElement(tag);
    if (className) {
      node.className = className;
    }
    if (text !== undefined) {
      node.textContent = text;
    }
    return node;
  }

  function clear(node) {
    while (node.firstChild) {
      node.removeChild(node.firstChild);
    }
  }

  function announce(text) {
    $("status-live").textContent = text;
  }

  function showMessage(id, text) {
    var node = $(id);
    node.textContent = text || "";
    node.hidden = !text;
  }

  function show(name, moveFocus) {
    SCREENS.forEach(function (screen) {
      $("screen-" + screen).hidden = screen !== name;
    });
    if (moveFocus !== false) {
      var heading = $("h-" + name);
      if (heading) {
        heading.focus();
      }
      window.scrollTo(0, 0);
    }
  }

  function format(iso, options) {
    try {
      return new Intl.DateTimeFormat("en-US", Object.assign({ timeZone: state.tz }, options)).format(
        new Date(iso)
      );
    } catch (error) {
      return String(iso);
    }
  }

  function dayKey(iso) {
    try {
      return new Intl.DateTimeFormat("en-CA", {
        timeZone: state.tz, year: "numeric", month: "2-digit", day: "2-digit",
      }).format(new Date(iso));
    } catch (error) {
      return String(iso).slice(0, 10);
    }
  }

  function waitText(label) {
    return label === "Now" ? "available now" : label + " away";
  }

  function badge(status, text) {
    var node = make("span", "badge badge-" + (BADGE[status] || "info"), text || status);
    node.setAttribute("data-status", status);
    return node;
  }

  // ---------------------------------------------------------------- errors and sessions
  function handleError(error, targetId) {
    if (error && error.status === 401) {
      sessionEnded();
      return;
    }
    if (error && error.code === "consent_required") {
      needConsent();
      return;
    }
    showMessage(targetId, (error && error.message) || "Something went wrong. Please try again.");
  }

  function resetSession() {
    stopPolling();
    Api.clearToken();
    var keep = { noticeVersion: state.noticeVersion, accepted: state.accepted };
    state = freshState();
    state.noticeVersion = keep.noticeVersion;
    state.accepted = keep.accepted;
  }

  function sessionEnded() {
    resetSession();
    showMessage("signin-error", "Your session has ended. Please sign in again.");
    show("signin");
  }

  async function needConsent() {
    stopPolling();
    state.reconsent = true;
    await loadNotice();
    show("notice");
  }

  // ---------------------------------------------------------------- 1. privacy notice
  async function loadNotice() {
    showMessage("notice-error", "");
    try {
      var notice = await Api.notice();
      state.noticeVersion = notice.version;
      $("notice-text").textContent = notice.text;
      $("notice-version").textContent = notice.version;
      $("notice-accept").disabled = false;
    } catch (error) {
      $("notice-accept").disabled = true;
      showMessage("notice-error", "We could not load the privacy notice. Please refresh the page and try again.");
    }
  }

  async function onAccept() {
    if (state.reconsent && Api.hasToken()) {
      try {
        await Api.recordConsent("C1", "Granted", state.noticeVersion);
        state.reconsent = false;
        await loadAppointment();
      } catch (error) {
        handleError(error, "notice-error");
      }
      return;
    }
    state.accepted = true;
    show("signin");
  }

  function onDecline() {
    state.accepted = false;
    show("declined");
  }

  // ---------------------------------------------------------------- 2. sign in
  async function onSignIn(event) {
    event.preventDefault();
    showMessage("signin-error", "");
    var apptId = $("appt-id").value.trim();
    var code = $("access-code").value;
    if (!apptId || !code) {
      showMessage("signin-error", "Please enter both your appointment ID and your access code.");
      (apptId ? $("access-code") : $("appt-id")).focus();
      return;
    }
    var button = $("signin-submit");
    button.disabled = true;
    try {
      var result = await Api.signIn(apptId, code);
      state.apptId = result.appt_id;
      $("access-code").value = "";
      await Api.recordConsent("C1", "Granted", state.noticeVersion);
      await loadAppointment();
    } catch (error) {
      $("access-code").value = "";
      if (error.code === "notice_outdated") {
        await needConsent();
      } else {
        // A failed sign-in is not an expired session: show the server's message as it is
        // (the same generic text for a wrong code and an unknown ID, or the lockout notice).
        showMessage("signin-error", error.message || "Something went wrong. Please try again.");
        $("access-code").focus();
      }
    } finally {
      button.disabled = false;
    }
  }

  // ---------------------------------------------------------------- 3. appointment
  function setAction(button, info, label, extraNote) {
    var allowed = !!(info && info.allowed) && !extraNote;
    button.disabled = !allowed;
    if (extraNote) {
      return label + ": " + extraNote;
    }
    return allowed || !info ? null : label + ": " + info.message;
  }

  function renderAppointment(appt) {
    state.appt = appt;
    state.tz = appt.time_zone || state.tz;
    $("appt-ident").textContent = appt.appt_id;
    $("appt-when").textContent = format(appt.start_time, FULL);
    $("appt-location").textContent = appt.location;
    $("appt-label").textContent = appt.label;
    var status = $("appt-status");
    clear(status);
    status.appendChild(badge(appt.status));

    var actions = appt.allowed_actions || {};
    var notes = [
      setAction($("action-confirm"), actions.confirm, "Confirm",
        appt.status === "Confirmed" ? "This appointment is already confirmed." : null),
      setAction($("action-reschedule"), actions.reschedule, "Reschedule"),
      setAction($("action-cancel"), actions.cancel, "Cancel"),
    ].filter(Boolean);
    var list = $("action-notes");
    clear(list);
    notes.forEach(function (note) {
      list.appendChild(make("li", "", note));
    });
    list.hidden = notes.length === 0;
    $("cancel-panel").hidden = true;
  }

  async function loadAppointment(message) {
    showMessage("action-error", "");
    try {
      var appt = await Api.appointment(state.apptId);
      renderAppointment(appt);
      showMessage("result-message", message || "");
      show("appointment");
      await loadReminders();
    } catch (error) {
      handleError(error, "signin-error");
    }
  }

  async function onConfirm() {
    showMessage("action-error", "");
    try {
      renderAppointment(await Api.confirm(state.apptId));
      showMessage("result-message", "Your appointment is confirmed.");
    } catch (error) {
      handleError(error, "action-error");
    }
  }

  function onCancelOpen() {
    $("cancel-panel").hidden = false;
    $("cancel-keep").focus();
  }

  function onCancelKeep() {
    $("cancel-panel").hidden = true;
    $("action-cancel").focus();
  }

  async function onCancelYes() {
    showMessage("action-error", "");
    try {
      renderAppointment(await Api.cancel(state.apptId));
      showMessage("result-message", "Your appointment was cancelled. The time is now free for someone else.");
      await loadReminders();
      $("h-appointment").focus();
    } catch (error) {
      $("cancel-panel").hidden = true;
      handleError(error, "action-error");
    }
  }

  async function loadReminders() {
    var toggle = $("reminders-toggle");
    var list = $("reminders-list");
    var note = $("reminders-note");
    try {
      var consents = await Api.consents();
      var on = !!(consents.c2 && consents.c2.satisfied);
      toggle.checked = on;
      clear(list);
      list.hidden = true;
      if (!on) {
        note.textContent =
          "Reminders are off. Turn them on to see when simulated reminders would be sent. Nothing is ever really sent.";
        return;
      }
      var result = await Api.reminders(state.apptId);
      if (!result.reminders.length) {
        note.textContent = "There are no reminders for this appointment.";
        return;
      }
      note.textContent = "These are simulated. They show when a reminder would be sent. Nothing is really sent.";
      result.reminders.forEach(function (item) {
        list.appendChild(make("li", "", format(item.due_at, FULL) + ": " + item.message));
      });
      list.hidden = false;
    } catch (error) {
      handleError(error, "action-error");
    }
  }

  async function onRemindersChange() {
    var toggle = $("reminders-toggle");
    var wanted = toggle.checked;
    showMessage("action-error", "");
    try {
      await Api.recordConsent("C2", wanted ? "Granted" : "Withdrawn", state.noticeVersion);
      await loadReminders();
      announce(wanted ? "Simulated reminders are on." : "Simulated reminders are off.");
    } catch (error) {
      toggle.checked = !wanted;
      handleError(error, "action-error");
    }
  }

  function onSignOut() {
    resetSession();
    state.accepted = false;
    $("appt-id").value = "";
    $("access-code").value = "";
    showMessage("signin-error", "");
    showMessage("result-message", "");
    loadNotice();
    show("notice");
    announce("You have signed out.");
  }

  // ---------------------------------------------------------------- 4. reschedule
  async function openReschedule() {
    state.selected = null;
    state.pendingKey = null;
    state.slotsSignature = "";
    state.waitsSignature = "";
    state.dayOpen = {};
    showMessage("reschedule-error", "");
    $("review-panel").hidden = true;
    $("review-open").disabled = true;
    $("reschedule-intro").textContent =
      "Your " + state.appt.label + " is now at " + format(state.appt.start_time, FULL) +
      ". Choose a new time below.";
    clear($("slots-container"));
    clear($("waits-list"));
    $("slots-summary").textContent = "Loading available times...";
    show("reschedule");
    try {
      await refreshSlots();
      startPolling();
    } catch (error) {
      handleError(error, "reschedule-error");
    }
  }

  async function refreshSlots() {
    if (state.refreshing) {
      return;
    }
    state.refreshing = true;
    try {
      var results = await Promise.all([Api.slots(state.apptId), Api.waitTimes()]);
      state.slots = results[0];
      state.waits = results[1];
      renderWaits();
      renderSlots();
    } finally {
      state.refreshing = false;
    }
  }

  function renderWaits() {
    var signature = JSON.stringify(state.waits);
    if (signature === state.waitsSignature) {
      return;
    }
    state.waitsSignature = signature;
    var list = $("waits-list");
    clear(list);
    $("waits-note").textContent = state.waits.estimate_note || "";
    var type = (state.waits.types || []).filter(function (item) {
      return item.label === state.appt.label;
    })[0];
    if (!type || !type.providers.length) {
      list.appendChild(make("li", "wait-item", "No estimate is available right now."));
      return;
    }
    type.providers.forEach(function (provider) {
      var item = make("li", "wait-item");
      var left = make("div");
      left.appendChild(make("strong", "", provider.provider));
      left.appendChild(make("div", "muted small", provider.location));
      item.appendChild(left);
      item.appendChild(
        make("div", "wait-when",
          "Next open " + format(provider.earliest_start, SHORT) + " (" + waitText(provider.wait_label) + ")")
      );
      if (provider.is_soonest) {
        item.appendChild(badge("Confirmed", "Soonest"));
      }
      list.appendChild(item);
    });
  }

  function findSlot(slotId) {
    return ((state.slots && state.slots.slots) || []).filter(function (slot) {
      return slot.slot_id === slotId;
    })[0];
  }

  function renderSlots() {
    var data = state.slots;
    var signature = JSON.stringify(data);
    if (signature === state.slotsSignature) {
      return;
    }
    state.slotsSignature = signature;

    if (state.selected) {
      var chosen = findSlot(state.selected);
      if (!chosen || !chosen.selectable) {
        state.selected = null;
        state.pendingKey = null;
        $("review-panel").hidden = true;
        showMessage("reschedule-error", "The time you picked is no longer available. Please choose another.");
      }
    }

    var focused = document.activeElement && document.activeElement.name === "slot"
      ? document.activeElement.value : null;
    var container = $("slots-container");
    clear(container);

    var count = data.available_count;
    var summary = count + (count === 1 ? " time is" : " times are") + " available, in time order. " +
      "Times are shown in " + (format(data.slots[0] ? data.slots[0].start_time : new Date(), { timeZoneName: "long" }).split(", ").pop()) + ".";
    if (data.needs_fallback && data.message) {
      summary += " " + data.message;
    }
    $("slots-summary").textContent = summary;

    var groups = [];
    var index = {};
    data.slots.forEach(function (slot) {
      var key = dayKey(slot.start_time);
      if (!index[key]) {
        index[key] = { key: key, label: format(slot.start_time, DAY), slots: [] };
        groups.push(index[key]);
      }
      index[key].slots.push(slot);
    });

    // One folded section per day. Days are opened for the person until at least three
    // available times are showing, and after that they stay as the person left them.
    var visibleOpen = 0;
    groups.forEach(function (group) {
      var grid = make("div", "slot-grid");
      grid.setAttribute("role", "group");
      grid.setAttribute("aria-label", group.label);
      var openCount = 0;
      group.slots.forEach(function (slot) {
        openCount += slot.selectable ? 1 : 0;
        var chosenNow = state.selected === slot.slot_id;
        var label = make(
          "label",
          "slot" + (slot.selectable ? "" : " is-unavailable") + (chosenNow ? " is-selected" : "")
        );
        var input = make("input");
        input.type = "radio";
        input.name = "slot";
        input.value = slot.slot_id;
        input.disabled = !slot.selectable;
        input.checked = chosenNow;
        input.setAttribute("data-start", slot.start_time);
        var text = make("span", "slot-text");
        text.appendChild(make("span", "slot-time", format(slot.start_time, TIME)));
        var meta = slot.provider + ", " + slot.location;
        if (!slot.selectable) {
          meta += " - " + (REASONS[slot.reason] || "Not available");
        }
        text.appendChild(make("span", "slot-meta", meta));
        label.appendChild(input);
        label.appendChild(text);
        grid.appendChild(label);
      });

      var day = make("details", "slot-day" + (openCount ? "" : " all-unavailable"));
      day.setAttribute("data-day", group.key);
      var wanted = state.dayOpen[group.key];
      if (wanted === undefined) {
        wanted = openCount > 0 && visibleOpen < 3;
      }
      if (wanted) {
        day.open = true;
        visibleOpen += openCount;
      }
      var summary = make("summary", "");
      summary.appendChild(make("span", "day-name", group.label));
      summary.appendChild(
        make("span", "day-count", openCount ? openCount + (openCount === 1 ? " time" : " times") : "No times available")
      );
      day.appendChild(summary);
      day.appendChild(grid);
      container.appendChild(day);
    });

    $("review-open").disabled = !state.selected;
    if (focused) {
      var again = container.querySelector('input[name="slot"][value="' + focused + '"]');
      if (again && !again.disabled) {
        again.focus();
      }
    }
  }

  function onSlotChange(event) {
    var input = event.target;
    if (!input || input.name !== "slot" || !input.checked) {
      return;
    }
    state.selected = input.value;
    state.pendingKey = null;
    $("review-panel").hidden = true;
    showMessage("reschedule-error", "");
    Array.prototype.forEach.call($("slots-container").querySelectorAll(".slot"), function (label) {
      var radio = label.querySelector("input");
      label.classList.toggle("is-selected", radio.checked);
    });
    $("review-open").disabled = false;
  }

  function onReviewOpen() {
    var slot = findSlot(state.selected);
    if (!slot) {
      return;
    }
    state.pendingKey = state.pendingKey || Api.newKey();
    $("review-text").textContent =
      "Move your appointment from " + format(state.appt.start_time, FULL) + " to " +
      format(slot.start_time, FULL) + " with " + slot.provider + " at " + slot.location + ".";
    $("review-panel").hidden = false;
    $("review-confirm").focus();
  }

  function onReviewChange() {
    $("review-panel").hidden = true;
    var chosen = $("slots-container").querySelector('input[name="slot"]:checked');
    (chosen || $("review-open")).focus();
  }

  async function onReviewConfirm() {
    var slot = findSlot(state.selected);
    if (!slot) {
      return;
    }
    var button = $("review-confirm");
    button.disabled = true;
    showMessage("reschedule-error", "");
    try {
      var result = await Api.reschedule(state.apptId, slot.slot_id, state.pendingKey);
      stopPolling();
      state.selected = null;
      state.pendingKey = null;
      await loadAppointment(
        "Your appointment was rescheduled from " + format(result.old_start_time, FULL) +
        " to " + format(result.new_start_time, FULL) + "."
      );
    } catch (error) {
      $("review-panel").hidden = true;
      if (error.status === 409) {
        state.selected = null;
        state.pendingKey = null;
        state.slotsSignature = "";
        showMessage("reschedule-error", error.message);
        try {
          await refreshSlots();
        } catch (ignored) {
          /* the next automatic refresh will try again */
        }
      } else {
        handleError(error, "reschedule-error");
      }
    } finally {
      button.disabled = false;
    }
  }

  function onRescheduleBack() {
    stopPolling();
    show("appointment");
  }

  function startPolling() {
    stopPolling();
    state.timer = setInterval(function () {
      if (document.hidden) {
        return;
      }
      refreshSlots().catch(function (error) {
        if (error && error.status === 401) {
          sessionEnded();
        }
      });
    }, REFRESH_MS);
  }

  function stopPolling() {
    if (state.timer) {
      clearInterval(state.timer);
      state.timer = null;
    }
  }

  // ---------------------------------------------------------------- theme button
  function syncThemeButton() {
    var dark = window.PatientTheme.current() === "dark";
    $("theme-toggle").setAttribute("aria-pressed", dark ? "true" : "false");
    $("theme-state").textContent = dark ? "On" : "Off";
  }

  // ---------------------------------------------------------------- start
  function init() {
    $("notice-accept").addEventListener("click", onAccept);
    $("notice-decline").addEventListener("click", onDecline);
    $("declined-back").addEventListener("click", function () {
      show("notice");
    });
    $("signin-form").addEventListener("submit", onSignIn);
    $("signin-back").addEventListener("click", function () {
      show("notice");
    });
    $("show-code").addEventListener("change", function (event) {
      $("access-code").type = event.target.checked ? "text" : "password";
    });
    $("action-confirm").addEventListener("click", onConfirm);
    $("action-cancel").addEventListener("click", onCancelOpen);
    $("cancel-keep").addEventListener("click", onCancelKeep);
    $("cancel-yes").addEventListener("click", onCancelYes);
    $("action-reschedule").addEventListener("click", openReschedule);
    $("reminders-toggle").addEventListener("change", onRemindersChange);
    $("signout").addEventListener("click", onSignOut);
    $("slots-container").addEventListener("change", onSlotChange);
    $("slots-container").addEventListener(
      "toggle",
      function (event) {
        var node = event.target;
        if (node && node.tagName === "DETAILS") {
          state.dayOpen[node.getAttribute("data-day")] = node.open;
        }
      },
      true
    );
    $("hide-unavailable").addEventListener("change", function (event) {
      $("slots-container").classList.toggle("hide-unavailable", event.target.checked);
    });
    $("review-open").addEventListener("click", onReviewOpen);
    $("review-change").addEventListener("click", onReviewChange);
    $("review-confirm").addEventListener("click", onReviewConfirm);
    $("reschedule-back").addEventListener("click", onRescheduleBack);
    $("theme-toggle").addEventListener("click", function () {
      window.PatientTheme.toggle();
      syncThemeButton();
    });
    window.addEventListener("themechange", syncThemeButton);

    syncThemeButton();
    show("notice", false);
    loadNotice();
  }

  init();
})();
