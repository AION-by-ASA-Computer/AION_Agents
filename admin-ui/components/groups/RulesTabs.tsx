"use client";

/**
 * Tabbed rule editor (Profili · Skill · MCP · Tool nativi · Funzioni ·
 * Pannello admin) shared
 * by the group rules dialog and the user "Access" dialog. "Funzioni" holds
 * end-user capabilities (today: creating private profiles in chat-ui); their
 * names come from i18n (`admin_groups.capabilities.<id>`), not the backend.
 *
 * Each tab hosts a `RuleEditor` for one resource type and shows a short
 * summary of the current rule, so the admin sees at a glance which types are
 * limited. For skills, MCP servers and native tools a note explains the
 * product rule: these rules never restrict the profiles the user may use,
 * they only decide what the user may add or turn on by themself
 * (Integrations, and private profiles from Phase 3).
 *
 * The tabs remember, per type, that the admin chose "Solo alcuni" even with
 * nothing checked (stored as `[]`, like "Nessuno"), so switching tab or
 * unchecking the last item never changes the mode the admin picked.
 *
 * `renderExtra` lets the caller append content to the active tab (the user
 * dialog shows the effective access of that type there).
 */

import { useState, type ReactNode } from "react";
import { cn } from "@/lib/cn";
import { AdminAreasEditor } from "./AdminAreasEditor";
import { RuleEditor } from "./RuleEditor";
import { describeRule, k, optionsFor, RESOURCE_TYPES, type Catalog, type ResourceType, type Rule, type Rules, type T } from "./shared";

export function RulesTabs({
  rules,
  onChange,
  catalog,
  subject,
  t,
  renderExtra,
}: {
  rules: Rules;
  onChange: (rt: ResourceType, rule: Rule) => void;
  catalog: Catalog;
  subject: "user" | "group";
  t: T;
  renderExtra?: (rt: ResourceType) => ReactNode;
}) {
  const [active, setActive] = useState<ResourceType>("profile");
  // Types where the admin chose "some": an empty "some" must not show as "nothing".
  const [someChosen, setSomeChosen] = useState<Partial<Record<ResourceType, boolean>>>({});
  const options = (rt: ResourceType) => optionsFor(catalog, rt, t);

  return (
    <div className="flex flex-col gap-3">
      <div role="tablist" aria-label={t(`${k}.rule.tabs_aria`)} className="no-scrollbar flex gap-1 overflow-x-auto border-b border-border/60">
        {RESOURCE_TYPES.map((rt) => {
          const on = active === rt;
          return (
            <button
              key={rt}
              type="button"
              role="tab"
              id={`rules-tab-${rt}`}
              aria-selected={on}
              aria-controls="rules-panel"
              onClick={() => setActive(rt)}
              className={cn(
                "focus-ring relative inline-flex h-10 shrink-0 items-center gap-1.5 px-3 text-sm font-medium transition-colors",
                on ? "text-foreground" : "text-muted-foreground hover:text-foreground",
              )}
            >
              {t(`${k}.types.${rt}.tab`)}
              {/* Dot = this subject has a rule on the type. */}
              {rules[rt] !== null && <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-foreground/60" />}
              <span aria-hidden className={cn("absolute inset-x-3 bottom-0 h-0.5 rounded-full bg-foreground transition-opacity", on ? "opacity-100" : "opacity-0")} />
            </button>
          );
        })}
      </div>

      <div id="rules-panel" role="tabpanel" aria-labelledby={`rules-tab-${active}`} className="flex flex-col gap-3">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h3 className="text-sm font-semibold">{t(`${k}.types.${active}.title`)}</h3>
          <span className="text-xs text-muted-foreground">{describeRule(t, rules[active], active, new Map(options(active).map((o) => [o.id, o.name])))}</span>
        </div>
        {active !== "profile" && active !== "capability" && <p className="rounded-xl bg-secondary/60 px-3 py-2 text-xs text-muted-foreground">{t(`${k}.rule.scope_note`)}</p>}
        {active === "admin_section" ? (
          // Admin panel areas (Phase 5): per-area Nessuno / Lettura / Scrittura.
          <AdminAreasEditor value={rules.admin_section} onChange={(rule) => onChange("admin_section", rule)} t={t} />
        ) : (
        <RuleEditor
          key={active}
          value={rules[active]}
          onChange={(rule) => onChange(active, rule)}
          resourceType={active}
          options={options(active)}
          subject={subject}
          t={t}
          someWhileEmpty={!!someChosen[active]}
          onSomeWhileEmpty={(v) => setSomeChosen((s) => ({ ...s, [active]: v }))}
        />
        )}
        {renderExtra?.(active)}
      </div>
    </div>
  );
}
