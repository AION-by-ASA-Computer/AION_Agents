"use client";

/**
 * Applies the tenant branding to the whole page (mounted once in the root layout).
 *
 * - Fetches `GET /branding` on load and whenever `refreshBranding()` runs.
 * - Keeps `<style id="aion-branding">` in sync with the primary colours (the
 *   inline head script created it from cache before paint; here it is
 *   created, updated or emptied with fresh data).
 * - Swaps the favicon when the tenant uploaded one, restoring the default
 *   AION icon when it is removed.
 * Renders nothing.
 */

import { useEffect } from "react";
import { brandingAssetUrl, refreshBranding, useBranding } from "@/lib/branding/branding";
import { buildBrandingCss } from "@/lib/branding/colors";

const STYLE_ID = "aion-branding";
/** Our own icon link: Next.js re-creates its metadata links on navigation, so we never edit those in place. */
const FAVICON_ID = "aion-branding-favicon";
/** Next's icon links are parked under this rel while a tenant favicon is active (original kept in data-aion-rel). */
const PARKED_REL = "x-aion-parked-icon";

function applyColours(css: string) {
  let el = document.getElementById(STYLE_ID) as HTMLStyleElement | null;
  if (!el) {
    el = document.createElement("style");
    el.id = STYLE_ID;
    document.head.appendChild(el);
  }
  if (el.textContent !== css) el.textContent = css;
}

/**
 * Shows the tenant favicon, or restores the default one. Idempotent: it is
 * re-run whenever `<head>` changes, and only touches the DOM when needed, so
 * the MutationObserver below does not loop.
 */
function applyFavicon(href: string | null, mime: string) {
  const own = document.getElementById(FAVICON_ID) as HTMLLinkElement | null;
  if (href) {
    // Park every other icon so browsers cannot pick the AION one instead.
    document.querySelectorAll<HTMLLinkElement>('link[rel~="icon"]').forEach((link) => {
      if (link.id === FAVICON_ID) return;
      link.setAttribute("data-aion-rel", link.rel);
      link.rel = PARKED_REL;
    });
    const link = own ?? document.createElement("link");
    if (!own) {
      link.id = FAVICON_ID;
      link.rel = "icon";
    }
    if (link.getAttribute("href") !== href) link.href = href;
    if (mime && link.type !== mime) link.type = mime;
    if (!own) document.head.appendChild(link);
    return;
  }
  // No custom favicon: drop ours and give the bundled AION icons back.
  own?.remove();
  document.querySelectorAll<HTMLLinkElement>(`link[rel="${PARKED_REL}"]`).forEach((link) => {
    link.rel = link.getAttribute("data-aion-rel") || "icon";
    link.removeAttribute("data-aion-rel");
  });
}

export function BrandingSync() {
  const branding = useBranding();

  useEffect(() => {
    void refreshBranding();
  }, []);

  useEffect(() => {
    if (!branding) return;
    applyColours(buildBrandingCss(branding.primary_color_light, branding.primary_color_dark));
    const href = branding.favicon ? brandingAssetUrl(branding.favicon) : null;
    const mime = branding.favicon?.mime ?? "";
    applyFavicon(href, mime);
    // Next.js swaps metadata <link>s on client navigation: re-park them as they appear.
    const observer = new MutationObserver(() => applyFavicon(href, mime));
    observer.observe(document.head, { childList: true });
    return () => observer.disconnect();
  }, [branding]);

  return null;
}
