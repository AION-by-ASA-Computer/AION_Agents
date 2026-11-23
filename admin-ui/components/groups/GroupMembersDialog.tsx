"use client";

/**
 * Member picker of a user group: searchable checklist of every account
 * (people and service accounts). Selected members are listed first.
 *
 * Collects the full member list; the Groups page persists it with
 * `PUT /admin/groups/{id}/members` (replace semantics).
 */

import { useMemo, useState } from "react";
import { Bot, Loader2 } from "lucide-react";
import { cn } from "@/lib/cn";
import { Dialog } from "@/components/profiles/Dialog";
import { SearchField } from "@/components/ui/SearchField";
import { ghostBtn, solidBtn } from "@/components/ui/classes";
import { k, type T } from "./shared";

/** Account option (subset of `GET /admin/users?include_service=1`). */
export interface AccountOption {
  id: string;
  identifier: string;
  display_name?: string | null;
  email?: string | null;
  account_type?: string;
}

export function GroupMembersDialog({
  groupName,
  accounts,
  initial,
  busy,
  t,
  onSave,
  onClose,
}: {
  groupName: string;
  accounts: AccountOption[];
  /** Current member ids. */
  initial: string[];
  busy: boolean;
  t: T;
  onSave: (userIds: string[]) => void;
  onClose: () => void;
}) {
  const [selected, setSelected] = useState<string[]>(initial);
  const [query, setQuery] = useState("");

  // Members first (stable order on open), then the rest alphabetically.
  const ordered = useMemo(() => {
    const label = (a: AccountOption) => (a.display_name || a.identifier).toLowerCase();
    const isInitial = new Set(initial);
    return [...accounts].sort((a, b) => Number(isInitial.has(b.id)) - Number(isInitial.has(a.id)) || label(a).localeCompare(label(b)));
  }, [accounts, initial]);

  const q = query.trim().toLowerCase();
  const shown = q ? ordered.filter((a) => [a.identifier, a.display_name, a.email].some((v) => (v || "").toLowerCase().includes(q))) : ordered;
  const toggle = (id: string) => setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));

  return (
    <Dialog title={t(`${k}.members.title`, { name: groupName })} closeLabel={t(`${k}.members.cancel`)} onClose={onClose} className="max-w-xl">
      <div className="flex flex-col gap-4">
        <p className="text-sm text-muted-foreground">{t(`${k}.members.body`)}</p>
        <SearchField value={query} onChange={setQuery} placeholder={t(`${k}.members.search`)} />
        <ul className="-mx-1 flex max-h-[22rem] flex-col gap-0.5 overflow-y-auto px-1">
          {shown.map((a) => {
            const on = selected.includes(a.id);
            return (
              <li key={a.id}>
                <label className={cn("flex cursor-pointer items-center gap-3 rounded-xl px-3 py-2", on ? "bg-secondary" : "hover:bg-secondary/60")}>
                  <input type="checkbox" checked={on} onChange={() => toggle(a.id)} className="focus-ring h-4 w-4 shrink-0 accent-foreground" />
                  <span className="min-w-0 flex-1">
                    <span className="flex items-center gap-1.5 truncate text-sm font-medium">
                      {a.account_type === "service" && <Bot className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-label={t(`${k}.members.service`)} />}
                      {a.display_name || a.identifier}
                    </span>
                    <span className="block truncate font-mono text-xs text-muted-foreground">{a.email || a.identifier}</span>
                  </span>
                </label>
              </li>
            );
          })}
        </ul>
        {shown.length === 0 && <p className="py-4 text-center text-sm text-muted-foreground">{t(`${k}.members.empty`)}</p>}
        <div className="flex items-center justify-between gap-2">
          <span className="text-xs tabular-nums text-muted-foreground">{t(`${k}.members.selected`, { count: selected.length })}</span>
          <div className="flex gap-2">
            <button type="button" className={ghostBtn} onClick={onClose}>
              {t(`${k}.members.cancel`)}
            </button>
            <button type="button" className={solidBtn} disabled={busy} onClick={() => onSave(selected)}>
              {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />}
              {busy ? t(`${k}.members.saving`) : t(`${k}.members.save`)}
            </button>
          </div>
        </div>
      </div>
    </Dialog>
  );
}
