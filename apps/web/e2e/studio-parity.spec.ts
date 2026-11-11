import { expect, test, type Page } from "@playwright/test";

/**
 * The Studio must show a module's page 1:1: Preview renders the same tree at the
 * same place, Edit renders the same grid scaled by `data-zoom`. Needs a lite
 * stack with the scaffolded module loaded (`make e2e` sets that up).
 */

const API = process.env.E2E_API_URL ?? "http://127.0.0.1:8777";
const MODULE = process.env.E2E_MODULE ?? "e2e_studio";
const AUTH = { Authorization: "Bearer demo-access-token" }; // DEMO_ACCESS_TOKEN of the lite API

const viz = (i: string, x: number, y: number, w: number, h: number, tab?: string) => ({
  i,
  kind: "viz",
  module_id: MODULE,
  dataset_ref: { module_id: MODULE, dataset_id: "summary" },
  viz_id: "table",
  mapping: {},
  title: `Card ${i}`,
  x,
  y,
  w,
  h,
  config: {},
  ...(tab ? { tab } : {}),
});

const LAYOUT = {
  version: 1,
  tabs: [
    { id: "overview", title: "Overview" },
    { id: "details", title: "Details" },
  ],
  items: [
    { i: "intro", kind: "text", x: 0, y: 0, w: 12, h: 3, config: { markdown: "## Parity intro\nFixed E2E layout." } },
    viz("a", 0, 3, 12, 8),
    viz("b", 0, 11, 6, 10),
    viz("c", 6, 11, 6, 10),
    viz("d", 0, 0, 4, 6, "details"),
  ],
};
const FIRST_TAB_CARDS = 4;

let logId = "";

async function api(path: string, init: RequestInit = {}) {
  const res = await fetch(`${API}${path}`, {
    ...init,
    headers: { ...AUTH, "Content-Type": "application/json", ...init.headers },
  });
  if (!res.ok) throw new Error(`${init.method ?? "GET"} ${path}: ${res.status} ${await res.text()}`);
  return res.json();
}

test.beforeAll(async () => {
  // lite.sh seeds a sample log on a fresh data dir; wait until it is ingested.
  await expect
    .poll(
      async () => {
        const logs: { id: string; status: string }[] = await api("/api/v1/event-logs");
        logId = logs.find((l) => l.status === "ready")?.id ?? "";
        return logId;
      },
      { timeout: 180_000, intervals: [2_000] },
    )
    .not.toBe("");
});

test.beforeEach(async () => {
  await api(`/api/v1/dev/modules/${MODULE}/layout`, { method: "PUT", body: JSON.stringify(LAYOUT) });
});

type Rect = { x: number; y: number; w: number; h: number; gx: number; gy: number };
type Snapshot = { zoom: number; cards: Record<string, Rect> };

/** Card rects, absolute and relative to the grid origin, once two reads agree
 * (the zoom, fonts and RGL positions settle over a few frames). */
async function snapshot(page: Page, cards: number): Promise<Snapshot> {
  await expect(page.locator("[data-grid-id]")).toHaveCount(cards);
  let prev = "";
  for (let i = 0; i < 40; i++) {
    const snap: Snapshot = await page.evaluate(() => {
      const grid = document.querySelector(".react-grid-layout")!.getBoundingClientRect();
      const stage = document.querySelector<HTMLElement>("[data-studio-stage]");
      const cards: Record<string, Rect> = {};
      for (const el of document.querySelectorAll<HTMLElement>("[data-grid-id]")) {
        const r = el.getBoundingClientRect();
        cards[el.dataset.gridId!] = { x: r.x, y: r.y, w: r.width, h: r.height, gx: r.x - grid.x, gy: r.y - grid.y };
      }
      return { zoom: Number(stage?.dataset.zoom ?? 1), cards };
    });
    const json = JSON.stringify(snap);
    if (json === prev) return snap;
    prev = json;
    await page.waitForTimeout(250);
  }
  throw new Error("card positions never settled");
}

/** Go to `path` through the demo login. The login signs in on its own and returns
 * to the callback URL, but an occasional auth.js MissingCSRF lands on /processes
 * instead, so retry once from the then-signed-in session. */
async function open(page: Page, path: string) {
  const target = path.split("?")[0];
  for (let i = 0; i < 2; i++) {
    await page.goto(path);
    await page.waitForURL((u) => !u.pathname.startsWith("/login"));
    if (new URL(page.url()).pathname === target) return;
  }
  throw new Error(`could not open ${path}, ended on ${page.url()}`);
}

async function openModulePage(page: Page) {
  await open(page, `/processes/${logId}/modules/${MODULE}`);
  return snapshot(page, FIRST_TAB_CARDS);
}

const tabLabels = (page: Page) => page.getByRole("tab").allTextContents();
const filterBars = (page: Page) => page.getByRole("button", { name: "Add filter" }).count();

for (const [width, height] of [
  [1600, 1000],
  [1280, 800],
] as const) {
  test.describe(`${width}x${height}`, () => {
    test.use({ viewport: { width, height } });

    test("preview renders the module page 1:1", async ({ page }) => {
      const ref = await openModulePage(page);
      const refTabs = await tabLabels(page);
      const refFilter = await filterBars(page);
      expect(refTabs).toEqual(["Overview", "Details"]);
      expect(refFilter).toBe(1);

      await open(page, `/dev/studio/${MODULE}?log=${logId}&mode=preview`);
      const got = await snapshot(page, FIRST_TAB_CARDS);
      expect(await tabLabels(page)).toEqual(refTabs);
      expect(await filterBars(page)).toBe(refFilter);
      for (const [id, r] of Object.entries(ref.cards)) {
        const g = got.cards[id];
        for (const k of ["x", "y", "w", "h"] as const) {
          expect(Math.abs(g[k] - r[k]), `${id}.${k}: studio ${g[k]} vs page ${r[k]}`).toBeLessThanOrEqual(1);
        }
      }
    });

    test("edit mode is the module page grid, scaled", async ({ page }) => {
      const ref = await openModulePage(page);

      await open(page, `/dev/studio/${MODULE}?log=${logId}`);
      await expect(page.locator("[data-studio-stage]")).toBeVisible();
      const got = await snapshot(page, FIRST_TAB_CARDS);
      // Editable, not the read-only stacked column.
      await expect(page.locator(".react-grid-layout")).toHaveCount(1);
      await expect(page.locator(".dashboard-drag-handle")).toHaveCount(FIRST_TAB_CARDS);
      expect(got.zoom).toBeGreaterThan(0);
      expect(got.zoom).toBeLessThanOrEqual(1);
      for (const [id, r] of Object.entries(ref.cards)) {
        const g = got.cards[id];
        const scaled = { gx: g.gx / got.zoom, gy: g.gy / got.zoom, w: g.w / got.zoom, h: g.h / got.zoom };
        for (const k of ["gx", "gy", "w", "h"] as const) {
          expect(Math.abs(scaled[k] - r[k]), `${id}.${k}: studio ${scaled[k]} vs page ${r[k]}`).toBeLessThanOrEqual(2);
        }
      }
    });

    test("a text element added in the Studio shows on the module page", async ({ page }) => {
      await open(page, `/dev/studio/${MODULE}?log=${logId}`);
      await snapshot(page, FIRST_TAB_CARDS);
      await page.getByText("Heading and markdown copy.").click();
      await expect(page.locator("[data-grid-id]")).toHaveCount(FIRST_TAB_CARDS + 1);
      await page.getByRole("button", { name: "Save", exact: true }).click();
      await expect(page.getByText("Saved layout.json")).toBeVisible();

      await open(page, `/processes/${logId}/modules/${MODULE}`);
      await expect(page.getByText("Describe what this page shows.")).toBeVisible();
    });
  });
}
