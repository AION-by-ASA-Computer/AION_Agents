/**
 * Tenant branding store (logo, favicon, primary colours) for chat-ui.
 *
 * Data comes from the public `GET /branding` (no auth, so login pages are
 * branded too). The last value is cached in localStorage so that:
 *  - the inline `<head>` script in `app/layout.tsx` can apply the colours
 *    before first paint (`aion-branding-css`), and
 *  - `ChatBrand` can show the right logo immediately on reload.
 * Exposed as a tiny external store (`useBranding`) so every logo updates
 * when `refreshBranding()` refetches. Mirrors `admin-ui/lib/branding/branding.ts`.
 */

import { useSyncExternalStore } from "react";
import { apiBase } from "../config";
import { buildBrandingCss } from "./colors";
import { BRANDING_CSS_STORAGE_KEY, BRANDING_STORAGE_KEY } from "./init-script";

export type BrandingAsset = { url: string; mime: string };

export type Branding = {
  primary_color_light: string | null;
  primary_color_dark: string | null;
  logo_light: BrandingAsset | null;
  logo_dark: BrandingAsset | null;
  favicon: BrandingAsset | null;
  /** True when a custom logo replaces AION's: "Powered by AION" must then be shown. */
  custom_logo: boolean;
};

export const DEFAULT_BRANDING: Branding = {
  primary_color_light: null,
  primary_color_dark: null,
  logo_light: null,
  logo_dark: null,
  favicon: null,
  custom_logo: false,
};


function parseAsset(v: unknown): BrandingAsset | null {
  if (!v || typeof v !== "object") return null;
  const a = v as Record<string, unknown>;
  return typeof a.url === "string" && a.url.startsWith("/branding/")
    ? { url: a.url, mime: typeof a.mime === "string" ? a.mime : "" }
    : null;
}

/** Normalises untrusted JSON (network or localStorage) into a `Branding`. */
function parseBranding(j: unknown): Branding {
  const o = (j && typeof j === "object" ? j : {}) as Record<string, unknown>;
  const logo_light = parseAsset(o.logo_light);
  const logo_dark = parseAsset(o.logo_dark);
  return {
    primary_color_light: typeof o.primary_color_light === "string" ? o.primary_color_light : null,
    primary_color_dark: typeof o.primary_color_dark === "string" ? o.primary_color_dark : null,
    logo_light,
    logo_dark,
    favicon: parseAsset(o.favicon),
    // Derived locally as well: a custom logo always implies the attribution.
    custom_logo: Boolean(o.custom_logo) || Boolean(logo_light || logo_dark),
  };
}

/** Absolute URL of a branding asset (`/branding/...` is relative to the API base). */
export function brandingAssetUrl(asset: BrandingAsset): string {
  return `${apiBase()}${asset.url}`;
}

// --- external store ----------------------------------------------------------

/** `undefined` = not known yet (no cache, fetch pending): logos render a placeholder. */
let current: Branding | undefined;
let initialised = false;
const listeners = new Set<() => void>();

function readCache(): Branding | undefined {
  try {
    const raw = window.localStorage.getItem(BRANDING_STORAGE_KEY);
    return raw ? parseBranding(JSON.parse(raw)) : undefined;
  } catch {
    return undefined;
  }
}

function writeCache(b: Branding) {
  try {
    window.localStorage.setItem(BRANDING_STORAGE_KEY, JSON.stringify(b));
    window.localStorage.setItem(BRANDING_CSS_STORAGE_KEY, buildBrandingCss(b.primary_color_light, b.primary_color_dark));
  } catch {
    /* storage unavailable (private mode): branding still works for this page */
  }
}

function setBranding(b: Branding) {
  current = b;
  writeCache(b);
  listeners.forEach((l) => l());
}

function getSnapshot(): Branding | undefined {
  if (!initialised && typeof window !== "undefined") {
    initialised = true;
    current = readCache();
  }
  return current;
}

function subscribe(cb: () => void) {
  listeners.add(cb);
  return () => listeners.delete(cb);
}

let inflight: Promise<Branding> | null = null;

/**
 * Fetches `GET /branding` (one request per page unless `force`) and updates
 * every subscriber. On error keeps the cached value, or falls back to AION defaults.
 */
export function refreshBranding(force = false): Promise<Branding> {
  if (!force && inflight) return inflight;
  inflight = (async () => {
    try {
      const r = await fetch(`${apiBase()}/branding`, { cache: force ? "no-store" : "default" });
      if (!r.ok) throw new Error(String(r.status));
      const b = parseBranding(await r.json());
      setBranding(b);
      return b;
    } catch {
      const fallback = getSnapshot() ?? DEFAULT_BRANDING;
      if (current === undefined) setBranding(fallback);
      return fallback;
    }
  })();
  return inflight;
}

/** Current branding; `undefined` until known (render a same-size placeholder, never the AION logo). */
export function useBranding(): Branding | undefined {
  return useSyncExternalStore(subscribe, getSnapshot, () => undefined);
}
