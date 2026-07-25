#!/usr/bin/env node

/**
 * Remote-browser Asset Library performance and reliability harness.
 *
 * Required credentials are read from environment variables and are never
 * written to the report. Network records are reduced to aggregate media
 * classes so cookies, query strings, signed URLs, and private filenames do not
 * enter artifacts.
 */

import fs from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { performance } from "node:perf_hooks";
import { chromium, firefox, webkit } from "playwright";

const PROFILE_DEFINITIONS = {
  desktop: {
    viewport: { width: 1440, height: 1200 },
    deviceScaleFactor: 1,
    isMobile: false,
    hasTouch: false,
    cpuRate: 1,
    network: null,
  },
  "fast-4g": {
    viewport: { width: 1440, height: 1200 },
    deviceScaleFactor: 1,
    isMobile: false,
    hasTouch: false,
    cpuRate: 1,
    network: {
      offline: false,
      latency: 150,
      downloadThroughput: (4 * 1024 * 1024) / 8,
      uploadThroughput: (3 * 1024 * 1024) / 8,
      connectionType: "cellular4g",
    },
  },
  "slow-4g": {
    viewport: { width: 1440, height: 1200 },
    deviceScaleFactor: 1,
    isMobile: false,
    hasTouch: false,
    cpuRate: 2,
    network: {
      offline: false,
      latency: 300,
      downloadThroughput: (1.6 * 1024 * 1024) / 8,
      uploadThroughput: (750 * 1024) / 8,
      connectionType: "cellular3g",
    },
  },
  "mobile-fast-4g": {
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 3,
    isMobile: true,
    hasTouch: true,
    cpuRate: 4,
    network: {
      offline: false,
      latency: 150,
      downloadThroughput: (4 * 1024 * 1024) / 8,
      uploadThroughput: (3 * 1024 * 1024) / 8,
      connectionType: "cellular4g",
    },
  },
};

const BROWSER_ENGINES = { chromium, firefox, webkit };

function positiveInteger(name, fallback, maximum = 10_000) {
  const raw = process.env[name];
  if (!raw) return fallback;
  const value = Number(raw);
  if (!Number.isInteger(value) || value < 1 || value > maximum) {
    throw new Error(`${name} must be an integer between 1 and ${maximum}`);
  }
  return value;
}

function percentile(values, quantile) {
  if (values.length === 0) return null;
  const sorted = [...values].sort((left, right) => left - right);
  const index = Math.min(
    sorted.length - 1,
    Math.max(0, Math.ceil(quantile * sorted.length) - 1),
  );
  return Math.round(sorted[index] * 10) / 10;
}

function summary(values) {
  const finite = values.filter(Number.isFinite);
  return {
    count: finite.length,
    min: finite.length ? Math.round(Math.min(...finite) * 10) / 10 : null,
    p50: percentile(finite, 0.5),
    p95: percentile(finite, 0.95),
    p99: percentile(finite, 0.99),
    max: finite.length ? Math.round(Math.max(...finite) * 10) / 10 : null,
  };
}

function safeError(error) {
  const value = error instanceof Error ? error.message : String(error);
  return value
    .replace(/([?&](?:token|signature|sig|key|password)=[^&\s]+)/gi, "?redacted")
    .replace(/https?:\/\/[^/\s]+/gi, "<origin>")
    .slice(0, 400);
}

function safePathClass(rawUrl) {
  try {
    const pathname = new URL(rawUrl).pathname;
    if (pathname.includes("/files/derivatives/")) return "derivative";
    if (pathname.includes("/files/")) return "original-private-media";
    return "other-image";
  } catch {
    return "other-image";
  }
}

async function installPerformanceObservers(page) {
  await page.addInitScript(() => {
    const state = {
      cls: 0,
      layoutShiftSamples: [],
      lcp: 0,
      longestEvent: 0,
      longTasks: [],
      domMutationBatches: 0,
    };
    Object.defineProperty(globalThis, "__enmotionPerformance", {
      value: state,
      configurable: false,
      enumerable: false,
      writable: false,
    });
    try {
      new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) {
          state.lcp = Math.max(state.lcp, entry.startTime);
        }
      }).observe({ type: "largest-contentful-paint", buffered: true });
    } catch {}
    try {
      new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) {
          if (entry.hadRecentInput) continue;
          state.cls += entry.value;
          if (state.layoutShiftSamples.length >= 8) continue;
          state.layoutShiftSamples.push({
            value: Math.round(entry.value * 1_000_000) / 1_000_000,
            sources: (entry.sources || []).slice(0, 3).map((source) => {
              const node = source.node;
              return {
                node: node
                  ? `${node.tagName || "node"}.${String(node.className || "")
                    .trim().split(/\s+/).slice(0, 3).join(".")}`.slice(0, 160)
                  : "unknown",
                previousRect: source.previousRect,
                currentRect: source.currentRect,
              };
            }),
          });
        }
      }).observe({ type: "layout-shift", buffered: true });
    } catch {}
    try {
      new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) {
          state.longTasks.push(entry.duration);
        }
      }).observe({ type: "longtask", buffered: true });
    } catch {}
    try {
      new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) {
          state.longestEvent = Math.max(state.longestEvent, entry.duration || 0);
        }
      }).observe({ type: "event", buffered: true, durationThreshold: 16 });
    } catch {}
    document.addEventListener("DOMContentLoaded", () => {
      const observer = new MutationObserver(() => {
        state.domMutationBatches += 1;
      });
      observer.observe(document.documentElement, {
        childList: true,
        subtree: true,
        attributes: true,
      });
    }, { once: true });
  });
}

async function applyProfile(page, browserName, profile) {
  if (browserName !== "chromium") return null;
  const session = await page.context().newCDPSession(page);
  await session.send("Network.enable");
  if (profile.network) {
    await session.send("Network.emulateNetworkConditions", profile.network);
  }
  if (profile.cpuRate > 1) {
    await session.send("Emulation.setCPUThrottlingRate", {
      rate: profile.cpuRate,
    });
  }
  return session;
}

async function loginIfNeeded(page, username, password, timeoutMs) {
  const usernameInput = page.locator('input[name="username"]');
  const authenticatedAccount = page.locator('header button[aria-expanded]').first();
  await Promise.race([
    usernameInput.waitFor({ state: "visible", timeout: timeoutMs }),
    authenticatedAccount.waitFor({ state: "visible", timeout: timeoutMs }),
  ]).catch(() => undefined);
  if (await usernameInput.isVisible().catch(() => false)) {
    await usernameInput.fill(username);
    await page.locator('input[name="password"]').fill(password);
    await page.locator('form button[type="submit"]').click();
    await usernameInput.waitFor({ state: "hidden", timeout: timeoutMs });
    await authenticatedAccount.waitFor({ state: "visible", timeout: timeoutMs });
  }
}

async function imageState(page) {
  return page.evaluate(() => {
    const viewportWidth = window.innerWidth;
    const viewportHeight = window.innerHeight;
    const images = [...document.images].filter((image) => {
      const rect = image.getBoundingClientRect();
      return rect.width > 80 && rect.height > 80;
    });
    const visible = images.filter((image) => {
      const rect = image.getBoundingClientRect();
      return (
        rect.bottom > 0
        && rect.right > 0
        && rect.top < viewportHeight
        && rect.left < viewportWidth
      );
    });
    const loaded = images.filter((image) => image.complete && image.naturalWidth > 0);
    const visibleLoaded = visible.filter(
      (image) => image.complete && image.naturalWidth > 0,
    );
    const dimensions = visibleLoaded.map((image) => {
      const rect = image.getBoundingClientRect();
      return {
        naturalWidth: image.naturalWidth,
        naturalHeight: image.naturalHeight,
        displayWidth: Math.round(rect.width),
        displayHeight: Math.round(rect.height),
        derivative: (() => {
          try {
            return new URL(image.currentSrc, location.href).pathname.includes(
              "/files/derivatives/",
            );
          } catch {
            return false;
          }
        })(),
      };
    });
    return {
      total: images.length,
      loaded: loaded.length,
      visible: visible.length,
      visibleLoaded: visibleLoaded.length,
      dimensions,
    };
  });
}

async function waitForImageCondition(page, predicate, timeoutMs) {
  const started = performance.now();
  while (performance.now() - started < timeoutMs) {
    const state = await imageState(page);
    if (predicate(state)) {
      return { elapsedMs: performance.now() - started, state };
    }
    await page.waitForTimeout(50);
  }
  return { elapsedMs: null, state: await imageState(page) };
}

async function collectVitals(page) {
  return page.evaluate(() => {
    const state = globalThis.__enmotionPerformance || {};
    const navigation = performance.getEntriesByType("navigation")[0];
    const longTasks = Array.isArray(state.longTasks) ? state.longTasks : [];
    return {
      lcp_ms: Math.round((state.lcp || 0) * 10) / 10,
      cls: Math.round((state.cls || 0) * 1_000) / 1_000,
      layout_shift_samples: Array.isArray(state.layoutShiftSamples)
        ? state.layoutShiftSamples
        : [],
      inp_ms: Math.round((state.longestEvent || 0) * 10) / 10,
      tbt_ms: Math.round(
        longTasks.reduce((total, duration) => total + Math.max(0, duration - 50), 0) * 10,
      ) / 10,
      long_task_count: longTasks.length,
      dom_mutation_batches: state.domMutationBatches || 0,
      dom_content_loaded_ms: navigation
        ? Math.round(navigation.domContentLoadedEventEnd * 10) / 10
        : null,
      load_ms: navigation ? Math.round(navigation.loadEventEnd * 10) / 10 : null,
    };
  });
}

async function measureNavigation({
  page,
  cdp,
  baseUrl,
  username,
  password,
  timeoutMs,
  cold,
}) {
  if (cold && cdp) await cdp.send("Network.clearBrowserCache");
  const errors = [];
  const network = [];
  const cdpImages = new Map();
  const cdpCacheHits = new Set();
  const consoleErrors = [];
  const started = performance.now();
  const deadline = started + timeoutMs;
  const remainingMs = () => Math.max(1, deadline - performance.now());
  let feedResponse = null;
  let feedRequestStarted = null;
  let feedResolve;
  const feedPromise = new Promise((resolve) => {
    feedResolve = resolve;
  });

  const onPageError = (error) => errors.push(safeError(error));
  const onConsole = (message) => {
    if (message.type() === "error") consoleErrors.push(message.text().slice(0, 240));
  };
  const onResponse = (response) => {
    try {
      const pathname = new URL(response.url()).pathname;
      if (
        pathname === "/library/feed"
        || pathname === "/library/feed/v3"
        || pathname === "/api/library/feed"
        || pathname === "/api/library/feed/v3"
      ) {
        feedResponse = {
          status: response.status(),
          requestMs: feedRequestStarted === null
            ? null
            : performance.now() - feedRequestStarted,
          fromNavigationMs: performance.now() - started,
          contentLength: Number(response.headers()["content-length"] || 0),
          serverTiming: response.headers()["server-timing"] || "",
        };
        feedResolve(feedResponse);
      }
    } catch {}
  };
  const onRequest = (request) => {
    try {
      const pathname = new URL(request.url()).pathname;
      if (
        pathname === "/library/feed"
        || pathname === "/library/feed/v3"
        || pathname === "/api/library/feed"
        || pathname === "/api/library/feed/v3"
      ) {
        feedRequestStarted = performance.now();
      }
    } catch {}
  };
  const onRequestFinished = async (request) => {
    if (cdp || request.resourceType() !== "image") return;
    try {
      const sizes = await request.sizes();
      network.push({
        class: safePathClass(request.url()),
        bodyBytes: Math.max(0, sizes.responseBodySize),
        cached: false,
      });
    } catch {
      network.push({
        class: safePathClass(request.url()),
        bodyBytes: 0,
        cached: false,
      });
    }
  };
  const onCdpServedFromCache = ({ requestId }) => {
    cdpCacheHits.add(requestId);
  };
  const onCdpResponse = ({ requestId, response, type }) => {
    if (type !== "Image") return;
    cdpImages.set(requestId, {
      class: safePathClass(response.url),
      cached: Boolean(
        response.fromDiskCache
        || response.fromPrefetchCache
        || response.fromServiceWorker
        || cdpCacheHits.has(requestId)
      ),
    });
  };
  const onCdpLoadingFinished = ({ requestId, encodedDataLength }) => {
    const record = cdpImages.get(requestId);
    if (!record) return;
    const cached = record.cached || cdpCacheHits.has(requestId);
    network.push({
      class: record.class,
      bodyBytes: cached ? 0 : Math.max(0, encodedDataLength || 0),
      cached,
    });
    cdpImages.delete(requestId);
    cdpCacheHits.delete(requestId);
  };

  page.on("pageerror", onPageError);
  page.on("console", onConsole);
  page.on("request", onRequest);
  page.on("response", onResponse);
  page.on("requestfinished", onRequestFinished);
  cdp?.on("Network.requestServedFromCache", onCdpServedFromCache);
  cdp?.on("Network.responseReceived", onCdpResponse);
  cdp?.on("Network.loadingFinished", onCdpLoadingFinished);
  try {
    if (page.url() === "about:blank") {
      await page.goto(`${baseUrl}/#/library`, {
        waitUntil: "domcontentloaded",
        timeout: remainingMs(),
      });
    } else {
      await page.evaluate(() => {
        history.replaceState(null, "", `${location.pathname}${location.search}#/library`);
      });
      await page.reload({
        waitUntil: "domcontentloaded",
        timeout: remainingMs(),
      });
    }
    await loginIfNeeded(page, username, password, remainingMs());
    const feed = await Promise.race([
      feedPromise,
      page.waitForTimeout(remainingMs()).then(() => null),
    ]);
    let metadataVisibleMs = null;
    while (performance.now() < deadline) {
      const state = await imageState(page);
      const emptyState = await page
        .locator("text=/No assets|暂无资产|还没有资产/i")
        .count()
        .catch(() => 0);
      if (state.total > 0 || emptyState > 0) {
        metadataVisibleMs = performance.now() - started;
        break;
      }
      await page.waitForTimeout(50);
    }
    const first = await waitForImageCondition(
      page,
      (state) => state.visibleLoaded >= 1,
      remainingMs(),
    );
    const firstThumbnailMs = first.elapsedMs === null ? null : performance.now() - started;
    const firstTwelve = await waitForImageCondition(
      page,
      (state) => state.loaded >= Math.min(12, state.total),
      remainingMs(),
    );
    const firstTwelveMs = firstTwelve.elapsedMs === null ? null : performance.now() - started;
    const viewport = await waitForImageCondition(
      page,
      (state) => state.visible > 0 && state.visibleLoaded >= state.visible,
      remainingMs(),
    );
    const viewportCompleteMs = viewport.elapsedMs === null ? null : performance.now() - started;
    await page.waitForTimeout(300);
    const vitals = await collectVitals(page);
    const finalImages = await imageState(page);
    const classCounts = {};
    let imageBodyBytes = 0;
    let imageCacheHits = 0;
    for (const record of network) {
      classCounts[record.class] = (classCounts[record.class] || 0) + 1;
      imageBodyBytes += record.bodyBytes;
      if (record.cached) imageCacheHits += 1;
    }
    const sourceWidths = finalImages.dimensions.map((item) => item.naturalWidth);
    const oversize = finalImages.dimensions.filter((item) => (
      item.naturalWidth > item.displayWidth * 2.1
      || item.naturalHeight > item.displayHeight * 2.1
    )).length;
    return {
      ok: Boolean(feed && metadataVisibleMs !== null && finalImages.total > 0),
      cold,
      feed_status: feed?.status ?? feedResponse?.status ?? null,
      feed_ms: feed?.requestMs ?? feedResponse?.requestMs ?? null,
      feed_from_navigation_ms:
        feed?.fromNavigationMs ?? feedResponse?.fromNavigationMs ?? null,
      feed_content_length: feed?.contentLength ?? feedResponse?.contentLength ?? null,
      server_timing: feed?.serverTiming ?? feedResponse?.serverTiming ?? "",
      metadata_visible_ms: metadataVisibleMs,
      first_thumbnail_ms: firstThumbnailMs,
      first_12_thumbnails_ms: firstTwelveMs,
      viewport_complete_ms: viewportCompleteMs,
      image_requests: network.length,
      image_body_bytes: imageBodyBytes,
      image_cache_hits: imageCacheHits,
      image_network_requests: network.length - imageCacheHits,
      image_request_classes: classCounts,
      visible_images: finalImages.visible,
      loaded_images: finalImages.loaded,
      source_width_p50: percentile(sourceWidths, 0.5),
      source_width_max: sourceWidths.length ? Math.max(...sourceWidths) : null,
      oversized_visible_images: oversize,
      ...vitals,
      frontend_errors: errors.length,
      console_errors: consoleErrors.length,
      error_samples: [...errors, ...consoleErrors].slice(0, 3).map(safeError),
    };
  } finally {
    page.off("pageerror", onPageError);
    page.off("console", onConsole);
    page.off("request", onRequest);
    page.off("response", onResponse);
    page.off("requestfinished", onRequestFinished);
    cdp?.off("Network.requestServedFromCache", onCdpServedFromCache);
    cdp?.off("Network.responseReceived", onCdpResponse);
    cdp?.off("Network.loadingFinished", onCdpLoadingFinished);
  }
}

async function runContext({
  browser,
  browserName,
  profile,
  storageState,
  baseUrl,
  username,
  password,
  cycles,
  timeoutMs,
  cacheMode,
}) {
  const context = await browser.newContext({
    storageState,
    viewport: profile.viewport,
    deviceScaleFactor: profile.deviceScaleFactor,
    isMobile: profile.isMobile,
    hasTouch: profile.hasTouch,
    reducedMotion: "reduce",
    locale: "en-US",
  });
  const page = await context.newPage();
  await installPerformanceObservers(page);
  const cdp = await applyProfile(page, browserName, profile);
  const results = [];
  try {
    // The context already carries the authenticated storage state. Measure its
    // first application navigation directly so setup requests cannot leak into
    // error or network counts.
    for (let cycle = 0; cycle < cycles; cycle += 1) {
      const cold = cacheMode === "cold" || (cacheMode === "both" && cycle === 0);
      results.push(await measureNavigation({
        page,
        cdp,
        baseUrl,
        username,
        password,
        timeoutMs,
        cold,
      }));
    }
  } finally {
    await context.close();
  }
  return results;
}

async function authenticatedStorageState({
  browser,
  baseUrl,
  username,
  password,
  timeoutMs,
}) {
  const context = await browser.newContext({ locale: "en-US" });
  try {
    const page = await context.newPage();
    await page.goto(`${baseUrl}/#/library`, {
      waitUntil: "domcontentloaded",
      timeout: timeoutMs,
    });
    await loginIfNeeded(page, username, password, timeoutMs);
    return await context.storageState();
  } finally {
    await context.close();
  }
}

function aggregate(runs) {
  return {
    runs: runs.length,
    successful_runs: runs.filter((run) => run.ok).length,
    false_empty_or_invalid_runs: runs.filter((run) => !run.ok).length,
    frontend_error_total: runs.reduce((total, run) => total + run.frontend_errors, 0),
    console_error_total: runs.reduce((total, run) => total + run.console_errors, 0),
    image_cache_hit_total: runs.reduce((total, run) => total + run.image_cache_hits, 0),
    image_network_request_total: runs.reduce(
      (total, run) => total + run.image_network_requests,
      0,
    ),
    feed_ms: summary(runs.map((run) => run.feed_ms)),
    metadata_visible_ms: summary(runs.map((run) => run.metadata_visible_ms)),
    first_thumbnail_ms: summary(runs.map((run) => run.first_thumbnail_ms)),
    first_12_thumbnails_ms: summary(runs.map((run) => run.first_12_thumbnails_ms)),
    viewport_complete_ms: summary(runs.map((run) => run.viewport_complete_ms)),
    image_body_bytes: summary(runs.map((run) => run.image_body_bytes)),
    lcp_ms: summary(runs.map((run) => run.lcp_ms)),
    inp_ms: summary(runs.map((run) => run.inp_ms)),
    cls: summary(runs.map((run) => run.cls)),
    tbt_ms: summary(runs.map((run) => run.tbt_ms)),
  };
}

async function main() {
  const baseUrl = (process.env.ENMOTION_PERF_BASE_URL || "http://127.0.0.1:8080")
    .replace(/\/+$/, "");
  const username = process.env.ENMOTION_PERF_USERNAME || "";
  const password = process.env.ENMOTION_PERF_PASSWORD || "";
  const secondaryUsername = process.env.ENMOTION_PERF_SECONDARY_USERNAME || "";
  const secondaryPassword = process.env.ENMOTION_PERF_SECONDARY_PASSWORD || "";
  if (!username || !password) {
    throw new Error("ENMOTION_PERF_USERNAME and ENMOTION_PERF_PASSWORD are required");
  }
  if (Boolean(secondaryUsername) !== Boolean(secondaryPassword)) {
    throw new Error("Both secondary credential variables must be supplied together");
  }
  const browserName = process.env.ENMOTION_PERF_BROWSER || "chromium";
  const engine = BROWSER_ENGINES[browserName];
  if (!engine) throw new Error(`Unsupported browser engine: ${browserName}`);
  const profileName = process.env.ENMOTION_PERF_PROFILE || "desktop";
  const profile = PROFILE_DEFINITIONS[profileName];
  if (!profile) throw new Error(`Unsupported performance profile: ${profileName}`);
  const cycles = positiveInteger("ENMOTION_PERF_CYCLES", 1, 100);
  const sessions = positiveInteger("ENMOTION_PERF_SESSIONS", 1, 20);
  const timeoutMs = positiveInteger("ENMOTION_PERF_TIMEOUT_MS", 90_000, 300_000);
  const cacheMode = process.env.ENMOTION_PERF_CACHE_MODE || "both";
  if (!["cold", "warm", "both"].includes(cacheMode)) {
    throw new Error("ENMOTION_PERF_CACHE_MODE must be cold, warm, or both");
  }

  const launchOptions = { headless: true };
  if (browserName === "chromium" && !process.env.ENMOTION_PERF_BROWSER_EXECUTABLE) {
    launchOptions.channel = process.env.ENMOTION_PERF_CHROMIUM_CHANNEL || "chrome";
  }
  if (process.env.ENMOTION_PERF_BROWSER_EXECUTABLE) {
    launchOptions.executablePath = process.env.ENMOTION_PERF_BROWSER_EXECUTABLE;
  }
  const browser = await engine.launch(launchOptions);
  let runs;
  try {
    // Prepare sessions before starting the concurrent measurement. This tests
    // simultaneous authenticated users without turning the login rate limiter
    // into the load target or creating an unrealistic login burst.
    const primaryStorageState = await authenticatedStorageState({
      browser,
      baseUrl,
      username,
      password,
      timeoutMs,
    });
    const secondaryStorageState = secondaryUsername
      ? await authenticatedStorageState({
          browser,
          baseUrl,
          username: secondaryUsername,
          password: secondaryPassword,
          timeoutMs,
        })
      : null;
    const work = Array.from({ length: sessions }, (_, index) => {
      const secondary = secondaryUsername && index % 2 === 1;
      return runContext({
        browser,
        browserName,
        profile,
        storageState: secondary ? secondaryStorageState : primaryStorageState,
        baseUrl,
        username: secondary ? secondaryUsername : username,
        password: secondary ? secondaryPassword : password,
        cycles,
        timeoutMs,
        cacheMode,
      });
    });
    runs = (await Promise.all(work)).flat();
  } finally {
    await browser.close();
  }

  const report = {
    schema_version: 1,
    created_at: new Date().toISOString(),
    run_label: process.env.ENMOTION_PERF_RUN_LABEL || "unlabeled",
    origin: new URL(baseUrl).origin,
    browser: browserName,
    profile: profileName,
    cache_mode: cacheMode,
    sessions,
    cycles_per_session: cycles,
    workspace_mode: secondaryUsername ? "alternating-isolated-workspaces" : "single-workspace",
    aggregate: aggregate(runs),
    runs,
  };
  const output = process.env.ENMOTION_PERF_OUTPUT;
  if (output) {
    const destination = path.resolve(output);
    await fs.mkdir(path.dirname(destination), { recursive: true });
    await fs.writeFile(destination, `${JSON.stringify(report, null, 2)}\n`, {
      mode: 0o600,
    });
  }
  process.stdout.write(`${JSON.stringify(report)}\n`);
  if (report.aggregate.successful_runs !== report.aggregate.runs) {
    process.exitCode = 1;
  }
}

main().catch((error) => {
  process.stderr.write(`${safeError(error)}\n`);
  process.exitCode = 1;
});
