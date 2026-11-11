import { expect, test, type Locator, type Page } from "@playwright/test";

/**
 * The Studio's visualization catalog: groups, the custom-widget footer, catalog
 * entries that bind the module's first compatible dataset (fixture module
 * e2e_viz, apps/web/e2e/fixtures) or fall back to a "Needs data" placeholder
 * (e2e_studio, one table dataset). `make e2e` loads both modules.
 */

const API = process.env.E2E_API_URL ?? "http://127.0.0.1:8777";
const VIZ_MODULE = process.env.E2E_VIZ_MODULE ?? "e2e_viz";
const TABLE_MODULE = process.env.E2E_MODULE ?? "e2e_studio";
const AUTH = { Authorization: "Bearer demo-access-token" }; // DEMO_ACCESS_TOKEN of the lite API

const GROUPS = ["Charts", "Process models & graphs", "Matrices", "KPIs", "Tables & lists", "Text & status"];
// Catalog entry title -> the base viz id its card renders.
const ENTRIES: [string, string][] = [
  ["Ranked bars", "bar"],
  ["Heatmap", "heatmap"],
  ["Petri net", "petri-net"],
  ["Tree diagram", "tree-diagram"],
  ["Ranked list", "ranked-list"],
  ["KPI grid", "kpi-grid"],
  ["Callout", "callout"],
  ["Proportion bar", "proportion-bar"],
];

let logId = "";

async function api(path: string, init: RequestInit = {}) {
  const res = await fetch(`${API}${path}`, {
    ...init,
    headers: { ...AUTH, "Content-Type": "application/json", ...init.headers },
  });
  if (!res.ok) throw new Error(`${init.method ?? "GET"} ${path}: ${res.status} ${await res.text()}`);
  return res.json();
}

const emptyLayout = (module: string) =>
  api(`/api/v1/dev/modules/${module}/layout`, {
    method: "PUT",
    body: JSON.stringify({ version: 1, items: [] }),
  });

test.beforeAll(async () => {
  await expect
    .poll(
      async () => {
        const logs: { id: string; status: string; log_model: string }[] = await api("/api/v1/event-logs");
        logId = logs.find((l) => l.status === "ready" && l.log_model === "case_centric")?.id ?? "";
        return logId;
      },
      { timeout: 180_000, intervals: [2_000] },
    )
    .not.toBe("");
});

test.beforeEach(async () => {
  await emptyLayout(VIZ_MODULE);
});

test.afterAll(async () => {
  // Leave no placeholder behind for studio-parity.spec.ts (it PUTs its own layout too).
  await emptyLayout(VIZ_MODULE);
});

/** Go to `path` through the demo login (retry once on an auth.js MissingCSRF bounce). */
async function open(page: Page, path: string) {
  const target = path.split("?")[0];
  for (let i = 0; i < 2; i++) {
    await page.goto(path);
    await page.waitForURL((u) => !u.pathname.startsWith("/login"));
    if (new URL(page.url()).pathname === target) return;
  }
  throw new Error(`could not open ${path}, ended on ${page.url()}`);
}

async function openStudio(page: Page, module: string) {
  await open(page, `/dev/studio/${module}?log=${logId}`);
  await expect(page.locator("[data-studio-stage]")).toBeVisible();
  // Wait for the saved layout to hydrate, so addEntry's "before" count is not taken too early.
  const { layout } = await api(`/api/v1/modules/${module}/layout`);
  const firstTab = layout?.tabs?.[0]?.id;
  const shown = (layout?.items ?? []).filter((i: { tab?: string }) => !i.tab || i.tab === firstTab).length;
  await expect(page.locator("[data-grid-id]")).toHaveCount(shown);
}

const search = (page: Page) => page.getByPlaceholder(/^Search/);

/** Add a catalog entry by click (top fit): search opens every group, then the row. */
async function addEntry(page: Page, title: string) {
  const before = await page.locator("[data-grid-id]").count();
  await search(page).fill(title);
  await page.getByText(title, { exact: true }).first().click();
  await expect(page.locator("[data-grid-id]")).toHaveCount(before + 1);
  await search(page).fill("");
}

/** Every ready viz shows something: a chart/graph mark or text. */
async function expectContent(cards: Locator) {
  for (const card of await cards.all()) {
    await expect
      .poll(() =>
        card.evaluate(
          (el) => !!el.querySelector("svg, canvas, .react-flow__node") || (el as HTMLElement).innerText.trim().length > 0,
        ),
      )
      .toBe(true);
  }
}

test.use({ viewport: { width: 1600, height: 1000 } });

test("palette lists the catalog groups and the custom-widget footer", async ({ page }) => {
  await openStudio(page, VIZ_MODULE);
  for (const label of GROUPS) await expect(page.getByText(label, { exact: true }).first()).toBeVisible();
  await expect(page.getByText("Need something not listed?").first()).toBeVisible();
  await expect(page.getByText(`make widget ID=${VIZ_MODULE} NAME=my-widget`).first()).toBeVisible();
  await expect(page.getByText(/docs\/DEV_LITE\.md/).first()).toBeVisible();
});

test("catalog entries bind the module's data, render, and survive save", async ({ page }) => {
  test.slow(); // a cold `next dev` compiles every viz plus the module page here
  await openStudio(page, VIZ_MODULE);
  for (const [title] of ENTRIES) await addEntry(page, title);

  const ready = page.locator('[data-viz-state="ready"]');
  await expect(ready).toHaveCount(ENTRIES.length);
  await expect(page.locator('[data-viz-state]:not([data-viz-state="ready"])')).toHaveCount(0);
  await expectContent(ready);
  const ids = (await ready.evaluateAll((els) => els.map((e) => e.getAttribute("data-viz-id") ?? ""))).sort();
  expect(ids).toEqual(ENTRIES.map(([, id]) => id).sort());

  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByText("Saved layout.json")).toBeVisible();

  await open(page, `/processes/${logId}/modules/${VIZ_MODULE}`);
  const onPage = page.locator('[data-viz-state="ready"]');
  await expect(onPage).toHaveCount(ENTRIES.length);
  await expectContent(onPage);
  expect((await onPage.evaluateAll((els) => els.map((e) => e.getAttribute("data-viz-id") ?? ""))).sort()).toEqual(ids);
});

test("the scaffolded widget template renders its dataset", async ({ page }) => {
  await openStudio(page, VIZ_MODULE);
  await addEntry(page, "Sample bars");
  await expect(page.locator("[data-grid-id] .recharts-wrapper").first()).toBeVisible();
});

test("an entry without compatible data is a Needs data placeholder", async ({ page }) => {
  await openStudio(page, TABLE_MODULE);
  await addEntry(page, "Petri net");
  const card = page.locator('[data-viz-id="petri-net"][data-viz-state="needs-data"]');
  await expect(card).toBeVisible();
  await expect(card).toContainText("Needs data");
  await expect(card).toContainText("shape: graph");
});

test("switching the visualization in the inspector re-renders the card", async ({ page }) => {
  await openStudio(page, VIZ_MODULE);
  await addEntry(page, "Ranked bars");
  await expect(page.locator('[data-viz-id="bar"][data-viz-state="ready"]')).toBeVisible();
  // Select the card (pointerdown on it), then pick Line chart in the inspector.
  await page.locator("[data-grid-id]").first().click();
  await page.getByRole("combobox").filter({ hasText: /^Bar chart$/ }).click();
  await page.getByRole("option", { name: "Line chart", exact: true }).click();
  const line = page.locator('[data-viz-id="line"][data-viz-state="ready"]');
  await expect(line).toBeVisible();
  await expect(line.locator("svg").first()).toBeVisible();
});

test('searching "matrix" lists the heatmap presets', async ({ page }) => {
  await openStudio(page, VIZ_MODULE);
  await search(page).fill("matrix");
  await expect(page.getByText("Matrices", { exact: true }).first()).toBeVisible();
  await expect(page.getByText(/similarity/i).first()).toBeVisible();
  await expect(page.getByText(/transition/i).first()).toBeVisible();
});
