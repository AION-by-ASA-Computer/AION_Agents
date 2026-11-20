"use client";

/**
 * Brand mark of chat-ui (login pages, thread sidebar).
 *
 * Shows the tenant logo when the admin uploaded one (light version, plus the
 * optional dark one swapped by theme) followed by the mandatory "Powered by
 * AION" link; otherwise the bundled AION SVG. While branding is still unknown
 * (first visit, nothing cached) it renders an empty box of the same height,
 * so neither logo flashes before the real one.
 */

import { cn } from "@/lib/cn";
import { Logo } from "@/components/ui/logo";
import { brandingAssetUrl, useBranding } from "@/lib/branding/branding";
import { PoweredByAion } from "./PoweredByAion";

/**
 * Box of a custom logo per placement (`box` = wrapper width, `img` = height cap).
 *
 * Custom logos get a larger box than the AION SVG: the AION mark has no
 * margins and fills its box, while uploaded logos usually carry empty margins
 * and any aspect ratio, so in the same box they looked noticeably smaller. The
 * image takes the full box width and is capped in height (`object-contain`),
 * so wide logos fill the width, tall/square ones the height, and small rasters
 * are scaled up instead of staying tiny.
 */
const CUSTOM_SIZES = {
  /** Login, password and first-setup pages: centred above the card. */
  auth: { box: "max-w-[260px] sm:max-w-[300px]", img: "max-h-20 sm:max-h-24" },
  /** Sidebar header: as wide as the space next to the collapse button. */
  sidebar: { box: "max-w-full", img: "max-h-12" },
  /** Mobile top bar (56 px high): room for the logo plus the attribution line. */
  topbar: { box: "max-w-[150px]", img: "max-h-9" },
} as const;

export type BrandPlacement = keyof typeof CUSTOM_SIZES;

type ChatBrandProps = {
  /** Size classes of the AION SVG (and of the placeholder while branding loads). */
  className?: string;
  /** Where the mark is shown: picks the box of a custom logo. */
  placement?: BrandPlacement;
  /** Horizontal alignment of logo and attribution. */
  align?: "start" | "center";
};

export function ChatBrand({ className = "max-w-[140px]", placement = "sidebar", align = "start" }: ChatBrandProps) {
  const branding = useBranding();
  const size = cn("h-15 w-auto", className);

  if (!branding) return <div aria-hidden className={cn(size, "w-[190px]")} />;
  if (!branding.custom_logo) return <Logo width="190" className={size} />;

  const light = branding.logo_light ?? branding.logo_dark;
  const dark = branding.logo_dark ?? light;
  const custom = CUSTOM_SIZES[placement];
  const img = cn(
    "block h-auto w-full object-contain",
    align === "center" ? "object-center" : "object-left",
    custom.img,
  );
  return (
    <div
      className={cn(
        "flex w-full min-w-0 flex-col gap-1",
        align === "center" ? "items-center" : "items-start",
        custom.box,
      )}
    >
      {/* Plain <img>: assets are served by the API (not Next static files) and are tiny. */}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      {light && <img src={brandingAssetUrl(light)} alt="Logo" className={cn(img, "dark:hidden")} />}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      {dark && <img src={brandingAssetUrl(dark)} alt="Logo" className={cn(img, "hidden dark:block")} />}
      <PoweredByAion />
    </div>
  );
}
