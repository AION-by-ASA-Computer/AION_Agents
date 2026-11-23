/**
 * Admin-panel permission helpers.
 *
 * The backend sends effective permissions in `GET /auth/me` as strings
 * `<resource_type>:<resource_id>:<action>` (see `src/access/policy.py`).
 * Admin sections use `admin_section:<nav id | *>:read|write`, where the nav id
 * is the `id` of an item in `lib/admin-nav.ts`.
 *
 * Today only super-admins (role `admin`) get `admin_section:*:*`; Phase 5 of
 * PLAN_access_control.md adds per-section permissions, and these helpers are
 * already the single client-side check for them (`AdminAuthGate`, and later
 * nav filtering / read-only pages). They are UI hints: the backend re-checks
 * every `/admin/*` request.
 */

/** The `/auth/me` fields the admin UI needs for access decisions. */
export type AdminAccessInfo = {
  roles?: string[];
  permissions?: string[];
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

/**
 * May this user open the admin panel at all?
 * Super-admin role, or read access to at least one admin section. The role
 * check keeps working against backends that do not send `permissions` yet.
 */
export function canAccessAdmin(me: AdminAccessInfo): boolean {
  if (Array.isArray(me.roles) && me.roles.includes("admin")) return true;
  return hasPermission(me.permissions, "admin_section", "read");
}

/** Read/write access to one admin section (nav item id). */
export function canUseSection(
  me: AdminAccessInfo,
  sectionId: string,
  action: "read" | "write",
): boolean {
  if (Array.isArray(me.roles) && me.roles.includes("admin")) return true;
  return hasPermission(me.permissions, "admin_section", action, sectionId);
}
