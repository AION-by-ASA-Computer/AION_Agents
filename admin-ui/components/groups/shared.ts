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

/** Resource types an admin can write rules for (`EDITABLE_RESOURCE_TYPES` in `src/access/store.py`). */
export const RESOURCE_TYPES = ["profile", "skill", "mcp_server", "native_tool_group", "capability", "admin_section"] as const;
export type ResourceType = (typeof RESOURCE_TYPES)[number];

/** One id that can go in a rule (`GET /admin/access/catalog`). */
export interface ResourceOption {
  id: string;
  name: string;
  description?: string;
}

/** Existing ids per resource type, as returned by `GET /admin/access/catalog`. */
export type Catalog = Record<ResourceType, ResourceOption[]>;

/** Empty catalogue, used until `GET /admin/access/catalog` answers. */
export const EMPTY_CATALOG: Catalog = { profile: [], skill: [], mcp_server: [], native_tool_group: [], capability: [], admin_section: [] };

/**
 * Options of one type as shown to the admin. Capabilities are translated
 * (`admin_groups.capabilities.<id with _>`): the backend only knows ids;
 * other types keep the catalogue names.
 */
export function optionsFor(catalog: Catalog, rt: ResourceType, t: T): ResourceOption[] {
  // Admin areas: "agents:write" -> "Gestione agenti · Scrittura".
  if (rt === "admin_section") {
    return catalog.admin_section.map((o) => {
      const [area, level] = o.id.split(":");
      return { ...o, name: `${t(`${k}.areas.${area}.name`)} · ${t(`${k}.levels.${level}`)}` };
    });
  }
  if (rt !== "capability") return catalog[rt];
  return catalog.capability.map((o) => {
    const key = `${k}.capabilities.${o.id.replace(/\./g, "_")}`;
    return { ...o, name: t(`${key}.name`), description: t(`${key}.description`) };
  });
}

/** Rules of one subject (user or group); `null` = no rule for that type. */
export type Rules = Record<ResourceType, Rule>;

/** Rules as stored (absent type = no rule) -> one entry per editable type. */
export function rulesFrom(stored: Record<string, string[]> | undefined | null): Rules {
  const out = {} as Rules;
  for (const rt of RESOURCE_TYPES) out[rt] = stored && rt in stored ? stored[rt] : null;
  return out;
}

/** Loads `GET /admin/access/catalog`; on failure every list is empty. */
export async function fetchCatalog(fetcher: (url: string) => Promise<Response>, base: string): Promise<Catalog> {
  try {
    const res = await fetcher(`${base}/admin/access/catalog`);
    if (!res.ok) return EMPTY_CATALOG;
    const data = await res.json();
    return { ...EMPTY_CATALOG, ...(data.types || {}) };
  } catch {
    return EMPTY_CATALOG;
  }
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

/** Short, translated description of a rule ("No rule", "All skills", "3 profiles", "No MCP server"). */
export function describeRule(t: T, rule: Rule, rt: ResourceType, names?: Map<string, string>): string {
  if (rule === null) return t(`${k}.rule.no_rule_short`);
  if (rule.includes("*")) return t(`${k}.types.${rt}.summary_all`);
  if (rule.length === 0) return t(`${k}.types.${rt}.summary_nothing`);
  if (names && rule.length <= 3) return rule.map((s) => names.get(s) || s).join(", ");
  return t(`${k}.types.${rt}.summary_count`, { count: rule.length });
}
