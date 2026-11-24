"use client";

/**
 * React context with the signed-in user's admin-panel access (Phase 5).
 *
 * Filled once by `AdminAuthGate` from `GET /auth/me` and read by the sidebar
 * (menu filtered by area), `AppShell` (page gate + "read only" banner) and
 * pages that hide write controls (`useAreaLevel`, `useCanWrite`). The backend
 * re-checks every request; this only shapes the UI.
 */

import { createContext, useContext, type ReactNode } from "react";
import { usePathname } from "next/navigation";
import { navItemFor } from "@/lib/admin-nav";
import { areaLevel, isSuperadmin, type AdminAccessInfo, type AdminArea, type AreaLevel } from "./permissions";

const AdminAccessContext = createContext<AdminAccessInfo | null>(null);

export function AdminAccessProvider({ me, children }: { me: AdminAccessInfo | null; children: ReactNode }) {
  return <AdminAccessContext.Provider value={me}>{children}</AdminAccessContext.Provider>;
}

/** The `/auth/me` access fields (`null` while unknown or with admin auth off). */
export function useAdminAccess(): AdminAccessInfo | null {
  return useContext(AdminAccessContext);
}

/**
 * Level of an area for the current user. With no user info (dev escape hatch
 * `AION_ADMIN_PASSWORD_AUTH=0`) everything is writable, as on the backend.
 */
export function useAreaLevel(area: AdminArea): AreaLevel | null {
  const me = useAdminAccess();
  if (!me) return "write";
  return areaLevel(me, area);
}

/** True if the user may change things in `area`. */
export function useCanWrite(area: AdminArea): boolean {
  return useAreaLevel(area) === "write";
}

/**
 * Admin area of the current page (from the nav item that owns the URL),
 * `undefined` on pages open to every panel user (dashboard).
 */
export function usePageArea(): AdminArea | undefined {
  const pathname = usePathname() || "/";
  return navItemFor(pathname)?.area;
}

/**
 * True if the user may change things on the current page: write level on the
 * page's area (pages without an area are writable). Components shared by
 * pages of different areas should pass their area to `useCanWrite` instead.
 */
export function usePageCanWrite(): boolean {
  const area = usePageArea();
  const level = useAreaLevel(area ?? "agents");
  return !area || level === "write";
}

/**
 * Renders `children` only when the user may write: in `area` if given, else
 * in the current page's area. Use it around create / edit / delete / save
 * controls so read-only users see data without actions (the backend refuses
 * the writes anyway with 403).
 */
export function WriteOnly({ area, children }: { area?: AdminArea; children: ReactNode }) {
  const pageCanWrite = usePageCanWrite();
  const areaCanWrite = useCanWrite(area ?? "agents");
  const allowed = area ? areaCanWrite : pageCanWrite;
  return allowed ? <>{children}</> : null;
}

/** True for super-admins (or with admin auth off). */
export function useIsSuperadmin(): boolean {
  const me = useAdminAccess();
  return !me || isSuperadmin(me);
}
