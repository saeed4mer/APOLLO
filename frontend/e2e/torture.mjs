/**
 * Interaction torture test (real browser, real API, real data).
 *
 *   npm run e2e                      (from frontend/)
 *
 * Starts its own FastAPI server (port 8765) and Vite dev server (port 5174), drives the
 * installed Chrome/Edge via playwright-core (no browser download), and fails on any console
 * error, page error, unhandled rejection, NaN/Infinity, failed assertion, duplicated loop or
 * listener, or displayed value that does not match the API. Writes e2e/artifacts/report.json.
 *
 * Browser selection: CHROME_PATH env var, else the standard Chrome / Edge install locations.
 */
import { spawn } from "node:child_process";
import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";

const FRONTEND = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const REPO = resolve(FRONTEND, "..");
const API_PORT = 8765;
const WEB_PORT = 5174;
const API = `http://127.0.0.1:${API_PORT}`;
const WEB = `http://127.0.0.1:${WEB_PORT}`;
const ARTIFACTS = join(FRONTEND, "e2e", "artifacts");
const TW54 = "3548666";
const ST = "3427460";

const report = { steps: [], dataAccuracy: [], console: [], requests: {}, diagnostics: [], timings: {}, memory: {} };
const failures = [];
const children = [];
/** True only inside steps that deliberately provoke HTTP errors (404 profile, API outage). */
let expectHttpErrors = false;

function step(name, details = {}) {
  report.steps.push({ name, ...details });
  console.log(`  ✓ ${name}${Object.keys(details).length ? " " + JSON.stringify(details) : ""}`);
}
function check(condition, message) {
  if (!condition) {
    failures.push(message);
    console.log(`  ✗ ${message}`);
  }
  return condition;
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function browserPath() {
  const candidates = [
    process.env.CHROME_PATH,
    "C:/Program Files/Google/Chrome/Application/chrome.exe",
    "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    "/usr/bin/google-chrome",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  ].filter(Boolean);
  const found = candidates.find((p) => existsSync(p));
  if (!found) throw new Error("No Chrome/Edge found; set CHROME_PATH");
  return found;
}

function start(name, command, args, options) {
  const child = spawn(command, args, { ...options, stdio: ["ignore", "pipe", "pipe"], windowsHide: true });
  child.stdout.on("data", () => {});
  child.stderr.on("data", () => {});
  child.name = name;
  children.push(child);
  return child;
}
function stop(child) {
  if (!child || child.exitCode !== null) return;
  if (process.platform === "win32") spawn("taskkill", ["/pid", String(child.pid), "/T", "/F"], { windowsHide: true });
  else child.kill("SIGTERM");
}
async function waitForUrl(url, timeoutMs = 60_000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    try {
      const res = await fetch(url);
      if (res.status < 500) return;
    } catch {}
    await sleep(300);
  }
  throw new Error(`Timed out waiting for ${url}`);
}
const startApi = () =>
  start("api", process.env.PYTHON ?? "python", ["-m", "uvicorn", "api.main:app", "--host", "127.0.0.1", "--port", String(API_PORT)], { cwd: REPO });

async function main() {
  mkdirSync(ARTIFACTS, { recursive: true });
  let api = startApi();
  const viteBin = join(FRONTEND, "node_modules", "vite", "bin", "vite.js");
  start("web", process.execPath, [viteBin, "--port", String(WEB_PORT), "--strictPort"], {
    cwd: FRONTEND,
    env: { ...process.env, ASTEROID_API_TARGET: API },
  });
  await waitForUrl(`${API}/health`);
  await waitForUrl(WEB);

  // Ground truth comes straight from the API, never from the UI.
  const worldApi = await (await fetch(`${API}/asteroids/world`)).json();
  const profileApi = await (await fetch(`${API}/asteroids/${TW54}/profile`)).json();

  const browser = await chromium.launch({
    executablePath: browserPath(),
    headless: true,
    args: ["--enable-unsafe-swiftshader", "--use-angle=swiftshader", "--js-flags=--expose-gc"],
  });
  const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
  page.on("console", (msg) => {
    const text = msg.text();
    const expected = expectHttpErrors && msg.type() === "error" && /^Failed to load resource/.test(text);
    report.console.push({ type: msg.type(), text, expected });
    if (msg.type() === "error" && !expected) failures.push(`console error: ${text}`);
    if (/\bNaN\b|\bInfinity\b|Maximum update depth|Too many re-renders|Cannot read properties of undefined/.test(text)) {
      failures.push(`suspicious console output: ${text}`);
    }
  });
  page.on("pageerror", (error) => failures.push(`page error: ${error.message}`));
  page.on("response", (res) => {
    if (res.status() >= 400) report.console.push({ type: "http", text: `${res.status()} ${new URL(res.url()).pathname}`, expected: expectHttpErrors });
  });
  page.on("request", (req) => {
    const path = new URL(req.url()).pathname;
    if (path.startsWith("/api/")) report.requests[path] = (report.requests[path] ?? 0) + 1;
  });

  const dbg = (fn, ...args) => page.evaluate(([f, a]) => window.__ASTEROID_DEBUG__[f](...a), [fn, args]);
  const waitFor = async (predicate, label, timeoutMs = 15_000) => {
    const started = Date.now();
    while (Date.now() - started < timeoutMs) {
      if (await predicate()) return true;
      await sleep(50);
    }
    return check(false, `timed out: ${label}`);
  };
  const diagnostics = async (label) => {
    const d = await dbg("diagnostics");
    report.diagnostics.push({ label, ...d });
    check(d.activeLoops === 1, `${label}: activeLoops=${d.activeLoops}`);
    check(d.inputListeners === 6, `${label}: inputListeners=${d.inputListeners}`);
    check(d.storeListeners === 1, `${label}: storeListeners=${d.storeListeners}`);
    return d;
  };
  const position = async (id) => {
    const p = await dbg("screenPositionOf", id);
    check(p !== null, `no screen position for ${id}`);
    return p;
  };
  const clickAsteroid = async (id) => {
    const p = await position(id);
    await page.mouse.move(p.x, p.y);
    await page.mouse.down();
    await page.mouse.up();
  };
  const heap = () => page.evaluate(() => {
    window.gc?.();
    return performance.memory?.usedJSHeapSize ?? null;
  });
  const fmt = (v, d, unit) => `${new Intl.NumberFormat("en-US", { maximumFractionDigits: d }).format(v)} ${unit}`;

  // 1-3. Open, wait for world, confirm real objects render.
  const t0 = Date.now();
  await page.goto(`${WEB}/#/`);
  await waitFor(async () => (await page.evaluate(() => window.__ASTEROID_DEBUG__?.worldStatus())) === "ready", "world ready");
  report.timings.worldReadyMs = Date.now() - t0;
  const ids = await dbg("worldIds");
  check(ids.length === worldApi.data.length, `rendered ${ids.length} of ${worldApi.data.length} records`);
  check(JSON.stringify([...ids].sort()) === JSON.stringify(worldApi.data.map((r) => r.neows_id).sort()), "rendered IDs equal API IDs");
  check((await page.textContent(".status-overlay")).includes(`LOADED · ${worldApi.data.length} OBJECTS`), "loaded banner shows real count");
  await page.screenshot({ path: join(ARTIFACTS, "01-world.png") });
  const twPos = await position(TW54);
  const shot = await page.screenshot({ clip: { x: twPos.x - 1, y: twPos.y - 1, width: 3, height: 3 } });
  const pixel = await page.evaluate(async (b64) => {
    const img = new Image();
    img.src = `data:image/png;base64,${b64}`;
    await img.decode();
    const c = document.createElement("canvas");
    c.width = img.width;
    c.height = img.height;
    const ctx = c.getContext("2d");
    ctx.drawImage(img, 0, 0);
    return [...ctx.getImageData(1, 1, 1, 1).data];
  }, shot.toString("base64"));
  check(pixel[0] + pixel[1] + pixel[2] > 60, `marker for ${TW54} is drawn at its screen position (rgb ${pixel.slice(0, 3)})`);
  step("world loaded and rendered", { objects: ids.length, ms: report.timings.worldReadyMs, markerPixel: pixel.slice(0, 3) });
  await diagnostics("after load");
  report.memory.heapAfterLoad = await heap();

  // 4-8. Hover, compare to API, move away, hover another.
  await page.mouse.move(twPos.x, twPos.y);
  await waitFor(async () => (await dbg("hoveredId")) === TW54, "hover TW54");
  const tooltip = await page.textContent(".hover-tooltip");
  const apiRec = worldApi.data.find((r) => r.neows_id === TW54);
  const expected = {
    name: apiRec.name,
    neows_id: apiRec.neows_id,
    miss: fmt(apiRec.encounter.miss_distance_km, 0, "km"),
    velocity: fmt(apiRec.encounter.relative_velocity_km_s, 3, "km/s"),
    pha: apiRec.encounter.is_potentially_hazardous === null ? "Unknown" : apiRec.encounter.is_potentially_hazardous ? "Yes" : "No",
    sentry: { available: "Linked Sentry record available" }[apiRec.sentry.status],
  };
  for (const [field, value] of Object.entries(expected)) {
    const ok = check(tooltip.includes(value), `hover ${field}: expected "${value}" in tooltip`);
    report.dataAccuracy.push({ view: "hover", neows_id: TW54, field, api: value, shown: ok });
  }
  step("hover shows API values", { tooltip: tooltip.slice(0, 160) });
  await page.mouse.move(3, 3);
  await waitFor(async () => (await dbg("hoveredId")) === null, "hover cleared");
  check(await page.isHidden(".hover-tooltip"), "tooltip hidden after moving away");
  const stPos = await position(ST);
  await page.mouse.move(stPos.x, stPos.y);
  await waitFor(async () => (await dbg("hoveredId")) === ST, "hover second asteroid");
  step("hover cleared and moved to another asteroid");

  // 9-13. Rapid zoom bursts.
  await page.mouse.move(700, 450);
  for (let round = 0; round < 3; round++) {
    for (let i = 0; i < 25; i++) await page.mouse.wheel(0, -400);
    for (let i = 0; i < 25; i++) await page.mouse.wheel(0, 600);
  }
  for (let i = 0; i < 40; i++) await page.mouse.wheel(0, -50);
  await sleep(1200);
  const zoom = await dbg("zoom");
  check(Number.isFinite(zoom.current) && Number.isFinite(zoom.target), `zoom finite: ${JSON.stringify(zoom)}`);
  check(zoom.current >= zoom.min && zoom.current <= zoom.max, `zoom within bounds: ${JSON.stringify(zoom)}`);
  check(zoom.current === zoom.target, `zoom settled (no runaway): ${JSON.stringify(zoom)}`);
  const scaleText = await page.textContent(".scale-indicator");
  check(/Visualization radius ≈ [\d,.]+ km/.test(scaleText), `scale indicator readable: ${scaleText}`);
  await diagnostics("after zoom bursts");
  step("rapid zoom settled within bounds", { zoom, scaleText });
  await page.screenshot({ path: join(ARTIFACTS, "02-zoomed.png") });
  for (let i = 0; i < 20; i++) await page.mouse.wheel(0, 300); // back out so both asteroids are on screen
  await sleep(800);

  // 14-18. Click A then immediately B: B must win.
  const reqBefore = Object.keys(report.requests).filter((k) => k.endsWith("/profile")).length;
  const a = await position(TW54);
  const b = await position(ST);
  await page.mouse.click(a.x, a.y);
  await page.mouse.click(b.x, b.y);
  await waitFor(async () => (await dbg("profileStatus")) === "ready", "profile ready after A->B");
  check((await dbg("selectedId")) === ST, `B remains selected (got ${await dbg("selectedId")})`);
  const stName = worldApi.data.find((r) => r.neows_id === ST).name;
  check((await page.textContent(".profile-panel h2")) === stName, "panel shows B's name");
  check(page.url().endsWith(`#/asteroid/${ST}`), `URL reflects B: ${page.url()}`);
  step("A->B rapid selection keeps B", { url: page.url() });

  // 19. Back to world.
  await page.click("text=← Back to world");
  await waitFor(async () => (await dbg("selectedId")) === null, "back to world");
  check(await page.isHidden(".profile-panel"), "profile panel hidden after Back");

  // Data accuracy on the full profile of a real linked object.
  await clickAsteroid(TW54);
  await waitFor(async () => (await dbg("profileStatus")) === "ready", "TW54 profile ready");
  const row = async (section, label) =>
    page.evaluate(([s, l]) => {
      for (const r of document.querySelectorAll(`[data-section="${s}"] .fact`)) {
        if (r.querySelector(".fact-label")?.textContent === l) return r.querySelector(".fact-value")?.textContent;
      }
      return null;
    }, [section, label]);
  const p = profileApi.data;
  const profileChecks = [
    ["encounter", "Miss distance", fmt(p.encounter.miss_distance_km, 0, "km")],
    ["encounter", "Relative velocity", fmt(p.encounter.relative_velocity_km_s, 3, "km/s")],
    ["encounter", "Potentially hazardous (NeoWs)", p.encounter.is_potentially_hazardous ? "Yes" : "No"],
    ["encounter", "NeoWs 'Sentry object' flag", p.encounter.is_sentry_object ? "Yes" : "No"],
    ["identity", "NeoWs ID", p.identity.neows_id],
    ["identity", "Sentry ID", p.identity.sentry_id],
    ["orbit", "Semi-major axis a", fmt(p.orbit.semi_major_axis_au, 6, "AU")],
    ["orbit", "Ascending node Ω", fmt(p.orbit.ascending_node_longitude_deg, 4, "deg")],
    ["neows_physical", "Absolute magnitude H", fmt(p.neows_physical.absolute_magnitude_h, 2, "mag")],
    ["physical", "Absolute magnitude H", fmt(p.physical.absolute_magnitude, 2, "mag")],
    ["sentry_assessment", "Cumulative impact probability (as published)", String(p.sentry.assessment.impact_probability)],
    ["sentry_assessment", "Torino scale (max)", String(p.sentry.assessment.torino_scale_max)],
  ];
  for (const [section, label, apiValue] of profileChecks) {
    const shown = await row(section, label);
    check(shown === apiValue, `profile ${section}.${label}: shown "${shown}" vs API "${apiValue}"`);
    report.dataAccuracy.push({ view: "profile", neows_id: TW54, section, field: label, api: apiValue, shown });
  }
  const sentryLine = await page.textContent('[data-section="sentry_assessment"] .sentry-status');
  check(sentryLine === "Linkage: Linked Sentry record available", `Sentry linkage line: ${sentryLine}`);
  const tw = await position(TW54);
  await page.mouse.move(tw.x + 1, tw.y);
  await page.mouse.move(tw.x, tw.y);
  await waitFor(async () => (await dbg("hoveredId")) === TW54, "hover TW54 with profile open");
  const overlap = await page.evaluate(() => {
    const tip = document.querySelector(".hover-tooltip");
    const panel = document.querySelector(".profile-panel");
    if (tip.hidden || panel.hidden) return null;
    return { tipRight: tip.getBoundingClientRect().right, panelLeft: panel.getBoundingClientRect().left };
  });
  check(overlap !== null && overlap.tipRight <= overlap.panelLeft, `tooltip never covers the open profile: ${JSON.stringify(overlap)}`);
  await page.screenshot({ path: join(ARTIFACTS, "03-profile.png") });
  step("profile values match API", { checked: profileChecks.length });

  // 20-21. Select an unresolved object; nothing is inferred.
  await page.click("text=← Back to world");
  await waitFor(async () => (await dbg("selectedId")) === null, "back");
  const unresolved = worldApi.data.find((r) => r.resolution.match_state === "UNRESOLVED" && r.encounter.is_potentially_hazardous === true)
    ?? worldApi.data.find((r) => r.resolution.match_state === "UNRESOLVED");
  await clickAsteroid(unresolved.neows_id);
  await waitFor(async () => (await dbg("profileStatus")) === "ready", "unresolved profile ready");
  check((await row("orbit", "Semi-major axis a")) === "Unavailable", "unresolved orbit reads Unavailable");
  check((await page.textContent('[data-section="sentry_assessment"] .sentry-status')) === "Linkage: Not linkable: identity not resolved",
    `PHA=${unresolved.encounter.is_potentially_hazardous} object without linkage shows no Sentry assessment`);
  step("unresolved profile shows Unavailable, no inferred Sentry", { neows_id: unresolved.neows_id, pha: unresolved.encounter.is_potentially_hazardous });

  // Open/close 12 times; loops/listeners must not grow.
  for (let i = 0; i < 12; i++) {
    await page.click("text=← Back to world");
    await waitFor(async () => (await dbg("selectedId")) === null, `cycle ${i} back`);
    await clickAsteroid(i % 2 ? TW54 : ST);
    await waitFor(async () => (await dbg("profileStatus")) === "ready", `cycle ${i} profile`);
  }
  await diagnostics("after 12 open/close cycles");
  report.memory.heapAfterCycles = await heap();
  step("12 open/close cycles stable");

  // Browser Back button returns to the world.
  await page.goBack();
  await waitFor(async () => (await dbg("selectedId")) !== TW54 && (await dbg("selectedId")) !== ST || (await dbg("selectedId")) === null, "browser back");
  step("browser back navigates", { url: page.url() });

  // Resize repeatedly.
  for (const [w, h] of [[900, 600], [1600, 1000], [700, 900], [1400, 900], [500, 500], [1400, 900]]) {
    await page.setViewportSize({ width: w, height: h });
    await sleep(150);
  }
  await diagnostics("after resizes");
  const posAfterResize = await position(TW54);
  check(posAfterResize.x > 0 && posAfterResize.x < 1400 && posAfterResize.y > 0 && posAfterResize.y < 900, "marker on screen after resizes");
  step("resizes handled");

  // 22-23. Refresh with a selection in the URL, then refresh repeatedly while loading.
  await page.goto(`${WEB}/#/asteroid/${TW54}`);
  await page.reload();
  await waitFor(async () => (await page.evaluate(() => window.__ASTEROID_DEBUG__?.profileStatus())) === "ready", "profile restored after refresh");
  check((await dbg("selectedId")) === TW54, "selection restored from URL after refresh");
  for (let i = 0; i < 4; i++) {
    await page.reload({ waitUntil: "commit" });
    await sleep(60);
  }
  await waitFor(async () => (await page.evaluate(() => window.__ASTEROID_DEBUG__?.worldStatus())) === "ready", "world after rapid reloads");
  await diagnostics("after rapid reloads");
  step("refresh restores state; rapid reloads clean");

  // 404 profile (the browser logs the 404 resource load; expected here only).
  expectHttpErrors = true;
  await page.goto(`${WEB}/#/asteroid/99999999`);
  await waitFor(async () => (await page.evaluate(() => window.__ASTEROID_DEBUG__?.profileStatus())) === "error", "404 profile");
  check((await page.textContent(".profile-panel .status-title")) === "Asteroid not found", "404 reads 'Asteroid not found'");
  step("unknown asteroid shows not-found");

  // API outage and recovery.
  stop(api);
  await sleep(1500);
  await page.goto(`${WEB}/#/`);
  await page.reload();
  await waitFor(async () => (await page.evaluate(() => window.__ASTEROID_DEBUG__?.worldStatus())) === "error", "world error during outage", 30_000);
  const outage = await page.textContent(".status-overlay");
  check(outage.includes("ASTEROID INTELLIGENCE UNAVAILABLE") && outage.includes("could not be reached"), `outage message: ${outage}`);
  await page.screenshot({ path: join(ARTIFACTS, "04-outage.png") });
  api = startApi();
  await waitForUrl(`${API}/health`);
  await page.click(".status-overlay button");
  await waitFor(async () => (await dbg("worldStatus")) === "ready", "world after recovery");
  await diagnostics("after outage recovery");
  expectHttpErrors = false;
  step("API outage shown; Retry recovers", { outageText: outage });

  report.requests.final = { ...report.requests };
  const worldRequests = report.requests["/api/asteroids/world"] ?? 0;
  const profileRequests = Object.entries(report.requests).filter(([k]) => k.endsWith("/profile")).reduce((n, [, v]) => n + v, 0);
  report.requests.summary = { worldRequests, profileRequests, profileRequestsBeforeFirstClick: reqBefore };
  check(reqBefore === 0, "no profile requests before the first selection");
  await browser.close();
}

main()
  .catch((error) => failures.push(`aborted: ${error.stack ?? error}`))
  .finally(() => {
    for (const child of children) stop(child);
    report.failures = failures;
    mkdirSync(ARTIFACTS, { recursive: true });
    writeFileSync(join(ARTIFACTS, "report.json"), JSON.stringify(report, null, 2));
    const errors = report.console.filter((c) => c.type === "error").length;
    console.log(`\n${failures.length === 0 ? "PASS" : "FAIL"}: ${report.steps.length} steps, ${report.dataAccuracy.length} value checks, ${errors} console errors, ${failures.length} failures`);
    for (const f of failures) console.log(`  - ${f}`);
    setTimeout(() => process.exit(failures.length === 0 ? 0 : 1), 500);
  });
