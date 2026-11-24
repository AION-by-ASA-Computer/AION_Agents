"use client";

import type { ReactNode } from "react";
import { useRouter } from "next/navigation";

import { cn } from "@/lib/cn";
import { useT } from "@/lib/i18n/use-t";
import { useShellActions, useSidebarOpen } from "@/lib/shell/shell-context";
import { ArrowLeft, PanelLeft } from "lucide-react";

type Props = {
  title: string;
  subtitle?: string;
  icon?: ReactNode;
  action?: ReactNode;
  className?: string;
  /** Show the "back" button at the top left (default: true). */
  showBack?: boolean;
};

/**
 * Compact header for non-chat sections inside the unified shell
 * (Integrazioni, Automazioni, I miei profili, Impostazioni).
 *
 * Starts with a discreet "back" button: it returns to the previous page of
 * the app, or to the chat when the page was opened directly (no history,
 * e.g. a bookmark or a new tab).
 */
export function ShellSectionHeader({ title, subtitle, icon, action, className, showBack = true }: Props) {
  const { toggleSidebar } = useShellActions();
  const sidebarOpen = useSidebarOpen();
  const router = useRouter();
  const t = useT();

  const goBack = () => {
    if (typeof window !== "undefined" && window.history.length > 1) router.back();
    else router.push("/");
  };

  return (
    <header
      className={cn(
        "flex h-14 shrink-0 items-center gap-3 border-b border-border/40 bg-background/80 px-3 backdrop-blur-sm sm:px-4",
        className,
      )}
    >
      {showBack ? (
        <button
          type="button"
          onClick={goBack}
          title={t("btn.back")}
          aria-label={t("btn.back")}
          className="focus-ring -ml-1 inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        >
          <ArrowLeft className="h-4 w-4" aria-hidden />
        </button>
      ) : null}
      <button
        type="button"
        onClick={toggleSidebar}
        className="focus-ring inline-flex h-9 w-9 items-center justify-center rounded-lg text-muted-foreground hover:bg-muted hover:text-foreground lg:hidden"
        aria-label={sidebarOpen ? "Chiudi menu" : "Apri menu"}
      >
        <PanelLeft className="h-4 w-4" aria-hidden />
      </button>
      {icon ? (
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-primary/20 bg-primary/10 text-primary">
          {icon}
        </div>
      ) : null}
      <div className="min-w-0 flex-1">
        <h1 className="truncate text-sm font-semibold tracking-tight sm:text-base">{title}</h1>
        {subtitle ? (
          <p className="truncate text-xs text-muted-foreground">{subtitle}</p>
        ) : null}
      </div>
      {action ? <div className="shrink-0">{action}</div> : null}
    </header>
  );
}
