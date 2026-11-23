"use client";

/**
 * SubagentPanel — read-only transcript of a sub-agent run in the right-hand dock.
 *
 * Opened from a SubagentCard in the parent chat (ChatWorkspace provides the
 * bridge, see lib/subagent/panel-context.ts). The child conversation
 * (`sub_*`) is written only by the backend orchestrator, so the panel has no
 * composer.
 *
 * Data sources:
 * - persisted history of the child conversation (`fetchConversationHistory`),
 *   reloaded when the run status changes and polled while it is running,
 *   because the child turn has no reconnectable SSE stream of its own;
 * - the live `SubagentInfo` pushed by the card (status, child tool calls),
 *   shown as an activity list until the child's answer is persisted.
 *
 * The brief the parent sent (first user message) is collapsed to a few lines:
 * it is a prompt written for a model, useful on demand, noisy by default.
 * See docs/agents/subagents.md.
 */

import Link from "next/link";
import { useCallback, useEffect, useRef, useState, type ComponentProps } from "react";
import { AlertCircle, ChevronDown, Loader2, Lock, SquareArrowOutUpRight } from "lucide-react";

import { cn } from "@/lib/cn";
import { useT } from "@/lib/i18n/use-t";
import { fetchConversationHistory } from "@/lib/api/aion";
import { fromHistory, type SideThreadMessage } from "@/lib/side-thread/use-side-thread-chat";
import type { SubagentInfo } from "@/lib/sse/types";
import { TurnTimeline } from "@/components/chat/TurnTimeline";
import { AgentAvatar, StepGlyph, SubagentMeta } from "@/components/chat/SubagentCard";
import { ShimmerText } from "@/components/chat/ShimmerText";

type TimelineProps = ComponentProps<typeof TurnTimeline>;

type Props = {
  /** Live run state from the card; `conversationId` is the child conversation to load. */
  info: SubagentInfo;
  userId: string;
  token?: string | null;
  toolsView: TimelineProps["toolsView"];
  renderMarkdownLink?: TimelineProps["renderMarkdownLink"];
  formatTextWithCitations?: TimelineProps["formatTextWithCitations"];
};

/** History poll interval while the child is running (no live stream to attach to). */
const RUNNING_POLL_MS = 4000;

export function SubagentPanel({
  info,
  userId,
  token,
  toolsView,
  renderMarkdownLink,
  formatTextWithCitations,
}: Props) {
  const t = useT();
  const childId = info.conversationId ?? "";
  const running = info.status === "running";
  const name = info.profileName || info.profile || t("chat.subagent.unknown_agent");

  const [messages, setMessages] = useState<SideThreadMessage[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [briefOpen, setBriefOpen] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const stickRef = useRef(true);

  /** Fetch the child transcript (resolves to null when there is nothing to fetch). */
  const fetchHistory = useCallback(
    (signal: AbortSignal) =>
      childId
        ? fetchConversationHistory(childId, userId, token, signal, { includeArchived: true })
        : Promise.resolve(null),
    [childId, userId, token],
  );

  /** Store a fetch result; state is only set from the async callback, never synchronously in an effect. */
  const apply = useCallback(
    (res: Awaited<ReturnType<typeof fetchConversationHistory>> | null, signal: AbortSignal) => {
      if (!res || signal.aborted) return;
      setLoading(false);
      if (!res.ok) {
        setError(res.error);
        return;
      }
      setError(null);
      setMessages(fromHistory(res.messages));
    },
    [],
  );

  // Initial load + reload on every status change (e.g. running → done persists the answer).
  useEffect(() => {
    const ac = new AbortController();
    void fetchHistory(ac.signal).then((res) => apply(res, ac.signal));
    return () => ac.abort();
  }, [fetchHistory, apply, info.status]);

  // Poll while the run is live: the child turn has no reconnectable stream.
  useEffect(() => {
    if (!running) return;
    const ac = new AbortController();
    const id = window.setInterval(() => {
      void fetchHistory(ac.signal).then((res) => apply(res, ac.signal));
    }, RUNNING_POLL_MS);
    return () => {
      window.clearInterval(id);
      ac.abort();
    };
  }, [running, fetchHistory, apply]);

  // Follow new content unless the user scrolled up to read.
  useEffect(() => {
    const el = scrollRef.current;
    if (el && stickRef.current) el.scrollTop = el.scrollHeight;
  }, [messages, info.stepCount, info.status]);

  // The first user message is the parent's brief; render it in the header block.
  const briefIdx = messages.findIndex((m) => m.role === "user");
  const brief = (briefIdx >= 0 ? messages[briefIdx].content : "") || info.task || "";
  const body = messages.filter((_, i) => i !== briefIdx);
  const hasAnswer = body.some((m) => m.role === "assistant" && (m.segments?.length ?? 0) > 0);
  const lastStep = info.steps.length ? info.steps[info.steps.length - 1] : null;
  const hiddenSteps = Math.max(0, info.stepCount - info.steps.length);
  // Long briefs collapse; a short one is shown whole with no toggle.
  const briefLong = brief.length > 220 || brief.split("\n").length > 4;

  return (
    <section className="flex h-full min-h-0 flex-col" aria-label={t("chat.subagent.panel.aria", { name })}>
      <header className="shrink-0 space-y-3 border-b border-border/70 px-4 pb-3.5 pt-3.5">
        <div className="flex items-center gap-3">
          <AgentAvatar status={info.status} />
          <div className="min-w-0 flex-1">
            <h2 className="truncate text-[15px] font-semibold leading-snug text-foreground" title={name}>
              {name}
            </h2>
            <SubagentMeta info={info} className="mt-0.5 text-[12.5px]" />
          </div>
          {childId ? (
            <Link
              href={`/c/${childId}`}
              className="focus-ring -mr-1 shrink-0 rounded-lg p-1.5 text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground"
              title={t("chat.subagent.panel.open_full")}
              aria-label={t("chat.subagent.panel.open_full")}
            >
              <SquareArrowOutUpRight className="size-4" aria-hidden />
            </Link>
          ) : null}
        </div>

        {brief ? (
          <div className="rounded-lg bg-muted/45 px-3 py-2">
            <p className="mb-1 text-[11.5px] font-medium text-muted-foreground">
              {t("chat.subagent.panel.brief")}
            </p>
            <p
              className={cn(
                "whitespace-pre-wrap break-words text-[13px] leading-relaxed text-foreground/80",
                briefLong && !briefOpen && "line-clamp-3",
                briefOpen && "max-h-64 overflow-y-auto",
              )}
            >
              {brief}
            </p>
            {briefLong ? (
              <button
                type="button"
                onClick={() => setBriefOpen((v) => !v)}
                aria-expanded={briefOpen}
                className="focus-ring -ml-1 mt-1 inline-flex items-center gap-1 rounded-md px-1 text-[12px] font-medium text-muted-foreground transition-colors hover:text-foreground"
              >
                <ChevronDown
                  className={cn("size-3.5 transition-transform duration-200", briefOpen && "rotate-180")}
                  aria-hidden
                />
                {briefOpen ? t("chat.subagent.panel.brief_less") : t("chat.subagent.panel.brief_more")}
              </button>
            ) : null}
          </div>
        ) : null}
      </header>

      <div
        ref={scrollRef}
        onScroll={(e) => {
          const el = e.currentTarget;
          stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
        }}
        className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 py-4"
        aria-live="polite"
        aria-busy={running || loading}
      >
        {loading && messages.length === 0 ? (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="size-4 motion-safe:animate-spin" aria-hidden />
            {t("chat.subagent.panel.loading")}
          </div>
        ) : null}

        <div className="flex flex-col gap-4">
          {body.map((m) =>
            m.role === "user" ? (
              <div
                key={m.id}
                className="ml-auto max-w-[88%] whitespace-pre-wrap break-words rounded-2xl rounded-br-md bg-muted px-3.5 py-2 text-[14px] leading-relaxed text-foreground"
              >
                {m.content}
              </div>
            ) : (
              <div key={m.id} className="chat-font min-w-0 text-[14px]">
                <TurnTimeline
                  segments={m.segments ?? []}
                  toolsView={toolsView}
                  conversationId={childId}
                  token={token}
                  messageId={m.id}
                  renderMarkdownLink={renderMarkdownLink}
                  formatTextWithCitations={formatTextWithCitations}
                />
              </div>
            ),
          )}

          {/* Live activity until the child's answer is persisted. */}
          {running && !hasAnswer ? (
            <div className="space-y-2">
              {info.steps.length > 0 ? (
                <ul className="space-y-1 text-[13px]">
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
                      <span className="truncate font-mono text-[12px] text-foreground/80" title={s.name}>
                        {s.name}
                      </span>
                    </li>
                  ))}
                </ul>
              ) : null}
              <ShimmerText className="block text-[13px]">
                {lastStep && lastStep.status === "running"
                  ? t("chat.subagent.running_step", { name: lastStep.name })
                  : t("chat.subagent.panel.working")}
              </ShimmerText>
            </div>
          ) : null}

          {!running && !hasAnswer && !loading && info.error ? (
            <p className="whitespace-pre-wrap rounded-lg bg-destructive/[0.06] px-3 py-2 text-[13px] text-destructive">
              {info.error}
            </p>
          ) : null}

          {!running && !hasAnswer && !loading && !info.error && !error ? (
            <p className="text-[13px] text-muted-foreground">{t("chat.subagent.panel.empty")}</p>
          ) : null}

          {error ? (
            <div
              role="alert"
              className="flex items-start gap-2 rounded-xl bg-destructive/10 px-3 py-2.5 text-[13px] text-destructive"
            >
              <AlertCircle className="mt-0.5 size-4 shrink-0" aria-hidden />
              <p className="min-w-0 flex-1 break-words">{t("chat.subagent.panel.error", { msg: error })}</p>
            </div>
          ) : null}
        </div>
      </div>

      {/* Stands where a composer would be: explains why there is none. */}
      <footer className="flex shrink-0 items-center justify-center gap-1.5 border-t border-border/70 px-4 py-2.5 text-[12px] text-muted-foreground">
        <Lock className="size-3.5 shrink-0" aria-hidden />
        <span>{t("chat.subagent.panel.read_only")}</span>
      </footer>
    </section>
  );
}
