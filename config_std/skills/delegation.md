---
name: delegation
description: When and how to delegate a task to a sub-agent with the delegate_to_agent tool.
tags: [orchestration, delegation, multi-agent]
status: verified
source: curated
version: 2
---

# Delegation Protocol

## Preconditions
- Use **only** when the `delegate_to_agent` tool is available: it appears when your
  profile lists sub-agents (`subagents:` key) and you are not a sub-agent yourself.
  This skill is loaded automatically for those profiles; it is also present in
  plan/deep-research mode and in sub-agent sessions, where the tool is off: then
  simply do the work yourself.
- Pick the `agent` from the catalog in the tool description; its description says
  what that specialist is good at.
- Do not delegate if the task is a single tool call or a short answer you can do inline.

## When to delegate
- Work that matches a specialist profile better than yours (its tools, skills or instructions).
- Long or noisy work (heavy exploration, many tool calls) whose details would flood your context.
- Independent sub-tasks: you may call `delegate_to_agent` several times in the same
  step to run them **in parallel** (a per-chat cap applies).

## Handoff quality
The sub-agent does **not** see this conversation. The `task` must be self-contained:
- **Goal** and the **expected output format** (e.g. "a markdown table with …").
- **Inputs**: file paths, IDs, URLs, time ranges, numbers the user gave you.
- **Constraints** and **stop conditions** (what "done" means).
- Files: uploads of this chat are visible to the sub-agent; files it creates come
  back under `workspace/subagents/<child id>/`.

## After delegation
- Read the `status` in the result header (`done`, `error`, `timeout`, `cancelled`, `denied`).
- Integrate the results into a coherent reply; do not paste raw output without synthesis.
- On `error`/`timeout`, either retry once with a smaller, clearer task or do the work yourself.
- The user can open the full sub-agent transcript from the card in the chat.
