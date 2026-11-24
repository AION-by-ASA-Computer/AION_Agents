"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { Loader2, ShieldOff } from "lucide-react";
import { AuthHeading, AuthShell, authPrimaryBtnCls } from "@/components/auth/AuthShell";

import { apiBase } from "@/lib/api";
import { adminPath } from "@/lib/paths";
import {
  getStoredToken,
  setStoredAuth,
  isChangePwSkipped,
} from "@/lib/auth/storage";
import { fetchAuthStatus, resetAuthStatusCache } from "@/lib/auth/status";
import { canAccessAdmin, type AdminAccessInfo } from "@/lib/auth/permissions";
import { AdminAccessProvider } from "@/lib/auth/admin-access";
import {
  initLocaleFromStorage,
  setLocale,
  isValidLocale,
} from "@/lib/i18n/i18n-store";

type GateState =
  | "checking"
  | "ok"
  | "redirecting"
  | "must_change_password"
  | "no_admin_role";

/**
 * Guard globale dell'admin-ui.
 *
 * Comportamento (admin SEMPRE protetto di default, stile Grafana):
 * - Se ``AION_ADMIN_PASSWORD_AUTH=0`` (escape hatch dev) e nessun token,
 *   la UI passa senza login.
 * - Altrimenti: richiede token in localStorage. Se manca o e' invalido,
 *   redirect a ``/login`` (preserva ``next``).
 * - Se l'utente non ha il ruolo ``admin``, mostra "Accesso negato".
 * - Se ``must_change_password=true`` e l'utente non ha ancora "skippato",
 *   redirige a ``/change-password`` (skippabile per 24h).
 */
export function AdminAuthGate({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [state, setState] = useState<GateState>("checking");
  // `/auth/me` access fields shared with the shell (menu, page gate, read-only).
  const [me, setMe] = useState<AdminAccessInfo | null>(null);

  const skip =
    pathname === "/login" ||
    pathname?.startsWith("/login/") ||
    pathname === "/change-password" ||
    pathname?.startsWith("/change-password/");

  useEffect(() => {
    initLocaleFromStorage();
    if (skip) {
      setState("ok");
      return;
    }
    let cancelled = false;

    (async () => {
      const status = await fetchAuthStatus();
      if (cancelled) return;

      const adminAuthOn = status.admin_password_auth_enabled !== false;
      const token = getStoredToken();

      if (!adminAuthOn) {
        setState("ok");
        return;
      }

      if (!token) {
        setState("redirecting");
        const next = pathname ? encodeURIComponent(pathname) : "";
        router.replace(next ? `/login?next=${next}` : "/login");
        return;
      }

      try {
        const r = await fetch(`${apiBase()}/auth/me`, {
          headers: { Authorization: `Bearer ${token}` },
          cache: "no-store",
        });
        if (!r.ok) {
          setStoredAuth(null, null);
          resetAuthStatusCache();
          setState("redirecting");
          const next = pathname ? encodeURIComponent(pathname) : "";
          router.replace(next ? `/login?next=${next}` : "/login");
          return;
        }
        const me = (await r.json()) as AdminAccessInfo & {
          must_change_password?: boolean;
          password_change_required?: boolean;
          metadata?: { language?: string };
        };
        if (me.metadata?.language && isValidLocale(me.metadata.language)) {
          setLocale(me.metadata.language);
        }
        setMe(me);
        // Super-admin, any admin area, or the base panel access not excluded
        // ("Solo chat UI"); see lib/auth/permissions.ts.
        if (!canAccessAdmin(me)) {
          setState("no_admin_role");
          return;
        }
        // Temporary password: blocking, the 24h skip only applies to the default-password banner.
        if (me.password_change_required || (me.must_change_password && !isChangePwSkipped())) {
          setState("must_change_password");
          router.replace("/change-password");
          return;
        }
        if (!status.first_setup_complete) {
          if (pathname !== "/first-setup") {
            setState("redirecting");
            router.replace("/first-setup");
            return;
          }
        } else {
          if (pathname === "/first-setup") {
            setState("redirecting");
            router.replace("/");
            return;
          }
        }
        setState("ok");
      } catch {
        // Errore di rete: non sloggare. apiFetch interceptera' eventuali 401
        // sulle chiamate successive.
        setState("ok");
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [pathname, router, skip]);

  if (state === "ok") return <AdminAccessProvider me={me}>{children}</AdminAccessProvider>;

  if (state === "no_admin_role") {
    return (
      <div className="h-[100dvh] overflow-y-auto">
      <AuthShell>
        <AuthHeading
          icon={<ShieldOff className="h-5 w-5" aria-hidden />}
          title="Accesso negato"
          description={
            <>
              Il tuo account può usare solo la chat. Chiedi a un amministratore
              di darti accesso al pannello, oppure effettua login con un altro
              utente.
            </>
          }
        />
        <button
          className={authPrimaryBtnCls}
          onClick={() => {
            setStoredAuth(null, null);
            resetAuthStatusCache();
            window.location.replace(adminPath("/login"));
          }}
        >
          Logout e cambia utente
        </button>
      </AuthShell>
      </div>
    );
  }

  return (
    <div className="flex min-h-[100dvh] w-full flex-col items-center justify-center gap-3 bg-background text-muted-foreground">
      <Loader2 className="animate-spin text-primary" size={22} aria-hidden />
      <span className="text-sm">
        {state === "redirecting"
          ? "Reindirizzamento al login…"
          : state === "must_change_password"
          ? "Cambio password richiesto…"
          : "Verifica accesso amministratore…"}
      </span>
    </div>
  );
}
