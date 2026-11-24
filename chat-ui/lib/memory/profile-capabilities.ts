/**
 * Derive chat-ui capabilities (memory panels, web search toggle, Deep Research
 * mode) from the active agent profile's MCP servers / native tool groups.
 * Mirrors the backend gating: a native group missing from the profile means
 * the agent cannot use that capability, so the UI hides the related controls.
 */

export type ProfileMemorySource = {
  mcp_servers?: string[] | null;
  native_tool_groups?: string[] | null;
  /** Raw `skills:` list of the profile YAML (may contain fnmatch globs like `memory_*`). */
  skills?: string[] | null;
};

/** Skill that teaches the agent when/how to use Mnemos (`config/skills/memory_protocol.md`). */
export const MEMORY_PROTOCOL_SKILL = "memory_protocol";

function normServers(servers?: string[] | null): string[] {
  return (servers ?? []).map((s) => s.trim().toLowerCase()).filter(Boolean);
}

function normGroups(groups?: string[] | null): string[] {
  return (groups ?? []).map((s) => s.trim().toLowerCase()).filter(Boolean);
}

export function hasMnemosTools(profile?: ProfileMemorySource | null): boolean {
  const groups = normGroups(profile?.native_tool_groups);
  return groups.includes("mnemos");
}

/** True when the profile lists the `web_research` native group (web_search / web_fetch_page). */
export function hasWebResearch(profile?: ProfileMemorySource | null): boolean {
  return normGroups(profile?.native_tool_groups).includes("web_research");
}

/**
 * True when the profile has the Deep Research connector (`deep_research` native
 * group, or the legacy `deep_research` MCP slug): `trigger_research` is then
 * callable in normal mode too. Not used to hide the Deep Research agent mode,
 * which the backend enables for every profile.
 */
export function hasDeepResearch(profile?: ProfileMemorySource | null): boolean {
  return (
    normGroups(profile?.native_tool_groups).includes("deep_research") ||
    normServers(profile?.mcp_servers).includes("deep_research")
  );
}

/** @deprecated use hasMnemosTools */
export function hasMempalaceMcp(profile?: ProfileMemorySource | null): boolean {
  return hasMnemosTools(profile);
}

export function hasSqlQueryMemory(profile?: ProfileMemorySource | null): boolean {
  const groups = normGroups(profile?.native_tool_groups);
  if (groups.includes("sql_query_memory")) return true;
  const servers = normServers(profile?.mcp_servers);
  return servers.some((s) => {
    if (s === "memory" || s.endsWith("/memory")) return true;
    return (
      s === "query_memory" ||
      s.includes("query_memory") ||
      s === "sql_query_memory" ||
      s.includes("sql_query_memory")
    );
  });
}

/**
 * Whether the memory UI exists at all: the "Memoria" dock tab, the "+" menu
 * project entry and the per-message "memorize" button.
 *
 * Mnemos (notes) and query memory (saved SQL queries) are independent systems
 * that only share the `projects` table, so either one is enough. Inside the
 * panel each part is gated on its own: Mnemos cards by {@link hasMnemosTools},
 * the "Query SQL" card by {@link hasSqlQueryMemory}. With neither, no memory
 * control is shown.
 */
export function showProjectMemoryUi(profile?: ProfileMemorySource | null): boolean {
  return hasMnemosTools(profile) || hasSqlQueryMemory(profile);
}

/** Minimal `fnmatch` (`*`, `?`) as used by the backend for profile skill globs. */
function fnmatch(name: string, pattern: string): boolean {
  const re = pattern.replace(/[.+^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*").replace(/\?/g, ".");
  return new RegExp(`^${re}$`).test(name);
}

/**
 * True when the profile loads the `memory_protocol` skill (exact name or a
 * glob such as `memory_*`), mirroring `AgentProfile.resolved_skills()`.
 * Without it Mnemos still works but the agent rarely saves or recalls notes,
 * so the memory panel shows a warning instead of disabling anything.
 */
export function hasMemoryProtocolSkill(profile?: ProfileMemorySource | null): boolean {
  return (profile?.skills ?? []).some((s) => {
    const item = s.trim();
    if (!item) return false;
    return item.includes("*") || item.includes("?")
      ? fnmatch(MEMORY_PROTOCOL_SKILL, item)
      : item === MEMORY_PROTOCOL_SKILL;
  });
}
