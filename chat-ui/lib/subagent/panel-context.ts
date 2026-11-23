"use client";

/**
 * Bridge between SubagentCard (rendered deep inside TurnTimeline) and the
 * ChatWorkspace dock, which shows the sub-agent's read-only transcript in the
 * right-hand panel (components/dock/SubagentPanel.tsx).
 *
 * ChatWorkspace provides the API around the chat pane. The card is the source
 * of truth for the live run state (it is fed by the parent's `subagent_*` SSE
 * events), so while its run is the one open in the panel it pushes every
 * change through `sync`. Without a provider (e.g. a timeline rendered inside
 * another dock panel) the card falls back to a plain link to `/c/<child id>`.
 */

import { createContext, useContext } from "react";
import type { SubagentInfo } from "@/lib/sse/types";

export type SubagentPanelApi = {
  /** Child conversation id currently shown in the dock, or null when the panel is closed. */
  activeId: string | null;
  /** Open (or switch) the dock panel on this run. */
  open: (info: SubagentInfo) => void;
  /** Push fresh live state for the run shown in the panel; ignored for other runs. */
  sync: (info: SubagentInfo) => void;
};

export const SubagentPanelContext = createContext<SubagentPanelApi | null>(null);

/** Panel API when rendered inside ChatWorkspace's chat pane, otherwise null. */
export function useSubagentPanel(): SubagentPanelApi | null {
  return useContext(SubagentPanelContext);
}
