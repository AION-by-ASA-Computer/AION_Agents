/**
 * Pre-paint branding bootstrap, kept free of React imports so the server
 * root layout (`app/layout.tsx`) can embed it in `<head>`.
 * The store in `./branding.ts` writes these keys; this script only reads them.
 */

/** localStorage keys, shared with admin-ui (same origin behind Caddy) and the inline head script. */
export const BRANDING_STORAGE_KEY = "aion-branding";
export const BRANDING_CSS_STORAGE_KEY = "aion-branding-css";

/**
 * Inline `<head>` script: re-applies the cached colour CSS before first paint
 * so a branded tenant never flashes the AION red. Kept tiny and dependency-free.
 */
export const brandingInitScript = `
  try {
    var css = localStorage.getItem('${BRANDING_CSS_STORAGE_KEY}');
    if (css) {
      var el = document.createElement('style');
      el.id = 'aion-branding';
      el.textContent = css;
      document.head.appendChild(el);
    }
  } catch (e) {}
`;
