/**
 * Interaction torture test (real browser, real API, real data) for the M7.2 immersive world.
 *
 *   npm run e2e                      (from frontend/)
 *
 * Starts its own FastAPI server (port 8765) and Vite dev server (port 5174), drives the installed
 * Chrome/Edge via playwright-core (no browser download), and fails on any unexpected console error,
 * page error, NaN/Infinity, failed assertion, duplicated loop/listener, broken distance ordering,
 * restarted fall, or displayed value that does not match the API. Screenshots of the defined
 * progression states and report.json are written to e2e/artifacts/ (git-ignored).
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
const VIEW = { width: 1400, height: 860 };
const TW54 = "3548666";
const ST = "3427460";

const report = { steps: [], dataAccuracy: [], console: [], requests: {}, diagnostics: [], states: [], performance: {}, memory: {} };
const failures = [];
const children = [];
/** True only inside steps that deliberately provoke HTTP errors (404 asteroid, API outage). */
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

function start(command, args, options) {
  const child = spawn(command, args, { ...options, stdio: ["ignore", "pipe", "pipe"], windowsHide: true });
  child.stdout.on("data", () => {});
  child.stderr.on("data", () => {});
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
  start(process.env.PYTHON ?? "python", ["-m", "uvicorn", "api.main:app", "--host", "127.0.0.1", "--port", String(API_PORT)], { cwd: REPO });

const rgbOf = (css, which) => {
  const colors = [...css.matchAll(/rgb\((\d+), (\d+), (\d+)\)/g)].map((m) => m.slice(1, 4).map(Number));
  return which === "zenith" ? colors.at(-1) : colors[0];
};
const luminance = ([r, g, b]) => 0.2126 * r + 0.7152 * g + 0.0722 * b;

async function main() {
  mkdirSync(ARTIFACTS, { recursive: true });
  let api = startApi();
  const viteBin = join(FRONTEND, "node_modules", "vite", "bin", "vite.js");
  start(process.execPath, [viteBin, "--port", String(WEB_PORT), "--strictPort"], { cwd: FRONTEND, env: { ...process.env, ASTEROID_API_TARGET: API } });
  await waitForUrl(`${API}/health`);
  await waitForUrl(WEB);

  // Ground truth straight from the API, never from the UI.
  const worldApi = await (await fetch(`${API}/asteroids/world`)).json();
  const apiById = new Map(worldApi.data.map((r) => [r.neows_id, r]));
  const profiles = {};
  for (const id of [TW54, ST, "3830890"]) profiles[id] = (await (await fetch(`${API}/asteroids/${id}/profile`)).json()).data;

  const browser = await chromium.launch({
    executablePath: browserPath(),
    headless: true,
    args: ["--enable-unsafe-swiftshader", "--use-angle=swiftshader", "--js-flags=--expose-gc"],
  });
  const page = await browser.newPage({ viewport: VIEW });
  page.on("console", (msg) => {
    const text = msg.text();
    const expected = expectHttpErrors && msg.type() === "error" && /^Failed to load resource/.test(text);
    report.console.push({ type: msg.type(), text, expected });
    if (msg.type() === "error" && !expected) failures.push(`console error: ${text}`);
    if (/\bNaN\b|\bInfinity\b|Maximum update depth|Cannot read properties of undefined/.test(text)) failures.push(`suspicious console output: ${text}`);
  });
  page.on("pageerror", (error) => failures.push(`page error: ${error.message}`));
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
    check(d.activeLoops === 1 && d.inputListeners === 6 && d.storeListeners === 1 && d.keyListeners === 1, `${label}: diagnostics ${JSON.stringify(d)}`);
  };
  const phases = async () => Object.fromEntries(await Promise.all(worldApi.data.map(async (r) => [r.neows_id, await dbg("phaseOf", r.neows_id)])));
  const counts = (ph) => Object.values(ph).reduce((acc, p) => ({ ...acc, [p]: (acc[p] ?? 0) + 1 }), {});
  const waitSettled = (label) => waitFor(async () => !Object.values(await phases()).includes("FALLING"), `${label}: falls settle`, 10_000);
  const pixelAt = async (x, y) => {
    const shot = await page.screenshot({ clip: { x: Math.round(x) - 1, y: Math.round(y) - 1, width: 3, height: 3 } });
    return page.evaluate(async (b64) => {
      const img = new Image();
      img.src = `data:image/png;base64,${b64}`;
      await img.decode();
      const c = document.createElement("canvas");
      c.width = img.width;
      c.height = img.height;
      const ctx = c.getContext("2d");
      ctx.drawImage(img, 0, 0);
      return [...ctx.getImageData(1, 1, 1, 1).data].slice(0, 3);
    }, shot.toString("base64"));
  };
  const scrollTo = async (target) => {
    await page.mouse.move(VIEW.width / 2, VIEW.height * 0.45);
    for (let i = 0; i < 80; i++) {
      const p = (await dbg("progress")).target;
      if (Math.abs(p - target) < 0.03) break;
      await page.mouse.wheel(0, p < target ? 120 : -120);
    }
    await waitFor(async () => {
      const p = await dbg("progress");
      return p.current === p.target;
    }, `progress settles near ${target}`);
  };
  const captureState = async (name, label) => {
    const p = await dbg("progress");
    const ph = counts(await phases());
    const sky = await dbg("skyBackground");
    report.states.push({ name, label, progress: p.current, phases: ph, zenith: rgbOf(sky, "zenith") });
    await page.screenshot({ path: join(ARTIFACTS, `${name}.png`) });
    return { p, ph, zenith: rgbOf(sky, "zenith") };
  };
  const fmt = (v, d, unit) => `${new Intl.NumberFormat("en-US", { maximumFractionDigits: d }).format(v)} ${unit}`;
  const heap = () => page.evaluate(() => {
    window.gc?.();
    return performance.memory?.usedJSHeapSize ?? null;
  });

  // ── Composition: Earth below, sky above, tiny world, a few distant hints ─────────────────
  await page.goto(`${WEB}/#/`);
  await waitFor(async () => (await page.evaluate(() => window.__ASTEROID_DEBUG__?.worldStatus())) === "ready", "world ready");
  const initialPhases = counts(await phases());
  const revealedAtLoad = worldApi.data.length - (initialPhases.HIDDEN ?? 0);
  check(revealedAtLoad >= 1 && revealedAtLoad < worldApi.data.length / 4, `a few distant hints at load: ${JSON.stringify(initialPhases)}`);
  check((initialPhases.FALLING ?? 0) > 0, "asteroids fall in at load");
  await waitSettled("load");
  const crest = await dbg("earthCrestY");
  check(Math.abs(crest / VIEW.height - 0.3) < 0.01, `Earth crest in the lower third: ${crest}`);
  const earth = await dbg("earthCounts");
  check(earth && earth.trees > 0 && earth.houses > 0 && earth.people > 0 && earth.lakes > 0, `tiny world exists: ${JSON.stringify(earth)}`);
  const grass = await pixelAt(VIEW.width / 2, VIEW.height - crest + 20);
  check(grass[1] > grass[0] && grass[1] > grass[2], `green Earth below the crest: rgb ${grass}`);
  const sky0 = await captureState("state0-earth-sky", "Initial Earth + sky");
  check(sky0.zenith && sky0.zenith[2] > sky0.zenith[0] && luminance(sky0.zenith) > 120, `initial background is sky blue: ${sky0.zenith}`);
  const settledNow = await phases();
  const settledIds = Object.keys(settledNow).filter((id) => settledNow[id] === "SETTLED");
  const positions = await Promise.all(settledIds.map(async (id) => [id, await dbg("screenPositionOf", id)]));
  const isolated = positions.find(([, a]) => positions.every(([, b]) => a === b || Math.hypot(a.x - b.x, a.y - b.y) > 40));
  const firstId = (isolated ?? positions[0])[0];
  const rockPos = await dbg("screenPositionOf", firstId);
  const rock = await pixelAt(rockPos.x, rockPos.y);
  check(rock[0] > rock[2], `rock drawn at its position (not sky): rgb ${rock}`);
  check(await page.isVisible(".intro"), "title and scroll hint visible");
  step("composition: Earth arc, sky, tiny world, distant hints", { revealedAtLoad, crest, earth, rock });
  await diagnostics("after load");
  report.memory.heapAfterLoad = await heap();

  // ── Hover: lightweight facts from loaded world data, checked against the API ────────────
  await page.mouse.move(rockPos.x, rockPos.y);
  await waitFor(async () => (await dbg("hoveredId")) === firstId, "hover");
  const tip = await page.textContent(".hover-tooltip");
  const hov = apiById.get(firstId);
  for (const [field, value] of Object.entries({
    name: hov.name, neows_id: hov.neows_id, miss: fmt(hov.encounter.miss_distance_km, 0, "km"),
    pha: hov.encounter.is_potentially_hazardous === null ? "Unknown" : hov.encounter.is_potentially_hazardous ? "Yes" : "No",
  })) {
    const ok = check(tip.includes(value), `hover ${field} "${value}"`);
    report.dataAccuracy.push({ view: "hover", neows_id: firstId, field, api: value, shown: ok });
  }
  await page.mouse.move(3, 3);
  await waitFor(async () => (await dbg("hoveredId")) === null, "hover cleared");
  step("hover shows API facts and clears");

  // ── Scroll journey: progressive reveal + continuous sky -> space ────────────────────────
  let lastRevealed = revealedAtLoad;
  let lastLum = luminance(sky0.zenith);
  for (const [target, name, label] of [[0.2, "state1-early-reveal", "Early asteroid appearance"], [0.45, "state2-intermediate", "Intermediate field"], [0.7, "state3-space-transition", "Sky/space transition"], [1, "state4-deep", "Deep asteroid environment"]]) {
    await scrollTo(target);
    await waitSettled(name);
    const s = await captureState(name, label);
    const revealed = worldApi.data.length - (s.ph.HIDDEN ?? 0);
    check(revealed >= lastRevealed, `${name}: reveal is monotonic (${lastRevealed} -> ${revealed})`);
    check(luminance(s.zenith) < lastLum, `${name}: sky darkens toward space (${lastLum.toFixed(1)} -> ${luminance(s.zenith).toFixed(1)})`);
    check(s.p.current >= 0 && s.p.current <= 1, `${name}: progress bounded ${s.p.current}`);
    lastRevealed = revealed;
    lastLum = luminance(s.zenith);
  }
  check(lastRevealed === worldApi.data.length, `every real asteroid revealed at depth (${lastRevealed}/${worldApi.data.length})`);
  check(await page.isHidden(".intro") || Number(await page.$eval(".intro", (e) => getComputedStyle(e).opacity)) < 0.05, "intro faded at depth");
  const labelCount = await page.$$eval(".asteroid-label:not([hidden])", (n) => n.length);
  check(labelCount > 0, `labels appear at depth (${labelCount})`);
  step("scroll journey: progressive reveal, sky -> space", { states: report.states.map((s) => [s.name, s.progress.toFixed(2), s.zenith]) });

  // ── Distance ordering on the real population ─────────────────────────────────────────
  const altitudes = [];
  for (const r of worldApi.data) altitudes.push([r.encounter.miss_distance_km, await dbg("restAltitudeOf", r.neows_id), r.neows_id]);
  altitudes.sort((a, b) => a[0] - b[0]);
  let ordered = true;
  for (let i = 1; i < altitudes.length; i++) if (!(altitudes[i][1] > altitudes[i - 1][1])) ordered = false;
  check(ordered, "rest altitude strictly increases with real miss distance (all objects)");
  const nearest = altitudes[0];
  const farthest = altitudes.at(-1);
  step("distance ordering preserved", { nearest: [nearest[2], nearest[0], nearest[1].toFixed(1)], farthest: [farthest[2], farthest[0], farthest[1].toFixed(1)] });

  // ── Rapid up/down: bounded, settles, never re-drops anything ─────────────────────────
  const before = await phases();
  await page.mouse.move(VIEW.width / 2, VIEW.height * 0.45);
  for (let round = 0; round < 20; round++) {
    for (let i = 0; i < 6; i++) await page.mouse.wheel(0, round % 2 ? 240 : -240);
  }
  for (let i = 0; i < 30; i++) await page.mouse.wheel(0, 240);
  await waitFor(async () => {
    const p = await dbg("progress");
    return p.current === p.target;
  }, "progress settles after rapid scrolling");
  const p = await dbg("progress");
  check(p.current >= 0 && p.current <= 1 && Number.isFinite(p.current), `progress bounded after rapid scroll ${JSON.stringify(p)}`);
  const after = await phases();
  check(Object.keys(before).every((id) => before[id] === "SETTLED" && after[id] === "SETTLED"), "no asteroid restarted its fall during rapid scrolling");
  await diagnostics("after rapid scroll");
  step("rapid up/down scrolling is bounded and restarts nothing", { progress: p });

  // ── Real click focuses an asteroid; it becomes the visual subject at the centre ──────────
  const clickPos = await dbg("screenPositionOf", ST);
  await page.mouse.click(clickPos.x, clickPos.y);
  await waitFor(async () => (await dbg("profileStatus")) === "ready" && (await dbg("focusSettled")) && (await dbg("focusProgress")) === 1, "click focuses ST");
  check((await dbg("selectedId")) === ST, `click selected ST (got ${await dbg("selectedId")})`);
  const center = await dbg("screenPositionOf", ST);
  check(Math.abs(center.x - VIEW.width / 2) < 4 && Math.abs(center.y - VIEW.height / 2) < 4, `focused asteroid is the visual subject at the centre: ${JSON.stringify(center)}`);
  check(await page.isHidden(".hover-tooltip"), "no hover card over the focus view");
  step("click -> focus: asteroid centred, intelligence around it");

  // ── Rapid A -> B -> C selection (URL-driven, as clicks are): the latest always wins ───────
  const pick = [TW54, ST, worldApi.data.find((r) => r.resolution.match_state === "UNRESOLVED").neows_id];
  for (const id of pick) await page.evaluate((target) => (location.hash = `#/asteroid/${target}`), id);
  await waitFor(async () => (await dbg("profileStatus")) === "ready" && (await dbg("focusProgress")) === 1, "focus on C");
  await sleep(600);
  check((await dbg("selectedId")) === pick[2], `C remains selected (got ${await dbg("selectedId")})`);
  check((await page.textContent(".focus-title")).includes(apiById.get(pick[2]).name), "focus title is C");
  check((await page.textContent('[data-callout="identity"]')).includes(pick[2]), "C's identity callout shows C");
  step("A -> B -> C rapid selection keeps C", { selected: pick[2] });

  // ── Data accuracy in focus for three real objects ─────────────────────────────────────
  const calloutValue = (key, label) => page.evaluate(([k, l]) => {
    for (const row of document.querySelectorAll(`[data-callout="${k}"] .fact`)) {
      if (row.querySelector(".fact-label")?.textContent === l) return row.querySelector(".fact-value")?.textContent;
    }
    return null;
  }, [key, label]);
  for (const id of [TW54, ST, "3830890"]) {
    await page.evaluate((target) => (location.hash = `#/asteroid/${target}`), id);
    await waitFor(async () => (await dbg("selectedId")) === id && (await dbg("profileStatus")) === "ready", `focus ${id}`);
    const prof = profiles[id];
    const checks = [
      ["identity", "NeoWs ID", prof.identity.neows_id],
      ["identity", "Name (NeoWs)", prof.identity.name],
      ["encounter", "Miss distance", fmt(prof.encounter.miss_distance_km, 0, "km")],
      ["encounter", "Relative velocity", fmt(prof.encounter.relative_velocity_km_s, 3, "km/s")],
      ["encounter", "Potentially hazardous (NeoWs)", prof.encounter.is_potentially_hazardous ? "Yes" : "No"],
    ];
    for (const [key, label, apiValue] of checks) {
      const shown = await calloutValue(key, label);
      check(shown === apiValue, `${id} ${key}.${label}: shown "${shown}" vs API "${apiValue}"`);
      report.dataAccuracy.push({ view: "focus", neows_id: id, field: `${key}.${label}`, api: apiValue, shown });
    }
    const sentryShown = prof.sentry.status === "available"
      ? await calloutValue("sentry_assessment", "Linkage")
      : (await page.$$eval(".focus-unavailable", (n) => n.map((x) => x.textContent))).find((t) => t.startsWith("Sentry"));
    const sentryExpected = prof.sentry.status === "available" ? "Linked Sentry record available" : "Sentry · JPL Sentry — Not linkable: identity not resolved";
    check(sentryShown === sentryExpected, `${id} Sentry state: "${sentryShown}"`);
    report.dataAccuracy.push({ view: "focus", neows_id: id, field: "sentry", api: prof.sentry.status, shown: sentryShown });
    if (id === TW54) await page.screenshot({ path: join(ARTIFACTS, "state5-focus.png") });
  }
  step("focus callouts match the API for three objects");

  // ── Escape returns; nothing re-drops; open/close cycles stay clean ──────────────────────
  await page.keyboard.press("Escape");
  await waitFor(async () => (await dbg("selectedId")) === null && (await dbg("focusProgress")) === 0, "Escape returns to the world");
  check(Object.values(await phases()).every((ph) => ph === "SETTLED"), "returning from focus re-drops nothing");
  for (let i = 0; i < 10; i++) {
    const id = i % 2 ? TW54 : ST;
    const pos = await dbg("screenPositionOf", id);
    await page.mouse.click(pos.x, pos.y);
    await waitFor(async () => (await dbg("profileStatus")) === "ready", `cycle ${i} focus`);
    await page.keyboard.press("Escape");
    await waitFor(async () => (await dbg("focusProgress")) === 0, `cycle ${i} back`);
  }
  await diagnostics("after 10 focus cycles");
  report.memory.heapAfterCycles = await heap();
  check(Object.values(await phases()).every((ph) => ph === "SETTLED"), "10 focus cycles re-drop nothing");
  step("Escape / focus cycles stable");

  // ── Resize repeatedly: composition holds, nothing restarts ─────────────────────────────
  for (const [w, h] of [[1920, 1080], [900, 1200], [2560, 1440], [700, 500], [1280, 720], [VIEW.width, VIEW.height]]) {
    await page.setViewportSize({ width: w, height: h });
    await sleep(200);
    const crestNow = await dbg("earthCrestY");
    const expected = h * (0.3 + (0.17 - 0.3) * 1); // progress is at 1 here
    check(Math.abs(crestNow - expected) < 2, `${w}x${h}: Earth stays the lower anchor (${crestNow.toFixed(1)} vs ${expected.toFixed(1)})`);
  }
  check(Object.values(await phases()).every((ph) => ph === "SETTLED"), "resizing re-drops nothing");
  await diagnostics("after resizes");
  step("resizes keep the composition and the lifecycle");

  // ── Refresh restores focus; rapid reloads are clean ────────────────────────────────────
  await page.goto(`${WEB}/#/asteroid/${TW54}`);
  await page.reload();
  await waitFor(async () => (await page.evaluate(() => window.__ASTEROID_DEBUG__?.profileStatus())) === "ready", "focus restored after refresh");
  check((await dbg("selectedId")) === TW54, "selection restored from URL");
  await waitFor(async () => (await dbg("focusProgress")) === 1, "deep-linked asteroid becomes the focus");
  for (let i = 0; i < 4; i++) {
    await page.reload({ waitUntil: "commit" });
    await sleep(60);
  }
  await waitFor(async () => (await page.evaluate(() => window.__ASTEROID_DEBUG__?.worldStatus())) === "ready", "world after rapid reloads");
  await diagnostics("after rapid reloads");
  step("refresh restores focus; rapid reloads clean");

  // ── 404 and API outage ────────────────────────────────────────────────────────────────
  expectHttpErrors = true;
  await page.goto(`${WEB}/#/asteroid/99999999`);
  await waitFor(async () => (await page.evaluate(() => window.__ASTEROID_DEBUG__?.profileStatus())) === "error", "404 profile");
  check((await page.textContent(".focus-error .status-title")) === "Asteroid not found", "404 reads 'Asteroid not found'");
  stop(api);
  await sleep(1500);
  await page.goto(`${WEB}/#/`);
  await page.reload();
  await waitFor(async () => (await page.evaluate(() => window.__ASTEROID_DEBUG__?.worldStatus())) === "error", "outage error", 30_000);
  const outage = await page.textContent(".status-overlay");
  check(outage.includes("ASTEROID INTELLIGENCE UNAVAILABLE") && outage.includes("could not be reached"), `outage message: ${outage}`);
  api = startApi();
  await waitForUrl(`${API}/health`);
  await page.click(".status-overlay button");
  await waitFor(async () => (await dbg("worldStatus")) === "ready", "recovered");
  await diagnostics("after outage recovery");
  expectHttpErrors = false;
  step("404 and outage handled; Retry recovers");

  const worldRequests = report.requests["/api/asteroids/world"] ?? 0;
  const profileRequests = Object.entries(report.requests).filter(([k]) => k.endsWith("/profile")).reduce((n, [, v]) => n + v, 0);
  report.requests.summary = { worldRequests, profileRequests };
  await page.close();

  // ── Performance: real 35 vs clearly-labelled synthetic stress populations ───────────────
  for (const stress of [0, 1000]) {
    const perf = await browser.newPage({ viewport: VIEW });
    perf.on("pageerror", (error) => failures.push(`stress page error: ${error.message}`));
    await perf.goto(`${WEB}/${stress ? `?stress=${stress}` : ""}#/`);
    await perf.waitForFunction(() => window.__ASTEROID_DEBUG__?.worldStatus() === "ready", null, { timeout: 30_000 });
    if (stress) check((await perf.textContent(".synthetic-banner"))?.includes("SYNTHETIC STRESS DATA"), "synthetic data is labelled as fake");
    const count = (await perf.evaluate(() => window.__ASTEROID_DEBUG__.worldIds().length));
    const sample = async (label, action) => {
      const f0 = await perf.evaluate(() => window.__ASTEROID_DEBUG__.frames());
      const t0 = Date.now();
      await action();
      const f1 = await perf.evaluate(() => window.__ASTEROID_DEBUG__.frames());
      return { label, fps: +(((f1 - f0) * 1000) / (Date.now() - t0)).toFixed(1) };
    };
    await perf.mouse.move(VIEW.width / 2, VIEW.height * 0.45);
    const idle = await sample("idle", () => sleep(2000));
    const idleTiming = await perf.evaluate(() => window.__ASTEROID_DEBUG__.timing());
    const scroll = await sample("scrolling + falling", async () => {
      for (let i = 0; i < 30; i++) {
        await perf.mouse.wheel(0, 120);
        await sleep(60);
      }
    });
    const scrollTiming = await perf.evaluate(() => window.__ASTEROID_DEBUG__.timing());
    report.performance[`objects_${count}`] = {
      synthetic: stress > 0,
      samples: [{ ...idle, ...idleTiming }, { ...scroll, ...scrollTiming }],
      heap: await perf.evaluate(() => performance.memory?.usedJSHeapSize ?? null),
    };
    if (stress) await perf.screenshot({ path: join(ARTIFACTS, "stress-1035.png") });
    await perf.close();
  }
  step("performance sampled", report.performance);
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
    console.log(`\n${failures.length === 0 ? "PASS" : "FAIL"}: ${report.steps.length} steps, ${report.dataAccuracy.length} value checks, ${errors} console errors (${report.console.filter((c) => c.expected).length} expected), ${failures.length} failures`);
    for (const f of failures) console.log(`  - ${f}`);
    setTimeout(() => process.exit(failures.length === 0 ? 0 : 1), 500);
  });
