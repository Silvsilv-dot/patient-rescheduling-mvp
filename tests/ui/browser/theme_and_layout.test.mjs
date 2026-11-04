// Light/dark theme (BR-23), every screen size, accessibility, and the security policy,
// all checked in a real browser (tests T02-10, T02-11, T08-1, T08-3, T08-7).
import test from "node:test";
import assert from "node:assert/strict";
import {
  BANNER, BASE, CREDS, activeId, assertClean, axeViolations, isShown, openPage, pickFirstSlot,
  reachAppointment, reachReschedule, reachSignIn, shot, take, text, withPage,
} from "./harness.mjs";

const T = { timeout: 180000 };
const WIDTHS = [320, 360, 390, 768, 1024, 1366, 1920];
const AUDIT_WIDTHS = [360, 768, 1366];
const BG = { light: "rgb(243, 245, 248)", dark: "rgb(15, 20, 25)" }; // the --bg token of each theme

const background = (page) => page.evaluate(() => getComputedStyle(document.body).backgroundColor);
const themeOf = (page) => page.evaluate(() => document.documentElement.getAttribute("data-theme"));

// ------------------------------------------------------------------ theme
test("with nothing saved it follows the system setting, before the first paint", T, async () => {
  for (const dark of [true, false]) {
    const want = dark ? "dark" : "light";
    await withPage({ dark }, async ({ page, problems }) => {
      assert.equal(await themeOf(page), want);
      const first = await page.evaluate(() => window.__themeFirst);
      assert.deepEqual(first, { theme: want, bodyExisted: false }, "set while the page head is still loading");
      assert.equal(await background(page), BG[want]);
      assert.equal(await page.$eval("#theme-toggle", (el) => el.getAttribute("aria-pressed")), String(dark));
      assert.equal(await text(page, "#theme-state"), dark ? "On" : "Off");
      assertClean(problems);
    });
  }
});

test("a saved choice wins over the system setting", T, async () => {
  await withPage({ dark: true }, async ({ page }) => {
    await page.evaluate(() => localStorage.setItem("theme", "light"));
    await page.reload({ waitUntil: "networkidle0" });
    assert.equal(await themeOf(page), "light");
    assert.equal(await background(page), BG.light);
  });
});

test("the toggle switches the theme, announces it, and remembers it on this device", T, async () => {
  await withPage({ dark: false }, async ({ page, problems }) => {
    assert.equal(await page.$eval("#theme-toggle", (el) => el.tagName), "BUTTON");
    await page.click("#theme-toggle");
    assert.equal(await themeOf(page), "dark");
    assert.equal(await page.$eval("#theme-toggle", (el) => el.getAttribute("aria-pressed")), "true");
    assert.equal(await text(page, "#theme-state"), "On");
    assert.equal(await background(page), BG.dark);
    assert.equal(await page.evaluate(() => localStorage.getItem("theme")), "dark");

    await page.reload({ waitUntil: "networkidle0" });
    assert.equal(await themeOf(page), "dark", "still dark after a reload, even though the system is light");
    assert.equal(await text(page, "#theme-state"), "On");

    await page.keyboard.press("Tab"); // skip link
    await page.keyboard.press("Tab"); // the theme button comes next
    assert.equal(await activeId(page), "theme-toggle");
    await page.keyboard.press("Enter");
    assert.equal(await themeOf(page), "light", "the toggle works from the keyboard");
    assert.equal(await page.evaluate(() => localStorage.getItem("theme")), "light");
    assertClean(problems);
  });
});

test("until a choice is made it follows later system changes, then it stops", T, async () => {
  await withPage({ dark: false }, async ({ page }) => {
    await page.emulateMediaFeatures([{ name: "prefers-color-scheme", value: "dark" }]);
    await page.waitForFunction(() => document.documentElement.getAttribute("data-theme") === "dark");
    await page.click("#theme-toggle"); // the person chooses light
    assert.equal(await themeOf(page), "light");
    await page.emulateMediaFeatures([{ name: "prefers-color-scheme", value: "light" }]);
    await page.emulateMediaFeatures([{ name: "prefers-color-scheme", value: "dark" }]);
    await new Promise((resolve) => setTimeout(resolve, 300));
    assert.equal(await themeOf(page), "light", "their choice is respected");
  });
});

test("it still works when the browser blocks storage", T, async () => {
  const browser = (await import("./harness.mjs")).launch;
  const instance = await browser();
  try {
    const { page, problems } = await openPage(instance, { dark: false, navigate: false });
    await page.evaluateOnNewDocument(() => {
      Storage.prototype.getItem = () => { throw new Error("blocked"); };
      Storage.prototype.setItem = () => { throw new Error("blocked"); };
    });
    await page.goto(BASE + "/patient/", { waitUntil: "networkidle0" });
    assert.equal(await themeOf(page), "light");
    await page.click("#theme-toggle");
    assert.equal(await themeOf(page), "dark", "it still switches, it just cannot remember");
    assertClean(problems);
  } finally {
    await instance.close();
  }
});

test("with JavaScript off it explains why, and the theme still follows the system", T, async () => {
  await withPage({ dark: true, navigate: false }, async ({ page }) => {
    await page.setJavaScriptEnabled(false);
    await page.goto(BASE + "/patient/", { waitUntil: "load" });
    assert.ok((await text(page, "noscript")).includes("needs JavaScript"));
    assert.equal(await background(page), BG.dark, "the CSS fallback gives dark without any script");
  });
});

// ------------------------------------------------------------------ security policy
test("the browser enforces the strict security policy on the page", T, async () => {
  await withPage({}, async ({ page, problems }) => {
    const blocked = await page.evaluate(() => {
      const script = document.createElement("script");
      script.textContent = "window.__injected = true";
      document.head.appendChild(script);
      return window.__injected === true;
    });
    assert.equal(blocked, false, "an injected inline script must not run");
    const violation = problems.find((p) => /violates the following Content Security Policy directive.*script-src/.test(p));
    assert.ok(violation, "the browser reported the blocked script: " + problems);
    const headers = await page.evaluate(async () => {
      const r = await fetch("/patient/", { cache: "no-store" });
      return { csp: r.headers.get("content-security-policy"), nosniff: r.headers.get("x-content-type-options") };
    });
    assert.ok(headers.csp.includes("script-src 'self'") && !headers.csp.includes("unsafe-inline"));
    assert.equal(headers.nosniff, "nosniff");
  });
});

test("page text from the server is only ever shown as text", T, async () => {
  // Nothing on the page may be built from HTML strings; a hostile value must stay inert.
  await withPage({}, async ({ page, problems }) => {
    await page.evaluate(() => {
      const holder = document.getElementById("signin-error");
      holder.textContent = "<img src=x onerror=window.__pwned=1><script>window.__pwned=1</script>";
    });
    assert.equal(await page.evaluate(() => window.__pwned === undefined), true);
    assert.equal(await page.$$eval("#signin-error img, #signin-error script", (els) => els.length), 0);
    assertClean(problems);
  });
});

// ------------------------------------------------------------------ every screen size, both themes
async function tapTargetFailures(page) {
  return page.evaluate(() => {
    const failures = [];
    const controls = document.querySelectorAll("button, input, summary, a[href], select, textarea");
    for (const el of controls) {
      if (el.getClientRects().length === 0 || el.classList.contains("skip-link")) continue;
      let target = el;
      if (el.matches('input[type="checkbox"], input[type="radio"]')) target = el.closest("label") || el;
      const box = target.getBoundingClientRect();
      if (box.width < 43.5 || box.height < 43.5) {
        failures.push(`${el.id || el.tagName.toLowerCase() + "." + el.className}: ${Math.round(box.width)}x${Math.round(box.height)}`);
      }
    }
    return failures;
  });
}

async function overflowPixels(page) {
  return page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
}

async function inspect(page, label, { audit }) {
  const problems = [];
  const overflow = await overflowPixels(page);
  if (overflow > 1) problems.push(`${label}: sideways scrolling by ${overflow}px`);
  const small = await tapTargetFailures(page);
  if (small.length) problems.push(`${label}: touch targets under 44px: ${small.slice(0, 4).join(", ")}`);
  const hasH1 = await page.$$eval("h1", (els) => els.length);
  if (hasH1 !== 1) problems.push(`${label}: expected one h1, found ${hasH1}`);
  if (audit) {
    for (const v of await axeViolations(page)) problems.push(`${label}: accessibility: ${v}`);
  }
  return problems;
}

test("every screen works at every size in both themes, with no accessibility violations", T, async () => {
  const cred = take();
  const failures = [];
  for (const width of WIDTHS) {
    for (const dark of [false, true]) {
      const theme = dark ? "dark" : "light";
      const audit = AUDIT_WIDTHS.includes(width);
      await withPage({ width, height: width < 600 ? 844 : 900, dark }, async ({ page, problems }) => {
        const tag = `${width}px ${theme}`;
        assert.equal(await themeOf(page), theme);
        assert.equal(await page.evaluate(() => document.documentElement.lang), "en");
        assert.ok((await page.title()).includes("DEMO"));
        assert.equal(await text(page, ".demo-banner"), BANNER);

        failures.push(...(await inspect(page, `${tag} notice`, { audit })));
        await shot(page, `${width}_${theme}_1_notice`);
        await reachSignIn(page);
        failures.push(...(await inspect(page, `${tag} sign-in`, { audit })));
        await shot(page, `${width}_${theme}_2_signin`);
        await reachAppointmentFromSignIn(page, cred);
        failures.push(...(await inspect(page, `${tag} appointment`, { audit })));
        await shot(page, `${width}_${theme}_3_appointment`);
        await reachReschedule(page);
        failures.push(...(await inspect(page, `${tag} reschedule`, { audit })));
        await shot(page, `${width}_${theme}_4_reschedule`);
        await pickFirstSlot(page);
        await page.click("#review-open");
        await page.waitForSelector("#review-panel:not([hidden])");
        failures.push(...(await inspect(page, `${tag} review`, { audit })));
        await page.evaluate(() => document.querySelector("#review-panel").scrollIntoView({ block: "center" }));
        await shot(page, `${width}_${theme}_5_review`);
        assertClean(problems);
      });
    }
  }
  assert.deepEqual(failures, [], "layout or accessibility problems:\n" + failures.join("\n"));
});

async function reachAppointmentFromSignIn(page, cred) {
  await page.type("#appt-id", cred.appt_id);
  await page.type("#access-code", cred.code);
  await page.click("#signin-submit");
  await page.waitForSelector("#screen-appointment:not([hidden])");
  await page.waitForFunction(() => document.querySelector("#reminders-note").textContent.length > 0);
}

test("a cancelled appointment and the declined screen also pass the accessibility audit", T, async () => {
  const cred = take((c) => c.status === "Confirmed");
  for (const dark of [false, true]) {
    await withPage({ width: 390, dark }, async ({ page }) => {
      await page.click("#notice-decline");
      assert.deepEqual(await axeViolations(page), []);
      await page.click("#declined-back");
      await reachAppointment(page, cred);
      await page.click("#action-cancel");
      assert.deepEqual(await axeViolations(page), [], "with the cancel question open");
    });
  }
});

test("text can be enlarged to 200 percent without losing anything", T, async () => {
  const cred = take();
  await withPage({ width: 390 }, async ({ page }) => {
    await page.evaluate(() => (document.documentElement.style.fontSize = "200%"));
    await reachAppointment(page, cred);
    assert.ok((await overflowPixels(page)) <= 1, "no sideways scrolling at 200% text");
    for (const id of ["#action-confirm", "#action-reschedule", "#action-cancel", "#signout"]) {
      assert.ok(await isShown(page, id), id + " is still there");
    }
  });
});

test("the narrowest screens work for every type of visit, because the content differs", T, async () => {
  // A 3px sideways scroll once showed up only for one visit type, so test each type.
  const types = [...new Set(CREDS.far.map((c) => c.type_id))];
  assert.ok(types.length >= 4, "the seeded data should cover every visit type");
  const failures = [];
  for (const typeId of types) {
    const cred = take((c) => c.type_id === typeId);
    for (const width of [320, 360]) {
      for (const dark of [false, true]) {
        await withPage({ width, dark }, async ({ page, problems }) => {
          await reachAppointment(page, cred);
          await reachReschedule(page);
          failures.push(...(await inspect(page, `${typeId} ${width}px ${dark ? "dark" : "light"} reschedule`, { audit: false })));
          for (const open of await page.$$("details.slot-day > summary")) await open.click(); // every day expanded
          failures.push(...(await inspect(page, `${typeId} ${width}px ${dark ? "dark" : "light"} all days open`, { audit: false })));
          assertClean(problems);
        });
      }
    }
  }
  assert.deepEqual(failures, [], failures.join("\n"));
});
