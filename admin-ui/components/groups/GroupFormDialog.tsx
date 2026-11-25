"use client";

/**
 * Create / edit form of a user group (name, slug, description).
 *
 * The slug is suggested from the name until the admin edits it by hand. The
 * dialog only validates and collects the values; the Groups page calls
 * `POST /admin/groups` or `PATCH /admin/groups/{id}` and passes back a
 * `slugError` when the slug is already taken (409).
 */

import { useState } from "react";
import { Loader2 } from "lucide-react";
import { Dialog } from "@/components/profiles/Dialog";
import { ghostBtn, inputCls, solidBtn } from "@/components/ui/classes";
import { GROUP_SLUG_RE, k, slugify, type Group, type T } from "./shared";

export type GroupFormValues = { name: string; slug: string; description: string };

export function GroupFormDialog({
  group,
  busy,
  slugError,
  t,
  onSubmit,
  onClose,
}: {
  /** `null` = create. */
  group: Group | null;
  busy: boolean;
  slugError?: string;
  t: T;
  onSubmit: (v: GroupFormValues) => void;
  onClose: () => void;
}) {
  const [name, setName] = useState(group?.name ?? "");
  const [slug, setSlug] = useState(group?.slug ?? "");
  const [slugTouched, setSlugTouched] = useState(!!group);
  const [description, setDescription] = useState(group?.description ?? "");

  const effectiveSlug = slugTouched ? slug : slugify(name);
  const slugInvalid = effectiveSlug.length > 0 && !GROUP_SLUG_RE.test(effectiveSlug);
  const canSave = name.trim().length > 0 && GROUP_SLUG_RE.test(effectiveSlug) && !busy;

  return (
    <Dialog title={t(`${k}.form.${group ? "edit_title" : "create_title"}`)} closeLabel={t(`${k}.form.cancel`)} onClose={onClose}>
      <form
        className="flex flex-col gap-4"
        onSubmit={(e) => {
          e.preventDefault();
          if (canSave) onSubmit({ name: name.trim(), slug: effectiveSlug, description: description.trim() });
        }}
      >
        <label className="flex flex-col gap-1.5 text-sm">
          <span className="font-medium">{t(`${k}.form.name`)}</span>
          <input className={inputCls} value={name} maxLength={128} autoFocus onChange={(e) => setName(e.target.value)} placeholder={t(`${k}.form.name_placeholder`)} />
        </label>
        <label className="flex flex-col gap-1.5 text-sm">
          <span className="font-medium">{t(`${k}.form.slug`)}</span>
          <input
            className={`${inputCls} font-mono`}
            value={effectiveSlug}
            maxLength={64}
            onChange={(e) => {
              setSlugTouched(true);
              setSlug(e.target.value.toLowerCase());
            }}
            aria-invalid={slugInvalid || !!slugError}
          />
          <span className={slugInvalid || slugError ? "text-xs text-destructive" : "text-xs text-muted-foreground"}>
            {slugError || (slugInvalid ? t(`${k}.form.slug_invalid`) : t(`${k}.form.slug_hint`))}
          </span>
        </label>
        <label className="flex flex-col gap-1.5 text-sm">
          <span className="font-medium">{t(`${k}.form.description`)}</span>
          <textarea className={`${inputCls} min-h-[5rem] resize-y`} value={description} maxLength={2000} onChange={(e) => setDescription(e.target.value)} />
        </label>
        <div className="flex justify-end gap-2">
          <button type="button" className={ghostBtn} onClick={onClose}>
            {t(`${k}.form.cancel`)}
          </button>
          <button type="submit" className={solidBtn} disabled={!canSave}>
            {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />}
            {busy ? t(`${k}.form.saving`) : t(`${k}.form.save`)}
          </button>
        </div>
      </form>
    </Dialog>
  );
}
