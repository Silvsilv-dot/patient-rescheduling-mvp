// Shared tools for the browser tests. They drive a REAL headless Chrome against the REAL
// server, so what is checked is what a patient would actually get (including the strict
// security policy, which Chrome enforces).
import fs from "node:fs";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import chromium from "@sparticuz/chromium";
import puppeteer from "puppeteer-core";

const require = createRequire(import.meta.url);

export const BASE = process.env.UI_BASE;
export const CREDS = JSON.parse(process.env.UI_CREDS || '{"far":[],"soon":[]}');
export const SHOTS = process.env.UI_SCREENSHOT_DIR || "";
export const GENERIC_SIGN_IN_ERROR = "We could not find a match. Please check your details and try again.";
export const BANNER = "DEMO - Synthetic Data. Educational prototype, not a clinical system.";

// ---- choosing test appointments (each test gets its own, so tests never disturb each other)
const used = new Set();
export function take(predicate = () => true, list = CREDS.far) {
  const found = list.find((c) => !used.has(c.appt_id) && predicate(c));
  assert.ok(found, "the seeded data has no appointment for this test");
  used.add(found.appt_id);
  return found;
}
export function takePair() {
  const first = take();
  const second = take((c) => c.type_id === first.type_id && c.patient_id !== first.patient_id);
  return [first, second];
}

// ---- browser
export async function launch() {
  const failures = [];
  try {
    return await puppeteer.launch({
      args: chromium.args,
      executablePath: await chromium.executablePath(),
      headless: "shell",
    });
  } catch (error) {
    failures.push(String(error.message || error));
  }
  const candidates = [process.env.CHROME_PATH, "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser"];
  for (const path of candidates.filter(Boolean)) {
    if (!fs.existsSync(path)) continue;
    try {
      return await puppeteer.launch({ executablePath: path, headless: true, args: ["--no-sandbox", "--disable-gpu"] });
    } catch (error) {
      failures.push(String(error.message || error));
    }
  }
  throw new Error("Could not start a browser: " + failures.join(" | "));
}

/** Open the patient page in a fresh browser tab with problem-spotting attached. */
export async function openPage(browser, options = {}) {
  const { width = 390, height = 844, dark = null, path = "/patient/", refreshMs = null, navigate = true } = options;
  const page = await browser.newPage();
  const problems = [];
  const requests = [];
  const hooks = { failNext: null };

  page.on("console", (m) => {
    if (m.type() === "error" || m.type() === "warning") problems.push(`console ${m.type()}: ${m.text()}`);
  });
  page.on("pageerror", (e) => problems.push(`page error: ${e.message}`));
  page.on("requestfailed", (r) => problems.push(`request failed: ${r.url()}`));
  await page.evaluateOnNewDocument(() => {
    document.addEventListener("securitypolicyviolation", (e) =>
      console.error("CSP violation: " + e.violatedDirective + " " + e.blockedURI)
    );
    window.__themeFirst = null;
    // Watch the whole document (the root element does not exist yet at this moment).
    new MutationObserver(() => {
      if (!window.__themeFirst) {
        window.__themeFirst = {
          theme: document.documentElement.getAttribute("data-theme"),
          bodyExisted: !!document.body,
        };
      }
    }).observe(document, { attributes: true, subtree: true, attributeFilter: ["data-theme"] });
  });

  await page.setViewport({
    width, height,
    deviceScaleFactor: width < 600 ? 2 : 1,
    isMobile: width < 600,
    hasTouch: width < 600,
  });
  if (dark !== null) {
    await page.emulateMediaFeatures([{ name: "prefers-color-scheme", value: dark ? "dark" : "light" }]);
  }

  await page.setRequestInterception(true);
  page.on("request", async (request) => {
    const url = new URL(request.url());
    requests.push(`${request.method()} ${url.pathname}`);
    if (hooks.failNext && url.pathname.startsWith("/api/v1" + hooks.failNext.prefix)) {
      const { status, body } = hooks.failNext;
      hooks.failNext = null;
      return request.respond({ status, contentType: "application/json", body: JSON.stringify(body) });
    }
    if (refreshMs && url.pathname === "/patient/") {
      const real = await fetch(request.url());
      const html = (await real.text()).replace('data-refresh-ms="10000"', `data-refresh-ms="${refreshMs}"`);
      const headers = Object.fromEntries(real.headers.entries());
      delete headers["content-length"];
      delete headers["content-encoding"];
      return request.respond({ status: real.status, headers, body: html });
    }
    return request.continue();
  });

  if (navigate) await page.goto(BASE + path, { waitUntil: "networkidle0" });
  return { page, problems, requests, hooks };
}

/** Run a test body with a fresh browser, always closing it afterwards. */
export async function withPage(options, body) {
  const browser = await launch();
  try {
    const context = await openPage(browser, options);
    return await body({ ...context, browser });
  } finally {
    await browser.close();
  }
}

export function assertClean(problems) {
  assert.deepEqual(problems, [], "the browser reported problems:\n" + problems.join("\n"));
}

// ---- reading the page
export const text = (page, selector) =>
  page.$eval(selector, (el) => el.textContent.replace(/\s+/g, " ").trim());

export const isShown = (page, selector) =>
  page.$eval(selector, (el) => !el.hidden && el.getClientRects().length > 0).catch(() => false);

export const activeId = (page) => page.evaluate(() => document.activeElement && document.activeElement.id);

export async function screenText(page, selector) {
  return page.$eval(selector, (el) => el.innerText);
}

// ---- driving the page
export async function reachSignIn(page) {
  await page.click("#notice-accept");
  await page.waitForSelector("#screen-signin:not([hidden])");
}

export async function signIn(page, cred, code = cred.code) {
  await page.$eval("#appt-id", (el) => (el.value = ""));
  await page.type("#appt-id", cred.appt_id);
  await page.type("#access-code", code);
  await page.click("#signin-submit");
}

export async function reachAppointment(page, cred) {
  await reachSignIn(page);
  await signIn(page, cred);
  await page.waitForSelector("#screen-appointment:not([hidden])");
  await page.waitForFunction(() => document.querySelector("#reminders-note").textContent.length > 0);
}

export async function reachReschedule(page) {
  await page.click("#action-reschedule");
  await page.waitForSelector("#slots-container .slot");
}

export const firstOpenSlot = (page) =>
  page.$eval('#slots-container input[name="slot"]:not(:disabled)', (el) => ({
    value: el.value,
    start: el.getAttribute("data-start"),
  }));

/** Open the first day section that holds a choosable time, then pick that time. */
export async function pickFirstSlot(page) {
  await page.click('#slots-container input[name="slot"]:not(:disabled)');
  return page.$eval('#slots-container input[name="slot"]:checked', (el) => ({
    value: el.value,
    start: el.getAttribute("data-start"),
  }));
}

/** Choose a time that is open on this page AND in the given list of slot IDs; return it. */
export async function pickSlotAmong(page, allowedIds) {
  const value = await page.evaluate((ids) => {
    const wanted = new Set(ids);
    const input = [...document.querySelectorAll('#slots-container input[name="slot"]:not(:disabled)')]
      .find((el) => wanted.has(el.value));
    if (!input) return null;
    const day = input.closest("details");
    if (day) day.open = true;
    return input.value;
  }, allowedIds);
  assert.ok(value, "no time is open for both people");
  await page.click(`#slots-container input[value="${value}"]`);
  return page.$eval('#slots-container input[name="slot"]:checked', (el) => ({
    value: el.value,
    start: el.getAttribute("data-start"),
  }));
}

/** Slot IDs that a given appointment is allowed to choose, according to the API. */
export async function selectableIds(cred, minHoursAhead = 0) {
  const token = await apiSession(cred);
  const result = await api("GET", `/appointments/${cred.appt_id}/slots`, { token });
  assert.equal(result.status, 200);
  const cutoff = Date.now() + minHoursAhead * 3600 * 1000;
  return result.data.slots
    .filter((s) => s.selectable && Date.parse(s.start_time) >= cutoff)
    .map((s) => s.slot_id);
}

// ---- talking to the API directly (for what "another person" does, and for checking results)
export async function api(method, path, { token, body, headers } = {}) {
  const response = await fetch(BASE + "/api/v1" + path, {
    method,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: "Bearer " + token } : {}),
      ...(headers || {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const raw = await response.text();
  let data = null;
  try { data = raw ? JSON.parse(raw) : null; } catch { data = raw; }
  return { status: response.status, data };
}

export async function apiSession(cred) {
  const signedIn = await api("POST", "/auth/patient", { body: { appt_id: cred.appt_id, access_code: cred.code } });
  assert.equal(signedIn.status, 200, "API sign-in failed");
  const token = signedIn.data.access_token;
  const consent = await api("POST", "/consents", {
    token, body: { consent_type: "C1", status: "Granted", notice_version: "1.0" },
  });
  assert.equal(consent.status, 201);
  return token;
}

export async function apiAppointment(cred) {
  const token = await apiSession(cred);
  const result = await api("GET", `/appointments/${cred.appt_id}`, { token });
  assert.equal(result.status, 200);
  return { token, appt: result.data };
}

// ---- accessibility audit (axe-core, run inside the real page)
const AXE_SOURCE = fs.readFileSync(require.resolve("axe-core/axe.min.js"), "utf8");

export async function axeViolations(page) {
  await page.evaluate(AXE_SOURCE);
  const results = await page.evaluate(() =>
    window.axe.run(document, {
      runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "best-practice"] },
    })
  );
  return results.violations.map(
    (v) => `${v.id} (${v.impact}): ${v.help} -> ${v.nodes.slice(0, 3).map((n) => n.target.join(" ")).join(" | ")}`
  );
}

export async function shot(page, name) {
  if (!SHOTS) return;
  fs.mkdirSync(SHOTS, { recursive: true });
  await page.screenshot({ path: `${SHOTS}/${name}.png` });
}
