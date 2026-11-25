"use client";

/**
 * Access rules of one user group. Phase 1 exposes only the agent-profile rule;
 * Phase 2 adds skills, MCP servers and native tools as more sections of this
 * dialog. The Groups page persists the result with
 * `PUT /admin/groups/{id}/rules` (`{rules: {profile: Rule}}`).
 */

import { useState } from "react";
import { Loader2 } from "lucide-react";
import { Dialog } from "@/components/profiles/Dialog";
import { ghostBtn, solidBtn } from "@/components/ui/classes";
import { RuleEditor } from "./RuleEditor";
import { k, type ProfileOption, type Rule, type T } from "./shared";

export function GroupRulesDialog({
  groupName,
  profiles,
  initialProfileRule,
  busy,
  t,
  onSave,
  onClose,
}: {
  groupName: string;
  profiles: ProfileOption[];
  initialProfileRule: Rule;
  busy: boolean;
  t: T;
  onSave: (rules: { profile: Rule }) => void;
  onClose: () => void;
}) {
  const [profileRule, setProfileRule] = useState<Rule>(initialProfileRule);

  return (
    <Dialog title={t(`${k}.rules.title`, { name: groupName })} closeLabel={t(`${k}.rules.cancel`)} onClose={onClose} className="max-w-2xl">
      <div className="flex flex-col gap-4">
        <p className="text-sm text-muted-foreground">{t(`${k}.rules.body`)}</p>
        <section className="flex flex-col gap-2">
          <h3 className="text-sm font-semibold">{t(`${k}.rules.profiles`)}</h3>
          <RuleEditor value={profileRule} onChange={setProfileRule} profiles={profiles} subject="group" t={t} />
        </section>
        <div className="flex justify-end gap-2">
          <button type="button" className={ghostBtn} onClick={onClose}>
            {t(`${k}.rules.cancel`)}
          </button>
          <button type="button" className={solidBtn} disabled={busy} onClick={() => onSave({ profile: profileRule })}>
            {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />}
            {busy ? t(`${k}.rules.saving`) : t(`${k}.rules.save`)}
          </button>
        </div>
      </div>
    </Dialog>
  );
}
