"use client";

/**
 * "Access" dialog of the Users page: a user's groups, their direct profile
 * rule and the resulting effective access with the reason ("from group X").
 *
 * Self-contained: on open it loads `GET /admin/users/{id}/groups`,
 * `.../rules` and `.../effective-access`; on save it writes
 * `PUT /admin/users/{id}/groups` and `PUT /admin/users/{id}/rules`, then
 * reloads the effective view and calls `onSaved` (the page refreshes its list).
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Loader2 } from "lucide-react";
import { apiBase } from "@/lib/api";
import { apiFetch } from "@/lib/api/headers";
import { cn } from "@/lib/cn";
import { Dialog } from "@/components/profiles/Dialog";
import { ghostBtn, solidBtn } from "@/components/ui/classes";
import { RuleEditor } from "./RuleEditor";
import { describeRule, k, readError, type EffectiveAccess, type Group, type ProfileOption, type Rule, type T } from "./shared";

type LoadState = "loading" | "ready" | "error";

export function UserAccessDialog({
  userId,
  userName,
  groups,
  profiles,
  t,
  onSaved,
  onError,
  onClose,
}: {
  userId: string;
  userName: string;
  /** Every group of the tenant (`GET /admin/groups`). */
  groups: Group[];
  profiles: ProfileOption[];
  t: T;
  onSaved: () => void;
  onError: (message: string) => void;
  onClose: () => void;
}) {
  const [state, setState] = useState<LoadState>("loading");
  const [busy, setBusy] = useState(false);
  const [groupIds, setGroupIds] = useState<string[]>([]);
  const [profileRule, setProfileRule] = useState<Rule>(null);
  const [effective, setEffective] = useState<EffectiveAccess | null>(null);
  const profileNames = useMemo(() => new Map(profiles.map((p) => [p.slug, p.name])), [profiles]);
  // Latest error callback without re-running the load effect when the parent
  // passes a new inline function on every render.
  const onErrorRef = useRef(onError);
  useEffect(() => {
    onErrorRef.current = onError;
  }, [onError]);

  const base = `${apiBase()}/admin/users/${encodeURIComponent(userId)}`;

  /** Loads the effective access only (after a save). */
  const loadEffective = useCallback(async () => {
    const res = await apiFetch(`${base}/effective-access`);
    if (!res.ok) throw new Error((await readError(res)).message);
    const data = await res.json();
    setEffective(data.access?.profile ?? null);
  }, [base]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [g, r] = await Promise.all([apiFetch(`${base}/groups`), apiFetch(`${base}/rules`)]);
        if (!g.ok) throw new Error((await readError(g)).message);
        if (!r.ok) throw new Error((await readError(r)).message);
        const gData = await g.json();
        const rData = await r.json();
        await loadEffective();
        if (cancelled) return;
        setGroupIds((gData.items || []).map((x: Group) => x.id));
        setProfileRule(rData.rules?.profile ?? null);
        setState("ready");
      } catch (err) {
        if (cancelled) return;
        setState("error");
        onErrorRef.current(err instanceof Error ? err.message : String(err));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [base, loadEffective]);

  const save = async () => {
    setBusy(true);
    try {
      const put = (path: string, body: unknown) =>
        apiFetch(`${base}/${path}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const g = await put("groups", { group_ids: groupIds });
      if (!g.ok) throw new Error((await readError(g)).message);
      const r = await put("rules", { rules: { profile: profileRule } });
      if (!r.ok) throw new Error((await readError(r)).message);
      await loadEffective();
      onSaved();
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const toggleGroup = (id: string) => setGroupIds((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));

  return (
    <Dialog title={t(`${k}.access.title`, { name: userName })} closeLabel={t(`${k}.access.close`)} onClose={onClose} className="max-w-2xl">
      {state === "loading" && (
        <div className="flex items-center gap-2 py-10 text-sm text-muted-foreground" role="status">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
          {t(`${k}.access.loading`)}
        </div>
      )}
      {state === "error" && <p className="py-10 text-sm text-destructive">{t(`${k}.access.load_failed`)}</p>}
      {state === "ready" && (
        <div className="flex flex-col gap-5">
          <section className="flex flex-col gap-2">
            <h3 className="text-sm font-semibold">{t(`${k}.access.groups`)}</h3>
            {groups.length === 0 ? (
              <p className="text-sm text-muted-foreground">{t(`${k}.access.no_groups`)}</p>
            ) : (
              <div className="flex flex-wrap gap-1.5">
                {groups.map((g) => {
                  const on = groupIds.includes(g.id);
                  return (
                    <button
                      key={g.id}
                      type="button"
                      aria-pressed={on}
                      onClick={() => toggleGroup(g.id)}
                      className={cn(
                        "focus-ring rounded-full border px-3 py-1 text-sm transition-colors",
                        on ? "border-foreground/40 bg-secondary font-medium" : "border-border text-muted-foreground hover:bg-secondary/60",
                      )}
                    >
                      {g.name}
                    </button>
                  );
                })}
              </div>
            )}
          </section>

          <section className="flex flex-col gap-2">
            <h3 className="text-sm font-semibold">{t(`${k}.access.direct_profiles`)}</h3>
            <RuleEditor value={profileRule} onChange={setProfileRule} profiles={profiles} subject="user" t={t} />
          </section>

          {effective && (
            <section className="flex flex-col gap-2 rounded-2xl border border-border/70 bg-secondary/30 px-4 py-3">
              <h3 className="text-sm font-semibold">{t(`${k}.access.effective`)}</h3>
              <p className="text-sm">
                {!effective.limited
                  ? t(`${k}.access.effective_unlimited`)
                  : effective.allowed === null
                    ? t(`${k}.access.effective_all`)
                    : effective.allowed.length === 0
                      ? t(`${k}.access.effective_nothing`)
                      : effective.allowed.map((s) => profileNames.get(s) || s).join(", ")}
              </p>
              <ul className="flex flex-col gap-0.5 text-xs text-muted-foreground">
                {effective.sources.map((s) => (
                  <li key={`${s.kind}:${s.id}`}>
                    {s.kind === "user" ? t(`${k}.access.source_user`) : t(`${k}.access.source_group`, { name: s.name || s.id })}
                    {": "}
                    {describeRule(t, s.rule, profileNames)}
                  </li>
                ))}
              </ul>
              <p className="text-xs text-muted-foreground">{t(`${k}.access.effective_hint`)}</p>
            </section>
          )}

          <div className="flex justify-end gap-2">
            <button type="button" className={ghostBtn} onClick={onClose}>
              {t(`${k}.access.close`)}
            </button>
            <button type="button" className={solidBtn} disabled={busy} onClick={() => void save()}>
              {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />}
              {busy ? t(`${k}.access.saving`) : t(`${k}.access.save`)}
            </button>
          </div>
        </div>
      )}
    </Dialog>
  );
}
