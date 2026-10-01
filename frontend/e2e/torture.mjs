/**
 * Interaction torture test (real browser, real API, real data) for the M7.2 immersive world
 * (distance-driven, reversible reveal).
 *
 *   npm run e2e                      (from frontend/)
 *
 * Starts its own FastAPI server (port 8765) and Vite dev server (port 5174), drives the installed
 * Chrome/Edge via playwright-core (no browser download), and fails on any unexpected console error,
 * page error, NaN/Infinity, failed assertion, duplicated loop/listener/object, broken distance
 * ordering, an asteroid visible before the revealed distance reaches its exact miss distance, a
 * fall on load or on return from focus, or a displayed value that does not match the API. Screenshots of the defined
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

  // Watchdog: a hung browser call fails the run with the call that hung, instead of stalling silently.
  let lastCall = { what: "start", at: Date.now() };
  const watchdog = setInterval(() => {
    if (Date.now() - lastCall.at > 45_000) {
      failures.push(`watchdog: no progress for 45 s; last call ${lastCall.what}`);
      console.log(`  ✗ watchdog: stuck in ${lastCall.what}`);
      process.exitCode = 1;
      clearInterval(watchdog);
      for (const child of children) stop(child);
      setTimeout(() => process.exit(1), 1000);
    }
  }, 5_000);
  watchdog.unref?.();
  // Wheel input and screenshots also count as progress (and name themselves if they hang).
  const wheelRaw = page.mouse.wheel.bind(page.mouse);
  page.mouse.wheel = async (...a) => {
    lastCall = { what: `wheel(${a.join(",")})`, at: Date.now() };
    await wheelRaw(...a);
    lastCall = { what: "after wheel", at: Date.now() };
  };
  const screenshotRaw = page.screenshot.bind(page);
  page.screenshot = async (...a) => {
    lastCall = { what: "screenshot", at: Date.now() };
    const shot = await screenshotRaw(...a);
    lastCall = { what: "after screenshot", at: Date.now() };
    return shot;
  };
  const dbg = async (fn, ...args) => {
    lastCall = { what: `dbg ${fn}(${JSON.stringify(args)})`, at: Date.now() };
    const result = await page.evaluate(([f, a]) => window.__ASTEROID_DEBUG__[f](...a), [fn, args]);
    lastCall = { what: `after dbg ${fn}`, at: Date.now() };
    return result;
  };
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
  const counts = (ph) => Object.values(ph).reduce((acc, p) => ({ ...acc, [p]: (acc[p] ?? 0) + 1 }), {});
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
  const captureState = async (name, label) => {
    const p = await dbg("progress");
    const ph = counts(await dbg("phases"));
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

  const allPhases = () => dbg("phases");
  const realIds = worldApi.data.map((r) => r.neows_id);
  const missKm = (id) => apiById.get(id).encounter.miss_distance_km;
  const moving = (ph) => Object.values(ph).some((p) => p === "FALLING" || p === "RETREATING");
  const waitStill = (label) => waitFor(async () => {
    const p = await dbg("progress");
    return p.current === p.target && !moving(await allPhases());
  }, `${label}: progress and animations settle`, 15_000);
  /** At rest, an asteroid is SETTLED exactly when its real miss distance <= the revealed distance. */
  const checkEligibility = async (label) => {
    const { revealedKm } = await dbg("progress");
    const ph = await allPhases();
    const wrong = realIds.filter((id) => (ph[id] === "SETTLED") !== (missKm(id) <= revealedKm) || (ph[id] !== "SETTLED" && ph[id] !== "HIDDEN"));
    check(wrong.length === 0, `${label}: eligibility exact at ${revealedKm.toFixed(0)} km (wrong: ${wrong.join(",")})`);
    return { revealedKm, shown: realIds.filter((id) => ph[id] === "SETTLED") };
  };
  /** While the frontier moves, nothing may be visible beyond it unless it is on its way out. */
  const watchTransition = async (label, frames = 40) => {
    const seen = {};
    for (let i = 0; i < frames; i++) {
      const [{ revealedKm }, ph] = await Promise.all([dbg("progress"), allPhases()]);
      for (const id of realIds) {
        (seen[id] ??= new Set()).add(ph[id]);
        if ((ph[id] === "FALLING" || ph[id] === "SETTLED") && missKm(id) > revealedKm) {
          check(false, `${label}: ${id} visible (${ph[id]}) beyond the frontier (${missKm(id)} > ${revealedKm})`);
        }
      }
      await sleep(40);
    }
    return seen;
  };
  const wheel = async (notches, delta = 120) => {
    await page.mouse.move(VIEW.width * 0.4, VIEW.height * 0.45);
    for (let i = 0; i < Math.abs(notches); i++) await page.mouse.wheel(0, Math.sign(notches) * delta);
  };
  /** Wheel toward the progress whose revealed distance is `km` (real user input, so approximate). */
  /** Optionally records every phase seen per asteroid while scrolling (falls can finish mid-scroll). */
  const scrollToKm = async (km, seen = null) => {
    const target = await dbg("progressForKm", km);
    for (let i = 0; i < 400; i++) {
      const p = (await dbg("progress")).target;
      if (km <= 0 ? p === 0 : Math.abs(p - target) < 0.008) break; // "the top" means exactly 0
      await wheel(1 * Math.sign(target - p), Math.abs(p - target) > 0.05 ? 120 : 40);
      if (seen) for (const [id, ph] of Object.entries(await allPhases())) (seen[id] ??= new Set()).add(ph);
    }
  };
  const visibleGuideKms = async () => (await dbg("visibleGuides")).map((g) => g.km);
  const earthOnScreen = async () => (await dbg("earthCrestScreenY")) < VIEW.height;
  /** The viewport shows only the local distance window, never the whole field. */
  const checkLocalWindow = async (label) => {
    const { revealedKm } = await dbg("progress");
    const w = await dbg("viewWindowKm");
    const guides = await visibleGuideKms();
    check(w.highKm - w.lowKm < 10e6 && w.lowKm > revealedKm - 8e6 && w.highKm < revealedKm + 4e6,
      `${label}: viewport is a local window ${(w.lowKm / 1e6).toFixed(1)}M-${(w.highKm / 1e6).toFixed(1)}M around ${(revealedKm / 1e6).toFixed(1)}M`);
    check(guides.length > 0 && guides.length <= 12 && guides.every((km) => km >= w.lowKm - 1e6 && km <= w.highKm + 1e6),
      `${label}: only local guides drawn (${guides.length}: ${guides.map((k) => k / 1e6).join(",")})`);
    const spacing = (await dbg("visibleGuides")).map((g) => g.altitude).sort((a, b) => a - b);
    check(spacing.every((a, i) => i === 0 || a - spacing[i - 1] > 60), `${label}: each million km has real spacing (${spacing.slice(1).map((a, i) => Math.round(a - spacing[i])).join(",")} px)`);
    return { window: [Math.round(w.lowKm / 1e5) / 10, Math.round(w.highKm / 1e5) / 10], guides: guides.length };
  };
  const parseKm = (text) => Number(text.replace(/[^0-9]/g, ""));
  const positionsOf = async (ids) => Object.fromEntries(await Promise.all(ids.map(async (id) => [id, await dbg("screenPositionOf", id)])));
  /** World positions (screen position + camera travel): comparable across different camera positions. */
  const worldPositionsOf = async (ids) => {
    const travel = await dbg("travelPx");
    const screen = await positionsOf(ids);
    return Object.fromEntries(ids.map((id) => [id, screen[id] && { x: +screen[id].x.toFixed(3), y: +(VIEW.height + travel - screen[id].y).toFixed(3) }]));
  };

  // ── 1-3. Fresh load: Earth arc, and NO asteroid falls before the user scrolls ─────────────
  await page.goto(`${WEB}/#/`);
  await waitFor(async () => (await page.evaluate(() => window.__ASTEROID_DEBUG__?.worldStatus())) === "ready", "world ready");
  step("1. fresh load", { records: realIds.length });
  const crest = await dbg("earthCrestY");
  check(Math.abs(crest / VIEW.height - 0.3) < 0.01, `Earth crest in the lower third: ${crest}`);
  const earth = await dbg("earthCounts");
  check(earth && earth.trees > 0 && earth.houses > 0 && earth.people > 0 && earth.lakes > 0, `tiny world exists: ${JSON.stringify(earth)}`);
  const grass = await pixelAt(VIEW.width / 2, VIEW.height - crest + 20);
  check(grass[1] > grass[0] && grass[1] > grass[2], `green Earth below the crest: rgb ${grass}`);
  step("2. Earth arc with its tiny world", { crest, earth });
  for (let i = 0; i < 6; i++) {
    const ph = await allPhases();
    check(Object.values(ph).every((p) => p === "HIDDEN"), `load sample ${i}: nothing visible before scroll ${JSON.stringify(counts(ph))}`);
    check((await dbg("progress")).revealedKm === 0, `load sample ${i}: revealed distance is 0 km`);
    await sleep(500);
  }
  const sky0 = await captureState("state0-earth-sky", "Initial Earth + sky, nothing falling");
  check(sky0.zenith && sky0.zenith[2] > sky0.zenith[0] && luminance(sky0.zenith) > 120, `initial background is sky blue: ${sky0.zenith}`);
  check(await page.isVisible(".intro"), "title and scroll hint visible");
  // APOLLO identity: exact texts, subtitle on ONE line, wordmark docked only once the journey starts.
  const SUBTITLE = "Asteroid Proximity & Orbital Logistics Lookout Operation";
  const hero = await page.$eval(".brand-hero", (n) => {
    const sub = n.querySelector(".brand-subtitle");
    const range = document.createRange();
    range.selectNodeContents(sub);
    const lineHeight = parseFloat(getComputedStyle(sub).lineHeight) || parseFloat(getComputedStyle(sub).fontSize) * 1.3;
    return {
      name: n.querySelector(".brand-name").textContent, subtitle: sub.textContent,
      lines: range.getClientRects().length, height: sub.getBoundingClientRect().height, lineHeight,
      nameSize: parseFloat(getComputedStyle(n.querySelector(".brand-name")).fontSize), subSize: parseFloat(getComputedStyle(sub).fontSize),
      right: sub.getBoundingClientRect().right, left: sub.getBoundingClientRect().left,
    };
  });
  check(hero.name === "APOLLO" && hero.subtitle === SUBTITLE, `title texts exact: ${JSON.stringify([hero.name, hero.subtitle])}`);
  check(hero.lines === 1 && hero.height < hero.lineHeight * 1.5, `subtitle on one line (${hero.lines} line box(es), ${hero.height}px)`);
  check(hero.left >= 0 && hero.right <= VIEW.width, "subtitle fits the viewport");
  check(hero.nameSize >= hero.subSize * 3, `APOLLO dominates the subtitle (${hero.nameSize}px vs ${hero.subSize}px)`);
  check(Number(await page.$eval(".brand-docked", (n) => getComputedStyle(n).opacity)) === 0, "docked wordmark hidden at load");
  check((await page.title()).startsWith("APOLLO"), `document title: ${await page.title()}`);
  report.brand = hero;
  check((await dbg("frontierLabel")) === null, "no frontier label before scrolling");
  check((await dbg("moonOpacity")) === 0 && !(await dbg("moonLabelVisible")), "no Moon at load");
  check((await dbg("visibleGuides")).length === 0, "no distance guides at load");
  step("3. no asteroid falls, no Moon, no distance field before scroll (3 s observed)");
  await diagnostics("after load");
  report.memory.heapAfterLoad = await heap();

  // ── 4-5. A small scroll reveals only distance-eligible asteroids ──────────────────────────
  await wheel(4);
  await watchTransition("small scroll", 20);
  await waitStill("small scroll");
  const small = await checkEligibility("small scroll");
  check(small.shown.length === 0, `small scroll (${small.revealedKm.toFixed(0)} km) is short of the nearest asteroid (${Math.min(...realIds.map(missKm)).toFixed(0)} km): nothing shown`);
  const skySmall = await captureState("state1-small-scroll", "Small scroll: sky darkening, no Moon yet");
  check(luminance(skySmall.zenith) < luminance(sky0.zenith), `small scroll darkens the sky (${luminance(sky0.zenith).toFixed(1)} -> ${luminance(skySmall.zenith).toFixed(1)})`);
  check((await dbg("moonOpacity")) === 0 && !(await dbg("moonLabelVisible")), "Moon still hidden after a small scroll");
  step("4. small scroll: sky darkens, Moon still hidden", { revealedKm: Math.round(small.revealedKm), zenith: skySmall.zenith });

  await scrollToKm(250_000);
  await waitStill("approaching the Moon");
  check((await dbg("moonOpacity")) === 0, `Moon hidden before its distance (${Math.round((await dbg("progress")).revealedKm)} km)`);
  await scrollToKm(700_000);
  await waitStill("past the Moon");
  await waitFor(async () => (await dbg("moonOpacity")) === 1, "Moon fully revealed");
  check(await dbg("moonLabelVisible"), "Moon label shown with the Moon");
  const night = await captureState("state2-night-moon", "Night transition: Moon distance reached");
  check(luminance(night.zenith) < 45, `night sky when the Moon appears (${night.zenith})`);
  check(await earthOnScreen(), "Earth still visible when the Moon appears");
  const docked = await page.$eval(".brand-docked", (n) => ({
    opacity: Number(getComputedStyle(n).opacity), name: n.querySelector(".brand-name").textContent,
    subtitle: n.querySelector(".brand-subtitle").textContent, lines: (() => { const r = document.createRange(); r.selectNodeContents(n.querySelector(".brand-subtitle")); return r.getClientRects().length; })(),
  }));
  check(docked.opacity > 0.99 && docked.name === "APOLLO" && docked.subtitle === SUBTITLE && docked.lines === 1, `docked APOLLO wordmark once travelling (Moon stage) ${JSON.stringify(docked)}`);
  const moonPos = await dbg("moonScreenPosition");
  check(moonPos.y > 0 && moonPos.y < VIEW.height, `Moon on screen ${JSON.stringify(moonPos)}`);
  check((await page.textContent(".moon-title")) === "MOON DISTANCE" && (await page.textContent(".moon-km")) === "384,400 km", "Moon labelled MOON DISTANCE / 384,400 km");
  const moonLabelBox = await page.$eval(".moon-label", (n) => n.getBoundingClientRect().toJSON());
  check(Math.abs(moonLabelBox.right - (moonPos.x - 18)) < 3 && Math.abs(moonLabelBox.top - (moonPos.y - 14)) < 3, `Moon label next to the Moon ${JSON.stringify(moonPos)}`);
  check(!(await dbg("worldIds")).includes("moon") && (await dbg("worldIds")).length === realIds.length, "Moon is not a data record");
  step("Moon appears when the frontier reaches 384,400 km", { revealedKm: Math.round((await dbg("progress")).revealedKm), zenith: night.zenith });

  await scrollToKm(1.6e6);
  await waitStill("first million");
  const firstGuides = await visibleGuideKms();
  check(firstGuides.includes(1e6), `the first distance guide (1M km) appears: ${firstGuides.map((k) => k / 1e6).join(",")}`);
  await captureState("state3-first-guides", "Early distance field: first guides");
  step("first distance guides appear", { guides: firstGuides.map((k) => `${k / 1e6}M`) });

  await scrollToKm(9e6);
  await watchTransition("to ~9M km");
  await waitStill("to ~9M km");
  const near = await checkEligibility("~9M km");
  check(near.shown.length > 0 && near.shown.length < realIds.length / 4, `only the nearest few are shown at ${near.revealedKm.toFixed(0)} km: ${near.shown.join(",")}`);
  const early = await visibleGuideKms();
  const win9 = await dbg("viewWindowKm");
  const everyMillion = [];
  for (let km = Math.ceil(win9.lowKm / 1e6 + 1) * 1e6; km <= near.revealedKm; km += 1e6) everyMillion.push(km);
  check(everyMillion.length >= 3 && everyMillion.every((km) => early.includes(km)), `every 1M guide in the local window is drawn: ${early.map((k) => k / 1e6).sort((a, b) => a - b).join(",")}`);
  const earlyLabels = await dbg("guideLabels");
  check(everyMillion.every((km) => earlyLabels.includes(`${km / 1e6}M km`)), `each local million is labelled: ${JSON.stringify(earlyLabels)}`);
  const local9 = await checkLocalWindow("~9M km");
  check(!(await earthOnScreen()), "Earth has been left behind by ~9M km");
  check(!(await dbg("moonLabelVisible")), "the Moon has been passed by ~9M km");
  check(near.shown[0] === realIds.slice().sort((a, b) => missKm(a) - missKm(b))[0], "the closest asteroid appears first");
  const s1 = await captureState("state4-early-field", "Early field: 1M increments, closest asteroid");
  step("5. only distance-eligible asteroids appear; 1M increments visible", { revealedKm: Math.round(near.revealedKm), shown: near.shown.map((id) => [id, Math.round(missKm(id))]) });

  // ── 6-8. Farther: more appear; the frontier label counts up in 1M steps ──────────────────
  const frontierSeen = [];
  let lastShown = near.shown.length;
  let lastLum = luminance(s1.zenith);
  for (const [km, name, label] of [[20.3e6, "state5-field-20M", "Local field around 20M km"], [50.3e6, "state6-field-50M", "Local field around 50M km"], [1.2e8, "state7-deep-100M", "Deep space around 100M km"]]) {
    const seen = {};
    await scrollToKm(km, seen);
    for (const [id, set] of Object.entries(await watchTransition(name))) for (const ph of set) (seen[id] ??= new Set()).add(ph);
    await waitStill(name);
    const e = await checkEligibility(name);
    check(e.shown.length >= lastShown, `${name}: more asteroids as the frontier moves out (${lastShown} -> ${e.shown.length})`);
    check(Object.values(seen).some((set) => set.has("FALLING")) || e.shown.length === lastShown, `${name}: newly reached asteroids fall in`);
    const text = await dbg("frontierLabel");
    frontierSeen.push([Math.round(e.revealedKm), text]);
    check(/^REVEALED TO [\d,]+ km$/.test(text ?? ""), `${name}: frontier label "${text}"`);
    const shownKm = parseKm(text ?? "");
    check(shownKm % 1e6 === 0 && shownKm <= e.revealedKm && e.revealedKm - shownKm < 1e6, `${name}: frontier label floors to whole millions (${shownKm} vs ${e.revealedKm.toFixed(0)})`);
    const guideLabels = await dbg("guideLabels");
    check(guideLabels.length > 0 && guideLabels.every((t) => /^\d+M km$/.test(t)), `${name}: guide labels in 1M units ${JSON.stringify(guideLabels)}`);
    const guides = await dbg("visibleGuides");
    const strongest = guides.reduce((m, g) => (g.alpha > m.alpha ? g : m), guides[0]);
    check(strongest && strongest.km <= e.revealedKm && e.revealedKm - strongest.km < 1.5e6,
      `${name}: the explored distance is the strongest guide (${strongest?.km / 1e6}M at frontier ${(e.revealedKm / 1e6).toFixed(2)}M)`);
    check(guides.every((g) => g.km <= e.revealedKm + 3e6), `${name}: no unreached guides far ahead`);
    const local = await checkLocalWindow(name);
    frontierSeen.at(-1).push(local.window);
    check(!(await earthOnScreen()), `${name}: no Earth/ground in deep field`);
    const s = await captureState(name, label);
    check(luminance(s.zenith) < lastLum, `${name}: sky darkens toward space (${lastLum.toFixed(1)} -> ${luminance(s.zenith).toFixed(1)})`);
    lastLum = luminance(s.zenith);
    lastShown = e.shown.length;
  }
  check(lastShown === realIds.length, `every real asteroid revealed at depth (${lastShown}/${realIds.length})`);
  const labelSteps = [];
  await scrollToKm(9e6);
  await waitStill("back to 9M for label sampling");
  for (let i = 0; i < 14; i++) {
    await wheel(1, 60);
    await waitFor(async () => { const p = await dbg("progress"); return p.current === p.target; }, "label step settles");
    labelSteps.push(parseKm((await dbg("frontierLabel")) ?? "0"));
  }
  check(labelSteps.every((v, i) => i === 0 || v >= labelSteps[i - 1]), `frontier label is monotonic while scrolling out: ${labelSteps.join(" ")}`);
  check(labelSteps.every((v) => v % 1e6 === 0) && new Set(labelSteps).size >= 5, `frontier label moves through 1M steps: ${labelSteps.join(" ")}`);
  step("6-7. farther objects appear as the frontier moves out", { frontier: frontierSeen });
  step("8. distance label progression", { labels: labelSteps.map((v) => `${v / 1e6}M`) });

  // Asteroid labels carry the ACTUAL miss distance (never the rounded grid value).
  await scrollToKm(20e6);
  await waitStill("20M for labels");
  const asteroidLabels = await page.$$eval(".asteroid-label:not([hidden])", (n) => n.map((x) => [x.querySelector(".label-name")?.textContent, x.querySelector(".label-detail")?.textContent]));
  const byName = new Map(worldApi.data.map((r) => [r.name, r]));
  let labelChecks = 0;
  for (const [name, detail] of asteroidLabels) {
    const r = byName.get(name);
    const expected = fmt(r.encounter.miss_distance_km, 0, "km");
    check(detail === expected, `label ${name}: "${detail}" vs API "${expected}"`);
    report.dataAccuracy.push({ view: "label", neows_id: r.neows_id, field: "miss_distance", api: expected, shown: detail });
    labelChecks++;
  }
  check(labelChecks >= 4, `asteroid labels checked against the API (${labelChecks})`);
  step("12. asteroid labels show the actual miss distance", { checked: labelChecks });

  // ── 9. World heights of every real asteroid (distance ordering is checked below) ───────────
  const ladder = [];
  for (const id of realIds) ladder.push([missKm(id), await dbg("restAltitudeOf", id)]);
  const domain = await dbg("distanceDomain");
  const layout = await page.evaluate(() => window.__ASTEROID_DEBUG__.earthCrestY());
  step("9. world heights captured", { domain, earthCrestWorldY: layout });

  // ── 10. PHA hazard badge on real PHA=true objects only ───────────────────────────────────
  await scrollToKm(1.2e8);
  await waitStill("deep for hazard");
  const hazardRows = [];
  for (const r of worldApi.data) {
    const shown = await dbg("hazardShownOf", r.neows_id);
    hazardRows.push([r.neows_id, r.encounter.is_potentially_hazardous, shown]);
    check(shown === (r.encounter.is_potentially_hazardous === true), `${r.neows_id}: hazard badge ${shown} for PHA ${r.encounter.is_potentially_hazardous}`);
  }
  const pha = worldApi.data.find((r) => r.neows_id === "2138971") ?? worldApi.data.find((r) => r.encounter.is_potentially_hazardous === true);
  await scrollToKm(missKm(pha.neows_id) + 1.5e6); // bring its local region into view
  await waitStill("PHA object in view");
  const phaPos = await dbg("screenPositionOf", pha.neows_id);
  check(phaPos.y > 40 && phaPos.y < VIEW.height - 40, `PHA object on screen in its local window ${JSON.stringify(phaPos)}`);
  const badge = await page.screenshot({ clip: { x: Math.round(phaPos.x) - 4, y: Math.round(phaPos.y) - 30, width: 34, height: 34 } });
  writeFileSync(join(ARTIFACTS, "hazard-badge.png"), badge);
  const yellow = await page.evaluate(async (b64) => {
    const img = new Image();
    img.src = `data:image/png;base64,${b64}`;
    await img.decode();
    const c = document.createElement("canvas");
    c.width = img.width;
    c.height = img.height;
    const ctx = c.getContext("2d");
    ctx.drawImage(img, 0, 0);
    const d = ctx.getImageData(0, 0, c.width, c.height).data;
    let n = 0;
    for (let i = 0; i < d.length; i += 4) if (d[i] > 200 && d[i + 1] > 150 && d[i + 2] < 120) n++;
    return n;
  }, badge.toString("base64"));
  check(yellow > 10, `hazard badge drawn beside real PHA object ${pha.neows_id} (${yellow} badge pixels)`);
  // Hover: lightweight facts from loaded world data, checked against the API.
  await page.mouse.move(phaPos.x, phaPos.y);
  await waitFor(async () => (await dbg("hoveredId")) === pha.neows_id, "hover the PHA object");
  const tip = await page.textContent(".hover-tooltip");
  for (const [field, value] of Object.entries({
    name: pha.name, neows_id: pha.neows_id, miss: fmt(pha.encounter.miss_distance_km, 0, "km"), pha: "Yes",
  })) {
    const ok = check(tip.includes(value), `hover ${field} "${value}"`);
    report.dataAccuracy.push({ view: "hover", neows_id: pha.neows_id, field, api: value, shown: ok });
  }
  await page.mouse.move(3, 3);
  await waitFor(async () => (await dbg("hoveredId")) === null, "hover cleared");
  step("10. PHA hazard badge", { phaTrue: hazardRows.filter((r) => r[1] === true).map((r) => r[0]), badgePixels: yellow });

  // ── 11-12. Scroll backward: farther asteroids retreat and disappear ───────────────────────
  await scrollToKm(1.2e8);
  await waitStill("deep before backward");
  const deepShown = (await checkEligibility("before backward")).shown;
  const deepPositions = await worldPositionsOf(realIds);
  await scrollToKm(16e6);
  const back = await watchTransition("backward to ~16M km", 50);
  await waitStill("backward");
  const backE = await checkEligibility("after backward");
  const retreated = realIds.filter((id) => missKm(id) > backE.revealedKm);
  check(retreated.length > 0 && retreated.every((id) => back[id].has("RETREATING") || back[id].has("HIDDEN")), "asteroids beyond the frontier retreated");
  check(retreated.every((id) => !back[id].has("FALLING")), "nothing falls on a backward scroll");
  check(backE.shown.every((id) => [...back[id]].every((p) => p === "SETTLED")), "asteroids within the frontier stay settled on the way back");
  await captureState("state6-backward", "Scrolled back: farther asteroids retreated");
  step("11-12. backward scroll retreats farther asteroids", { revealedKm: Math.round(backE.revealedKm), stillShown: backE.shown.length, retreated: retreated.length });

  // ── 13-14. Forward again: the same asteroids re-reveal at the same positions ─────────────
  await scrollToKm(1.2e8);
  await watchTransition("forward again");
  await waitStill("forward again");
  const again = await checkEligibility("forward again");
  check(JSON.stringify(again.shown.sort()) === JSON.stringify([...deepShown].sort()), "the same asteroids are shown again");
  const againPositions = await worldPositionsOf(realIds);
  check(realIds.every((id) => JSON.stringify(againPositions[id]) === JSON.stringify(deepPositions[id])), "re-revealed asteroids rest at identical positions");
  step("13-14. forward again re-reveals deterministically");

  // ── Acceptance example: a real asteroid is passed and comes back ───────────────────────────
  {
    const id = ST;
    const x = missKm(id);
    const go = async (km, label) => { // exact targets, so "the same distance" means exactly the same camera
      await dbg("exploreTo", await dbg("progressForKm", km));
      await waitStill(label);
    };
    await go(x - 1.2e6, "short of ST");
    check((await dbg("phaseOf", id)) === "HIDDEN", `${id} absent while the revealed distance < ${x}`);
    await go(x + 1.2e6, "at ST");
    const atRest = await dbg("screenPositionOf", id);
    check((await dbg("phaseOf", id)) === "SETTLED" && atRest.y > 0 && atRest.y < VIEW.height, `${id} settled in view at its distance ${JSON.stringify(atRest)}`);
    await go(x + 20e6, "far past ST");
    const passed = await dbg("screenPositionOf", id);
    check((await dbg("phaseOf", id)) === "SETTLED" && passed.y > VIEW.height, `${id} left the viewport behind the traveller (y ${Math.round(passed.y)})`);
    await go(x + 1.2e6, "back at ST");
    const back = await dbg("screenPositionOf", id);
    check(Math.abs(back.y - atRest.y) < 1 && Math.abs(back.x - atRest.x) < 1, `${id} returns to the same place in view (${Math.round(atRest.y)} -> ${Math.round(back.y)})`);
    step("acceptance: a real asteroid is passed and returns", { neows_id: id, miss_distance_km: x, atRest, passedY: Math.round(passed.y) });
  }

  // ── Full reversibility: TOP -> 10M -> 30M -> 60M -> 100M -> 60M -> 30M -> 10M -> TOP, twice ──
  const objects0 = await dbg("objectCounts");
  const route = [];
  for (let lap = 0; lap < 2; lap++) {
    for (const km of [0, 10e6, 30e6, 60e6, 1.2e8, 60e6, 30e6, 10e6, 0]) {
      await scrollToKm(km);
      await waitStill(`lap ${lap} ${km}`);
      await checkEligibility(`lap ${lap} at ${km / 1e6}M`);
      const objects = await dbg("objectCounts");
      check(JSON.stringify(objects) === JSON.stringify(objects0), `lap ${lap} ${km / 1e6}M: no duplicated/missing objects ${JSON.stringify(objects)}`);
      const onEarth = await earthOnScreen();
      if (km === 0) {
        const p = await dbg("progress");
        check(p.revealedKm === 0 && (await dbg("travelPx")) === 0, `lap ${lap}: back at the top`);
        check(onEarth && Math.abs((await dbg("earthCrestScreenY")) - VIEW.height * 0.7) < 2, `lap ${lap}: the Earth is back in its starting place`);
        check((await dbg("moonOpacity")) === 0 && !(await dbg("moonLabelVisible")), `lap ${lap}: no Moon at the top`);
        check(Object.values(await allPhases()).every((ph) => ph === "HIDDEN"), `lap ${lap}: every asteroid hidden at the top`);
      } else {
        check(!onEarth, `lap ${lap} ${km / 1e6}M: Earth not on screen`);
        await checkLocalWindow(`lap ${lap} ${km / 1e6}M`);
      }
      route.push([lap, km / 1e6, Math.round((await dbg("progress")).revealedKm / 1e5) / 10, onEarth]);
    }
  }
  const top = await captureState("state8-return-to-earth", "Returned to Earth after two full journeys");
  check(luminance(top.zenith) > 120, `bright day sky again at the top (${top.zenith})`);
  await diagnostics("after two full journeys");
  step("full reversibility: TOP -> 100M -> TOP twice", { route });

  // ── Distance ordering on the real population ─────────────────────────────────────────
  const altitudes = realIds.map((id) => [missKm(id), ladder.find(([km]) => km === missKm(id))[1], id]).sort((a, b) => a[0] - b[0]);
  check(altitudes.every((a, i) => i === 0 || a[1] > altitudes[i - 1][1]), "rest altitude strictly increases with real miss distance (all objects)");
  step("distance ordering preserved", { nearest: altitudes[0], farthest: altitudes.at(-1) });

  // ── Exact threshold on a real non-round distance (exact target, then real phases) ────────
  const demoId = pha.neows_id;
  const demoKm = missKm(demoId);
  const threshold = {};
  for (const [label, km] of [["below", demoKm - 1], ["at", demoKm * (1 + 1e-12)]]) {
    await dbg("exploreTo", await dbg("progressForKm", km));
    await waitStill(`threshold ${label}`);
    const { revealedKm } = await dbg("progress");
    threshold[label] = { revealedKm, phase: await dbg("phaseOf", demoId), labelText: await dbg("frontierLabel") };
  }
  check(threshold.below.revealedKm < demoKm && threshold.below.phase === "HIDDEN", `below threshold: ${demoId} hidden at ${threshold.below.revealedKm}`);
  check(threshold.at.revealedKm >= demoKm && threshold.at.phase === "SETTLED", `at threshold: ${demoId} revealed at ${threshold.at.revealedKm}`);
  report.thresholdDemo = { neows_id: demoId, miss_distance_km: demoKm, ...threshold };
  step("exact threshold on a real non-round distance", report.thresholdDemo);
  await scrollToKm(18.5e6);
  await waitStill("local window with ST and TW54 before focus");

  // Gold = actual Sentry link (served sentry.status), never PHA.
  const linkedIds = worldApi.data.filter((r) => r.sentry.status === "available" || r.sentry.status === "linked_no_record").map((r) => r.neows_id);
  check(JSON.stringify(linkedIds.sort()) === JSON.stringify([ST, TW54].sort()), `the API reports exactly two Sentry-linked objects: ${linkedIds}`);
  for (const r of worldApi.data) {
    const gold = await dbg("sentryGoldShownOf", r.neows_id);
    const expected = linkedIds.includes(r.neows_id) && (await dbg("phaseOf", r.neows_id)) !== "HIDDEN";
    check(gold === expected, `${r.neows_id}: gold ${gold} (sentry ${r.sentry.status}, PHA ${r.encounter.is_potentially_hazardous})`);
  }
  const goldPixels = async (id) => {
    const at = await dbg("screenPositionOf", id);
    const shot = await page.screenshot({ clip: { x: Math.round(at.x) - 14, y: Math.round(at.y) - 14, width: 28, height: 28 } });
    return page.evaluate(async (b64) => {
      const img = new Image();
      img.src = `data:image/png;base64,${b64}`;
      await img.decode();
      const c = document.createElement("canvas");
      c.width = img.width;
      c.height = img.height;
      const ctx = c.getContext("2d");
      ctx.drawImage(img, 0, 0);
      const d = ctx.getImageData(0, 0, c.width, c.height).data;
      let n = 0;
      for (let i = 0; i < d.length; i += 4) if (d[i] > 170 && d[i + 1] > 120 && d[i] - d[i + 2] > 90) n++;
      return n;
    }, shot.toString("base64"));
  };
  const plainInView = (await allPhases());
  const plainId = realIds.find((id) => !linkedIds.includes(id) && plainInView[id] === "SETTLED" && missKm(id) > 12e6);
  const goldCounts = { [ST]: await goldPixels(ST), [TW54]: await goldPixels(TW54), plain: await goldPixels(plainId) };
  writeFileSync(join(ARTIFACTS, "sentry-gold.png"), await page.screenshot({ clip: { x: 0, y: 0, width: VIEW.width, height: VIEW.height } }));
  check(goldCounts[ST] > 25 && goldCounts[TW54] > 25 && goldCounts.plain < 5, `gold drawn on the two Sentry-linked asteroids only ${JSON.stringify(goldCounts)}`);
  const stPos = await dbg("screenPositionOf", ST);
  await page.mouse.move(stPos.x, stPos.y);
  await waitFor(async () => (await dbg("hoveredId")) === ST, "hover ST");
  check((await page.textContent(".hover-tooltip .sentry-tag")) === "SENTRY LINKED", "hover tags a Sentry-linked asteroid");
  await page.mouse.move(3, 3);
  await waitFor(async () => (await dbg("hoveredId")) === null, "hover cleared");
  step("gold Sentry designation on the two linked objects only", { linked: linkedIds, goldCounts, plainId });

  // ── 15-16. Select a real asteroid by clicking it; callouts match the API ─────────────────
  const preFocus = { progress: await dbg("progress"), phases: await allPhases() };
  const clickPos = await dbg("screenPositionOf", ST);
  await page.mouse.click(clickPos.x, clickPos.y);
  await waitFor(async () => (await dbg("profileStatus")) === "ready" && (await dbg("focusSettled")) && (await dbg("focusProgress")) === 1, "click focuses ST");
  check((await dbg("selectedId")) === ST, `click selected ST (got ${await dbg("selectedId")})`);
  const center = await dbg("screenPositionOf", ST);
  check(Math.abs(center.x - VIEW.width / 2) < 4 && Math.abs(center.y - VIEW.height / 2) < 4, `focused asteroid is the visual subject at the centre: ${JSON.stringify(center)}`);
  await wheel(10, 240); // wheel during focus must not move the exploration
  check((await dbg("progress")).target === preFocus.progress.target, "wheel during focus leaves the exploration untouched");
  step("15. select an asteroid by click");

  const calloutValue = (key, label) => page.evaluate(([k, l]) => {
    for (const row of document.querySelectorAll(`[data-callout="${k}"] .fact`)) {
      if (row.querySelector(".fact-label")?.textContent === l) return row.querySelector(".fact-value")?.textContent;
    }
    return null;
  }, [key, label]);
  const pick = [TW54, ST, worldApi.data.find((r) => r.resolution.match_state === "UNRESOLVED").neows_id];
  for (const id of pick) await page.evaluate((target) => (location.hash = `#/asteroid/${target}`), id);
  await waitFor(async () => (await dbg("profileStatus")) === "ready" && (await dbg("focusProgress")) === 1, "focus on C");
  await sleep(600);
  check((await dbg("selectedId")) === pick[2], `rapid A -> B -> C keeps C (got ${await dbg("selectedId")})`);
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
  step("16. callouts match the API for three objects (and rapid A -> B -> C keeps C)");

  // ── 17-18. Return: same exploration distance, same states, nothing re-falls ──────────────
  await page.keyboard.press("Escape");
  const returning = await watchTransition("return from focus", 30);
  await waitFor(async () => (await dbg("selectedId")) === null && (await dbg("focusProgress")) === 0, "Escape returns to the world");
  await waitStill("after return");
  check(realIds.every((id) => !returning[id].has("FALLING")), "nothing re-falls when returning from focus");
  const postFocus = { progress: await dbg("progress"), phases: await allPhases() };
  check(postFocus.progress.revealedKm === preFocus.progress.revealedKm && postFocus.progress.target === preFocus.progress.target, `same exploration distance after focus (${preFocus.progress.revealedKm} -> ${postFocus.progress.revealedKm})`);
  check(JSON.stringify(postFocus.phases) === JSON.stringify(preFocus.phases), "same asteroid states after focus");
  step("17. return to world");
  for (let i = 0; i < 6; i++) {
    const id = i % 2 ? TW54 : ST;
    const pos = await dbg("screenPositionOf", id);
    await page.mouse.click(pos.x, pos.y);
    await waitFor(async () => (await dbg("profileStatus")) === "ready", `cycle ${i} focus`);
    await page.keyboard.press("Escape");
    await waitFor(async () => (await dbg("focusProgress")) === 0, `cycle ${i} back`);
  }
  await waitStill("after focus cycles");
  check((await dbg("progress")).revealedKm === preFocus.progress.revealedKm, "focus cycles keep the revealed distance");
  await scrollToKm(16e6);
  await waitStill("backward after focus");
  const afterFocusBack = await checkEligibility("backward after focus");
  check(afterFocusBack.shown.length < realIds.length, "backward scroll after focus retreats the farther asteroids");
  await diagnostics("after focus cycles");
  report.memory.heapAfterCycles = await heap();
  step("18. the world keeps its exploration distance through focus", { revealedKm: Math.round(preFocus.progress.revealedKm) });

  // ── 19-20. Rapid up/down: bounded, consistent, no duplicates, no runaway animation ───────
  const objectsBefore = await dbg("objectCounts");
  await page.mouse.move(VIEW.width * 0.4, VIEW.height * 0.45);
  for (let round = 0; round < 24; round++) {
    for (let i = 0; i < 6; i++) await page.mouse.wheel(0, round % 2 ? 240 : -240);
    await sleep(round % 3 === 0 ? 120 : 0);
  }
  for (let i = 0; i < 12; i++) await page.mouse.wheel(0, 240);
  await waitStill("after rapid scrolling");
  const p = await dbg("progress");
  check(p.current >= 0 && p.current <= 1 && Number.isFinite(p.current) && Number.isFinite(p.revealedKm), `progress bounded after rapid scroll ${JSON.stringify(p)}`);
  await checkEligibility("after rapid scrolling");
  const objectsAfter = await dbg("objectCounts");
  check(objectsAfter.asteroidInstances === realIds.length && JSON.stringify(objectsAfter) === JSON.stringify(objectsBefore), `no duplicate objects ${JSON.stringify(objectsBefore)} -> ${JSON.stringify(objectsAfter)}`);
  const still1 = await positionsOf(realIds);
  await sleep(1000);
  const still2 = await positionsOf(realIds);
  check(JSON.stringify(still1) === JSON.stringify(still2) && !moving(await allPhases()), "no runaway animation: everything is stationary once input stops");
  await diagnostics("after rapid scroll");
  step("19-20. rapid up/down: bounded, no duplicates, no runaway animation", { progress: p, objects: objectsAfter });

  // ── Resize repeatedly: composition holds, eligibility unchanged ──────────────────────────
  for (const [w, h] of [[1920, 1080], [900, 1200], [2560, 1440], [700, 500], [1280, 720], [VIEW.width, VIEW.height]]) {
    await page.setViewportSize({ width: w, height: h });
    await sleep(200);
    const crestNow = await dbg("earthCrestY");
    const expected = h * 0.3;
    check(Math.abs(crestNow - expected) < 2, `${w}x${h}: the Earth stays the world's base (${crestNow.toFixed(1)} vs ${expected.toFixed(1)})`);
  }
  await waitStill("after resizes");
  await checkEligibility("after resizes");
  await diagnostics("after resizes");
  step("resizes keep the composition and the reveal state");

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
  check(outage.includes("APOLLO UNAVAILABLE") && outage.includes("could not be reached"), `outage message: ${outage}`);
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
    const scroll = await sample("scrolling + revealing", async () => {
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
