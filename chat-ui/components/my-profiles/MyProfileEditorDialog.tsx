"use client";

/**
 * Editor of one private profile: name, description and three tabs like the
 * admin-ui profile editor (Istruzioni · Skill · Strumenti = MCP servers +
 * native tools). Only the items returned by `building-blocks` (what the
 * user's access rules allow) can be picked; revoked items still saved in the
 * profile are shown by `BlockPicker` and dropped on save.
 *
 * "Migliora con AI" sends the current (unsaved) form and a request to
 * `POST /v1/me/profiles/wizard-refine` and replaces the form with the draft
 * (name excluded: the user's name stays); nothing is saved until "Salva".
 *
 * Saves with `PUT /v1/me/profiles/{slug}`. The profile always exists already:
 * new ones are created (empty or copied) by `NewMyProfileDialog` first.
 */

import { useState } from "react";
import { Bot, Loader2, Save, Sparkles } from "lucide-react";

import { cn } from "@/lib/cn";
import {
  PROFILE_PARTS,
  updateMyProfile,
  wizardRefine,
  type BuildingBlocks,
  type MyProfile,
  type MyProfileInput,
  type ProfilePart,
} from "@/lib/api/my-profiles";
import { useT } from "@/lib/i18n/use-t";
import { BlockPicker, filterBlocks, PickerToolbar } from "./BlockPicker";
import { fieldCls, ModalShell, primaryBtn, secondaryBtn } from "./ModalShell";
import { describeSaveError } from "./errors";

type Tab = "instructions" | "skills" | "tools";

export function MyProfileEditorDialog({
  profile,
  blocks,
  maxInstructions,
  userId,
  token,
  onSaved,
  onClose,
}: {
  profile: MyProfile;
  blocks: BuildingBlocks;
  maxInstructions: number;
  userId: string;
  token: string | null;
  onSaved: (p: MyProfile) => void;
  onClose: () => void;
}) {
  const t = useT();
  const [tab, setTab] = useState<Tab>("instructions");
  // Search + "attached only" filter of the skills and tools tabs (as in the admin-ui editor).
  const [skillQuery, setSkillQuery] = useState("");
  const [skillsAttachedOnly, setSkillsAttachedOnly] = useState(false);
  const [toolQuery, setToolQuery] = useState("");
  const [toolsAttachedOnly, setToolsAttachedOnly] = useState(false);
  const [form, setForm] = useState<MyProfileInput>({
    name: profile.name,
    description: profile.description,
    instructions: profile.instructions,
    skills: profile.skills,
    mcp_servers: profile.mcp_servers,
    native_tool_groups: profile.native_tool_groups,
  });
  const [saving, setSaving] = useState(false);
  const [aiPrompt, setAiPrompt] = useState("");
  const [aiBusy, setAiBusy] = useState(false);
  const [aiNotice, setAiNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const setPart = (part: ProfilePart, next: string[]) =>
    setForm((f) => ({ ...f, [part]: next }));
  const tooLong = form.instructions.length > maxInstructions;
  const nameMissing = !form.name.trim();

  const save = async () => {
    setSaving(true);
    setError(null);
    // Revoked items are never sent back: the backend would reject them.
    const body = { ...form };
    for (const part of PROFILE_PARTS)
      body[part] = form[part].filter(
        (id) => !profile.unavailable[part].includes(id),
      );
    try {
      onSaved(await updateMyProfile(userId, profile.slug, body, token));
    } catch (err) {
      setError(describeSaveError(err, t));
    } finally {
      setSaving(false);
    }
  };

  /** Applies the wizard's draft to the form; the user reviews it and saves. */
  const refineWithAi = async () => {
    setAiBusy(true);
    setError(null);
    setAiNotice(null);
    try {
      const draft = await wizardRefine(userId, aiPrompt.trim(), form, token);
      setForm((f) => ({ ...draft, name: f.name }));
      setAiPrompt("");
      setAiNotice(t("myProfilesPage.wizard_applied"));
    } catch (err) {
      setError(describeSaveError(err, t));
    } finally {
      setAiBusy(false);
    }
  };

  const tabs: { id: Tab; label: string; count?: number }[] = [
    { id: "instructions", label: t("myProfilesPage.tab_instructions") },
    {
      id: "skills",
      label: t("myProfilesPage.tab_skills"),
      count: form.skills.length,
    },
    {
      id: "tools",
      label: t("myProfilesPage.tab_tools"),
      count: form.mcp_servers.length + form.native_tool_groups.length,
    },
  ];

  return (
    <ModalShell
      wide
      icon={<Bot className="h-5 w-5" aria-hidden />}
      title={t("myProfilesPage.edit_title", { name: profile.name })}
      subtitle={t("myProfilesPage.edit_subtitle")}
      closeLabel={t("myProfilesPage.cancel")}
      onClose={onClose}
      footer={
        <>
          <button
            type="button"
            onClick={onClose}
            disabled={saving}
            className={secondaryBtn}
          >
            {t("myProfilesPage.cancel")}
          </button>
          <button
            type="button"
            onClick={() => void save()}
            disabled={saving || aiBusy || tooLong || nameMissing}
            className={primaryBtn}
          >
            {saving ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
            ) : (
              <Save className="h-3.5 w-3.5" aria-hidden />
            )}
            {saving ? t("myProfilesPage.saving") : t("myProfilesPage.save")}
          </button>
        </>
      }
    >
      <div className="space-y-5">
        {error ? (
          <div className="rounded-2xl border border-destructive/40 bg-destructive/10 px-4 py-3 text-xs text-destructive">
            {error}
          </div>
        ) : null}

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <label className="space-y-1.5">
            <span className="text-xs font-semibold text-muted-foreground">
              {t("myProfilesPage.field_name")}
            </span>
            <input
              className={fieldCls}
              value={form.name}
              maxLength={128}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
            />
          </label>
          <label className="space-y-1.5">
            <span className="text-xs font-semibold text-muted-foreground">
              {t("myProfilesPage.field_description")}
            </span>
            <input
              className={fieldCls}
              value={form.description}
              maxLength={1000}
              onChange={(e) =>
                setForm({ ...form, description: e.target.value })
              }
            />
          </label>
        </div>

        <div className="space-y-2 rounded-2xl border border-primary/20 bg-primary/5 p-3">
          <p className="flex items-center gap-2 text-xs font-semibold text-foreground">
            <Sparkles className="h-3.5 w-3.5 text-primary" aria-hidden />
            {t("myProfilesPage.wizard_refine_title")}
          </p>
          <div className="flex flex-col gap-2 sm:flex-row">
            <input
              className={cn(fieldCls, "text-xs")}
              value={aiPrompt}
              maxLength={4000}
              placeholder={t("myProfilesPage.wizard_refine_placeholder")}
              onChange={(e) => setAiPrompt(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && aiPrompt.trim() && !aiBusy)
                  void refineWithAi();
              }}
            />
            <button
              type="button"
              onClick={() => void refineWithAi()}
              disabled={aiBusy || saving || !aiPrompt.trim()}
              className={cn(primaryBtn, "shrink-0 justify-center")}
            >
              {aiBusy ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
              ) : (
                <Sparkles className="h-3.5 w-3.5" aria-hidden />
              )}
              {aiBusy
                ? t("myProfilesPage.wizard_generating")
                : t("myProfilesPage.wizard_refine_btn")}
            </button>
          </div>
          {aiNotice ? (
            <p className="text-xs text-muted-foreground" role="status">
              {aiNotice}
            </p>
          ) : null}
        </div>

        <div
          role="tablist"
          aria-label={t("myProfilesPage.tabs_aria")}
          className="flex gap-1 border-b border-border/70 dark:border-white/10"
        >
          {tabs.map((x) => (
            <button
              key={x.id}
              type="button"
              role="tab"
              aria-selected={tab === x.id}
              onClick={() => setTab(x.id)}
              className={cn(
                "relative inline-flex h-10 cursor-pointer items-center gap-1.5 px-3 text-sm font-medium transition-colors",
                tab === x.id
                  ? "text-foreground"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {x.label}
              {x.count ? (
                <span className="rounded-full bg-muted px-1.5 text-[10px] font-bold dark:bg-zinc-700/60">
                  {x.count}
                </span>
              ) : null}
              <span
                aria-hidden
                className={cn(
                  "absolute inset-x-3 bottom-0 h-0.5 rounded-full bg-foreground",
                  tab === x.id ? "opacity-100" : "opacity-0",
                )}
              />
            </button>
          ))}
        </div>

        {tab === "instructions" ? (
          <div className="space-y-1.5">
            <textarea
              className={cn(
                fieldCls,
                "min-h-[18rem] resize-y font-mono text-xs leading-relaxed",
                tooLong && "border-destructive/60",
              )}
              value={form.instructions}
              placeholder={t("myProfilesPage.instructions_placeholder")}
              onChange={(e) =>
                setForm({ ...form, instructions: e.target.value })
              }
            />
            <p
              className={cn(
                "text-right text-xs tabular-nums",
                tooLong ? "text-destructive" : "text-muted-foreground",
              )}
            >
              {form.instructions.length.toLocaleString()} /{" "}
              {maxInstructions.toLocaleString()}
            </p>
          </div>
        ) : null}

        {tab === "skills" ? (
          <div className="space-y-4">
            <PickerToolbar
              query={skillQuery}
              onQuery={setSkillQuery}
              attachedOnly={skillsAttachedOnly}
              onAttachedOnly={setSkillsAttachedOnly}
              placeholder={t("myProfilesPage.search_skills")}
            />
            <BlockPicker
              items={blocks.skills}
              selected={form.skills}
              unavailable={profile.unavailable.skills}
              onChange={(next) => setPart("skills", next)}
              query={skillQuery}
              attachedOnly={skillsAttachedOnly}
            />
            {filterBlocks(
              blocks.skills,
              form.skills,
              skillQuery,
              skillsAttachedOnly,
            ).length === 0 ? (
              <p className="py-10 text-center text-sm text-muted-foreground">
                {blocks.skills.length === 0
                  ? t("myProfilesPage.no_skills")
                  : t("myProfilesPage.no_match")}
              </p>
            ) : null}
            <p className="px-1 text-xs text-muted-foreground">
              {t("myProfilesPage.selected_count", {
                count: form.skills.filter(
                  (s) => !profile.unavailable.skills.includes(s),
                ).length,
              })}
            </p>
          </div>
        ) : null}

        {tab === "tools" ? (
          <div className="space-y-5">
            <PickerToolbar
              query={toolQuery}
              onQuery={setToolQuery}
              attachedOnly={toolsAttachedOnly}
              onAttachedOnly={setToolsAttachedOnly}
              placeholder={t("myProfilesPage.search_tools")}
            />
            {/* Same order as the admin-ui editor: built-in tools first, then MCP servers. */}
            {blocks.native_tool_groups.length > 0 ? (
              <section className="space-y-2">
                <h3 className="px-1 text-sm font-semibold">
                  {t("myProfilesPage.section_native")}
                </h3>
                <BlockPicker
                  items={blocks.native_tool_groups}
                  selected={form.native_tool_groups}
                  unavailable={profile.unavailable.native_tool_groups}
                  onChange={(next) => setPart("native_tool_groups", next)}
                  query={toolQuery}
                  attachedOnly={toolsAttachedOnly}
                />
              </section>
            ) : null}
            {blocks.mcp_servers.length > 0 ? (
              <section className="space-y-2">
                <h3 className="px-1 text-sm font-semibold">
                  {t("myProfilesPage.section_mcp")}
                </h3>
                <p className="px-1 text-xs text-muted-foreground">
                  {t("myProfilesPage.section_mcp_hint")}
                </p>
                <BlockPicker
                  items={blocks.mcp_servers}
                  selected={form.mcp_servers}
                  unavailable={profile.unavailable.mcp_servers}
                  onChange={(next) => setPart("mcp_servers", next)}
                  query={toolQuery}
                  attachedOnly={toolsAttachedOnly}
                />
              </section>
            ) : null}
            {blocks.native_tool_groups.length === 0 &&
            blocks.mcp_servers.length === 0 ? (
              <p className="py-10 text-center text-sm text-muted-foreground">
                {t("myProfilesPage.no_tools")}
              </p>
            ) : null}
          </div>
        ) : null}
      </div>
    </ModalShell>
  );
}
