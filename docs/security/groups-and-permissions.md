---
title: User groups and access rules
sidebar_position: 7
description: Group users, limit which agent profiles, skills, MCP servers and native tools they can use, and decide which parts of the admin panel they can see or change.
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

## Admin panel permissions

Every user can open the admin panel, but sees and changes only the parts
their permissions allow. The panel is split into five **areas**, each granted
at **Lettura** (*Read*) or **Scrittura** (*Write*, includes read):

| Area | What it covers |
|---|---|
| **Gestione accessi** (*Access management*) | Menu **Accessi**: users (accounts, login mode, SSO, 2FA, new accounts, groups, migration), service accounts, security |
| **Gestione agenti** (*Agent management*) | Menu **Agenti**: profiles, skills, memory, scheduled jobs |
| **Impostazioni** (*Settings*) | The **Impostazioni** page: models, memory, web, speech, sandbox, SMTP, file server. It changes the whole platform, so it is granted on its own |
| **Applicazioni esterne** (*External applications*) | **MCP Hub** (listed in the Agenti menu), **WhatsApp**, **Telegram** |
| **Osservabilità** (*Observability*) | Metrics, feedback, diagnostics, system, including actions such as running diagnostics |

Rules:

- **No rule means no area.** Unlike the other rule types, a user without an
  admin-panel rule sees only the **Panoramica** (*Overview*) page. Grant
  areas on the tab **Pannello admin** (*Admin panel*) of the access rules of
  a user or a group.
- **Permissions add up.** The rules of the user and of all their groups are
  combined: for each area the highest level wins ("Lettura" from one group
  and "Scrittura" from another = "Scrittura"), and areas from different
  groups are all available.
- **Read only.** With "Lettura" a page shows a *Sola lettura* (*Read only*)
  banner and no create, edit, save or delete controls: forms show their
  values but cannot be changed. The backend refuses every change anyway.
- **Observability settings.** The Prometheus / OpenTelemetry / Opik settings
  of the **Metriche** (*Metrics*) page need **Osservabilità** in write, even
  for users who can change the other settings with **Impostazioni**. The
  scheduler switch of **Job pianificati** (*Scheduled jobs*) follows
  **Gestione agenti**.
- **Solo chat UI** (*Chat UI only*). On the tab **Funzioni** (*Features*),
  excluding **Pannello admin** takes away the basic access to the panel. If
  another rule grants an area, the user can still open the panel and see that
  area (permissions add up).
- **Super-admins** (users with the admin role) always have every area in
  write and are the only ones who can create other super-admins. New users
  are always created as normal users.

### What a user with "Gestione accessi" in write can do

They can give **any** permission to anyone (all five areas in write
included), with these exceptions, enforced by the backend
(`403 permission_denied` with a `reason`):

| Not allowed | `reason` |
|---|---|
| Change their own account, rules or groups | `self` |
| Change a group they belong to, or join a group | `own_group` |
| Change a super-admin (account, rules, groups, password, 2FA, SSO identities) or a group that contains one | `superadmin_target` |
| Give or remove the super-admin role | `admin_role_reserved` |

In the admin panel the rows of their own account and of super-admins have no
edit actions, and the super-admin option of the user form is shown only to
super-admins.

### Chat-ui features

On the tab **Funzioni** an administrator can also switch off, per user or
group, these chat-ui features (all allowed while no rule excludes them):

| Feature | What it blocks |
|---|---|
| Profili personali | Creating [private profiles](../clients/private-profiles.md) |
| Pannello admin | Opening the admin panel ("Solo chat UI") |
| Telegram | Linking a Telegram account and personal bots |
| WhatsApp | Linking a WhatsApp number |
| API key | Creating personal API keys |

Everything else in chat-ui has no permission and stays available to every
user.

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
- `src/access/admin_areas.py` defines the admin areas and the **route
  table**: every `/admin/*` request (method + path) maps to an area and
  `read`/`write`, to the dashboard (any panel user) or to super-admin only
  (one-click update). `require_admin_role` applies it to every admin router;
  an unmapped route is super-admin only, and `src/test/test_admin_rbac.py`
  fails when a new route is not in the table. A few list reads are shared
  across areas (profile list, user list, SMTP status, knowledge projects)
  because other pages show them in pickers and filters.
- `/admin/settings` is checked per key: `GET` is open to every panel user
  but returns only the keys of the caller's areas (plus a few non-secret
  dashboard keys); `POST` requires write on the area of each **changed**
  key (`AION_PROMETHEUS_*`, `AION_METRICS_*`, `AION_OTEL_*`, `AION_OPIK_*`,
  `OPIK_*` = observability, `AION_CRON_ENABLED` = agents, everything else =
  settings). A `POST` is a partial update: keys it does not send keep
  their value.
- In admin-ui, `WriteOnly`, `useCanWrite(area)` and `usePageCanWrite()` in
  `admin-ui/lib/auth/admin-access.tsx` hide write controls.
- `src/access/escalation.py` holds the anti-escalation checks used by the
  user, group and login-mode endpoints.

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
with the keys `profile`, `skill`, `mcp_server`, `native_tool_group`,
`capability` (ids: `private_profiles.create`, `admin_panel.access`,
`telegram.use`, `whatsapp.use`, `api_keys.create`) and `admin_section` (ids
`<area>:<level>`, e.g. `agents:write`; no `*`, no patterns); only
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

- `GET /auth/me` returns the user's `groups` (slugs), `permissions`
  (strings `<resource_type>:<id>:<action>`, for example
  `profile:coding_workspace:use` or `admin_section:agents:write`),
  `admin_access` (may the user open the admin panel) and `admin_areas`
  (`{"agents": "write", ...}`).
- `POST /auth/login` with `client=admin` answers `403
  admin_panel_not_allowed` to "chat UI only" users without areas; the admin
  role is not required (same rule for the SSO login of the admin panel).
- `POST /auth/sso/admin-handoff` (chat token only) turns the chat-ui session
  into a one-time code (60 s) and returns the admin-ui URL
  `/login/sso#code=…`, which exchanges it with `POST /auth/sso/exchange`:
  the **Pannello admin** item of the chat-ui profile menu opens the panel
  without a second login. "Chat UI only" users get `403
  admin_panel_not_allowed`.
- `GET /v1/me/access` returns, per type, `limited` and `allowed` (existing ids,
  patterns expanded; `null` = everything). For `mcp_server` it also returns
  `configurable`: the servers the user can set up in Integrations (allowed
  servers plus the servers of their profiles; `null` = all).

Clients use them to hide what the user cannot use; the backend always checks
again. In `/v1/integrations`, when an MCP rule applies, the list shows only the
configurable servers, and turning on, saving credentials or starting OAuth for
any other server returns `403 mcp_server_not_allowed`. Turning a server off is
always possible.
