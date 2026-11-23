"use client";

/**
 * Editor of one access rule (today: which agent profiles), used for groups
 * (`GroupRulesDialog`) and for a user's direct rule (`UserAccessDialog`).
 *
 * Four modes map to the backend rule values (see `shared.ts`):
 * - "none"    -> `null`  no rule: this subject does not limit profiles
 * - "all"     -> `["*"]` every profile
 * - "some"    -> ids     only the checked profiles
 * - "nothing" -> `[]`    no profile at all
 * Controlled component: the parent owns the value and persists it.
 */

import { cn } from "@/lib/cn";
import { k, type ProfileOption, type Rule, type T } from "./shared";

type Mode = "none" | "all" | "some" | "nothing";

function modeOf(rule: Rule): Mode {
  if (rule === null) return "none";
  if (rule.includes("*")) return "all";
  return rule.length === 0 ? "nothing" : "some";
}

/**
 * @param subject "user" | "group": picks the explanation of the "no rule" mode.
 */
export function RuleEditor({
  value,
  onChange,
  profiles,
  subject,
  t,
}: {
  value: Rule;
  onChange: (rule: Rule) => void;
  profiles: ProfileOption[];
  subject: "user" | "group";
  t: T;
}) {
  const mode = modeOf(value);
  const selected = mode === "some" ? (value as string[]) : [];

  const setMode = (m: Mode) => {
    if (m === "none") onChange(null);
    else if (m === "all") onChange(["*"]);
    else if (m === "nothing") onChange([]);
    // Switching to "some" keeps the current picks; an empty pick list would be
    // the "nothing" rule, so start from the first profile as a hint.
    else onChange(selected.length ? selected : profiles.slice(0, 1).map((p) => p.slug));
  };
  const toggle = (slug: string) => {
    const next = selected.includes(slug) ? selected.filter((s) => s !== slug) : [...selected, slug];
    onChange(next);
  };

  return (
    <div className="flex flex-col gap-3">
      <div role="radiogroup" aria-label={t(`${k}.rule.aria`)} className="grid gap-1.5 sm:grid-cols-2">
        {(["none", "all", "some", "nothing"] as const).map((m) => (
          <label
            key={m}
            className={cn(
              "flex cursor-pointer items-start gap-2.5 rounded-xl border px-3 py-2.5 text-sm transition-colors",
              mode === m ? "border-foreground/40 bg-secondary" : "border-border hover:bg-secondary/60",
            )}
          >
            <input type="radio" name="rule-mode" checked={mode === m} onChange={() => setMode(m)} className="focus-ring mt-0.5 accent-foreground" />
            <span className="min-w-0">
              <span className="block font-medium">{t(`${k}.rule.mode.${m}`)}</span>
              <span className="mt-0.5 block text-xs text-muted-foreground">
                {t(`${k}.rule.mode_hint.${m === "none" ? `none_${subject}` : m}`)}
              </span>
            </span>
          </label>
        ))}
      </div>

      {mode === "some" && (
        <>
          <ul className="-mx-1 flex max-h-[18rem] flex-col gap-0.5 overflow-y-auto px-1">
            {profiles.map((p) => {
              const on = selected.includes(p.slug);
              return (
                <li key={p.slug}>
                  <label className={cn("flex cursor-pointer items-start gap-3 rounded-xl px-3 py-2", on ? "bg-secondary" : "hover:bg-secondary/60")}>
                    <input type="checkbox" checked={on} onChange={() => toggle(p.slug)} className="focus-ring mt-1 h-4 w-4 shrink-0 accent-foreground" />
                    <span className="min-w-0">
                      <span className="block truncate text-sm font-medium">{p.name}</span>
                      {p.description && <span className="mt-0.5 block line-clamp-2 text-xs text-muted-foreground">{p.description}</span>}
                    </span>
                  </label>
                </li>
              );
            })}
          </ul>
          {profiles.length === 0 && <p className="py-4 text-center text-sm text-muted-foreground">{t(`${k}.rule.no_profiles`)}</p>}
          <p className="text-xs text-muted-foreground">{t(`${k}.rule.selected`, { count: selected.length })}</p>
        </>
      )}
    </div>
  );
}
