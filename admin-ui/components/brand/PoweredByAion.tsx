/**
 * "Powered by AION" attribution shown under every custom tenant logo.
 *
 * Product rule: a tenant may replace the AION logo, but the attribution with a
 * link to aion-asa.com must always stay visible next to it. It cannot be
 * disabled and the string is intentionally not translated (it is a brand mark).
 * Shared verbatim with chat-ui.
 */

import { cn } from "@/lib/cn";

export const AION_SITE_URL = "https://aion-asa.com";

export function PoweredByAion({ className }: { className?: string }) {
  return (
    <a
      href={AION_SITE_URL}
      target="_blank"
      rel="noopener noreferrer"
      className={cn(
        "whitespace-nowrap text-[10px] leading-none text-muted-foreground transition-colors hover:text-foreground",
        className,
      )}
    >
      Powered by <span className="font-semibold">AION</span>
    </a>
  );
}
