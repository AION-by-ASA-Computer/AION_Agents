---
title: User groups and access rules
sidebar_position: 7
description: Group users and limit which agent profiles, skills, MCP servers and native tools they can use, with rules on groups or on single accounts.
---

# User groups and access rules

Administrators can put users (people and service accounts) into **groups** and
attach **access rules** to a group or to a single account. Use groups when
several people should see the same things, for example "Sales" only uses the
sales assistants. This page is for administrators; the last section is for
developers.

Rules exist for four types of resource, each set on its own tab:

| Type | What a rule decides |
|---|---|
| **Profiles** | Which agent profiles the account can chat and schedule jobs with |
| **Skills** | Which skills the account can add to its own profiles |
| **MCP** | Which MCP servers (integrations) the account can turn on and set up by itself |
| **Native tools** | Which native tool groups (web research, memory, …) the account can add to its own profiles |
| **Features** | End-user features that can be turned off, today **Profili personali** (creating [private profiles](../clients/private-profiles.md) in chat-ui) |

Rules on skills, MCP servers and native tools **never limit a profile the
user can use**. If an administrator gives a user the profile "Data agent",
that profile works in full even if it contains a skill or an MCP server the
user has no rule for. These rules only decide what the user can choose by
themself: today the integrations they can turn on in chat-ui
(**Integrations**), and later their private profiles. The MCP servers used by
the user's profiles always stay available in Integrations, so the user can
enter the credentials those profiles need.

## How rules combine

The model is designed so that nobody has to be placed in a hidden "default"
group:

Each type is evaluated on its own:

- **No rule means no limit.** A user who is in no group and has no rule of
  their own can use everything. That is the state of every new account.
- **Only subjects with a rule count.** For each user, AION looks at their own
  rule and at the rule of each of their groups. Groups without a rule for that
  type are ignored.
- **Rules add up.** If at least one of them has a rule, the user can use the
  union of what those rules grant.

| Rule (on a user or a group) | Meaning |
|---|---|
| No rule | Does not limit this type; ignored when combining |
| All (`*`) | Grants everything of this type, even if another group grants only a few |
| Only some | Grants the selected items |
| None | Has a rule, but grants nothing |

Example: Anna is in *Sales* (rule: profiles A, B) and *Board* (no profile
rule). She can use A and B. If she is also given an own rule "C", she can use
A, B and C.

When a client asks for the default profile (`aion_std`) and the user may not
use it, AION falls back to the first allowed profile: first the user's own
rule, then the groups in the order the user joined them. With a "no profile"
rule there is no fallback and the request is refused (`403
profile_not_allowed` on the API).

Administrators follow the same rules for **using** profiles: without rules an
administrator can use everything, but a rule on their account or group limits
them too. Access to the admin panel itself is not affected by groups.

## Manage groups in the admin panel

1. Open **Accessi → Utenti** (*Access → Users*), select the **Gruppi**
   (*Groups*) tab and click **Nuovo gruppo** (*New group*). Give it a
   name; the identifier is suggested from it.
2. Click the members icon of the group and select the accounts.
3. Click the shield icon (*access rules*) and choose a rule on each tab
   (*Profili*, *Skill*, *MCP*, *Tool nativi*, *Funzioni*). A dot on a tab means the group
   has a rule for that type.

To see or change what one user can do, open the **Account** tab of
**Accessi → Utenti** and click the **Accessi** (*Access*) icon of the user. The dialog shows:

- the user's groups (click to add or remove),
- the user's own rules, one tab per type,
- under each tab, what the user can use **now** and which rule each item
  comes from.

Changes apply at the user's next request; nobody needs to log in again.

Deleting a group removes its members' memberships and rules immediately.
Deleting a user removes their memberships and own rules.

## For developers

**Storage** (Alembic revision `j8g9m0e038`):

| Table | Content |
|---|---|
| `user_groups` | One row per group (`slug` unique per tenant) |
| `user_group_members` | `group_id` ↔ `user_id` (`users.id`) |
| `access_grants` | `subject_type` (`user`/`group`), `subject_id`, `resource_type`, `resource_id`, `action` |

A rule is the set of `access_grants` rows of one subject for one
`resource_type` and `action`. `resource_id` is a slug, `*`, or `!none` (a rule
that grants nothing). The migration copies the legacy per-user allowlist
`user_profile_access` into direct user rules; that table is still read as a
direct rule until it is dropped in a later release, so databases where the
data step did not run stay correct.

**Code**:

- `src/access/principal.py` builds the caller's `Principal` (roles from the
  database, groups, effective rules) with a short cache
  (`AION_ACCESS_CACHE_TTL_SEC`, default 15 s). Every write invalidates the
  affected users.
- `src/access/policy.py` holds the decisions (`can`, `resolve_profile_for`,
  `filter_profiles`). Chat, cron, Telegram, WhatsApp, A2A and the agent
  factory all go through it.
- `src/access/store.py` writes groups, memberships and rules.
- `src/access/catalog.py` lists the ids that can go in a rule, per type.
- `src/access/mcp_filter.py` computes the MCP servers a user can set up in
  Integrations (`configurable_mcp_servers`).

**Admin API** (super-admin only; `src/api/admin_groups.py`):

| Method and path | Purpose |
|---|---|
| `GET` / `POST /admin/groups` | List / create groups |
| `GET` / `PATCH` / `DELETE /admin/groups/{id}` | Detail (members, rules) / rename / delete |
| `PUT /admin/groups/{id}/members` | Replace members (`{"user_ids": [...]}`) |
| `GET` / `PUT /admin/groups/{id}/rules` | Group rules |
| `GET` / `PUT /admin/users/{id}/groups` | Replace a user's groups (`{"group_ids": [...]}`) |
| `GET` / `PUT /admin/users/{id}/rules` | A user's own rules |
| `GET /admin/users/{id}/effective-access` | Effective access and the rule behind it, per type |
| `GET /admin/access/catalog` | Ids that can go in a rule, per type (`{types: {profile: [{id, name, description}], …}}`) |

Rules are sent as `{"rules": {"profile": ["a", "b"], "mcp_server": ["grafana"]}}`
with the keys `profile`, `skill`, `mcp_server`, `native_tool_group` and
`capability` (ids: `private_profiles.create`); only
the keys present change. `null` removes the rule, `[]` is "none", `["*"]` is
"all". Skill, MCP server and native tool ids may be glob patterns (`sql_*`)
that match at least one existing id. Ids that do not exist return `422`
(`unknown_profiles` for profiles, `unknown_resources` otherwise), and so do
resource types that cannot be edited yet (`resource_type_not_editable`). Every change is
written to the audit log (`group.*`, `user.groups.update`,
`user.rules.update`).

The older `GET` / `POST /admin/users/{id}/profiles` (used by the service
accounts page) still work: they read and write the user's own profile rule,
and an empty list removes it.

**User API**:

- `GET /auth/me` returns the user's `groups` (slugs) and `permissions`
  (strings `<resource_type>:<id>:<action>`, for example
  `profile:coding_workspace:use`).
- `GET /v1/me/access` returns, per type, `limited` and `allowed` (existing ids,
  patterns expanded; `null` = everything). For `mcp_server` it also returns
  `configurable`: the servers the user can set up in Integrations (allowed
  servers plus the servers of their profiles; `null` = all).

Clients use them to hide what the user cannot use; the backend always checks
again. In `/v1/integrations`, when an MCP rule applies, the list shows only the
configurable servers, and turning on, saving credentials or starting OAuth for
any other server returns `403 mcp_server_not_allowed`. Turning a server off is
always possible.
