// The patient page, driven the way a person would use it (US-02, US-03, US-04).
import test from "node:test";
import assert from "node:assert/strict";
import {
  BANNER, CREDS, GENERIC_SIGN_IN_ERROR, api, apiAppointment, apiSession, activeId, assertClean,
  isShown, pickFirstSlot, pickSlotAmong, reachAppointment, reachReschedule, reachSignIn, screenText,
  selectableIds, signIn, take, takePair, text, withPage,
} from "./harness.mjs";

const T = { timeout: 120000 };

test("there is enough seeded data for the tests", () => {
  assert.ok(CREDS.far.length >= 12, "need far-ahead appointments");
  assert.ok(CREDS.soon.length >= 1, "need an appointment starting within a day");
});

test("it opens on the privacy notice and asks for no appointment data before acceptance", T, async () => {
  await withPage({}, async ({ page, problems, requests }) => {
    assert.equal(await text(page, ".demo-banner"), BANNER);
    assert.ok((await text(page, ".notice-urgent")).includes("urgent medical help"));
    assert.ok(await isShown(page, "#screen-notice"));
    assert.ok(!(await isShown(page, "#screen-signin")));
    assert.ok((await text(page, "#notice-text")).includes("demonstration"));
    assert.equal(await text(page, "#notice-version"), "1.0");
    assert.equal(await page.$eval("#notice-accept", (el) => el.disabled), false);
    const apiCalls = requests.filter((r) => r.includes("/api/v1/"));
    assert.deepEqual(apiCalls, ["GET /api/v1/privacy-notice"]);
    assertClean(problems);
  });
});

test("declining shows a message and requests nothing more", T, async () => {
  await withPage({}, async ({ page, problems, requests }) => {
    const before = requests.length;
    await page.click("#notice-decline");
    assert.ok(await isShown(page, "#screen-declined"));
    assert.ok((await text(page, "#screen-declined")).includes("No appointment information was requested"));
    assert.ok(!(await isShown(page, "#screen-signin")));
    assert.equal(requests.length, before, "declining must not send any request");
    await page.click("#declined-back");
    assert.ok(await isShown(page, "#screen-notice"));
    assertClean(problems);
  });
});

test("wrong details give one generic message that never says which part was wrong", T, async () => {
  const cred = take();
  await withPage({}, async ({ page, problems }) => {
    await reachSignIn(page);
    await signIn(page, cred, "000000x");
    await page.waitForSelector("#signin-error:not([hidden])");
    assert.equal(await text(page, "#signin-error"), GENERIC_SIGN_IN_ERROR);
    assert.equal(await page.$eval("#access-code", (el) => el.value), "", "the code field is cleared");
    assert.equal(await activeId(page), "access-code", "focus returns to the code field");
    await signIn(page, { appt_id: "APT-99999999" }, "123456");
    await page.waitForFunction(() => document.querySelector("#signin-submit").disabled === false);
    assert.equal(await text(page, "#signin-error"), GENERIC_SIGN_IN_ERROR);
    await page.$eval("#appt-id", (el) => (el.value = ""));
    await page.click("#signin-submit");
    assert.ok((await text(page, "#signin-error")).includes("enter both"));
    assert.ok(!(await isShown(page, "#screen-appointment")));
    assertClean(problems.filter((p) => !p.includes("401") && !p.includes("Unauthorized")));
  });
});

test("five wrong codes lock sign-in, and the page says to wait", T, async () => {
  const cred = take();
  await withPage({}, async ({ page }) => {
    await reachSignIn(page);
    for (let i = 0; i < 5; i += 1) {
      await signIn(page, cred, "000000x");
      await page.waitForFunction(() => document.querySelector("#signin-submit").disabled === false);
      assert.equal(await text(page, "#signin-error"), GENERIC_SIGN_IN_ERROR);
    }
    await signIn(page, cred, cred.code); // even the right code is refused while locked
    await page.waitForFunction(() => /Too many attempts/.test(document.querySelector("#signin-error").textContent));
    assert.ok(!(await isShown(page, "#screen-appointment")));
  });
});

test("a patient sees only the permitted information, and nothing sensitive is stored", T, async () => {
  const cred = take();
  await withPage({}, async ({ page, problems }) => {
    await reachAppointment(page, cred);
    const shown = await screenText(page, "#screen-appointment");
    assert.ok(shown.includes(cred.appt_id));
    assert.ok(/Demo Clinic/.test(shown));
    for (const forbidden of [cred.patient_id, "Demo Patient", "Dr. Demo", "diagnosis", "insurance", "phone", "email"]) {
      assert.ok(!shown.toLowerCase().includes(forbidden.toLowerCase()), `must not show "${forbidden}"`);
    }
    const labels = await page.$$eval("#screen-appointment dl dt", (els) => els.map((e) => e.textContent.trim()));
    assert.deepEqual(labels, ["Appointment ID", "Date and time", "Location", "Type of visit", "Status"]);
    assert.ok(/EST|EDT/.test(await text(page, "#appt-when")), "the time zone is shown");
    assert.equal(await activeId(page), "h-appointment", "focus moves to the new screen's heading");
    assert.ok((await text(page, ".notice-urgent")).includes("urgent medical help"));

    const stored = await page.evaluate(() => ({
      local: Object.keys(window.localStorage), session: Object.keys(window.sessionStorage), cookie: document.cookie,
    }));
    assert.deepEqual(stored.session, []);
    assert.equal(stored.cookie, "");
    assert.ok(stored.local.every((k) => k === "theme"), "only the theme choice is stored: " + stored.local);
    assertClean(problems);
  });
});

test("confirming updates the status", T, async () => {
  const cred = take((c) => c.status === "Scheduled");
  await withPage({}, async ({ page, problems }) => {
    await reachAppointment(page, cred);
    assert.equal(await text(page, "#appt-status"), "Scheduled");
    await page.click("#action-confirm");
    await page.waitForFunction(() => document.querySelector("#appt-status").textContent.includes("Confirmed"));
    assert.ok((await text(page, "#result-message")).includes("confirmed"));
    assert.equal(await page.$eval("#action-confirm", (el) => el.disabled), true);
    assert.ok((await text(page, "#action-notes")).includes("already confirmed"));
    assert.equal((await apiAppointment(cred)).appt.status, "Confirmed");
    assertClean(problems);
  });
});

test("cancelling asks first, can be backed out of, and then works", T, async () => {
  const cred = take((c) => c.status === "Confirmed");
  await withPage({}, async ({ page, problems }) => {
    await reachAppointment(page, cred);
    await page.click("#action-cancel");
    assert.ok(await isShown(page, "#cancel-panel"));
    assert.equal(await activeId(page), "cancel-keep", "focus starts on the safe choice");
    await page.click("#cancel-keep");
    assert.ok(!(await isShown(page, "#cancel-panel")));
    assert.equal(await activeId(page), "action-cancel");
    assert.equal((await apiAppointment(cred)).appt.status, "Confirmed", "nothing changed yet");

    await page.click("#action-cancel");
    await page.click("#cancel-yes");
    await page.waitForFunction(() => document.querySelector("#appt-status").textContent.includes("Cancelled"));
    assert.ok((await text(page, "#result-message")).includes("cancelled"));
    for (const id of ["#action-confirm", "#action-reschedule", "#action-cancel"]) {
      assert.equal(await page.$eval(id, (el) => el.disabled), true, id + " is disabled");
    }
    assert.ok((await text(page, "#action-notes")).includes("can no longer be changed online"));
    assert.equal((await apiAppointment(cred)).appt.status, "Cancelled");
    assertClean(problems);
  });
});

test("reminders are off by default and can be switched on and off", T, async () => {
  const cred = take();
  await withPage({}, async ({ page, problems }) => {
    await reachAppointment(page, cred);
    assert.equal(await page.$eval("#reminders-toggle", (el) => el.checked), false);
    assert.ok((await text(page, "#reminders-note")).includes("Reminders are off"));
    assert.ok(!(await isShown(page, "#reminders-list")));

    await page.click("#reminders-toggle");
    await page.waitForSelector("#reminders-list:not([hidden])");
    const items = await page.$$eval("#reminders-list li", (els) => els.map((e) => e.textContent));
    assert.equal(items.length, 2, "a 48 hour and a 24 hour reminder");
    assert.ok(items.every((i) => i.includes("simulated") && i.includes("never sent")));
    assert.ok((await text(page, "#reminders-note")).includes("Nothing is really sent"));

    await page.click("#reminders-toggle");
    await page.waitForSelector("#reminders-list", { hidden: true });
    assert.ok((await text(page, "#reminders-note")).includes("Reminders are off"));
    assertClean(problems);
  });
});

test("inside 24 hours, rescheduling is disabled with a reason and cancelling is not", T, async () => {
  const cred = take((c) => c.hours_ahead >= 1, CREDS.soon);
  await withPage({}, async ({ page, problems }) => {
    await reachAppointment(page, cred);
    assert.equal(await page.$eval("#action-reschedule", (el) => el.disabled), true);
    assert.ok((await text(page, "#action-notes")).includes("too close to change online"));
    assert.equal(await page.$eval("#action-cancel", (el) => el.disabled), false);
    assertClean(problems);
  });
});

test("the reschedule screen compares providers and lists times correctly", T, async () => {
  const cred = take();
  await withPage({}, async ({ page, problems }) => {
    await reachAppointment(page, cred);
    await reachReschedule(page);
    assert.equal(await activeId(page), "h-reschedule");

    const waits = await page.$$eval("#waits-list .wait-item", (els) => els.map((e) => e.textContent.replace(/\s+/g, " ")));
    assert.equal(waits.length, 3, "three providers offer this type of visit");
    assert.equal(waits.filter((w) => w.includes("Soonest")).length, 1);
    assert.ok(waits[0].includes("Soonest"), "the soonest provider is listed first");
    assert.ok((await text(page, "#waits-note")).includes("demo data"));

    const radios = await page.$$eval('#slots-container input[name="slot"]', (els) =>
      els.map((e) => ({ disabled: e.disabled, start: e.getAttribute("data-start"), label: e.closest("label").textContent })));
    const open = radios.filter((r) => !r.disabled);
    assert.ok(open.length >= 3, "at least three times can be chosen");
    const starts = radios.map((r) => Date.parse(r.start));
    assert.deepEqual(starts, [...starts].sort((a, b) => a - b), "times are in order");
    const unavailable = radios.filter((r) => r.disabled);
    assert.ok(unavailable.length > 0 && unavailable.every((r) => /Booked|Not available|passed|current|Overlaps/.test(r.label)),
      "unavailable times are shown, disabled, with a reason");

    // A disabled time cannot be picked, even by force.
    const stillUnchecked = await page.$eval('#slots-container input[name="slot"]:disabled', (el) => { el.click(); return !el.checked; });
    assert.ok(stillUnchecked);

    // Only one time at a time can be chosen.
    const enabled = await page.$$('#slots-container input[name="slot"]:not(:disabled)');
    await enabled[0].click();
    await enabled[1].click();
    const checked = await page.$$eval('#slots-container input[name="slot"]:checked', (els) => els.map((e) => e.value));
    assert.equal(checked.length, 1);
    assert.equal(await page.$eval("#review-open", (el) => el.disabled), false);
    assertClean(problems);
  });
});

test("choosing a new time, reviewing it, and confirming it reschedules the appointment", T, async () => {
  const cred = take();
  await withPage({}, async ({ page, problems }) => {
    await reachAppointment(page, cred);
    const before = await text(page, "#appt-when");
    await reachReschedule(page);
    // A time days ahead, so the appointment can still be changed once more afterwards.
    const chosen = await pickSlotAmong(page, await selectableIds(cred, 72));
    await page.click("#review-open");
    await page.waitForSelector("#review-panel:not([hidden])");
    const review = await text(page, "#review-text");
    assert.ok(review.includes(before) && /with Dr\. Demo/.test(review), "the summary shows the old and new times: " + review);

    await page.click("#review-change"); // changed my mind about confirming
    assert.ok(!(await isShown(page, "#review-panel")));
    await page.click("#review-open");
    await page.waitForSelector("#review-panel:not([hidden])");
    await page.click("#review-confirm");
    await page.click("#review-confirm").catch(() => {}); // a second click must not matter

    await page.waitForSelector("#screen-appointment:not([hidden])");
    await page.waitForFunction(() => document.querySelector("#appt-status").textContent.includes("Rescheduled"));
    assert.ok((await text(page, "#result-message")).includes("was rescheduled from"));
    assert.notEqual(await text(page, "#appt-when"), before);

    const { appt } = await apiAppointment(cred);
    assert.equal(appt.status, "Rescheduled");
    assert.equal(Date.parse(appt.start_time), Date.parse(chosen.start), "the saved time is the one that was chosen");
    const token = await apiSession(cred);
    const again = await api("GET", `/appointments/${cred.appt_id}`, { token });
    assert.equal(again.data.allowed_actions.reschedule.allowed, true, "one change used, one left");
    assertClean(problems);
  });
});

test("a time taken by someone else while reviewing is handled kindly", T, async () => {
  const [mine, other] = takePair();
  await withPage({}, async ({ page, problems }) => {
    await reachAppointment(page, mine);
    await reachReschedule(page);
    const chosen = await pickSlotAmong(page, await selectableIds(other));
    await page.click("#review-open");
    await page.waitForSelector("#review-panel:not([hidden])");

    // Meanwhile another patient takes that very time.
    const token = await apiSession(other);
    const taken = await api("POST", `/appointments/${other.appt_id}/reschedule`, {
      token, body: { new_slot_id: chosen.value }, headers: { "Idempotency-Key": "taken-by-someone-else-123" },
    });
    assert.equal(taken.status, 200, JSON.stringify(taken.data));

    await page.click("#review-confirm");
    await page.waitForFunction(() => !document.querySelector("#reschedule-error").hidden);
    assert.ok((await text(page, "#reschedule-error")).includes("just taken"));
    assert.ok(!(await isShown(page, "#review-panel")));
    // The list refreshes, and that time is now disabled.
    await page.waitForFunction((value) => {
      const input = document.querySelector(`#slots-container input[value="${value}"]`);
      return input && input.disabled;
    }, {}, chosen.value);
    assert.equal((await apiAppointment(mine)).appt.status, mine.status, "my appointment is unchanged");
    assertClean(problems.filter((p) => !p.includes("409") && !p.includes("Conflict")));
  });
});

test("the list of times refreshes by itself", T, async () => {
  const [mine, other] = takePair();
  await withPage({ refreshMs: 400 }, async ({ page, problems }) => {
    await reachAppointment(page, mine);
    await reachReschedule(page);
    const target = await pickSlotAmong(page, await selectableIds(other));
    const token = await apiSession(other);
    const taken = await api("POST", `/appointments/${other.appt_id}/reschedule`, {
      token, body: { new_slot_id: target.value }, headers: { "Idempotency-Key": "refresh-test-key-123456" },
    });
    assert.equal(taken.status, 200, JSON.stringify(taken.data));
    await page.waitForFunction((value) => {
      const input = document.querySelector(`#slots-container input[value="${value}"]`);
      return input && input.disabled;
    }, { timeout: 8000 }, target.value);
    assertClean(problems);
  });
});

test("an expired session returns to sign-in with a clear message", T, async () => {
  const cred = take();
  await withPage({}, async ({ page, hooks }) => {
    await reachAppointment(page, cred);
    hooks.failNext = {
      prefix: `/appointments/${cred.appt_id}/slots`, status: 401,
      body: { error: { code: "unauthorized", message: "Please sign in again." } },
    };
    await page.click("#action-reschedule");
    await page.waitForSelector("#screen-signin:not([hidden])");
    assert.ok((await text(page, "#signin-error")).includes("session has ended"));
    // Signing in again works (the page forgot the old session and kept the accepted notice).
    await signIn(page, cred);
    await page.waitForSelector("#screen-appointment:not([hidden])");
  });
});

test("signing out forgets everything and returns to the notice", T, async () => {
  const cred = take();
  await withPage({}, async ({ page, requests }) => {
    await reachAppointment(page, cred);
    await page.click("#signout");
    assert.ok(await isShown(page, "#screen-notice"));
    assert.equal(await page.$eval("#appt-id", (el) => el.value), "");
    await reachSignIn(page);
    assert.ok(!(await isShown(page, "#screen-appointment")), "the old appointment is not shown any more");
    const before = requests.length;
    await page.reload({ waitUntil: "networkidle0" });
    assert.ok(await isShown(page, "#screen-notice"), "a refresh starts over");
    assert.ok(requests.slice(before).every((r) => !r.includes("/appointments")));
  });
});

test("it can be used with the keyboard alone", T, async () => {
  const cred = take();
  await withPage({}, async ({ page, problems }) => {
    await page.keyboard.press("Tab");
    assert.ok((await page.evaluate(() => document.activeElement.className)).includes("skip-link"));
    await page.keyboard.press("Enter");
    assert.equal(await activeId(page), "main", "the skip link moves into the content");

    for (let i = 0; i < 6 && (await activeId(page)) !== "notice-accept"; i += 1) await page.keyboard.press("Tab");
    assert.equal(await activeId(page), "notice-accept");
    await page.keyboard.press("Enter");
    await page.waitForSelector("#screen-signin:not([hidden])");
    assert.equal(await activeId(page), "h-signin");

    await page.keyboard.press("Tab");
    assert.equal(await activeId(page), "appt-id");
    await page.keyboard.type(cred.appt_id);
    await page.keyboard.press("Tab");
    assert.equal(await activeId(page), "access-code");
    await page.keyboard.type(cred.code);
    await page.keyboard.press("Enter"); // submits the form
    await page.waitForSelector("#screen-appointment:not([hidden])");
    assert.equal(await activeId(page), "h-appointment");
    assertClean(problems);
  });
});

test("the page loads and responds within two seconds", T, async () => {
  const cred = take();
  await withPage({ navigate: false }, async ({ page }) => {
    const BASE = process.env.UI_BASE;
    let started = Date.now();
    await page.goto(BASE + "/patient/", { waitUntil: "domcontentloaded" });
    await page.waitForFunction(() => document.querySelector("#notice-accept") && !document.querySelector("#notice-accept").disabled);
    const load = Date.now() - started;
    await reachSignIn(page);
    started = Date.now();
    await signIn(page, cred);
    await page.waitForSelector("#screen-appointment:not([hidden])");
    const signInTime = Date.now() - started;
    started = Date.now();
    await reachReschedule(page);
    const slots = Date.now() - started;
    console.log(`timings: load ${load} ms, sign-in ${signInTime} ms, slots ${slots} ms`);
    for (const [name, ms] of Object.entries({ load, signInTime, slots })) assert.ok(ms < 2000, `${name} took ${ms} ms`);
  });
});
