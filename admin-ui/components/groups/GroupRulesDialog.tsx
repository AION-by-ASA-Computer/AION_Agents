"use client";

/**
 * Access rules of one user group: profiles, skills, MCP servers and native
 * tools, one tab each (`RulesTabs`). The Groups tab of the Users page
 * persists the result with `PUT /admin/groups/{id}/rules`
 * (`{rules: {profile: Rule, skill: Rule, mcp_server: Rule, native_tool_group: Rule}}`).
 */

import { useState } from "react";
import { Loader2 } from "lucide-react";
import { Dialog } from "@/components/profiles/Dialog";
import { ghostBtn, solidBtn } from "@/components/ui/classes";
import { RulesTabs } from "./RulesTabs";
import { k, type Catalog, type Rules, type T } from "./shared";

export function GroupRulesDialog({
  groupName,
  catalog,
  initialRules,
  busy,
  t,
  onSave,
  onClose,
}: {
  groupName: string;
  catalog: Catalog;
  initialRules: Rules;
  busy: boolean;
  t: T;
  onSave: (rules: Rules) => void;
  onClose: () => void;
}) {
  const [rules, setRules] = useState<Rules>(initialRules);

  return (
    <Dialog title={t(`${k}.rules.title`, { name: groupName })} closeLabel={t(`${k}.rules.cancel`)} onClose={onClose} className="max-w-2xl">
      <div className="flex flex-col gap-4">
        <p className="text-sm text-muted-foreground">{t(`${k}.rules.body`)}</p>
        <RulesTabs rules={rules} onChange={(rt, rule) => setRules((r) => ({ ...r, [rt]: rule }))} catalog={catalog} subject="group" t={t} />
        <div className="flex justify-end gap-2">
          <button type="button" className={ghostBtn} onClick={onClose}>
            {t(`${k}.rules.cancel`)}
          </button>
          <button type="button" className={solidBtn} disabled={busy} onClick={() => onSave(rules)}>
            {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />}
            {busy ? t(`${k}.rules.saving`) : t(`${k}.rules.save`)}
          </button>
        </div>
      </div>
    </Dialog>
  );
}
