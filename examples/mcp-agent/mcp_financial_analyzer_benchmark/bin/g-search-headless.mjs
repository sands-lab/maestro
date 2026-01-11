#!/usr/bin/env node

/**
 * Wrapper that forces g-search-mcp to run Chrome in headless mode.
 *
 * The upstream server falls back to launching a headed browser when it thinks a
 * CAPTCHA needs manual solving, which crashes inside our sandbox because no X
 * server is available. We patch Playwright's chromium.launch method so the
 * process never tries to open a real window.
 */

import path from "node:path";
import { pathToFileURL } from "node:url";

const FALLBACK_ROOTS = (() => {
  const roots = [];
  if (process.env.G_SEARCH_GLOBAL_NODE_ROOT) {
    roots.push(process.env.G_SEARCH_GLOBAL_NODE_ROOT);
  }
  if (process.env.NODE_PATH) {
    roots.push(
      ...process.env.NODE_PATH
        .split(path.delimiter)
        .map((entry) => entry.trim())
        .filter(Boolean)
    );
  }
  return roots;
})();

async function loadPlaywright() {
  try {
    return await import("playwright");
  } catch (error) {
    for (const root of FALLBACK_ROOTS) {
      if (!root) continue;
      const candidate = path.join(root, "playwright", "index.js");
      try {
        return await import(pathToFileURL(candidate).href);
      } catch {
        // Try next candidate
      }
    }

    throw error;
  }
}

const playwrightModule = await loadPlaywright();
const chromium =
  playwrightModule.chromium ??
  playwrightModule.default?.chromium ??
  null;

if (!chromium) {
  throw new Error("Unable to locate Playwright's chromium export");
}

const originalLaunch = chromium.launch.bind(chromium);

const disableForce = (process.env.G_SEARCH_FORCE_HEADLESS || "")
  .trim()
  .toLowerCase();
const shouldForceHeadless =
  disableForce === "0" ||
  disableForce === "false" ||
  disableForce === "off" ||
  disableForce === "no"
    ? false
    : true;

function enforceHeadlessLaunch(originalFn) {
  return function patched(options = {}, ...rest) {
    const patchedOptions = { ...options, headless: true };
    return originalFn(patchedOptions, ...rest);
  };
}

if (shouldForceHeadless) {
  chromium.launch = enforceHeadlessLaunch(originalLaunch);
  if (typeof chromium.launchPersistentContext === "function") {
    chromium.launchPersistentContext = enforceHeadlessLaunch(
      chromium.launchPersistentContext.bind(chromium)
    );
  }
}

async function loadGSearchServer() {
  try {
    return await import("g-search-mcp/build/index.js");
  } catch (error) {
    for (const root of FALLBACK_ROOTS) {
      if (!root) continue;
      const candidate = path.join(root, "g-search-mcp", "build", "index.js");
      try {
        return await import(pathToFileURL(candidate).href);
      } catch {
        // Try the next candidate
      }
    }
    throw error;
  }
}

await loadGSearchServer();
