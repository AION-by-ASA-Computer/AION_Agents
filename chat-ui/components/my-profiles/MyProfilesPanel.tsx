"use client";

/**
 * "I miei profili" page (route `/profiles`, sidebar entry next to
 * Integrazioni and Automazioni): the user's private agent profiles
 * (access control Phase 3, `/v1/me/profiles`).
 *
 * Layout and shell wiring follow `SchedulesPanel`: section header with the
 * "Nuovo profilo" action, cards, empty state, dialogs. Creation is blocked
 * (with an explanation) when the admin removed the
 * `private_profiles.create` capability or the per-user limit is reached;
 * existing profiles stay editable. Private profiles show up in every profile
 * picker of the chat because `GET /profiles` lists them (`private: true`).
 */

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useState,
} from "react";
import {
  AlertTriangle,
  Bot,
  Copy,
  Pencil,
  Plus,
  Sparkles,
  Trash2,
  UserRound,
} from "lucide-react";

import { ShellSectionHeader } from "@/components/layout/ShellSectionHeader";
import type { ProfileItem } from "@/components/chat/ProfileSelectorDropdown";
import { fetchProfiles } from "@/lib/api/aion";
import {
  deleteMyProfile,
  fetchBuildingBlocks,
  listMyProfiles,
  PROFILE_PARTS,
  type BuildingBlocks,
  type MyProfile,
  type MyProfilesList,
} from "@/lib/api/my-profiles";
import { useStoredToken, useStoredUserId } from "@/lib/auth/use-stored-auth";
import { useShellActions } from "@/lib/shell/shell-context";
import { useT } from "@/lib/i18n/use-t";
import { DeleteMyProfileDialog } from "./DeleteMyProfileDialog";
import { MyProfileEditorDialog } from "./MyProfileEditorDialog";
import { NewMyProfileDialog } from "./NewMyProfileDialog";
import { describeSaveError } from "./errors";

const EMPTY_BLOCKS: BuildingBlocks = {
  skills: [],
  mcp_servers: [],
  native_tool_groups: [],
};

/** One private profile: counts, revoked-items warning and actions. */
function MyProfileCard({
  profile,
  onEdit,
  onDuplicate,
  onDelete,
}: {
  profile: MyProfile;
  onEdit: () => void;
  onDuplicate?: () => void;
  onDelete: () => void;
}) {
  const t = useT();
  const revoked = PROFILE_PARTS.reduce(
    (n, p) => n + profile.unavailable[p].length,
    0,
  );
  const iconBtn =
    "cursor-pointer rounded-xl p-2 text-muted-foreground transition hover:bg-muted hover:text-foreground dark:hover:bg-zinc-800";
  return (
    <div className="flex h-full flex-col rounded-3xl border border-border/80 dark:border-white/10 bg-card/60 p-5 shadow-xs">
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl border border-border/60 bg-muted/80 text-sm font-bold dark:border-white/10 dark:bg-zinc-800">
            {profile.name.charAt(0).toUpperCase()}
          </div>
          <div className="min-w-0">
            <h3 className="truncate text-sm font-semibold text-foreground">
              {profile.name}
            </h3>
            {profile.description ? (
              <p className="line-clamp-2 text-xs text-muted-foreground">
                {profile.description}
              </p>
            ) : null}
          </div>
        </div>
        <div className="flex shrink-0 items-center">
          <button
            type="button"
            className={iconBtn}
            onClick={onEdit}
            title={t("myProfilesPage.edit")}
            aria-label={t("myProfilesPage.edit")}
          >
            <Pencil className="h-4 w-4" />
          </button>
          {onDuplicate ? (
            <button
              type="button"
              className={iconBtn}
              onClick={onDuplicate}
              title={t("myProfilesPage.duplicate")}
              aria-label={t("myProfilesPage.duplicate")}
            >
              <Copy className="h-4 w-4" />
            </button>
          ) : null}
          <button
            type="button"
            className={`${iconBtn} hover:text-destructive`}
            onClick={onDelete}
            title={t("myProfilesPage.delete")}
            aria-label={t("myProfilesPage.delete")}
          >
            <Trash2 className="h-4 w-4" />
          </button>
        </div>
      </div>
      <div className="mt-4 flex flex-wrap gap-1.5 text-[11px] font-semibold text-muted-foreground">
        <span className="rounded-full bg-muted px-2 py-0.5 dark:bg-zinc-800">
          {t("myProfilesPage.count_skills", { count: profile.skills.length })}
        </span>
        <span className="rounded-full bg-muted px-2 py-0.5 dark:bg-zinc-800">
          {t("myProfilesPage.count_mcp", { count: profile.mcp_servers.length })}
        </span>
        <span className="rounded-full bg-muted px-2 py-0.5 dark:bg-zinc-800">
          {t("myProfilesPage.count_native", {
            count: profile.native_tool_groups.length,
          })}
        </span>
      </div>
      {revoked > 0 ? (
        <p className="mt-3 flex items-center gap-1.5 text-xs text-amber-800 dark:text-amber-300">
          <AlertTriangle className="h-3.5 w-3.5 shrink-0" aria-hidden />
          {t("myProfilesPage.card_revoked", { count: revoked })}
        </p>
      ) : null}
    </div>
  );
}

export function MyProfilesPanel() {
  const t = useT();
  const { setHeader, setDock, setDockOpen, clearChrome } = useShellActions();
  const userId = useStoredUserId();
  const token = useStoredToken();

  const [data, setData] = useState<MyProfilesList | null>(null);
  const [blocks, setBlocks] = useState<BuildingBlocks>(EMPTY_BLOCKS);
  const [usable, setUsable] = useState<ProfileItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const [newSource, setNewSource] = useState<ProfileItem | null | undefined>(
    undefined,
  );
  const [editing, setEditing] = useState<MyProfile | null>(null);
  const [deleting, setDeleting] = useState<MyProfile | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);

  /** Profiles list, building blocks and the profiles the user may copy. */
  const load = useCallback(async () => {
    if (!userId) return;
    setError(null);
    try {
      const [list, b, all] = await Promise.all([
        listMyProfiles(userId, token),
        fetchBuildingBlocks(userId, token),
        fetchProfiles(userId, token),
      ]);
      setData(list);
      setBlocks(b);
      setUsable(
        all
          .filter((p) => p.slug)
          .map((p) => ({
            name: p.name,
            slug: p.slug as string,
            description: p.description,
            private: p.private,
          })),
      );
    } catch (err) {
      setError(describeSaveError(err, t));
    }
  }, [userId, token, t]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- fetch on mount / auth change
    void load();
  }, [load]);

  const atLimit = !!data && data.items.length >= data.limits.max_profiles;
  const canCreate = !!data && data.can_create && !atLimit;

  const headerAction = useMemo(
    () =>
      canCreate ? (
        <button
          type="button"
          onClick={() => setNewSource(null)}
          className="focus-ring flex cursor-pointer items-center gap-1.5 rounded-xl bg-zinc-900 px-4 py-2 text-xs font-semibold text-white shadow-sm transition hover:bg-zinc-800 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-white sm:text-sm"
        >
          <Plus className="h-4 w-4" aria-hidden />
          {t("myProfilesPage.new")}
        </button>
      ) : null,
    [canCreate, t],
  );

  useLayoutEffect(() => {
    setHeader(
      <ShellSectionHeader
        title={t("myProfilesPage.title")}
        subtitle={t("myProfilesPage.subtitle")}
        icon={<UserRound className="h-5 w-5" aria-hidden />}
        action={headerAction}
      />,
    );
    setDock(null);
    setDockOpen(false);
  }, [setHeader, setDock, setDockOpen, t, headerAction]);

  useLayoutEffect(() => () => clearChrome(), [clearChrome]);

  const confirmDelete = async () => {
    if (!deleting) return;
    setDeleteBusy(true);
    try {
      const res = await deleteMyProfile(userId, deleting.slug, token);
      setNotice(
        res.paused_jobs
          ? t("myProfilesPage.deleted_paused", { count: res.paused_jobs })
          : t("myProfilesPage.deleted"),
      );
      setDeleting(null);
      await load();
    } catch (err) {
      setError(describeSaveError(err, t));
    } finally {
      setDeleteBusy(false);
    }
  };

  if (!data && !error) {
    return (
      <div className="flex flex-1 items-center justify-center p-8 text-sm text-muted-foreground">
        {t("myProfilesPage.loading")}
      </div>
    );
  }

  const items = data?.items ?? [];

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto max-w-5xl space-y-6 px-4 py-8 sm:px-6 lg:px-8">
        {error ? (
          <div className="rounded-2xl border border-destructive/40 bg-destructive/10 px-4 py-3 text-xs text-destructive sm:text-sm">
            {error}
          </div>
        ) : null}
        {notice ? (
          <div
            className="rounded-2xl border border-emerald-500/30 bg-emerald-500/10 px-4 py-3 text-xs text-emerald-800 dark:text-emerald-200 sm:text-sm"
            role="status"
          >
            {notice}
          </div>
        ) : null}
        {data && !data.can_create ? (
          <div className="rounded-2xl border border-amber-500/35 bg-amber-500/10 px-4 py-3 text-xs text-amber-900 dark:text-amber-200 sm:text-sm">
            {t("myProfilesPage.create_disabled")}
          </div>
        ) : null}
        {data && data.can_create && atLimit ? (
          <div className="rounded-2xl border border-amber-500/35 bg-amber-500/10 px-4 py-3 text-xs text-amber-900 dark:text-amber-200 sm:text-sm">
            {t("myProfilesPage.limit_reached", {
              max: data.limits.max_profiles,
            })}
          </div>
        ) : null}

        {items.length > 0 ? (
          <section className="space-y-3">
            <div className="flex items-center gap-2">
              <Bot className="h-4 w-4 text-muted-foreground" aria-hidden />
              <h2 className="text-xs font-bold uppercase tracking-wider text-muted-foreground">
                {t("myProfilesPage.list_title")}
              </h2>
              <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] font-bold dark:bg-zinc-700/60">
                {items.length} / {data?.limits.max_profiles}
              </span>
            </div>
            <div className="grid grid-cols-1 items-stretch gap-5 md:grid-cols-2">
              {items.map((p) => (
                <MyProfileCard
                  key={p.slug}
                  profile={p}
                  onEdit={() => setEditing(p)}
                  onDuplicate={
                    canCreate
                      ? () =>
                          setNewSource({
                            name: p.name,
                            slug: p.slug,
                            description: p.description,
                          })
                      : undefined
                  }
                  onDelete={() => setDeleting(p)}
                />
              ))}
            </div>
          </section>
        ) : data ? (
          <div className="rounded-3xl border border-dashed border-border/80 bg-gradient-to-b from-card/60 to-card/20 px-6 py-14 text-center shadow-sm">
            <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-2xl border border-border/80 bg-muted/60 dark:border-white/10 dark:bg-zinc-800">
              <Sparkles className="h-7 w-7" aria-hidden />
            </div>
            <h2 className="text-lg font-semibold tracking-tight text-foreground">
              {t("myProfilesPage.empty")}
            </h2>
            <p className="mx-auto mt-2 max-w-lg text-sm leading-relaxed text-muted-foreground">
              {t("myProfilesPage.empty_hint")}
            </p>
            {canCreate ? (
              <button
                type="button"
                onClick={() => setNewSource(null)}
                className="mt-6 inline-flex cursor-pointer items-center gap-1.5 rounded-xl bg-zinc-900 px-4 py-2 text-sm font-semibold text-white transition hover:bg-zinc-800 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-white"
              >
                <Plus className="h-4 w-4" aria-hidden />
                {t("myProfilesPage.new")}
              </button>
            ) : null}
          </div>
        ) : null}
      </div>

      {newSource !== undefined ? (
        <NewMyProfileDialog
          profiles={usable}
          initialSource={newSource}
          userId={userId}
          token={token}
          onClose={() => setNewSource(undefined)}
          onCreated={(p) => {
            setNewSource(undefined);
            setEditing(p);
            void load();
          }}
        />
      ) : null}

      {editing && data ? (
        <MyProfileEditorDialog
          profile={editing}
          blocks={blocks}
          maxInstructions={data.limits.max_instructions}
          userId={userId}
          token={token}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            setNotice(t("myProfilesPage.saved"));
            void load();
          }}
        />
      ) : null}

      {deleting ? (
        <DeleteMyProfileDialog
          profile={deleting}
          deleting={deleteBusy}
          onConfirm={() => void confirmDelete()}
          onClose={() => setDeleting(null)}
        />
      ) : null}
    </div>
  );
}
