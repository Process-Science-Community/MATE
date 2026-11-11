import os from "node:os";
import path from "node:path";
import { defineConfig } from "@playwright/test";

/**
 * Browser checks against a running lite stack (`make e2e` starts one).
 * @playwright/test is pinned to 1.58.2, whose Chromium (r1208) is already in
 * the local Playwright cache; elsewhere run `pnpm --filter web exec playwright
 * install chromium` once.
 */
export default defineConfig({
  testDir: "e2e",
  // The specs share one module's layout.json on disk: run them one at a time.
  workers: 1,
  fullyParallel: false,
  // The first visit to each route waits for `next dev` to compile it.
  timeout: 240_000,
  expect: { timeout: 60_000 },
  reporter: "list",
  // Outside the repo, so a run leaves nothing to ignore or commit.
  outputDir: path.join(os.tmpdir(), "mate-e2e-results"),
  use: {
    baseURL: process.env.E2E_WEB_URL ?? "http://localhost:3777",
    contextOptions: { reducedMotion: "reduce" },
    trace: "retain-on-failure",
  },
});
