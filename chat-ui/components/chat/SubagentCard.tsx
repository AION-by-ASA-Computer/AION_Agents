"use client";

/**
 * SubagentCard — live view of a `delegate_to_agent` call in the chat timeline.
 *
 * The parent agent can hand a self-contained task to another profile (a
 * "sub-agent"). The backend relays the child's progress on the parent stream
 * as `subagent_*` tool events; the SSE reducer folds them into
 * `ToolSegment.subagent` (see lib/sse/reducer.ts) and the backend
 * timeline_builder persists the same shape, so this card looks the same live
 * and after a reload.
 *
 * Design: one quiet row by default — who is working (profile name), how it is
 * going (status + step count) and, while running, the step in progress. The
 * brief sent to the sub-agent is often a long prompt written for a model, not
 * for the user, so it is never shown in the row: it sits behind an explicit
 * "show instructions" toggle inside the details. Details hold the child's tool
 * calls, the result preview, the files copied back to the parent workspace and
 * the action that opens the read-only child transcript. Inside ChatWorkspace
 * that action opens it in the right-hand dock (components/dock/SubagentPanel.tsx,
 * via lib/subagent/panel-context.ts) and is also a one-click icon in the row;
 * elsewhere it falls back to a link to `/c/<child id>`. Rendered by TurnTimeline in every tools view:
 * delegation is user-relevant work, never hidden in the compact trail.
 * See docs/agents/subagents.md.
 */

import { useEffect, useId, useState } from "react";
import Link from "next/link";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  ArrowUpRight,
  PanelRight,
  Bot,
  Check,
  ChevronDown,
  Clock,
  FileText,
  Loader2,
  Minus,
  X,
  type LucideIcon,
} from "lucide-react";

import { cn } from "@/lib/cn";
import { useT } from "@/lib/i18n/use-t";
import { sessionDownloadUrl } from "@/lib/api/aion";
import type { SubagentInfo, SubagentStep, TurnSegment } from "@/lib/sse/types";
import { ShimmerText } from "@/components/chat/ShimmerText";
import { useSubagentPanel } from "@/lib/subagent/panel-context";
import { SafeErrorBoundary } from "@/components/ui/ErrorBoundary";

type ToolSeg = Extract<TurnSegment, { kind: "tool" }>;
type Status = SubagentInfo["status"];

type Props = {
  seg: ToolSeg;
  /** Parent conversation id: files are downloaded from its session. */
  conversationId?: string;
  token?: string | null;
  /** False once the parent turn ended: a still-running card was cut off. */
  streaming?: boolean;
};

/**
 * Sub-agent state for the card. Before `subagent_start` arrives the segment
 * only has the tool input (`{agent, task}`), so a provisional "running" state
 * is derived from it; a tool_error without any sub-agent event (e.g. access
 * denied before the child started) becomes an "error" card.
 */
function cardState(seg: ToolSeg, streaming: boolean): SubagentInfo {
  const input = (seg.input && typeof seg.input === "object" ? seg.input : {}) as Record<
    string,
    unknown
  >;
  const base: SubagentInfo = seg.subagent ?? {
    profile: typeof input.agent === "string" ? input.agent : undefined,
    task: typeof input.task === "string" ? input.task : undefined,
    status: seg.status === "error" || seg.isError ? "error" : "running",
    steps: [],
    stepCount: 0,
    error: seg.isError ? seg.error || seg.output || null : null,
  };
  // The turn is over but no end event arrived (stop, network drop): never spin forever.
  if (base.status === "running" && !streaming && seg.status !== "running") {
    return { ...base, status: "cancelled" };
  }
  return base;
}

/**
 * Corner badge on the avatar for finished runs (running uses the orbit ring
 * instead). Colour carries the outcome at a glance; the status word in the
 * meta line carries it for screen readers and colour-blind users.
 */
const STATUS_BADGE: Record<Exclude<Status, "running">, { icon: LucideIcon; className: string }> = {
  done: { icon: Check, className: "bg-emerald-500 text-white dark:bg-emerald-400 dark:text-emerald-950" },
  error: { icon: X, className: "bg-destructive text-destructive-foreground" },
  denied: { icon: X, className: "bg-destructive text-destructive-foreground" },
  timeout: { icon: Clock, className: "bg-amber-500 text-white dark:bg-amber-400 dark:text-amber-950" },
  cancelled: { icon: Minus, className: "bg-muted-foreground text-background" },
};

/** Colour of the status word in the meta line. */
const STATUS_TEXT: Record<Status, string> = {
  running: "text-foreground/80",
  done: "text-emerald-700 dark:text-emerald-300",
  error: "text-destructive",
  denied: "text-destructive",
  timeout: "text-amber-700 dark:text-amber-300",
  cancelled: "text-muted-foreground",
};

/**
 * Round avatar for the sub-agent. While the run is live a thin arc orbits the
 * avatar — the card's single motion cue, so a long delegation reads as alive
 * without a spinner shouting next to the text. Static under reduced motion.
 */
export function AgentAvatar({ status }: { status: Status }) {
  const badge = status === "running" ? null : STATUS_BADGE[status];
  const BadgeIcon = badge?.icon;
  return (
    <span className="relative flex size-8 shrink-0 items-center justify-center" aria-hidden>
      <span
        className={cn(
          "flex size-8 items-center justify-center rounded-full border transition-colors",
          status === "running"
            ? "border-primary/25 bg-primary/10 text-primary"
            : "border-border/70 bg-background text-foreground/70",
        )}
      >
        <Bot className="size-4" strokeWidth={1.9} />
      </span>
      {status === "running" ? (
        <span className="subagent-orbit pointer-events-none absolute -inset-[3px] rounded-full" />
      ) : null}
      {badge && BadgeIcon ? (
        <span
          className={cn(
            "absolute -bottom-0.5 -right-0.5 flex size-3.5 items-center justify-center rounded-full ring-2 ring-card",
            badge.className,
          )}
        >
          <BadgeIcon className="size-2.5" strokeWidth={3} />
        </span>
      ) : null}
    </span>
  );
}

/** Status glyph of one child tool call in the activity list. */
export function StepGlyph({ status }: { status: SubagentStep["status"] }) {
  if (status === "running") {
    return <Loader2 className="size-3 shrink-0 text-primary motion-safe:animate-spin" aria-hidden />;
  }
  if (status === "error") {
    return <X className="size-3 shrink-0 text-destructive" strokeWidth={2.5} aria-hidden />;
  }
  return <Check className="size-3 shrink-0 text-emerald-600 dark:text-emerald-400" strokeWidth={2.5} aria-hidden />;
}

/** Quiet section title inside the details panel (sentence case, no shouting caps). */
function SectionTitle({ children }: { children: React.ReactNode }) {
  return <p className="mb-1.5 text-[0.857em] font-medium text-muted-foreground">{children}</p>;
}

/**
 * "Sub-agent · <status> · N steps" line, shared by the card row and the dock
 * panel header. The status word is coloured but always spelled out.
 */
export function SubagentMeta({ info, className }: { info: SubagentInfo; className?: string }) {
  const t = useT();
  return (
    <span className={cn("flex min-w-0 items-center gap-1.5 leading-snug text-muted-foreground", className)}>
      <span className="shrink-0">{t("chat.subagent.label")}</span>
      <span aria-hidden className="text-muted-foreground/50">·</span>
      <span className={cn("shrink-0 font-medium", STATUS_TEXT[info.status])}>
        {t(`chat.subagent.status.${info.status}`)}
      </span>
      {info.stepCount > 0 ? (
        <>
          <span aria-hidden className="text-muted-foreground/50">·</span>
          <span className="truncate tabular-nums">{t("chat.subagent.steps", { count: info.stepCount })}</span>
        </>
      ) : null}
    </span>
  );
}

export function SubagentCard({ seg, conversationId, token, streaming = false }: Props) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const [briefOpen, setBriefOpen] = useState(false);
  const panelId = useId();
  const briefId = useId();

  const info = cardState(seg, streaming);
  const running = info.status === "running";
  const failed = info.status === "error" || info.status === "denied";
  const name = info.profileName || info.profile || t("chat.subagent.unknown_agent");
  const lastStep = info.steps.length ? info.steps[info.steps.length - 1] : null;
  const hiddenSteps = Math.max(0, info.stepCount - info.steps.length);
  const files = info.files ?? [];
  const hasDetails =
    info.steps.length > 0 || Boolean(info.error) || Boolean(info.resultPreview) || files.length > 0 || Boolean(info.task);

  // Dock panel bridge: when this run is the one open on the right, keep the
  // panel's live state (status, steps, result) in step with this card.
  const panel = useSubagentPanel();
  const shownInPanel = Boolean(panel && info.conversationId && panel.activeId === info.conversationId);
  const lastStepStatus = lastStep?.status;
  useEffect(() => {
    if (shownInPanel) panel?.sync(info);
    // `info` is rebuilt every render: depend on the fields that actually change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shownInPanel, info.status, info.stepCount, lastStepStatus, info.resultPreview, info.error, info.files?.length]);
  const openTranscript = panel && info.conversationId ? () => panel.open(info) : null;

  return (
    <div
      className={cn(
        "group/subagent max-w-2xl rounded-xl border bg-card/60 text-[0.857em] transition-colors",
        failed ? "border-destructive/30" : "border-border/60",
        open && "bg-card",
        // Mirrors the dock: the run whose transcript is open on the right.
        shownInPanel && "border-primary/40 bg-card",
      )}
    >
      {/* Header row: the toggle owns avatar + text + chevron; the transcript
          link sits beside it (a link cannot live inside a button). */}
      <div className="flex items-center gap-1 pr-1.5">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          aria-controls={panelId}
          disabled={!hasDetails}
          className="focus-ring flex min-w-0 flex-1 items-center gap-3 rounded-xl py-2.5 pl-3 pr-1.5 text-left enabled:cursor-pointer disabled:cursor-default"
        >
          <AgentAvatar status={info.status} />
          <span className="min-w-0 flex-1">
            <span className="block truncate font-semibold leading-snug text-foreground" title={name}>
              {name}
            </span>
            {running ? (
              // Live line: what the child is doing right now, or how far it got.
              <ShimmerText className="mt-0.5 block truncate text-[0.929em] leading-snug">
                {lastStep && lastStep.status === "running"
                  ? t("chat.subagent.running_step", { name: lastStep.name })
                  : info.stepCount > 0
                    ? `${t("chat.subagent.label")} · ${t("chat.subagent.steps", { count: info.stepCount })}`
                    : t("chat.subagent.starting")}
              </ShimmerText>
            ) : (
              <SubagentMeta info={info} className="mt-0.5 text-[0.929em]" />
            )}
          </span>
          {hasDetails ? (
            <ChevronDown
              className={cn(
                "size-4 shrink-0 text-muted-foreground/70 transition-transform duration-200 group-hover/subagent:text-muted-foreground",
                open && "rotate-180",
              )}
              aria-hidden
            />
          ) : null}
        </button>

        {openTranscript ? (
          <button
            type="button"
            onClick={openTranscript}
            aria-label={t("chat.subagent.open_transcript")}
            aria-pressed={shownInPanel}
            title={t("chat.subagent.open_transcript")}
            className={cn(
              "focus-ring flex size-8 shrink-0 items-center justify-center rounded-lg transition-colors hover:bg-muted hover:text-foreground",
              shownInPanel ? "bg-primary/10 text-primary" : "text-muted-foreground",
            )}
          >
            <PanelRight className="size-4" aria-hidden />
          </button>
        ) : info.conversationId ? (
          <Link
            href={`/c/${info.conversationId}`}
            aria-label={t("chat.subagent.open_transcript")}
            title={t("chat.subagent.open_transcript")}
            className="focus-ring flex size-8 shrink-0 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          >
            <ArrowUpRight className="size-4" aria-hidden />
          </Link>
        ) : null}
      </div>

      {open && hasDetails ? (
        <div id={panelId} className="subagent-reveal space-y-4 border-t border-border/50 px-3 pb-3 pt-3">
          {info.steps.length > 0 ? (
            <div>
              <SectionTitle>{t("chat.subagent.activity")}</SectionTitle>
              <ul className="space-y-1 text-[0.929em] text-muted-foreground">
                {hiddenSteps > 0 ? (
                  <li className="pl-5 text-muted-foreground/80">
                    {t("chat.subagent.earlier_steps", { count: hiddenSteps })}
                  </li>
                ) : null}
                {info.steps.map((s) => (
                  <li key={s.id} className="flex min-w-0 items-center gap-2">
                    <span className="flex size-3 shrink-0 items-center justify-center">
                      <StepGlyph status={s.status} />
                    </span>
                    <span className="truncate font-mono text-[0.93em] text-foreground/80" title={s.name}>
                      {s.name}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {info.error ? (
            <p className="whitespace-pre-wrap rounded-lg bg-destructive/[0.06] px-2.5 py-2 text-destructive">
              {info.error}
            </p>
          ) : null}

          {info.resultPreview ? (
            <div>
              <SectionTitle>{t("chat.subagent.result")}</SectionTitle>
              <SafeErrorBoundary fallback={<p className="whitespace-pre-wrap">{info.resultPreview}</p>}>
                {/* Capped height: the full answer lives in the transcript and in the parent's reply. */}
                <div className="prose-chat max-h-64 overflow-y-auto text-[0.95em]">
                  <ReactMarkdown remarkPlugins={[remarkGfm]} disallowedElements={["script"]} unwrapDisallowed>
                    {info.resultPreview}
                  </ReactMarkdown>
                </div>
              </SafeErrorBoundary>
            </div>
          ) : null}

          {files.length > 0 ? (
            <div>
              <SectionTitle>{t("chat.subagent.files")}</SectionTitle>
              <ul className="flex flex-wrap gap-1.5">
                {files.map((path) => {
                  const label = path.split("/").pop() || path;
                  const href =
                    conversationId && token ? sessionDownloadUrl(conversationId, path, token) : undefined;
                  const chip =
                    "inline-flex max-w-full items-center gap-1.5 rounded-md border border-border/60 bg-background px-2 py-1";
                  return (
                    <li key={path} className="min-w-0 max-w-full">
                      {href ? (
                        <a
                          href={href}
                          title={path}
                          className={cn(chip, "focus-ring text-foreground transition-colors hover:border-border hover:bg-muted")}
                        >
                          <FileText className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
                          <span className="truncate">{label}</span>
                        </a>
                      ) : (
                        <span className={cn(chip, "text-muted-foreground")} title={path}>
                          <FileText className="size-3.5 shrink-0" aria-hidden />
                          <span className="truncate">{label}</span>
                        </span>
                      )}
                    </li>
                  );
                })}
              </ul>
            </div>
          ) : null}

          {/* Footer: the brief stays one click away (transparency) without
              flooding the timeline; the transcript link repeats the header icon
              as a labelled action for users who did not spot it. */}
          {info.task || info.conversationId ? (
            <div className="space-y-2">
              <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1">
                {info.task ? (
                  <button
                    type="button"
                    onClick={() => setBriefOpen((v) => !v)}
                    aria-expanded={briefOpen}
                    aria-controls={briefId}
                    className="focus-ring inline-flex items-center gap-1 rounded-md text-muted-foreground transition-colors hover:text-foreground"
                  >
                    <ChevronDown
                      className={cn("size-3.5 transition-transform duration-200", briefOpen && "rotate-180")}
                      aria-hidden
                    />
                    {briefOpen ? t("chat.subagent.hide_brief") : t("chat.subagent.show_brief")}
                  </button>
                ) : (
                  <span />
                )}
                {openTranscript ? (
                  <button
                    type="button"
                    onClick={openTranscript}
                    className="focus-ring inline-flex items-center gap-1 rounded-md font-medium text-primary hover:underline hover:underline-offset-2"
                  >
                    {t("chat.subagent.open_transcript")}
                    <PanelRight className="size-3.5" aria-hidden />
                  </button>
                ) : info.conversationId ? (
                  <Link
                    href={`/c/${info.conversationId}`}
                    className="focus-ring inline-flex items-center gap-1 rounded-md font-medium text-primary hover:underline hover:underline-offset-2"
                  >
                    {t("chat.subagent.open_transcript")}
                    <ArrowUpRight className="size-3.5" aria-hidden />
                  </Link>
                ) : null}
              </div>
              {info.task && briefOpen ? (
                <p
                  id={briefId}
                  className="subagent-reveal max-h-48 overflow-y-auto whitespace-pre-wrap rounded-lg bg-muted/50 px-2.5 py-2 text-[0.929em] leading-relaxed text-muted-foreground"
                >
                  {info.task}
                </p>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
