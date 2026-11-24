"use client";

/**
 * Editor of one access rule for one resource type (profiles, skills, MCP
 * servers or native tool groups), used for groups (`GroupRulesDialog`) and for
 * a user's direct rules (`UserAccessDialog`) through `RulesTabs`.
 *
 * Four modes map to the backend rule values (see `shared.ts`):
 * - "none"    -> `null`  no rule: this subject does not limit the type
 * - "all"     -> `["*"]` every id
 * - "some"    -> ids     only the checked ids
 * - "nothing" -> `[]`    nothing at all
 * "some" with nothing checked is also `[]` (same effect as "nothing"): the
 * editor never picks or unpicks items for the admin, so the parent remembers
 * that the admin chose "some" (`someWhileEmpty`) and the mode does not jump.
 * Controlled component: the parent owns the value and persists it. Ids of a
 * rule that are not in the catalogue (glob patterns such as `sql_*` written
 * through the API, or removed items) are listed and kept until unchecked.
 */

import { useMemo, useState } from "react";
import { cn } from "@/lib/cn";
import { SearchField } from "@/components/ui/SearchField";
import { k, type ResourceOption, type ResourceType, type Rule, type T } from "./shared";

type Mode = "none" | "all" | "some" | "nothing";

/** Above this many options the list gets a search field (skills: ~70). */
const SEARCH_FROM = 10;

/** Mode shown for a rule; `[]` is "some" when the admin picked "some" and unchecked everything. */
function modeOf(rule: Rule, someWhileEmpty: boolean): Mode {
  if (rule === null) return "none";
  if (rule.includes("*")) return "all";
  if (rule.length > 0) return "some";
  return someWhileEmpty ? "some" : "nothing";
}

/**
 * @param resourceType picks the wording ("Tutti i profili", "Tutte le skill", …)
 * @param options      ids that exist for the type (`GET /admin/access/catalog`)
 * @param subject      "user" | "group": picks the explanation of the "no rule" mode
 * @param someWhileEmpty the admin chose "some" for this type (kept by `RulesTabs` across tabs)
 * @param onSomeWhileEmpty records the mode choice, so an empty "some" stays "some"
 */
export function RuleEditor({
  value,
  onChange,
  resourceType,
  options,
  subject,
  t,
  someWhileEmpty = false,
  onSomeWhileEmpty,
}: {
  value: Rule;
  onChange: (rule: Rule) => void;
  resourceType: ResourceType;
  options: ResourceOption[];
  subject: "user" | "group";
  t: T;
  someWhileEmpty?: boolean;
  onSomeWhileEmpty?: (v: boolean) => void;
}) {
  const [query, setQuery] = useState("");
  const mode = modeOf(value, someWhileEmpty);
  const selected = useMemo(() => (mode === "some" ? (value as string[]) : []), [mode, value]);
  const tt = (key: string, vars?: Record<string, string | number>) => t(`${k}.types.${resourceType}.${key}`, vars);

  // Catalogue plus any selected id the catalogue does not know (pattern / removed item).
  const allOptions = useMemo(() => {
    const known = new Set(options.map((o) => o.id));
    const extra = selected.filter((id) => !known.has(id)).map((id) => ({ id, name: id, description: t(`${k}.rule.not_in_catalog`) }));
    return [...extra, ...options];
  }, [options, selected, t]);
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return allOptions;
    return allOptions.filter((o) => [o.id, o.name, o.description].some((v) => (v || "").toLowerCase().includes(q)));
  }, [allOptions, query]);

  const setMode = (m: Mode) => {
    onSomeWhileEmpty?.(m === "some");
    if (m === "none") onChange(null);
    else if (m === "all") onChange(["*"]);
    else if (m === "nothing") onChange([]);
    // "some" starts from the current picks (none when coming from another
    // mode): the admin chooses every item, nothing is pre-selected.
    else onChange(selected);
  };
  const toggle = (id: string) => {
    const next = selected.includes(id) ? selected.filter((s) => s !== id) : [...selected, id];
    onChange(next);
  };

  const modeLabel = (m: Mode) => (m === "none" ? t(`${k}.rule.mode_none`) : tt(m));
  const modeHint = (m: Mode) => (m === "none" ? tt(`none_${subject}`) : tt(`${m}_hint`));

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
            <input type="radio" name={`rule-mode-${resourceType}`} checked={mode === m} onChange={() => setMode(m)} className="focus-ring mt-0.5 accent-foreground" />
            <span className="min-w-0">
              <span className="block font-medium">{modeLabel(m)}</span>
              <span className="mt-0.5 block text-xs text-muted-foreground">{modeHint(m)}</span>
            </span>
          </label>
        ))}
      </div>

      {mode === "some" && (
        <>
          {allOptions.length > SEARCH_FROM && <SearchField value={query} onChange={setQuery} placeholder={tt("search")} />}
          <ul className="-mx-1 flex max-h-[18rem] flex-col gap-0.5 overflow-y-auto px-1">
            {shown.map((o) => {
              const on = selected.includes(o.id);
              return (
                <li key={o.id}>
                  <label className={cn("flex cursor-pointer items-start gap-3 rounded-xl px-3 py-2", on ? "bg-secondary" : "hover:bg-secondary/60")}>
                    <input type="checkbox" checked={on} onChange={() => toggle(o.id)} className="focus-ring mt-1 h-4 w-4 shrink-0 accent-foreground" />
                    <span className="min-w-0">
                      <span className="block truncate text-sm font-medium">{o.name}</span>
                      {o.name !== o.id && <span className="block truncate font-mono text-xs text-muted-foreground">{o.id}</span>}
                      {o.description && <span className="mt-0.5 block line-clamp-2 text-xs text-muted-foreground">{o.description}</span>}
                    </span>
                  </label>
                </li>
              );
            })}
          </ul>
          {allOptions.length === 0 && <p className="py-4 text-center text-sm text-muted-foreground">{tt("empty")}</p>}
          {allOptions.length > 0 && shown.length === 0 && <p className="py-4 text-center text-sm text-muted-foreground">{t(`${k}.rule.no_match`)}</p>}
          <p className="text-xs text-muted-foreground">
            {selected.length === 0 ? tt("some_empty_hint") : tt("selected", { count: selected.length })}
          </p>
        </>
      )}
    </div>
  );
}
