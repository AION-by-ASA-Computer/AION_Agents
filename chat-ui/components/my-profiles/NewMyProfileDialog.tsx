"use client";

/**
 * First step of a new private profile: a name and where to start from.
 *
 * - "Vuoto": `POST /v1/me/profiles` with just the name;
 * - "Copia da un profilo": `POST /v1/me/profiles/{slug}/duplicate` from any
 *   profile the user may use (picked with the existing
 *   `ProfileSelectorDropdown`); the backend copies instructions and only the
 *   skills / MCP servers / native tools the user may add;
 * - "Wizard AI": `POST /v1/me/profiles/wizard-generate` turns a description
 *   into a draft (only items the user may add), then `POST /v1/me/profiles`
 *   creates it. The name is optional here: the wizard proposes one.
 *
 * On success the panel opens the full editor on the new profile.
 */

import { useState } from "react";
import { Copy, FilePlus2, Loader2, Plus, Sparkles } from "lucide-react";

import {
  ProfileSelectorDropdown,
  type ProfileItem,
} from "@/components/chat/ProfileSelectorDropdown";
import { cn } from "@/lib/cn";
import {
  createMyProfile,
  duplicateProfile,
  wizardGenerate,
  type MyProfile,
} from "@/lib/api/my-profiles";
import { useT } from "@/lib/i18n/use-t";
import { describeSaveError } from "./errors";
import { fieldCls, ModalShell, primaryBtn, secondaryBtn } from "./ModalShell";

type Start = "empty" | "copy" | "wizard";

export function NewMyProfileDialog({
  profiles,
  initialSource,
  userId,
  token,
  onCreated,
  onClose,
}: {
  /** Profiles the user may copy (`GET /profiles`: allowed global + own). */
  profiles: ProfileItem[];
  /** Pre-selects "copy from" (the "Duplica" action of a card). */
  initialSource?: ProfileItem | null;
  userId: string;
  token: string | null;
  onCreated: (p: MyProfile) => void;
  onClose: () => void;
}) {
  const t = useT();
  const [start, setStart] = useState<Start>(initialSource ? "copy" : "empty");
  const [source, setSource] = useState(
    initialSource?.slug || profiles[0]?.slug || "",
  );
  const [name, setName] = useState(
    initialSource
      ? t("myProfilesPage.copy_name", { name: initialSource.name })
      : "",
  );
  const [wizardPrompt, setWizardPrompt] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const create = async () => {
    setBusy(true);
    setError(null);
    try {
      let created: MyProfile;
      if (start === "copy" && source) {
        created = await duplicateProfile(userId, source, name.trim(), token);
      } else if (start === "wizard") {
        const draft = await wizardGenerate(userId, wizardPrompt.trim(), token);
        created = await createMyProfile(
          userId,
          { ...draft, name: name.trim() || draft.name },
          token,
        );
      } else {
        created = await createMyProfile(
          userId,
          {
            name: name.trim(),
            description: "",
            instructions: "",
            skills: [],
            mcp_servers: [],
            native_tool_groups: [],
          },
          token,
        );
      }
      onCreated(created);
    } catch (err) {
      setError(describeSaveError(err, t));
    } finally {
      setBusy(false);
    }
  };

  const option = (
    id: Start,
    icon: React.ReactNode,
    label: string,
    hint: string,
  ) => (
    <label
      className={cn(
        "flex cursor-pointer items-start gap-3 rounded-2xl border px-3.5 py-3 transition",
        start === id
          ? "border-primary/40 bg-primary/8"
          : "border-border/70 bg-card/30 hover:border-primary/25",
      )}
    >
      <input
        type="radio"
        name="start"
        checked={start === id}
        onChange={() => setStart(id)}
        className="mt-1 accent-foreground"
      />
      <span className="mt-0.5 text-muted-foreground">{icon}</span>
      <span className="min-w-0">
        <span className="block text-sm font-semibold text-foreground">
          {label}
        </span>
        <span className="mt-0.5 block text-xs text-muted-foreground">
          {hint}
        </span>
      </span>
    </label>
  );

  return (
    <ModalShell
      icon={<Plus className="h-5 w-5" aria-hidden />}
      title={t("myProfilesPage.new_title")}
      subtitle={t("myProfilesPage.new_subtitle")}
      closeLabel={t("myProfilesPage.cancel")}
      onClose={onClose}
      footer={
        <>
          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            className={secondaryBtn}
          >
            {t("myProfilesPage.cancel")}
          </button>
          <button
            type="button"
            onClick={() => void create()}
            disabled={
              busy ||
              (start === "wizard" ? !wizardPrompt.trim() : !name.trim()) ||
              (start === "copy" && !source)
            }
            className={primaryBtn}
          >
            {busy ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
            ) : (
              <Plus className="h-3.5 w-3.5" aria-hidden />
            )}
            {busy && start === "wizard"
              ? t("myProfilesPage.wizard_generating")
              : t("myProfilesPage.create")}
          </button>
        </>
      }
    >
      <div className="space-y-4">
        {error ? (
          <div className="rounded-2xl border border-destructive/40 bg-destructive/10 px-4 py-3 text-xs text-destructive">
            {error}
          </div>
        ) : null}
        <label className="block space-y-1.5">
          <span className="text-xs font-semibold text-muted-foreground">
            {t("myProfilesPage.field_name")}
          </span>
          <input
            className={fieldCls}
            value={name}
            maxLength={128}
            autoFocus
            placeholder={
              start === "wizard"
                ? t("myProfilesPage.wizard_name_placeholder")
                : t("myProfilesPage.name_placeholder")
            }
            onChange={(e) => setName(e.target.value)}
          />
        </label>
        <div className="grid grid-cols-1 gap-2">
          {option(
            "empty",
            <FilePlus2 className="h-4 w-4" />,
            t("myProfilesPage.start_empty"),
            t("myProfilesPage.start_empty_hint"),
          )}
          {option(
            "wizard",
            <Sparkles className="h-4 w-4" />,
            t("myProfilesPage.start_wizard"),
            t("myProfilesPage.start_wizard_hint"),
          )}
          {profiles.length > 0
            ? option(
                "copy",
                <Copy className="h-4 w-4" />,
                t("myProfilesPage.start_copy"),
                t("myProfilesPage.start_copy_hint"),
              )
            : null}
        </div>
        {start === "wizard" ? (
          <label className="block space-y-1.5">
            <span className="text-xs font-semibold text-muted-foreground">
              {t("myProfilesPage.wizard_prompt_label")}
            </span>
            <textarea
              className={cn(fieldCls, "min-h-[7rem] resize-y")}
              value={wizardPrompt}
              maxLength={4000}
              placeholder={t("myProfilesPage.wizard_prompt_placeholder")}
              onChange={(e) => setWizardPrompt(e.target.value)}
            />
          </label>
        ) : null}
        {start === "copy" ? (
          <ProfileSelectorDropdown
            profiles={profiles}
            value={source}
            onChange={setSource}
            label={t("myProfilesPage.copy_from")}
          />
        ) : null}
      </div>
    </ModalShell>
  );
}
