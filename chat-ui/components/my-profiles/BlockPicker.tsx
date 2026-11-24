"use client";

/**
 * Switch list of the items a user may add to a private profile (skills,
 * MCP servers or native tool groups). Same look as the admin-ui profile
 * editor (`admin-ui/components/profiles/PickerList.tsx`): one row per item,
 * a switch, the id in monospace and a one-line description.
 *
 * Search text and the "attached only" filter are owned by the editor
 * (`PickerToolbar`, one per tab) so the tools tab filters MCP servers and
 * native tools together, as in the admin-ui.
 *
 * Items saved in the profile that the user may no longer use (an admin
 * revoked them) come from `unavailable`: they are listed first with a badge
 * and can only be switched off; the editor drops them on save anyway (the
 * backend ignores them at runtime). Controlled: the parent owns `selected`.
 */

import { useMemo } from "react";

import { cn } from "@/lib/cn";
import type { BuildingBlock } from "@/lib/api/my-profiles";
import { useT } from "@/lib/i18n/use-t";

/** Visual switch (the whole row is the button), as in the admin-ui. */
function SwitchMark({ on, muted }: { on: boolean; muted?: boolean }) {
  return (
    <span
      aria-hidden
      className={cn(
        "relative h-5 w-9 shrink-0 rounded-full transition-colors duration-200",
        on ? "bg-foreground" : "bg-muted-foreground/30",
        muted && "opacity-60",
      )}
    >
      <span
        className={cn(
          "absolute left-0.5 top-0.5 h-4 w-4 rounded-full bg-background shadow-sm transition-transform duration-200 ease-out",
          on && "translate-x-4",
        )}
      />
    </span>
  );
}

/** Items matching the editor's search text and "attached only" filter. */
export function filterBlocks(
  items: BuildingBlock[],
  selected: string[],
  query: string,
  attachedOnly: boolean,
): BuildingBlock[] {
  const q = query.trim().toLowerCase();
  return items.filter(
    (i) =>
      (!attachedOnly || selected.includes(i.id)) &&
      (!q ||
        [i.id, i.name, i.description].some((v) =>
          (v || "").toLowerCase().includes(q),
        )),
  );
}

export function BlockPicker({
  items,
  selected,
  unavailable,
  onChange,
  query,
  attachedOnly,
}: {
  items: BuildingBlock[];
  selected: string[];
  unavailable: string[];
  onChange: (next: string[]) => void;
  /** Search text of the tab toolbar. */
  query: string;
  /** "Collegate" filter of the tab toolbar. */
  attachedOnly: boolean;
}) {
  const t = useT();

  // Revoked items still saved in the profile come first, then the catalogue.
  const rows = useMemo(() => {
    const revoked = unavailable
      .filter((id) => selected.includes(id))
      .map((id) => ({
        id,
        name: id,
        description: t("myProfilesPage.revoked_hint_short"),
        revoked: true,
      }));
    const shown = filterBlocks(items, selected, query, attachedOnly).map(
      (i) => ({ ...i, revoked: false }),
    );
    return [...revoked, ...shown];
  }, [items, selected, unavailable, query, attachedOnly, t]);

  const toggle = (id: string) =>
    onChange(
      selected.includes(id)
        ? selected.filter((s) => s !== id)
        : [...selected, id],
    );

  if (rows.length === 0) return null;

  return (
    <ul className="divide-y divide-border/50 overflow-hidden rounded-2xl border border-border/70 bg-card">
      {rows.map((it) => {
        const on = selected.includes(it.id);
        return (
          <li key={it.id}>
            <button
              type="button"
              role="switch"
              aria-checked={on}
              onClick={() => toggle(it.id)}
              className="focus-ring flex w-full min-w-0 items-center gap-3.5 px-4 py-3 text-left transition-colors hover:bg-secondary/50 focus-visible:bg-secondary/50"
            >
              <SwitchMark on={on} muted={it.revoked} />
              <span className="min-w-0 flex-1">
                <span className="flex items-center gap-2">
                  <span
                    className={cn(
                      "truncate font-mono text-[13px] font-medium",
                      it.revoked && "line-through opacity-70",
                    )}
                  >
                    {it.id}
                  </span>
                  {it.revoked ? (
                    <span className="shrink-0 rounded-full bg-amber-500/15 px-2 py-0.5 text-[11px] font-medium text-amber-700 dark:text-amber-300">
                      {t("myProfilesPage.revoked_badge")}
                    </span>
                  ) : null}
                </span>
                {it.description ? (
                  <span
                    className="mt-0.5 block truncate text-xs text-muted-foreground"
                    title={it.description}
                  >
                    {it.description}
                  </span>
                ) : null}
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}

/** Search field + "Tutte / Collegate" filter on top of a picker tab (as in the admin-ui). */
export function PickerToolbar({
  query,
  onQuery,
  attachedOnly,
  onAttachedOnly,
  placeholder,
}: {
  query: string;
  onQuery: (q: string) => void;
  attachedOnly: boolean;
  onAttachedOnly: (v: boolean) => void;
  placeholder: string;
}) {
  const t = useT();
  return (
    <div className="flex flex-col gap-2 sm:flex-row">
      <input
        type="search"
        value={query}
        onChange={(e) => onQuery(e.target.value)}
        placeholder={placeholder}
        className="h-10 w-full flex-1 rounded-xl border border-border/80 bg-background px-3 text-sm text-foreground outline-none transition placeholder:text-muted-foreground focus:border-zinc-400 focus:ring-2 focus:ring-zinc-400/20 dark:border-white/15 dark:bg-black/40 dark:focus:border-zinc-500"
      />
      <div
        role="group"
        aria-label={t("myProfilesPage.filter_aria")}
        className="inline-flex shrink-0 rounded-xl border border-border bg-background p-0.5"
      >
        {[
          { v: false, label: t("myProfilesPage.filter_all") },
          { v: true, label: t("myProfilesPage.filter_attached") },
        ].map((o) => (
          <button
            key={String(o.v)}
            type="button"
            aria-pressed={attachedOnly === o.v}
            onClick={() => onAttachedOnly(o.v)}
            className={cn(
              "focus-ring h-9 rounded-[10px] px-3.5 text-sm font-medium transition-colors",
              attachedOnly === o.v
                ? "bg-secondary text-foreground"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            {o.label}
          </button>
        ))}
      </div>
    </div>
  );
}
