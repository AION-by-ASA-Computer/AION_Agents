"use client";

/**
 * Modal frame of the "I miei profili" dialogs (editor, new, delete): same
 * look as the Schedules dialogs (`components/schedules/*Dialog.tsx`) with a
 * header (icon, title, subtitle, close), a scrolling body and a footer.
 * Clicking the backdrop or the X calls `onClose`.
 */

import type { ReactNode } from "react";
import { X } from "lucide-react";

import { cn } from "@/lib/cn";

export function ModalShell({
  icon,
  title,
  subtitle,
  closeLabel,
  onClose,
  footer,
  children,
  wide = false,
  danger = false,
}: {
  icon: ReactNode;
  title: string;
  subtitle?: string;
  closeLabel: string;
  onClose: () => void;
  footer: ReactNode;
  children: ReactNode;
  /** Editor width instead of the confirm-dialog width. */
  wide?: boolean;
  /** Red icon badge (destructive confirmations). */
  danger?: boolean;
}) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 p-4 sm:p-6 backdrop-blur-md animate-in fade-in duration-200"
      role="presentation"
      onClick={onClose}
    >
      <div
        className={cn(
          "flex max-h-[90vh] w-full flex-col overflow-hidden rounded-3xl border border-border/80 dark:border-white/10 bg-card dark:bg-[#18181b] text-card-foreground shadow-2xl animate-in zoom-in-95 duration-200",
          wide ? "max-w-3xl" : "max-w-lg",
        )}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex shrink-0 items-center justify-between border-b border-border/70 dark:border-white/10 bg-muted/25 dark:bg-zinc-900/60 p-5 sm:p-6">
          <div className="flex min-w-0 items-center gap-3.5 pr-4">
            <div
              className={cn(
                "flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl border shadow-xs",
                danger
                  ? "border-destructive/30 bg-destructive/10 text-destructive"
                  : "border-border/80 dark:border-white/10 bg-muted/60 dark:bg-zinc-800 text-foreground",
              )}
            >
              {icon}
            </div>
            <div className="min-w-0 flex-1">
              <h2 className="truncate text-lg font-bold tracking-tight text-foreground sm:text-xl">{title}</h2>
              {subtitle ? <p className="mt-0.5 text-xs text-muted-foreground">{subtitle}</p> : null}
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="shrink-0 cursor-pointer rounded-xl p-2 text-muted-foreground transition hover:bg-muted hover:text-foreground dark:hover:bg-zinc-800"
            aria-label={closeLabel}
          >
            <X className="h-5 w-5" />
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto bg-muted/10 p-5 dark:bg-zinc-950/40 sm:p-6">{children}</div>
        <div className="flex shrink-0 items-center justify-end gap-3 border-t border-border/70 dark:border-white/10 bg-muted/25 p-4 dark:bg-zinc-900/60 sm:px-6">
          {footer}
        </div>
      </div>
    </div>
  );
}

/** Secondary (cancel) button of the dialogs. */
export const secondaryBtn =
  "rounded-xl border border-border/80 dark:border-white/15 bg-background dark:bg-zinc-800/80 px-4 py-2 text-xs font-semibold text-foreground hover:bg-muted dark:hover:bg-zinc-700/80 transition cursor-pointer disabled:opacity-50";

/** Primary (save) button, same style as the Schedules "Nuova" button. */
export const primaryBtn =
  "flex items-center gap-2 rounded-xl bg-zinc-900 px-5 py-2 text-xs font-semibold text-white shadow-sm transition hover:bg-zinc-800 disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-white cursor-pointer";

/** Text field style shared by the editor inputs. */
export const fieldCls =
  "w-full rounded-xl border border-border/80 dark:border-white/15 bg-background/90 dark:bg-black/40 px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground outline-none transition focus:border-zinc-400 focus:ring-2 focus:ring-zinc-400/20 dark:focus:border-zinc-500";
