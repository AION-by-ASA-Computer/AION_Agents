import type { AdminArea } from "@/lib/auth/permissions";
import {
  BarChart3,
  Bot,
  Brain,
  Clock,
  FlaskConical,
  HeartPulse,
  LayoutDashboard,
  MessageCircle,
  Send,
  Server,
  Settings,
  ShieldCheck,
  Sparkles,
  ThumbsUp,
  Users,
  type LucideIcon,
} from "lucide-react";

/**
 * Admin destinations, shared by the sidebar and the dashboard command bar.
 * Groups follow the permission areas: Agenti = `agents` (MCP Hub is the
 * exception, governed by `external`), Osservabilità = `observability`,
 * Accessi = `access`, Piattaforma = `external` (Settings is `agents`).
 */
export type AdminNavItem = {
  id: string;
  href: string;
  icon: LucideIcon;
  /** i18n key for the label (admin_nav.*). */
  labelKey: string;
  /**
   * Admin area that governs the page (Phase 5, `src/access/admin_areas.py`);
   * absent = open to every panel user (dashboard).
   */
  area?: AdminArea;
};

export type AdminNavGroup = {
  id: string;
  /** i18n key for the group heading; null for the ungrouped top entry. */
  labelKey: string | null;
  items: AdminNavItem[];
};

export const ADMIN_NAV: AdminNavGroup[] = [
  {
    id: "home",
    labelKey: null,
    items: [{ id: "dashboard", href: "/", icon: LayoutDashboard, labelKey: "admin_nav.dashboard" }],
  },
  {
    id: "agents",
    labelKey: "admin_nav.groups.agents",
    items: [
      { id: "profiles", href: "/profiles", icon: Bot, labelKey: "admin_nav.profiles", area: "agents" },
      { id: "skills", href: "/skills", icon: Sparkles, labelKey: "admin_nav.skills", area: "agents" },
      // MCP Hub stays in "Agenti" but is governed by "Applicazioni esterne".
      { id: "hub", href: "/hub", icon: Server, labelKey: "admin_nav.hub", area: "external" },
      { id: "memory", href: "/memory", icon: Brain, labelKey: "admin_nav.memory", area: "agents" },
      { id: "schedules", href: "/schedules", icon: Clock, labelKey: "admin_nav.schedules", area: "agents" },
    ],
  },
  {
    id: "observability",
    labelKey: "admin_nav.groups.observability",
    items: [
      { id: "metrics", href: "/metrics", icon: BarChart3, labelKey: "admin_nav.metrics", area: "observability" },
      { id: "feedback", href: "/feedback", icon: ThumbsUp, labelKey: "admin_nav.feedback", area: "observability" },
      { id: "diagnostics", href: "/diagnostics", icon: FlaskConical, labelKey: "admin_nav.diagnostics", area: "observability" },
      { id: "system", href: "/system", icon: HeartPulse, labelKey: "admin_nav.system", area: "observability" },
    ],
  },
  {
    id: "access",
    labelKey: "admin_nav.groups.access",
    items: [
      { id: "users", href: "/users", icon: Users, labelKey: "admin_nav.users", area: "access" },
      { id: "service-accounts", href: "/service-accounts", icon: Bot, labelKey: "admin_nav.service_accounts", area: "access" },
      { id: "security", href: "/security", icon: ShieldCheck, labelKey: "admin_nav.security", area: "access" },
    ],
  },
  {
    id: "platform",
    labelKey: "admin_nav.groups.platform",
    items: [
      // Settings (models, memory, SMTP, file server…) is an area of its own ("Impostazioni").
      { id: "settings", href: "/settings", icon: Settings, labelKey: "admin_nav.settings", area: "settings" },
      { id: "whatsapp", href: "/whatsapp", icon: MessageCircle, labelKey: "admin_nav.whatsapp", area: "external" },
      { id: "telegram", href: "/telegram", icon: Send, labelKey: "admin_nav.telegram", area: "external" },
    ],
  },
];

/**
 * Menu filtered for one user: items whose area the user may at least read
 * (`level(area)` non-null), empty groups dropped.
 */
export function visibleNav(level: (area: AdminArea) => "read" | "write" | null): AdminNavGroup[] {
  return ADMIN_NAV.map((g) => ({ ...g, items: g.items.filter((i) => !i.area || level(i.area) !== null) })).filter(
    (g) => g.items.length > 0,
  );
}

/** Nav item that owns `pathname` (longest matching href), if any. */
export function navItemFor(pathname: string): AdminNavItem | undefined {
  return ADMIN_NAV.flatMap((g) => g.items)
    .filter((i) => i.href !== "/" && isNavActive(pathname, i.href))
    .sort((a, b) => b.href.length - a.href.length)[0];
}

export function isNavActive(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(`${href}/`);
}
