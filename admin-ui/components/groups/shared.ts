/**
 * Shared types and helpers for the user-groups admin UI (Access → Groups and
 * the "Access" dialog of the Users page).
 *
 * Backend: `src/api/admin_groups.py`. A rule is `string[] | null` per resource
 * type: `null` = no rule (this user/group does not limit that type), `["*"]` =
 * everything, `[]` = nothing, otherwise the allowed ids. A user with no rule
 * anywhere for a type has no limit on it; otherwise they get the union of the
 * rules of their own account and of their groups that have one.
 */

/** i18n namespace of the Groups page and dialogs. */
export const k = "admin_groups";

export type T = (key: string, vars?: Record<string, string | number>) => string;

/** `null` = no rule, `["*"]` = all, `[]` = none, else allowed ids. */
export type Rule = string[] | null;

/** Group as listed by `GET /admin/groups`. */
export interface Group {
  id: string;
  slug: string;
  name: string;
  description?: string | null;
  member_count: number;
}

/** Member row of `GET /admin/groups/{id}`. */
export interface GroupMember {
  id: string;
  identifier: string;
  display_name?: string | null;
  account_type?: string;
}

/** `GET /admin/groups/{id}`: group, members and rules (`{type: ids}`; absent = no rule). */
export interface GroupDetail extends Group {
  members: GroupMember[];
  rules: Record<string, string[]>;
}

/** One rule source in `GET /admin/users/{id}/effective-access`. */
export interface AccessSource {
  kind: "user" | "group";
  id: string;
  name: string | null;
  rule: Rule;
}

/** Effective access of one resource type. */
export interface EffectiveAccess {
  limited: boolean;
  /** `null` = everything. */
  allowed: string[] | null;
  sources: AccessSource[];
}

/** Agent profile as returned by `GET /admin/profiles`. */
export interface ProfileOption {
  slug: string;
  name: string;
  description?: string;
}

/** Same slug shape the backend accepts (`src/access/store.py`). */
export const GROUP_SLUG_RE = /^[a-z0-9][a-z0-9_-]{0,63}$/;

/** Slug suggestion from a display name ("Ufficio Vendite" -> "ufficio-vendite"). */
export function slugify(name: string): string {
  return name
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 64);
}

/** Error message from a failed response (`detail.code`, `detail.message` or text). */
export async function readError(res: Response): Promise<{ code: string; message: string }> {
  try {
    const data = await res.json();
    const d = data?.detail;
    if (d && typeof d === "object") return { code: String(d.code || ""), message: String(d.message || d.code || res.status) };
    return { code: "", message: String(d ?? res.status) };
  } catch {
    return { code: "", message: String(res.status) };
  }
}

/** Short, translated description of a rule ("No rule", "All", "3 profiles", "None"). */
export function describeRule(t: T, rule: Rule, names?: Map<string, string>): string {
  if (rule === null) return t(`${k}.rule.summary.no_rule`);
  if (rule.includes("*")) return t(`${k}.rule.summary.all`);
  if (rule.length === 0) return t(`${k}.rule.summary.nothing`);
  if (names && rule.length <= 3) return rule.map((s) => names.get(s) || s).join(", ");
  return t(`${k}.rule.summary.count`, { count: rule.length });
}
