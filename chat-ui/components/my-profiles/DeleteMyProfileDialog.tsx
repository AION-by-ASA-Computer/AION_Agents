"use client";

/**
 * Confirmation before deleting a private profile. Explains the side effects
 * handled by the backend (`DELETE /v1/me/profiles/{slug}`): scheduled jobs
 * using the profile are paused, chats switch to the default profile.
 */

import { AlertTriangle, Loader2, Trash2 } from "lucide-react";

import type { MyProfile } from "@/lib/api/my-profiles";
import { useT } from "@/lib/i18n/use-t";
import { ModalShell, secondaryBtn } from "./ModalShell";

export function DeleteMyProfileDialog({
  profile,
  deleting,
  onConfirm,
  onClose,
}: {
  profile: MyProfile;
  deleting: boolean;
  onConfirm: () => void;
  onClose: () => void;
}) {
  const t = useT();
  return (
    <ModalShell
      danger
      icon={<Trash2 className="h-5 w-5" aria-hidden />}
      title={t("myProfilesPage.delete_title")}
      subtitle={profile.name}
      closeLabel={t("myProfilesPage.cancel")}
      onClose={onClose}
      footer={
        <>
          <button type="button" onClick={onClose} disabled={deleting} className={secondaryBtn}>
            {t("myProfilesPage.cancel")}
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={deleting}
            className="flex cursor-pointer items-center gap-2 rounded-xl bg-destructive px-5 py-2 text-xs font-semibold text-destructive-foreground shadow-sm transition hover:bg-destructive/90 disabled:opacity-50"
          >
            {deleting ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> : <Trash2 className="h-3.5 w-3.5" aria-hidden />}
            {t("myProfilesPage.delete_confirm")}
          </button>
        </>
      }
    >
      <div className="space-y-1.5 rounded-2xl border border-destructive/25 bg-destructive/10 p-4 text-xs leading-relaxed">
        <p className="flex items-center gap-2 font-bold text-destructive">
          <AlertTriangle className="h-4 w-4 shrink-0" aria-hidden />
          {t("myProfilesPage.delete_warning")}
        </p>
        <p className="text-muted-foreground">{t("myProfilesPage.delete_desc", { name: profile.name })}</p>
      </div>
    </ModalShell>
  );
}
