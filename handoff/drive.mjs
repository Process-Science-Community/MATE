// Browser check of the module "How to use" panel against the scratch MATE (web :3021, API :8021).
// Run: node drive.mjs <moduleId> <ModuleName>   (default: temporal_mapper "Temporal Mapper")
import { chromium } from "playwright-core";
import fs from "node:fs";

const MODULE_ID = process.argv[2] ?? "temporal_mapper";
const MODULE_NAME = process.argv[3] ?? "Temporal Mapper";
const WEB = "http://localhost:3021";
const API = "http://localhost:8021";
const OUT = "/Users/yannickbrusa/.claude-secondary/jobs/e2caa0e8/tmp/e2e/shots";
const CSV =
  "/Users/yannickbrusa/Documents/MATE/.claude/worktrees/module-how-to-use-guides/simulated_data/input/autonomous/simulated_log_0.csv";
fs.mkdirSync(OUT, { recursive: true });

const results = [];
const check = (name, ok, detail = "") => {
  results.push({ name, ok: Boolean(ok), detail });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? "  — " + detail : ""}`);
};

const browser = await chromium.launch({ channel: "chrome", headless: true });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();
const problems = [];
page.on("console", (m) => {
  if (m.type() === "error") problems.push("console.error: " + m.text().slice(0, 300));
});
page.on("pageerror", (e) => problems.push("pageerror: " + e.message.slice(0, 300)));
page.on("response", (r) => {
  if (r.status() >= 400) problems.push(`http ${r.status()}: ${r.request().method()} ${r.url().slice(0, 160)}`);
});

const guideAside = page.locator(`aside[aria-label="How to use ${MODULE_NAME}"]`);
const mateAside = page.locator('aside[aria-label="MATE AI assistant"]');
const trigger = page.locator(`button[aria-label="How to use ${MODULE_NAME}"]`);
const splashGone = () =>
  page.waitForSelector("text=Caching workspace", { state: "detached", timeout: 120000 }).catch(() => {});
const shot = (name, target = page) => target.screenshot({ path: `${OUT}/${name}.png` });
const hidden = async (loc) => (await loc.getAttribute("aria-hidden")) === "true";

try {
  // --- sign in (demo auto-login) -------------------------------------------------
  await page.goto(WEB + "/login");
  await page.waitForURL((u) => !u.pathname.startsWith("/login"), { timeout: 120000 });
  await splashGone();

  // --- import a log through the real wizard -------------------------------------
  const existing = await (await fetch(API + "/api/v1/event-logs", { headers: { Authorization: "Bearer demo-access-token" } })).json();
  const listOf = (j) => (Array.isArray(j) ? j : (j.items ?? []));
  let logId = listOf(existing)[0]?.id;
  if (!logId) {
    await page.goto(WEB + "/processes/import");
    await page.getByText("Drop an event log here").waitFor({ timeout: 120000 });
    await page.waitForTimeout(4000); // the file input is not wired up until hydration
    await page.setInputFiles('input[type="file"]', CSV);
    await page.getByRole("button", { name: "Confirm mapping" }).click({ timeout: 60000 });
    await page.getByRole("button", { name: "Start import" }).click({ timeout: 60000 });
    await page.getByText("Import complete").waitFor({ timeout: 180000 });
    const after = await (await fetch(API + "/api/v1/event-logs", { headers: { Authorization: "Bearer demo-access-token" } })).json();
    logId = listOf(after)[0]?.id;
  }
  check("a log is imported", Boolean(logId), String(logId));

  // --- module page ---------------------------------------------------------------
  await page.goto(`${WEB}/processes/${logId}/modules/${MODULE_ID}`);
  await splashGone();
  await trigger.waitFor({ timeout: 120000 });
  check("topbar shows the How to use button", await trigger.isVisible());
  check("button label is visible at desktop width", (await trigger.innerText()).includes("How to use"));
  check("panel is closed initially", await hidden(guideAside));
  const mainClosed = await page.locator("main").boundingBox();
  await shot("01-module-page-closed");

  // --- open ----------------------------------------------------------------------
  await trigger.click();
  await guideAside.getByText("How to use", { exact: true }).first().waitFor({ timeout: 30000 });
  await guideAside.locator("details").first().waitFor({ timeout: 60000 }); // lazy markdown chunk loaded
  await page.waitForTimeout(600);
  check("panel is open", !(await hidden(guideAside)));
  check("button reports pressed", (await trigger.getAttribute("aria-pressed")) === "true");
  const sections = await guideAside.locator("details > summary").allInnerTexts();
  check("six sections are listed", sections.length === 6, sections.map((s) => s.trim()).join(" | "));
  const opened = await guideAside.locator("details[open]").count();
  check("only the first section starts open", opened === 1, `open=${opened}`);
  const mainOpen = await page.locator("main").boundingBox();
  check(
    "docked: the page column shrinks by the panel width",
    mainClosed && mainOpen && mainClosed.width - mainOpen.width > 380,
    `main ${Math.round(mainClosed?.width)} -> ${Math.round(mainOpen?.width)}`,
  );
  check("title H1 is not duplicated in the body", (await guideAside.locator("h1").count()) === 0);
  await shot("02-guide-open");

  // --- expand all, tables and layout ---------------------------------------------
  await guideAside.getByRole("button", { name: "Expand all" }).click();
  await page.waitForTimeout(400);
  check("expand all opens every section", (await guideAside.locator("details[open]").count()) === 6);
  const tables = await guideAside.locator("table").count();
  check("markdown tables render as tables", tables >= 1, `tables=${tables}`);
  const overflow = await guideAside.evaluate((el) => el.scrollWidth > el.clientWidth + 1);
  check("no horizontal overflow of the panel itself", !overflow);
  const links = await guideAside.locator('a[target="_blank"]').count();
  console.log("INFO external links:", links);
  const settings = guideAside.getByText("Settings", { exact: true }).first();
  await settings.scrollIntoViewIfNeeded();
  await shot("03-guide-settings");
  await guideAside.getByRole("button", { name: "Collapse all" }).click();
  check("collapse all folds every section", (await guideAside.locator("details[open]").count()) === 0);

  // --- exclusive with MATE AI ------------------------------------------------------
  await page.locator('button[aria-label="Toggle MATE AI"]').click();
  await page.waitForTimeout(500);
  check("opening MATE AI closes the guide", await hidden(guideAside));
  check("MATE AI is open", !(await hidden(mateAside)));
  await shot("04-mate-open");
  await trigger.click();
  await page.waitForTimeout(500);
  check("opening the guide closes MATE AI", await hidden(mateAside));
  check("guide is open again", !(await hidden(guideAside)));

  // --- Escape ----------------------------------------------------------------------
  await guideAside.locator("summary").first().focus();
  await page.keyboard.press("Escape");
  await page.waitForTimeout(400);
  check("Escape closes the guide", await hidden(guideAside));
  check("focus returns to the button", await trigger.evaluate((el) => document.activeElement === el));

  // --- dark mode ---------------------------------------------------------------------
  await page.evaluate(() => localStorage.setItem("theme", "dark"));
  await page.reload();
  await splashGone();
  await trigger.waitFor({ timeout: 120000 });
  await trigger.click();
  await guideAside.locator("details").first().waitFor({ timeout: 60000 });
  await guideAside.getByRole("button", { name: "Expand all" }).click();
  await page.waitForTimeout(500);
  check("dark theme active", await page.evaluate(() => document.documentElement.classList.contains("dark")));
  await shot("05-guide-dark");
  await page.evaluate(() => localStorage.setItem("theme", "light"));

  // --- narrow viewport: overlay, not docked -------------------------------------------
  await page.setViewportSize({ width: 900, height: 800 });
  await page.waitForTimeout(500);
  const position = await guideAside.evaluate((el) => getComputedStyle(el).position);
  check("below lg the panel is an overlay", position === "fixed", `position=${position}`);
  await shot("06-narrow");
  await page.setViewportSize({ width: 1440, height: 900 });

  // --- follows the page: leaving module pages closes it --------------------------------
  await page.locator('a[href="/processes"]').first().click();
  await page.waitForURL((u) => u.pathname === "/processes", { timeout: 60000 });
  await page.waitForTimeout(800);
  check("panel closes on a non-module page", await hidden(page.locator("#module-guide-panel")));
  check("button is gone on a non-module page", (await trigger.count()) === 0);
} catch (err) {
  check("script completed without throwing", false, String(err).slice(0, 400));
  await shot("zz-failure").catch(() => {});
}

const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
const noise = (p) => /ERR_ABORTED|favicon|Download the React DevTools/i.test(p);
const real = problems.filter((p) => !noise(p));
console.log(`browser problems (${real.length}):`);
for (const p of real.slice(0, 15)) console.log("  " + p);
await browser.close();
process.exit(failed.length ? 1 : 0);
