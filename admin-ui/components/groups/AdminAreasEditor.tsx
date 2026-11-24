"use client";

/**
 * Editor of the admin-panel rule of a user or group (Phase 5): one row per
 * area (Gestione accessi, Gestione agenti, Applicazioni esterne,
 * Osservabilità) with Nessuno / Lettura / Scrittura.
 *
 * The rule is stored like every other rule (`shared.ts`) as a list of
 * `"<area>:<level>"` ids, e.g. `["agents:write", "external:read"]`; all areas
 * on "Nessuno" = `null` (no rule). Unlike other types, no rule means **no**
 * area: only the dashboard is visible. Rules of the user and of all their
 * groups add up (the highest level per area wins); super-admins always have
 * every area. Controlled component: the parent owns the value.
 */

import { cn } from "@/lib/cn";
import { k, type Rule, type T } from "./shared";

const AREAS = ["access", "agents", "settings", "external", "observability"] as const;
const LEVELS = ["none", "read", "write"] as const;
type Level = (typeof LEVELS)[number];

/** `{area: level}` from a stored rule (`null` / missing area = "none"). */
function levelsOf(rule: Rule): Record<string, Level> {
  const out: Record<string, Level> = {};
  for (const id of rule ?? []) {
    const [area, level] = id.split(":");
    if (level === "write" || (level === "read" && out[area] !== "write")) out[area] = level;
  }
  return out;
}

export function AdminAreasEditor({ value, onChange, t }: { value: Rule; onChange: (rule: Rule) => void; t: T }) {
  const levels = levelsOf(value);

  const setLevel = (area: string, level: Level) => {
    const next = { ...levels, [area]: level };
    const ids = AREAS.filter((a) => next[a] && next[a] !== "none").map((a) => `${a}:${next[a]}`);
    onChange(ids.length ? ids : null);
  };

  return (
    <div className="flex flex-col gap-2">
      <ul className="divide-y divide-border/50 overflow-hidden rounded-2xl border border-border/70 bg-card">
        {AREAS.map((area) => {
          const current = levels[area] ?? "none";
          return (
            <li key={area} className="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
              <span className="min-w-0">
                <span className="block text-sm font-medium">{t(`${k}.areas.${area}.name`)}</span>
                <span className="mt-0.5 block text-xs text-muted-foreground">{t(`${k}.areas.${area}.description`)}</span>
              </span>
              <div role="radiogroup" aria-label={t(`${k}.areas.${area}.name`)} className="inline-flex shrink-0 rounded-xl border border-border bg-background p-0.5">
                {LEVELS.map((lv) => (
                  <button
                    key={lv}
                    type="button"
                    role="radio"
                    aria-checked={current === lv}
                    onClick={() => setLevel(area, lv)}
                    className={cn(
                      "focus-ring h-8 rounded-[10px] px-3 text-xs font-medium transition-colors",
                      current === lv ? "bg-secondary text-foreground" : "text-muted-foreground hover:text-foreground",
                    )}
                  >
                    {t(`${k}.levels.${lv}`)}
                  </button>
                ))}
              </div>
            </li>
          );
        })}
      </ul>
      <p className="px-1 text-xs text-muted-foreground">{t(`${k}.areas.hint`)}</p>
    </div>
  );
}
