/**
 * Translated message for a failed private-profile call: known backend codes
 * (`src/access/private_profiles.py`) get a friendly text, anything else
 * shows the raw message.
 */

import { MyProfilesError } from "@/lib/api/my-profiles";

type T = (key: string, vars?: Record<string, string | number>) => string;

const KNOWN = new Set(["items_not_allowed", "private_profiles_limit", "private_profiles_not_allowed", "profile_not_found", "wizard_failed"]);

export function describeSaveError(err: unknown, t: T): string {
  if (err instanceof MyProfilesError && KNOWN.has(err.code)) return t(`myProfilesPage.error_${err.code}`);
  return t("myProfilesPage.error_generic", { error: err instanceof Error ? err.message : String(err) });
}
