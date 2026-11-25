"use client";

/**
 * Current-user context for chat-ui.
 *
 * Single place that reads `GET /auth/me` and exposes the logged-in user, their
 * roles, groups and effective permissions to every component (sidebar, future
 * "My profiles" editor, integrations picker...). Before this, components
 * fetched `/auth/me` on their own and each derived `isAdmin` locally.
 *
 * Permissions are strings `<resource_type>:<resource_id>:<action>` computed by
 * the backend (`src/access/policy.py:effective_permissions`). They only drive
 * what the UI shows: the backend re-checks every request.
 *
 * Mounted in `app/layout.tsx` inside `AuthGate`. Reloads when the stored token
 * changes (login/logout, also detected on navigation) and on the
 * `aion-profile-updated` window event fired after the user edits their account.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { usePathname } from "next/navigation";

import { apiBase } from "@/lib/config";
import { getStoredToken } from "@/lib/auth/storage";

/** Subset of the `/auth/me` payload used by the UI. */
export type CurrentUser = {
  user_id?: string;
  identifier?: string;
  display_name?: string;
  email?: string;
  roles: string[];
  groups: string[];
  permissions: string[];
  metadata?: { avatar_url?: string; profile_color?: string; language?: string };
};

type CurrentUserContextValue = {
  /** `null` while loading, when logged out or when auth is disabled. */
  user: CurrentUser | null;
  loading: boolean;
  /** True if the user may open the admin panel (role or any admin permission). */
  isAdmin: boolean;
  /** Permission check mirroring `src.access.policy.can` (UI hint only). */
  can: (resourceType: string, action: string, resourceId?: string) => boolean;
  /** Force a refetch of `/auth/me`. */
  reload: () => void;
};

const CurrentUserContext = createContext<CurrentUserContextValue | null>(null);

/**
 * True if `permissions` grants `action` on `resourceType`/`resourceId`.
 *
 * A `*` resource id in a permission matches any id; `write` implies `read`.
 * Without `resourceId` the check asks "on at least one resource".
 */
export function hasPermission(
  permissions: readonly string[],
  resourceType: string,
  action: string,
  resourceId?: string,
): boolean {
  const actions = action === "read" ? ["read", "write"] : [action];
  return permissions.some((perm) => {
    const [rt, rid, act] = perm.split(":");
    if (rt !== resourceType || !actions.includes(act)) return false;
    return !resourceId || rid === "*" || rid === resourceId;
  });
}

function normalize(data: Partial<CurrentUser> | null): CurrentUser | null {
  if (!data) return null;
  return {
    ...data,
    roles: Array.isArray(data.roles) ? data.roles : [],
    groups: Array.isArray(data.groups) ? data.groups : [],
    permissions: Array.isArray(data.permissions) ? data.permissions : [],
  };
}

export function CurrentUserProvider({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [loading, setLoading] = useState(true);
  // Token of the last fetch: navigation after login/logout changes it without
  // a `storage` event (same tab), so we compare on every pathname change.
  const fetchedToken = useRef<string | null | undefined>(undefined);
  const requestSeq = useRef(0);

  const load = useCallback(() => {
    const token = getStoredToken();
    fetchedToken.current = token;
    const seq = ++requestSeq.current;
    if (!token) {
      setUser(null);
      setLoading(false);
      return;
    }
    setLoading(true);
    void fetch(`${apiBase()}/auth/me`, {
      headers: { Authorization: `Bearer ${token}` },
      cache: "no-store",
    })
      .then((res) => (res.ok ? res.json() : null))
      .then((data: Partial<CurrentUser> | null) => {
        if (seq === requestSeq.current) setUser(normalize(data));
      })
      .catch(() => {
        if (seq === requestSeq.current) setUser(null);
      })
      .finally(() => {
        if (seq === requestSeq.current) setLoading(false);
      });
  }, []);

  useEffect(() => {
    if (fetchedToken.current !== getStoredToken()) load();
  }, [pathname, load]);

  useEffect(() => {
    window.addEventListener("aion-profile-updated", load);
    window.addEventListener("storage", load);
    return () => {
      window.removeEventListener("aion-profile-updated", load);
      window.removeEventListener("storage", load);
    };
  }, [load]);

  const value = useMemo<CurrentUserContextValue>(() => {
    const perms = user?.permissions ?? [];
    return {
      user,
      loading,
      isAdmin:
        !!user &&
        (user.roles.includes("admin") || hasPermission(perms, "admin_section", "read")),
      can: (resourceType, action, resourceId) =>
        hasPermission(perms, resourceType, action, resourceId),
      reload: load,
    };
  }, [user, loading, load]);

  return <CurrentUserContext.Provider value={value}>{children}</CurrentUserContext.Provider>;
}

/** Access the current user; must be used under `CurrentUserProvider`. */
export function useCurrentUser(): CurrentUserContextValue {
  const ctx = useContext(CurrentUserContext);
  if (!ctx) throw new Error("useCurrentUser must be used inside CurrentUserProvider");
  return ctx;
}
