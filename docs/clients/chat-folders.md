---
title: Chat folders
sidebar_position: 4
description: Group chat-ui conversations into personal folders, with drag and drop in the sidebar.
---

# Chat folders

**Chat folders** let each user group their conversations in the chat-ui
sidebar. They are purely organisational: a folder does not change the agent's
context, memory or profile. Projects are the feature that does that, and they
are a separate concept.

The first section ([User flow](#user-flow)) is for end users. The rest
describes the API, the storage and the sidebar implementation.

## User flow

- **Create a folder**: click **+** next to **Cartelle** ("Folders") in the
  sidebar, type a name and press Enter. Names are unique per user (case does
  not matter) and at most 128 characters long.
- **Move a chat**: drag it from the sidebar onto a folder. You can also open
  the chat's **⋯** menu and point at **Sposta in cartella** ("Move to folder"):
  the list of folders opens beside it, then click a folder. Clicking or tapping
  the item opens the list too, so this works with the keyboard and on touch
  screens, where drag and drop is not available.
- **Take a chat out of a folder**: drag it onto **Recenti** ("Recent"), or
  choose **Togli dalla cartella** ("Remove from folder") at the bottom of the
  same folder list.
- **Rename or delete a folder**: use the folder's **⋯** menu. Deleting a
  folder **keeps its chats**: they go back to **Recenti**.

New chats, chats coming from Telegram or WhatsApp and every chat created
before this feature have **no folder** and appear under **Recenti**. Each user
has at most 200 folders.

Sidebar order: **Preferiti** ("Favorites"), then **Cartelle**, then
**Recenti**. Click a section title to collapse or expand that section. The
choice is remembered in the browser, and so is the open/closed state of each
folder. Collapsed sections open automatically while you search. **Cartelle**
also opens while you drag a chat. A collapsed **Recenti** header still accepts
dropped chats.

A favorite chat is listed only under **Preferiti**, even when it is in a
folder. The folder's counter still includes it.

## Data model

Migration `i7f8l9d037` adds the following and merges the two previous Alembic
heads (`f3a4b5c034`, `h6m7r8g036`):

| Table / column | Notes |
|----------------|-------|
| `chat_folders` | `id` (uuid), `tenant_id`, `user_id` (same identity as `conversations.user_id`), `name`, `position`, `created_at`, `updated_at`. Unique `(tenant_id, user_id, name)`. |
| `conversations.folder_id` | Nullable FK to `chat_folders.id`, `ON DELETE SET NULL`, indexed. `NULL` = no folder. |

The migration needs no backfill, because every existing chat stays `NULL`.
On SQLite, `src/data/bootstrap.py` also patches the column on databases
created by `create_all` before the migration ran.

## API

The endpoints are in `src/api/chat_folders.py`, under the `/chat-ui` prefix.
Authentication is the chat token or an API key with the `conversations:*`
scopes. A folder or chat of another user answers `404`.

| Method | Path | Body | Result |
|--------|------|------|--------|
| `GET` | `/chat-ui/folders` | — | `{data: [{id, name, position, conversation_count, created_at, updated_at}]}` in sidebar order |
| `POST` | `/chat-ui/folders` | `{name}` | `201` with the new folder, added last |
| `PATCH` | `/chat-ui/folders/{id}` | `{name?, position?}` | Updated folder |
| `DELETE` | `/chat-ui/folders/{id}` | — | `{deleted: true, released_conversations: n}`; the chats go back to no folder |
| `PUT` | `/chat-ui/conversations/{id}/folder` | `{folder_id: string \| null}` | `{id, folder_id}` |

Errors are returned in `detail`:

- `folder_name_taken` (`409`)
- `folder_limit_reached` (`409`)
- `folder_name_required` (`422`)
- `folder_name_too_long` (`422`)
- `folder_not_found` (`404`)

`conversation_count` counts the chats that the sidebar lists: not archived,
no side threads and no branches.

Moving a chat does **not** change `updated_at`, so filing a chat does not
move it to the top of the list.

`GET /chat-ui/conversations` now returns `folder_id` for each item and
accepts a `folder_id` filter. Pass a folder id to list that folder, or
`none` to list chats without a folder. Without the filter it lists all chats,
as before. `GET /chat-ui/conversations/{id}` also returns `folder_id`.

## Sidebar implementation

The sidebar is built from these files:

- **`chat-ui/lib/shell/use-chat-folders.ts`**: loads the folders and reloads
  their counts whenever the main list refreshes. It also loads the chats of
  **expanded** folders with `folder_id=<id>` (up to 200). This is needed
  because the main list holds only the 80 most recent chats. Moves are
  optimistic: the chat moves at once and is restored if the API call fails.
- **`chat-ui/lib/shell/use-collapsed-sections.ts`**: stores which sections
  are collapsed, in localStorage (`useSyncExternalStore`, so hydration is
  safe).
- **`chat-ui/components/layout/ThreadSidebar.tsx`**: contains
  `SidebarSection`, `FolderRow` and `FolderNameInput`. Drag and drop uses the native HTML5 API
  with the MIME type `application/x-aion-conversation`, so no extra
  dependency is needed. Folder headers and the **Recenti** section are drop
  targets.
- **`chat-ui/lib/api/aion.ts`**: contains the API helpers `listChatFolders`,
  `createChatFolder`, `updateChatFolder`, `deleteChatFolder` and
  `moveConversationToFolder`.
- **`chat-ui/lib/i18n/locales/*.json`**: holds the strings under
  `sidebar.folders.*`.

The API already supports `position`, but the sidebar does not yet let users
reorder folders by dragging them.

Tests are in `src/test/test_chat_folders.py`. They cover CRUD, moving chats,
per-user isolation and the migration.
