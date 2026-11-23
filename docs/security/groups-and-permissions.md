---
title: User groups and access rules
sidebar_position: 7
description: Group users and limit which agent profiles they can use, with rules on groups or on single accounts.
---

# User groups and access rules

Administrators can put users (people and service accounts) into **groups** and
attach **access rules** to a group or to a single account. A rule says which
agent profiles the account may use. Use groups when several people should see
the same set of profiles, for example "Sales" only uses the sales assistants.
This page is for administrators; the last section is for developers.

Today rules cover **agent profiles**. Skills, MCP servers and other user
permissions use the same model and will be added later
(see `PLAN_access_control.md` in the repository).

## How rules combine

The model is designed so that nobody has to be placed in a hidden "default"
group:

- **No rule means no limit.** A user who is in no group and has no rule of
  their own can use every profile. That is the state of every new account.
- **Only subjects with a rule count.** For each user, AION looks at their own
  rule and at the rule of each of their groups. Groups without a profile rule
  are ignored.
- **Rules add up.** If at least one of them has a rule, the user can use the
  union of what those rules grant.

| Rule (on a user or a group) | Meaning |
|---|---|
| No rule | Does not limit profiles; ignored when combining |
| All profiles (`*`) | Grants every profile, even if another group grants only a few |
| Only some profiles | Grants the selected profiles |
| No profile | Has a rule, but grants nothing |

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
3. Click the shield icon (*access rules*) and choose the profile rule.

To see or change what one user can do, open the **Account** tab of
**Accessi → Utenti** and click the **Accessi** (*Access*) icon of the user. The dialog shows:

- the user's groups (click to add or remove),
- the user's own profile rule,
- the profiles the user can use **now**, and which rule each one comes from.

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

**Admin API** (super-admin only; `src/api/admin_groups.py`):

| Method and path | Purpose |
|---|---|
| `GET` / `POST /admin/groups` | List / create groups |
| `GET` / `PATCH` / `DELETE /admin/groups/{id}` | Detail (members, rules) / rename / delete |
| `PUT /admin/groups/{id}/members` | Replace members (`{"user_ids": [...]}`) |
| `GET` / `PUT /admin/groups/{id}/rules` | Group rules |
| `GET` / `PUT /admin/users/{id}/groups` | Replace a user's groups (`{"group_ids": [...]}`) |
| `GET` / `PUT /admin/users/{id}/rules` | A user's own rules |
| `GET /admin/users/{id}/effective-access` | Effective access and the rule behind it |

Rules are sent as `{"rules": {"profile": ["a", "b"]}}`: `null` removes the
rule, `[]` is "no profile", `["*"]` is "all profiles". Unknown profile slugs
and resource types that cannot be edited yet return `422`. Every change is
written to the audit log (`group.*`, `user.groups.update`,
`user.rules.update`).

The older `GET` / `POST /admin/users/{id}/profiles` (used by the service
accounts page) still work: they read and write the user's own profile rule,
and an empty list removes it.

`GET /auth/me` returns the user's `groups` (slugs) and `permissions` (strings
`<resource_type>:<id>:<action>`, for example `profile:coding_workspace:use`)
so clients can hide what the user cannot use. The backend always checks again.
