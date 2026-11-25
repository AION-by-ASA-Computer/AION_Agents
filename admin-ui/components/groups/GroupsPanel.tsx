"use client";

/**
 * "Groups" tab of the Users page (Access → Users → Groups).
 *
 * A group bundles users (people and service accounts) and carries access
 * rules that apply to every member. There is no default group: a user in no
 * group and without direct rules has no limits; a rule on a group limits its
 * members to the union of the rules of their groups (and their own direct
 * rule). Rules cover agent profiles, skills, MCP servers and native tools
 * (see PLAN_access_control.md).
 *
 * From this tab an admin lists, creates, renames and deletes groups, edits
 * members (`GroupMembersDialog`) and rules (`GroupRulesDialog`).
 * Backend: `/admin/groups*` (`src/api/admin_groups.py`). Rendered by
 * `app/users/page.tsx`, which owns the toast and refreshes its own data
 * (group chips, "Access" dialog) through `onChanged`.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { Loader2, Pencil, Plus, ShieldCheck, Trash2, UsersRound } from "lucide-react";
import { apiBase } from "@/lib/api";
import { apiFetch } from "@/lib/api/headers";
import { cn } from "@/lib/cn";
import { useT } from "@/lib/i18n/use-t";
import { getLocale } from "@/lib/i18n/i18n-store";
import type { ToastState } from "@/components/PageToast";
import { ghostBtn, outlineBtn, solidBtn } from "@/components/ui/classes";
import { SearchField } from "@/components/ui/SearchField";
import { DeleteDialog } from "@/components/ui/ConfirmDialogs";
import { GroupFormDialog, type GroupFormValues } from "@/components/groups/GroupFormDialog";
import { GroupMembersDialog, type AccountOption } from "@/components/groups/GroupMembersDialog";
import { GroupRulesDialog } from "@/components/groups/GroupRulesDialog";
import { EMPTY_CATALOG, fetchCatalog, k, readError, rulesFrom, type Catalog, type Group, type GroupDetail, type Rules } from "@/components/groups/shared";

/** Columns: group, members, actions (table from md up). */
const GRID = "md:grid-cols-[minmax(0,1fr)_7rem_10rem]";
type ListState = "loading" | "ready" | "error";

/** Square action button of a row, labelled for screen readers and on hover. */
function IconAction({ label, onClick, danger, children }: { label: string; onClick: () => void; danger?: boolean; children: React.ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={label}
      aria-label={label}
      className={cn(
        "focus-ring inline-flex h-9 w-9 items-center justify-center rounded-xl text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground",
        danger && "hover:bg-destructive/10 hover:text-destructive",
      )}
    >
      {children}
    </button>
  );
}

/** Empty state: explains groups and the "no rule = no limit" model. */
function EmptyState({ t, onCreate }: { t: (key: string, vars?: Record<string, string | number>) => string; onCreate: () => void }) {
  return (
    <div className="flex flex-col items-center rounded-3xl border border-dashed border-border px-6 py-16 text-center">
      <span aria-hidden className="flex h-11 w-11 items-center justify-center rounded-2xl bg-secondary text-muted-foreground">
        <UsersRound className="h-5 w-5" />
      </span>
      <h2 className="mt-4 text-lg font-semibold tracking-[-0.015em]">{t(`${k}.empty.title`)}</h2>
      <p className="mt-1.5 max-w-[56ch] text-sm text-muted-foreground">{t(`${k}.empty.body`)}</p>
      <button type="button" className={cn(solidBtn, "mt-7")} onClick={onCreate}>
        <Plus className="h-4 w-4" aria-hidden />
        {t(`${k}.add`)}
      </button>
    </div>
  );
}

/**
 * Group list with create / members / rules / rename / delete actions.
 *
 * @param onToast   shows a success or error message in the parent page's toast
 * @param onChanged called after any change to groups or memberships, so the
 *                  parent can reload what depends on them (user group chips,
 *                  the group list of the "Access" dialog)
 */
export function GroupsPanel({ onToast, onChanged }: { onToast: (toast: ToastState) => void; onChanged?: () => void }) {
  const t = useT();
  const [groups, setGroups] = useState<Group[]>([]);
  const [listState, setListState] = useState<ListState>("loading");
  const [catalog, setCatalog] = useState<Catalog>(EMPTY_CATALOG);
  const [query, setQuery] = useState("");
  const [saving, setSaving] = useState(false);

  const [formGroup, setFormGroup] = useState<Group | "new" | null>(null);
  const [slugError, setSlugError] = useState<string | undefined>();
  const [membersOf, setMembersOf] = useState<{ group: GroupDetail; accounts: AccountOption[] } | null>(null);
  const [rulesOf, setRulesOf] = useState<GroupDetail | null>(null);
  const [deleteGroup, setDeleteGroup] = useState<Group | null>(null);

  const fail = useCallback(
    (key: string, err: unknown) => onToast({ message: t(`${k}.toast.${key}`, { error: err instanceof Error ? err.message : String(err) }), variant: "error" }),
    [t, onToast],
  );

  const loadGroups = useCallback(async () => {
    try {
      const res = await apiFetch(`${apiBase()}/admin/groups`);
      if (!res.ok) throw new Error((await readError(res)).message);
      const data = await res.json();
      setGroups(data.items || []);
      setListState("ready");
    } catch (err) {
      setListState("error");
      fail("load_failed", err);
    }
  }, [fail]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- fetch on mount
    void loadGroups();
    // The catalogue only feeds the rules dialog: on failure its lists are empty.
    void fetchCatalog(apiFetch, apiBase()).then(setCatalog);
  }, [loadGroups]);

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return groups;
    return groups.filter((g) => [g.name, g.slug, g.description].some((v) => (v || "").toLowerCase().includes(q)));
  }, [groups, query]);

  const pluralKey = (n: number) => (new Intl.PluralRules(getLocale()).select(n) === "one" ? "one" : "other");
  const plural = (n: number) => t(`${k}.count_${pluralKey(n)}`, { count: n });
  const membersLabel = (n: number) => t(`${k}.members_count_${pluralKey(n)}`, { count: n });

  /** Group detail (members + rules), needed by the members and rules dialogs. */
  const fetchDetail = async (g: Group): Promise<GroupDetail> => {
    const res = await apiFetch(`${apiBase()}/admin/groups/${g.id}`);
    if (!res.ok) throw new Error((await readError(res)).message);
    return res.json();
  };

  const saveGroup = async (v: GroupFormValues) => {
    const editing = formGroup !== null && formGroup !== "new";
    setSaving(true);
    setSlugError(undefined);
    try {
      const res = await apiFetch(editing ? `${apiBase()}/admin/groups/${(formGroup as Group).id}` : `${apiBase()}/admin/groups`, {
        method: editing ? "PATCH" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: v.name, slug: v.slug, description: v.description }),
      });
      if (!res.ok) {
        const err = await readError(res);
        if (res.status === 409) {
          setSlugError(t(`${k}.form.slug_taken`));
          return;
        }
        throw new Error(err.message);
      }
      setFormGroup(null);
      onToast({ message: t(`${k}.toast.${editing ? "updated" : "created"}`), variant: "success" });
      void loadGroups();
      onChanged?.();
    } catch (err) {
      fail("save_failed", err);
    } finally {
      setSaving(false);
    }
  };

  const openMembers = async (g: Group) => {
    try {
      const [detail, usersRes] = await Promise.all([fetchDetail(g), apiFetch(`${apiBase()}/admin/users?include_service=1`)]);
      if (!usersRes.ok) throw new Error((await readError(usersRes)).message);
      const data = await usersRes.json();
      setMembersOf({ group: detail, accounts: data.users || [] });
    } catch (err) {
      fail("load_failed", err);
    }
  };

  const saveMembers = async (userIds: string[]) => {
    if (!membersOf) return;
    setSaving(true);
    try {
      const res = await apiFetch(`${apiBase()}/admin/groups/${membersOf.group.id}/members`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_ids: userIds }),
      });
      if (!res.ok) throw new Error((await readError(res)).message);
      setMembersOf(null);
      onToast({ message: t(`${k}.toast.members_saved`), variant: "success" });
      void loadGroups();
      onChanged?.();
    } catch (err) {
      fail("save_failed", err);
    } finally {
      setSaving(false);
    }
  };

  const openRules = async (g: Group) => {
    try {
      setRulesOf(await fetchDetail(g));
    } catch (err) {
      fail("load_failed", err);
    }
  };

  const saveRules = async (rules: Rules) => {
    if (!rulesOf) return;
    setSaving(true);
    try {
      const res = await apiFetch(`${apiBase()}/admin/groups/${rulesOf.id}/rules`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ rules }),
      });
      if (!res.ok) throw new Error((await readError(res)).message);
      setRulesOf(null);
      onToast({ message: t(`${k}.toast.rules_saved`), variant: "success" });
    } catch (err) {
      fail("save_failed", err);
    } finally {
      setSaving(false);
    }
  };

  const removeGroup = async () => {
    if (!deleteGroup) return;
    setSaving(true);
    try {
      const res = await apiFetch(`${apiBase()}/admin/groups/${deleteGroup.id}`, { method: "DELETE" });
      if (!res.ok) throw new Error((await readError(res)).message);
      setDeleteGroup(null);
      onToast({ message: t(`${k}.toast.deleted`), variant: "success" });
      void loadGroups();
      onChanged?.();
    } catch (err) {
      fail("delete_failed", err);
    } finally {
      setSaving(false);
    }
  };

  const openCreate = () => {
    setSlugError(undefined);
    setFormGroup("new");
  };

  return (
    <section aria-labelledby="groups-title" className="flex flex-col gap-4">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end">
        <div className="min-w-0 flex-1">
          <h2 id="groups-title" className="text-lg font-semibold tracking-[-0.015em]">
            {t(`${k}.title`)}
          </h2>
          <p className="mt-1 max-w-[70ch] text-sm text-muted-foreground">{t(`${k}.subtitle`)}</p>
        </div>
        {listState === "ready" && groups.length > 0 && (
          <button type="button" className={solidBtn} onClick={openCreate}>
            <Plus className="h-4 w-4" aria-hidden />
            {t(`${k}.add`)}
          </button>
        )}
      </div>

      {listState === "loading" && (
        <div className="flex items-center gap-2 px-1 py-10 text-sm text-muted-foreground" role="status">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
          {t(`${k}.loading`)}
        </div>
      )}

      {listState === "error" && (
        <div className="flex flex-col items-start gap-3 px-1 py-10">
          <p className="text-sm text-destructive">{t(`${k}.error`)}</p>
          <button
            type="button"
            className={outlineBtn}
            onClick={() => {
              setListState("loading");
              void loadGroups();
            }}
          >
            {t(`${k}.retry`)}
          </button>
        </div>
      )}

      {listState === "ready" && groups.length === 0 && <EmptyState t={t} onCreate={openCreate} />}

      {listState === "ready" && groups.length > 0 && (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <div className="min-w-[14rem] flex-1 sm:max-w-sm">
              <SearchField value={query} onChange={setQuery} placeholder={t(`${k}.search`)} />
            </div>
            <span className="ml-auto text-sm tabular-nums text-muted-foreground">
              {query.trim() ? t(`${k}.count_filtered`, { shown: rows.length, total: groups.length }) : plural(groups.length)}
            </span>
          </div>

          <div className="rounded-3xl border border-border/70 bg-card">
            <div className={cn("hidden items-center gap-4 border-b border-border/60 px-5 py-2 md:grid", GRID)}>
              <span className="text-xs font-medium text-muted-foreground">{t(`${k}.cols.group`)}</span>
              <span className="text-xs font-medium text-muted-foreground">{t(`${k}.cols.members`)}</span>
              <span className="text-right text-xs font-medium text-muted-foreground">{t(`${k}.cols.actions`)}</span>
            </div>
            {rows.length === 0 ? (
              <div className="px-6 py-16 text-center">
                <p className="text-sm text-muted-foreground">{t(`${k}.empty.search`)}</p>
                <button type="button" className={cn(ghostBtn, "mt-3")} onClick={() => setQuery("")}>
                  {t(`${k}.empty.clear_filters`)}
                </button>
              </div>
            ) : (
              <ul className="overflow-hidden rounded-b-3xl">
                {rows.map((g) => (
                  <li key={g.id} className="border-b border-border/40 last:border-b-0">
                    <div className={cn("grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-1 px-5 py-3 transition-colors hover:bg-secondary/40", GRID)}>
                      <div className="flex min-w-0 items-center gap-3">
                        <span aria-hidden className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-secondary text-muted-foreground">
                          <UsersRound className="h-4 w-4" />
                        </span>
                        <div className="min-w-0">
                          <div className="truncate text-sm font-medium">{g.name}</div>
                          <div className="truncate text-xs text-muted-foreground">
                            <span className="font-mono">{g.slug}</span>
                            {g.description && ` · ${g.description}`}
                          </div>
                        </div>
                      </div>
                      <div className="flex items-center justify-end gap-0.5 md:order-last">
                        <IconAction label={t(`${k}.row.members`, { name: g.name })} onClick={() => void openMembers(g)}>
                          <UsersRound className="h-4 w-4" aria-hidden />
                        </IconAction>
                        <IconAction label={t(`${k}.row.rules`, { name: g.name })} onClick={() => void openRules(g)}>
                          <ShieldCheck className="h-4 w-4" aria-hidden />
                        </IconAction>
                        <IconAction label={t(`${k}.row.edit`, { name: g.name })} onClick={() => setFormGroup(g)}>
                          <Pencil className="h-4 w-4" aria-hidden />
                        </IconAction>
                        <IconAction label={t(`${k}.row.delete`, { name: g.name })} onClick={() => setDeleteGroup(g)} danger>
                          <Trash2 className="h-4 w-4" aria-hidden />
                        </IconAction>
                      </div>
                      <div className="col-span-2 pl-12 text-sm tabular-nums text-muted-foreground md:col-span-1 md:pl-0 md:text-foreground">
                        {membersLabel(g.member_count)}
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </>
      )}

      {formGroup && (
        <GroupFormDialog
          group={formGroup === "new" ? null : formGroup}
          busy={saving}
          slugError={slugError}
          t={t}
          onSubmit={(v) => void saveGroup(v)}
          onClose={() => !saving && setFormGroup(null)}
        />
      )}

      {membersOf && (
        <GroupMembersDialog
          groupName={membersOf.group.name}
          accounts={membersOf.accounts}
          initial={membersOf.group.members.map((m) => m.id)}
          busy={saving}
          t={t}
          onSave={(ids) => void saveMembers(ids)}
          onClose={() => !saving && setMembersOf(null)}
        />
      )}

      {rulesOf && (
        <GroupRulesDialog
          groupName={rulesOf.name}
          catalog={catalog}
          initialRules={rulesFrom(rulesOf.rules)}
          busy={saving}
          t={t}
          onSave={(rules) => void saveRules(rules)}
          onClose={() => !saving && setRulesOf(null)}
        />
      )}

      {deleteGroup && (
        <DeleteDialog name={deleteGroup.slug} busy={saving} ns={`${k}.delete`} t={t} onConfirm={() => void removeGroup()} onClose={() => !saving && setDeleteGroup(null)} />
      )}
    </section>
  );
}
