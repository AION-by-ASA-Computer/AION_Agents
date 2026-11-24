/**
 * Admin-panel permission helpers (access control, Phase 5).
 *
 * The panel is split into four areas, each granted at `read` or `write`
 * (`write` includes `read`):
 * - `access`        menu "Accessi" (users, service accounts, security)
 * - `agents`        menu "Agenti" (except MCP Hub)
 * - `settings`      the Settings page (granted on its own)
 * - `external`      MCP Hub + menu "Piattaforma" (WhatsApp, Telegram)
 * - `observability` menu "Osservabilità"
 * The dashboard ("Panoramica") is open to everyone who may enter the panel.
 *
 * `GET /auth/me` sends `admin_access` (may the user enter at all) and
 * `admin_areas` (`{area: "read" | "write"}`, sum of the user's and groups'
 * rules; every area in write for super-admins). These helpers turn them into
 * UI decisions (gate, menu, read-only pages). They are hints only: the
 * backend checks every `/admin/*` request (`src/access/admin_areas.py`).
 */

export type AdminArea = "access" | "agents" | "settings" | "external" | "observability";
export type AreaLevel = "read" | "write";
export const ADMIN_AREAS: AdminArea[] = ["access", "agents", "settings", "external", "observability"];

/** The `/auth/me` fields the admin UI needs for access decisions. */
export type AdminAccessInfo = {
  user_id?: string;
  identifier?: string;
  roles?: string[];
  permissions?: string[];
  admin_access?: boolean;
  admin_areas?: Partial<Record<AdminArea, AreaLevel>>;
};

/**
 * True if `permissions` grants `action` on `resourceType`/`resourceId`.
 * A `*` id matches any resource; `write` implies `read`; no `resourceId`
 * means "on at least one resource".
 */
export function hasPermission(
  permissions: readonly string[] | undefined,
  resourceType: string,
  action: string,
  resourceId?: string,
): boolean {
  const actions = action === "read" ? ["read", "write"] : [action];
  return (permissions ?? []).some((perm) => {
    const [rt, rid, act] = perm.split(":");
    if (rt !== resourceType || !actions.includes(act)) return false;
    return !resourceId || rid === "*" || rid === resourceId;
  });
}

/** Holder of the `admin` role (every area in write, creates other admins). */
export function isSuperadmin(me: AdminAccessInfo | null | undefined): boolean {
  return !!me && Array.isArray(me.roles) && me.roles.includes("admin");
}

/**
 * May this user open the admin panel at all? Uses `admin_access` from the
 * backend; falls back to the role for backends that do not send it yet.
 */
export function canAccessAdmin(me: AdminAccessInfo): boolean {
  if (isSuperadmin(me)) return true;
  if (typeof me.admin_access === "boolean") return me.admin_access;
  return hasPermission(me.permissions, "admin_section", "read");
}

/** Level of one area for this user (`null` = no access). */
export function areaLevel(me: AdminAccessInfo | null | undefined, area: AdminArea): AreaLevel | null {
  if (!me) return null;
  if (isSuperadmin(me)) return "write";
  return me.admin_areas?.[area] ?? null;
}
