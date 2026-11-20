/**
 * Tenant branding colours -> CSS theme tokens.
 *
 * The admin sets one primary colour per theme (admin-ui → Settings → Brand,
 * stored by the backend as strict `#rrggbb`). This module turns them into the
 * HSL-channel tokens used by `styles/theme-tokens.css` (`--primary`, `--ring`,
 * `--sidebar-*`, light `--accent`) and picks a readable foreground.
 * Shared verbatim with chat-ui (`chat-ui/lib/branding/colors.ts`).
 */

const HEX_RE = /^#[0-9a-fA-F]{6}$/;

/** True for a strict `#rrggbb` string (the only shape that may reach a CSS rule). */
export function isHexColor(value: unknown): value is string {
  return typeof value === "string" && HEX_RE.test(value);
}

function rgb(hex: string): [number, number, number] {
  return [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255) as [number, number, number];
}

/** `#rrggbb` -> `"H S% L%"` channels, the format of the theme tokens. */
export function hexToHslChannels(hex: string): string {
  const [r, g, b] = rgb(hex);
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const l = (max + min) / 2;
  let h = 0;
  let s = 0;
  if (max !== min) {
    const d = max - min;
    s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
    if (max === r) h = (g - b) / d + (g < b ? 6 : 0);
    else if (max === g) h = (b - r) / d + 2;
    else h = (r - g) / d + 4;
    h *= 60;
  }
  const round = (n: number) => Math.round(n * 10) / 10;
  return `${round(h)} ${round(s * 100)}% ${round(l * 100)}%`;
}

/** WCAG relative luminance of `#rrggbb`. */
export function relativeLuminance(hex: string): number {
  const [r, g, b] = rgb(hex).map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** WCAG contrast ratio between two `#rrggbb` colours (1..21). */
export function contrastRatio(a: string, b: string): number {
  const la = relativeLuminance(a);
  const lb = relativeLuminance(b);
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

/** Foreground channels for text on `bg`: white, or near-black when white would be less readable. */
export function readableForegroundChannels(bg: string): string {
  return contrastRatio(bg, "#ffffff") >= contrastRatio(bg, "#171717") ? "0 0% 100%" : "0 0% 9%";
}

/**
 * Tailwind `rose-*` scale derived from the brand colour: chat-ui writes its
 * brand accents (CTA gradients, highlights, glows) as `rose-*` utilities, so
 * remapping the scale recolours them all without touching `red-*`, which
 * stays the error/danger palette. 500 is the brand colour itself; lighter
 * steps mix in white, darker ones black (percentages = share of brand colour).
 */
const ROSE_SCALE: ReadonlyArray<[step: number, brandPct: number, mixWith: "white" | "black"]> = [
  [50, 8, "white"],
  [100, 15, "white"],
  [200, 30, "white"],
  [300, 50, "white"],
  [400, 75, "white"],
  [600, 85, "black"],
  [700, 70, "black"],
  [800, 55, "black"],
  [900, 45, "black"],
  [950, 30, "black"],
];

function roseScale(hex: string): string[] {
  const mix = (pct: number, other: string) => `color-mix(in oklab, ${hex} ${pct}%, ${other})`;
  return [
    `--color-rose-500: ${hex};`,
    ...ROSE_SCALE.map(([step, pct, other]) => `--color-rose-${step}: ${mix(pct, other)};`),
    // End stop of the CTA gradients (`to-brand-end`, chat-ui/app/globals.css).
    `--color-brand-end: ${mix(85, "black")};`,
    `--color-brand-end-hover: ${mix(70, "black")};`,
  ];
}

/** Token overrides for one theme; `accent` only in light, where the accent is the brand colour. */
function themeBlock(hex: string, withAccent: boolean): string {
  const c = hexToHslChannels(hex);
  const fg = readableForegroundChannels(hex);
  const lines = [
    `--primary: ${c};`,
    `--primary-foreground: ${fg};`,
    `--ring: ${c};`,
    `--sidebar-primary: ${c};`,
    `--sidebar-primary-foreground: ${fg};`,
    `--sidebar-ring: ${c};`,
  ];
  if (withAccent) lines.push(`--accent: ${c};`, `--accent-foreground: ${fg};`);
  lines.push(...roseScale(hex));
  return lines.join(" ");
}

/**
 * CSS that overrides the brand tokens, or "" when both colours are default.
 * The dark theme falls back to the light colour when only that one is set.
 * Selectors mirror `theme-tokens.css` with an extra `:root` so they win on
 * specificity, whatever order Next.js inserts its stylesheets in.
 */
export function buildBrandingCss(light: string | null | undefined, dark: string | null | undefined): string {
  const l = isHexColor(light) ? light : null;
  const d = isHexColor(dark) ? dark : l;
  const out: string[] = [];
  if (l) out.push(`html:root[data-theme="light"] { ${themeBlock(l, true)} }`);
  if (d) out.push(`html:root[data-theme="dark"], html:root:not([data-theme]) { ${themeBlock(d, false)} }`);
  return out.join("\n");
}
