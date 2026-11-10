#!/usr/bin/env node
/**
 * Bundle every `modules/<id>/panel/index.{ts,tsx}` (and declared widgets)
 * into `modules/<id>/.dist/panel.js` as a CJS module with all runtime
 * externals preserved as `require(...)` calls. The frontend's module loader
 * intercepts each require() and resolves it against `window.__FF_RUNTIME__`
 * (see `apps/web/lib/module-panels.tsx`).
 *
 * Usage:
 *   node scripts/bundle-modules.mjs            # build once for all modules
 *   node scripts/bundle-modules.mjs --watch    # watch each panel + widget tree
 *   node scripts/bundle-modules.mjs <id> ...   # build only the listed module ids
 *
 * `MODULES_ONLY=a,b` (same env the API reads) limits the set to those ids plus
 * their hard `requirements.modules`, transitively. Explicit ids on the command
 * line win over it. In `--watch`, the modules dir is rescanned every 2s so a
 * module folder (or a panel/widget entry) created after start gets bundled too.
 */
import { build, context } from "esbuild";
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { parse as parseYaml } from "yaml";

const __dirname = dirname(fileURLToPath(import.meta.url));
const WEB_ROOT = resolve(__dirname, "..");
const REPO_ROOT = resolve(WEB_ROOT, "..", "..");

// Modules dir + runtime externals are env-overridable so the script works
// both from the repo root (dev – `pnpm dev`) and from a flat install dir
// inside the production Docker image (the runner stage drops the script at
// /opt/ff-bundler/ alongside its own node_modules, with no apps/web parent).
const MODULES_DIR = process.env.FF_MODULES_DIR
  ? resolve(process.env.FF_MODULES_DIR)
  : resolve(REPO_ROOT, "modules");

function resolveExternalsPath() {
  if (process.env.FF_RUNTIME_EXTERNALS) return resolve(process.env.FF_RUNTIME_EXTERNALS);
  const adjacent = resolve(__dirname, "runtime-externals.json");
  if (existsSync(adjacent)) return adjacent;
  return resolve(WEB_ROOT, "lib", "runtime-externals.json");
}

const RUNTIME_EXTERNALS = JSON.parse(readFileSync(resolveExternalsPath(), "utf8"));

// esbuild resolves a bare import (e.g. `bpmn-js`, bundled and intentionally NOT
// externalised) by walking up the directory tree from the importing module
// source. In the production image the module tree (/app/modules) and the
// bundler's own deps (/opt/ff-bundler/node_modules) live under separate roots,
// so that walk never reaches the bundler's deps. Mirror NODE_PATH (which
// esbuild ignores from the environment by design) via the nodePaths option:
// list the bundler's node_modules as a resolve fallback. Only consulted when
// normal resolution fails, so it never perturbs the dev tree-walk.
const NODE_PATHS = [
  resolve(__dirname, "node_modules"),
  resolve(WEB_ROOT, "node_modules"),
].filter((p) => existsSync(p));

const argv = process.argv.slice(2);
const WATCH = argv.includes("--watch");
const CLI_IDS = new Set(argv.filter((a) => !a.startsWith("--")));
const ENV_IDS = (process.env.MODULES_ONLY ?? "")
  .split(",")
  .map((s) => s.trim())
  .filter(Boolean);
const RESCAN_MS = 2000;

/** Ids to bundle (empty set = all). Recomputed on each watch rescan so a
 * MODULES_ONLY id whose folder appears later is picked up. */
function onlyIds(folders) {
  if (CLI_IDS.size || !ENV_IDS.length) return CLI_IDS;
  const deps = new Map();
  for (const folder of folders) {
    try {
      const m = readManifest(folder);
      deps.set(m.id, m.requirements?.modules ?? []);
    } catch {
      // Unreadable manifest (mid-write): bundleModule reports it.
    }
  }
  const out = new Set();
  const stack = [...ENV_IDS];
  while (stack.length) {
    const id = stack.pop();
    if (out.has(id)) continue;
    out.add(id);
    stack.push(...(deps.get(id) ?? []));
  }
  return out;
}

function* iterModuleFolders() {
  if (!existsSync(MODULES_DIR)) return;
  for (const name of readdirSync(MODULES_DIR)) {
    if (name.startsWith(".") || name.startsWith("_")) continue;
    const folder = join(MODULES_DIR, name);
    const stat = statSync(folder, { throwIfNoEntry: false });
    if (!stat?.isDirectory()) continue;
    if (!existsSync(join(folder, "manifest.yaml"))) continue;
    yield folder;
  }
}

function readManifest(folder) {
  const text = readFileSync(join(folder, "manifest.yaml"), "utf8");
  return parseYaml(text);
}

function entriesForModule(folder, manifest) {
  // The platform's manifest schema (§5.6) declares:
  //   frontend:
  //     panel: ./panel/index.tsx
  //     widgets:
  //       - id: throughput-chart
  //         entry: ./widgets/Throughput.tsx
  //         settings_entry: ./widgets/ThroughputSettings.tsx
  const out = [];
  const fe = manifest.frontend ?? {};
  if (typeof fe.panel === "string") {
    out.push({ name: "panel", path: resolve(folder, fe.panel) });
  }
  if (Array.isArray(fe.widgets)) {
    for (const w of fe.widgets) {
      if (!w || typeof w.id !== "string") continue;
      if (typeof w.entry === "string") {
        out.push({ name: `widget-${w.id}`, path: resolve(folder, w.entry) });
      }
      // A widget whose controls can't be expressed as JSON Schema ships its own
      // settings component. Bundled separately so the card's settings panel is
      // only fetched when the user actually opens it — the naming is the
      // contract (`assets/widget-<id>-settings.js`), which is why the card
      // catalog only needs to expose a boolean.
      if (typeof w.settings_entry === "string") {
        out.push({
          name: `widget-${w.id}-settings`,
          path: resolve(folder, w.settings_entry),
        });
      }
    }
  }
  return out.filter((e) => existsSync(e.path));
}

/** Aliases mirror apps/web/tsconfig.json – module source can use `@/...`. */
const ALIAS = {
  "@/": resolve(WEB_ROOT) + "/",
};

const aliasPlugin = {
  name: "ff-alias",
  setup(b) {
    for (const [prefix, target] of Object.entries(ALIAS)) {
      b.onResolve({ filter: new RegExp(`^${prefix.replace(/[/]/g, "\\/")}`) }, (args) => {
        // Workspace alias hits – but if the requested path matches one of
        // the runtime externals, we let esbuild's `external` list handle it
        // (which it will, since externals are matched before plugins).
        if (RUNTIME_EXTERNALS.includes(args.path)) {
          return { path: args.path, external: true };
        }
        return { path: resolve(target, args.path.slice(prefix.length)) };
      });
    }
  },
};

function commonOptions(entry, outdir) {
  return {
    entryPoints: [{ in: entry.path, out: entry.name }],
    outdir,
    bundle: true,
    format: "cjs",
    platform: "browser",
    target: "es2022",
    jsx: "automatic",
    jsxImportSource: "react",
    // Inline maps only while watching (dev DX). One-shot builds ship a linked
    // panel.js.map instead – inline maps double-to-triple panel.js (discovery:
    // 2.3MB inline vs ~0.7MB linked) and every user downloads them.
    sourcemap: WATCH ? "inline" : "linked",
    legalComments: "none",
    logLevel: WATCH ? "info" : "warning",
    // Runtime externals stay as require() calls; the frontend loader resolves
    // them against window.__FF_RUNTIME__.
    external: RUNTIME_EXTERNALS,
    // Fallback resolve roots for bundled (non-externalised) npm deps – see
    // NODE_PATHS above. Empty in dev (normal resolution already succeeds).
    nodePaths: NODE_PATHS,
    plugins: [aliasPlugin],
    define: {
      "process.env.NODE_ENV": JSON.stringify(process.env.NODE_ENV || "development"),
    },
    loader: { ".js": "jsx", ".ts": "ts", ".tsx": "tsx" },
  };
}

/**
 * Canvas contract check (warn only – see `modules/README.md` § Canvases).
 *
 * Every graph/diagram view goes through `CanvasShell` (or `CanvasControlCluster`
 * for a non-React-Flow viewer) so the zoom / fit / fullscreen / settings / reset
 * cluster is identical everywhere. A panel that mounts `<ReactFlow>` itself, or
 * hand-rolls the control pill, silently drifts – flag it at build time instead
 * of finding out in review.
 */
function checkCanvasContract(moduleId, folder) {
  const files = [];
  const walk = (dir) => {
    for (const name of readdirSync(dir)) {
      if (name === "node_modules" || name.startsWith(".")) continue;
      const p = join(dir, name);
      if (statSync(p).isDirectory()) walk(p);
      else if (/\.(tsx|jsx)$/.test(name)) files.push(p);
    }
  };
  for (const sub of ["panel", "widgets", "frontend"]) {
    const dir = join(folder, sub);
    if (existsSync(dir)) walk(dir);
  }
  for (const file of files) {
    const src = readFileSync(file, "utf8");
    const where = relative(REPO_ROOT, file);
    const warn = (msg) =>
      console.warn(`[bundle-modules] ${moduleId}: ${where}: ${msg} (modules/README.md § Canvases)`);
    if (/<ReactFlow[\s>]/.test(src)) {
      warn("mounts <ReactFlow> directly – render through CanvasShell instead");
    }
    // Own zoom/fit buttons are the tell-tale of a re-implemented control pill.
    const rollsOwnZoom = /Zoom in/.test(src) && /Zoom out/.test(src);
    const usesShared = /canvases\/shared\/canvas-(shell|toolbar)/.test(src);
    if (rollsOwnZoom && !usesShared) {
      warn("hand-rolls zoom/fit buttons – use CanvasControlCluster");
    }
  }
}

/** Watch contexts already running, keyed by output path (watch mode only). */
const started = new Set();

async function bundleModule(folder, onlyIdSet, rescan = false) {
  const manifest = readManifest(folder);
  const moduleId = manifest.id;
  if (onlyIdSet.size && !onlyIdSet.has(moduleId)) return [];
  const outdir = join(folder, ".dist");
  const all = entriesForModule(folder, manifest);
  const entries = all.filter((e) => !started.has(join(outdir, e.name)));
  if (!entries.length) {
    if (!all.length && manifest.frontend && !rescan) {
      console.warn(`[bundle-modules] ${moduleId}: manifest declares frontend but no entry found.`);
    }
    return [];
  }
  const tasks = [];
  checkCanvasContract(moduleId, folder);
  for (const entry of entries) {
    const opts = commonOptions(entry, outdir);
    if (WATCH) {
      const ctx = await context(opts);
      await ctx.watch();
      started.add(join(outdir, entry.name));
      tasks.push(ctx);
      console.log(`[bundle-modules] watch ${moduleId}/${entry.name}`);
    } else {
      await build(opts);
      const rel = relative(REPO_ROOT, join(outdir, `${entry.name}.js`));
      console.log(`[bundle-modules] built ${moduleId}/${entry.name} -> ${rel}`);
    }
  }
  return tasks;
}

async function main() {
  const folders = [...iterModuleFolders()];
  if (!WATCH) {
    // Surface the resolve fallbacks + whether the bundled (non-externalised)
    // bpmn deps are actually present in this image. If bpmn-js shows NOT FOUND
    // here, the runner image was built without them (stale/cached build) – the
    // discovery panel will then fail to bundle.
    const bpmnProbe = NODE_PATHS.map((p) => join(p, "bpmn-js")).find((p) => existsSync(p));
    console.log(
      `[bundle-modules] nodePaths=${JSON.stringify(NODE_PATHS)} ` +
        `bpmn-js=${bpmnProbe ?? "NOT FOUND – discovery panel will fail to bundle"}`,
    );
  }
  const ids = onlyIds(folders);
  if (ids.size) console.log(`[bundle-modules] only: ${[...ids].sort().join(", ")}`);
  for (const folder of folders) {
    try {
      await bundleModule(folder, ids);
    } catch (err) {
      console.error(`[bundle-modules] failed for ${folder}:`, err);
      if (!WATCH) process.exitCode = 1;
    }
  }
  if (!WATCH) return;
  // Rescan for module folders / entries created after start. Errors are logged
  // once per folder and message, not every tick (a manifest mid-write fails
  // until it is saved). The ref'd interval also keeps Node alive: a
  // never-resolving promise alone does NOT – Node 22 exits with code 13
  // ("unsettled top-level await") once no active handles remain.
  const lastError = new Map();
  let scanning = false;
  setInterval(async () => {
    if (scanning) return;
    scanning = true;
    try {
      const current = [...iterModuleFolders()];
      const currentIds = onlyIds(current);
      for (const folder of current) {
        try {
          await bundleModule(folder, currentIds, true);
          lastError.delete(folder);
        } catch (err) {
          const msg = String(err?.message ?? err);
          if (lastError.get(folder) !== msg) console.error(`[bundle-modules] failed for ${folder}:`, msg);
          lastError.set(folder, msg);
        }
      }
    } finally {
      scanning = false;
    }
  }, RESCAN_MS);
  await new Promise(() => {});
}

await main();
