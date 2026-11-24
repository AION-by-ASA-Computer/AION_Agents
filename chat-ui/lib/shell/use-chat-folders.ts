"use client";

/**
 * Sidebar chat folders: state, lazy loading and mutations for ThreadSidebar.
 *
 * - The folder list (with counts) is reloaded whenever the main conversation
 *   list changes (`items` identity), so badges follow deletes/renames/moves.
 * - A folder's chats are fetched only while it is expanded (the main list is
 *   capped at 80 chats, so older chats in a folder would otherwise be missing).
 * - Moves are optimistic: `overrides` holds the target folder of each moved
 *   chat until the reloaded data agrees, and is reverted if the API fails.
 * - Expanded folders are a per-browser convenience kept in localStorage.
 *
 * Folders are purely organisational (see docs/clients/chat-folders.md).
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import {
  createChatFolder,
  deleteChatFolder,
  listChatFolders,
  listChatUiConversations,
  moveConversationToFolder,
  updateChatFolder,
  type ChatFolder,
  type ConversationSummary,
} from "@/lib/api/aion";

const EXPANDED_KEY = "aion-chat-folders-expanded";

function readExpanded(): string[] {
  try {
    const raw = localStorage.getItem(EXPANDED_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter((x): x is string => typeof x === "string") : [];
  } catch {
    return [];
  }
}

function writeExpanded(ids: string[]): void {
  try {
    localStorage.setItem(EXPANDED_KEY, JSON.stringify(ids));
  } catch {
    /* private mode / blocked storage: expansion just is not remembered */
  }
}

/**
 * @param items main sidebar list (most recent chats of every folder)
 * @param onRefresh reloads `items` (shell `refreshThreads`)
 */
export function useChatFolders({
  userId,
  token,
  items,
  onRefresh,
}: {
  userId: string;
  token?: string | null;
  items: ConversationSummary[];
  onRefresh: () => void | Promise<void>;
}) {
  const [folders, setFolders] = useState<ChatFolder[]>([]);
  // Lazy init from localStorage. No hydration mismatch: folders are fetched
  // client-side after mount, so nothing depends on `expanded` in the SSR markup.
  const [expanded, setExpanded] = useState<string[]>(() =>
    typeof window === "undefined" ? [] : readExpanded(),
  );
  /** Chats of expanded folders, as last fetched (entries of collapsed folders are ignored). */
  const [folderItems, setFolderItems] = useState<Record<string, ConversationSummary[]>>({});
  /** Optimistic folder of chats being moved (null = no folder); cleared once the server agrees. */
  const [overrides, setOverrides] = useState<Record<string, string | null>>({});

  // Folder list + counts follow every reload of the main list (`items`).
  useEffect(() => {
    if (!userId) return;
    let cancelled = false;
    void listChatFolders(userId, token).then((rows) => {
      if (!cancelled) setFolders(rows);
    });
    return () => {
      cancelled = true;
    };
  }, [userId, token, items]);

  // (Re)load the chats of expanded folders, again whenever the main list changes.
  useEffect(() => {
    if (!userId || expanded.length === 0) return;
    const ac = new AbortController();
    void Promise.all(
      expanded.map(async (fid) => {
        const rows = await listChatUiConversations(userId, token, { folderId: fid, signal: ac.signal });
        return [fid, rows] as const;
      }),
    )
      .then((pairs) => {
        // Replace (not merge): rows of collapsed folders are dropped so a chat
        // deleted or moved meanwhile cannot linger in Favorites from a stale cache.
        if (!ac.signal.aborted) setFolderItems(Object.fromEntries(pairs));
      })
      .catch(() => undefined);
    return () => ac.abort();
  }, [userId, token, expanded, items]);

  // Ids of existing folders: chats pointing to a folder deleted elsewhere count as "no folder".
  const knownIds = useMemo(() => new Set(folders.map((f) => f.id)), [folders]);

  const toggleFolder = useCallback((folderId: string, open?: boolean) => {
    setExpanded((prev) => {
      const isOpen = prev.includes(folderId);
      const want = open ?? !isOpen;
      if (want === isOpen) return prev;
      const next = want ? [...prev, folderId] : prev.filter((id) => id !== folderId);
      writeExpanded(next);
      return next;
    });
  }, []);

  /** Every chat known to the sidebar (main list wins: it is the freshest copy). */
  const allConversations = useMemo(() => {
    const byId = new Map<string, ConversationSummary>();
    for (const fid of expanded) for (const c of folderItems[fid] ?? []) byId.set(c.id, c);
    for (const c of items) byId.set(c.id, c);
    return Array.from(byId.values()).sort((a, b) => (b.updated_at || "").localeCompare(a.updated_at || ""));
  }, [items, folderItems, expanded]);

  /** Folder of a chat, taking pending optimistic moves into account. */
  const folderOf = useCallback(
    (c: ConversationSummary): string | null => {
      if (Object.prototype.hasOwnProperty.call(overrides, c.id)) return overrides[c.id];
      const fid = c.folder_id ?? null;
      // A chat whose folder was deleted elsewhere is shown under Recent.
      return fid && knownIds.has(fid) ? fid : null;
    },
    [overrides, knownIds],
  );

  const createFolder = useCallback(
    async (name: string) => {
      const folder = await createChatFolder(name, userId, token);
      setFolders((prev) => [...prev, folder]);
      return folder;
    },
    [userId, token],
  );

  const renameFolder = useCallback(
    async (folderId: string, name: string) => {
      const updated = await updateChatFolder(folderId, { name }, userId, token);
      setFolders((prev) => prev.map((f) => (f.id === folderId ? { ...f, name: updated.name } : f)));
    },
    [userId, token],
  );

  const removeFolder = useCallback(
    async (folderId: string) => {
      await deleteChatFolder(folderId, userId, token);
      setFolders((prev) => prev.filter((f) => f.id !== folderId));
      setExpanded((prev) => {
        const next = prev.filter((id) => id !== folderId);
        writeExpanded(next);
        return next;
      });
      // Its chats are now folder-less on the server: refresh the main list.
      await onRefresh();
    },
    [userId, token, onRefresh],
  );

  /** Optimistically moves a chat; reverts and rethrows on API errors. */
  const moveConversation = useCallback(
    async (conversationId: string, targetFolderId: string | null) => {
      const had = Object.prototype.hasOwnProperty.call(overrides, conversationId);
      const previous = overrides[conversationId];
      setOverrides((prev) => ({ ...prev, [conversationId]: targetFolderId }));
      try {
        await moveConversationToFolder(conversationId, targetFolderId, userId, token);
        // Keep cached folder rows consistent until the next fetch.
        setFolderItems((prev) => {
          const next: Record<string, ConversationSummary[]> = {};
          for (const [fid, rows] of Object.entries(prev)) {
            next[fid] = rows.map((c) => (c.id === conversationId ? { ...c, folder_id: targetFolderId } : c));
          }
          return next;
        });
        // After the refresh `items` carries the new folder_id: drop the override.
        await onRefresh();
        setOverrides((prev) => {
          const next = { ...prev };
          delete next[conversationId];
          return next;
        });
      } catch (err) {
        setOverrides((prev) => {
          const next = { ...prev };
          if (had) next[conversationId] = previous;
          else delete next[conversationId];
          return next;
        });
        throw err;
      }
    },
    [overrides, userId, token, onRefresh],
  );

  /** True once the chats of an expanded folder have been fetched at least once. */
  const folderLoaded = useCallback(
    (folderId: string) => expanded.includes(folderId) && folderId in folderItems,
    [expanded, folderItems],
  );

  return {
    folders,
    expanded,
    folderLoaded,
    allConversations,
    folderOf,
    toggleFolder,
    createFolder,
    renameFolder,
    removeFolder,
    moveConversation,
  };
}

export type ChatFoldersApi = ReturnType<typeof useChatFolders>;
