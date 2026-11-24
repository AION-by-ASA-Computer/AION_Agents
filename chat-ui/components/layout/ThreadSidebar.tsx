"use client";

import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  Check,
  ChevronRight,
  Clock,
  Edit3,
  Folder,
  FolderInput,
  FolderMinus,
  FolderOpen,
  FolderPlus,
  Folders,
  MessageSquare,
  MessageSquarePlus,
  MessageSquareQuote,
  MoreHorizontal,
  PanelLeftClose,
  PanelLeftOpen,
  Plug,
  Search,
  Star,
  Trash2,
  X,
} from "lucide-react";

import {
  ChatFolderError,
  fetchConversationDetails,
  listSideThreads,
  type ChatFolder,
  type ConversationSummary,
  type SideThreadSummary,
} from "@/lib/api/aion";
import { apiBase } from "@/lib/config";
import type { ShellSection } from "@/lib/shell/use-conversation-threads";
import { useChatFolders } from "@/lib/shell/use-chat-folders";
import { useCollapsedSections, type SidebarSectionId } from "@/lib/shell/use-collapsed-sections";
import { cn } from "@/lib/cn";
import { ChatBrand } from "../brand/ChatBrand";
import { TelegramIcon } from "../brand/TelegramIcon";
import { WhatsAppIcon } from "../brand/WhatsAppIcon";
import { SidebarProfileMenu } from "./SidebarProfileMenu";
import { useStoredToken } from "@/lib/auth/use-stored-auth";
import { useT } from "@/lib/i18n/use-t";
import { usePathname } from "next/navigation";
import { randomUUID } from "@/lib/uuid";

function isFavorite(c: ConversationSummary) {
  return c.metadata?.favorite === true || c.metadata?.favorite === "true";
}

/** DataTransfer type of a dragged sidebar chat (its id); folders and "Recent" accept only this. */
const DRAG_MIME = "application/x-aion-conversation";

function isChatDrag(e: React.DragEvent): boolean {
  return Array.from(e.dataTransfer.types).includes(DRAG_MIME);
}

/** Maps a folder API error code to a translated, user-facing message. */
function folderErrorMessage(err: unknown, t: ReturnType<typeof useT>): string {
  const code = err instanceof ChatFolderError ? err.code : "";
  if (code === "folder_name_taken") return t("sidebar.folders.name_taken");
  if (code === "folder_name_too_long") return t("sidebar.folders.name_too_long");
  if (code === "folder_limit_reached") return t("sidebar.folders.limit_reached");
  return t("sidebar.folders.error");
}

/**
 * Drop-target props for a folder header or the "Recent" section.
 *
 * `target` is the folder id, or null for "no folder". The highlight is driven
 * by `dropTarget` in ThreadSidebar; `dragleave` ignores moves between children.
 */
function dropZoneProps(
  target: string | null,
  setDropTarget: (v: string | null | undefined) => void,
  onDropChat: (convId: string, target: string | null) => void,
) {
  return {
    onDragOver: (e: React.DragEvent) => {
      if (!isChatDrag(e)) return;
      e.preventDefault(); // required to allow the drop
      e.dataTransfer.dropEffect = "move";
      setDropTarget(target);
    },
    onDragLeave: (e: React.DragEvent) => {
      if (e.currentTarget.contains(e.relatedTarget as Node | null)) return;
      setDropTarget(undefined);
    },
    onDrop: (e: React.DragEvent) => {
      if (!isChatDrag(e)) return;
      e.preventDefault();
      setDropTarget(undefined);
      const convId = e.dataTransfer.getData(DRAG_MIME);
      if (convId) onDropChat(convId, target);
    },
  };
}

export function ThreadSidebar({
  currentId,
  userId,
  items,
  onRefresh,
  onSelectConversation,
  onDeleteConversation,
  onRenameConversation,
  onToggleFavorite,
  activeSection = "chat",
  chatHomeHref = "/",
  isCollapsed = false,
  onToggleCollapse,
}: {
  currentId: string;
  userId: string;
  items: ConversationSummary[];
  onRefresh: () => void;
  onSelectConversation?: (id: string) => void;
  onDeleteConversation?: (id: string) => void;
  onRenameConversation?: (id: string, newTitle: string) => void;
  onToggleFavorite?: (id: string, isFav: boolean) => void;
  activeSection?: ShellSection;
  chatHomeHref?: string;
  isCollapsed?: boolean;
  onToggleCollapse?: () => void;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const token = useStoredToken();
  const isLoggedIn = Boolean(token);
  const t = useT();


  const [searchQuery, setSearchQuery] = useState("");
  /** Parent of the open conversation when it is a side thread (to expand it in the list). */
  const [parentLookup, setParentLookup] = useState<{ id: string; parentId: string | null } | null>(null);

  // Sidebar folders: list, lazily loaded contents and optimistic moves.
  const chatFolders = useChatFolders({ userId, token, items, onRefresh });
  const { folders, folderOf, allConversations } = chatFolders;
  /** Chat being dragged (to reveal the "Recent" drop zone while dragging out of a folder). */
  const [draggingId, setDraggingId] = useState<string | null>(null);
  /** Highlighted drop zone: folder id, null = "Recent", undefined = none. */
  const [dropTarget, setDropTarget] = useState<string | null | undefined>(undefined);
  const [folderError, setFolderError] = useState<string | null>(null);
  const [creatingFolder, setCreatingFolder] = useState(false);
  /** Favorites / Folders / Recent collapsed state, remembered per browser. */
  const sections = useCollapsedSections();

  /** Shared by drag & drop and the "Move to folder" menu; errors show under the Folders header. */
  const moveChat = useCallback(
    (convId: string, target: string | null) => {
      const conv = allConversations.find((c) => c.id === convId);
      if (conv && folderOf(conv) === target) return;
      setFolderError(null);
      chatFolders.moveConversation(convId, target).catch((err: unknown) => {
        console.error("Error moving conversation to folder:", err);
        setFolderError(folderErrorMessage(err, t));
      });
    },
    [allConversations, folderOf, chatFolders, t],
  );

  // Reset the drag state at window level. The row's own `dragend` is not
  // enough: a successful drop moves the chat optimistically to another
  // section, React unmounts the dragged row and its `dragend` never fires,
  // leaving `draggingId` set (the "drop here" hint stayed visible).
  useEffect(() => {
    if (!draggingId) return;
    const reset = () => {
      setDraggingId(null);
      setDropTarget(undefined);
    };
    window.addEventListener("drop", reset);
    window.addEventListener("dragend", reset);
    return () => {
      window.removeEventListener("drop", reset);
      window.removeEventListener("dragend", reset);
    };
  }, [draggingId]);

  const draggingConv = draggingId ? allConversations.find((c) => c.id === draggingId) : undefined;
  const draggingFromFolder = draggingConv ? folderOf(draggingConv) !== null : false;

  const hasThreads = allConversations.some((c) => (c.thread_count ?? 0) > 0);
  const currentIsTopLevel = allConversations.some((c) => c.id === currentId);

  useEffect(() => {
    if (!currentId || currentIsTopLevel || !hasThreads || !token) return;
    let cancelled = false;
    fetchConversationDetails(currentId, userId, token)
      .then((d) => {
        if (cancelled) return;
        setParentLookup({
          id: currentId,
          parentId: d?.parent_conversation_id ? String(d.parent_conversation_id) : null,
        });
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [currentId, currentIsTopLevel, hasThreads, userId, token]);
  const activeParentId = parentLookup?.id === currentId ? parentLookup.parentId : null;
  const [profileLabel, setProfileLabel] = useState(userId);
  const [profileSubtitle, setProfileSubtitle] = useState("");
  const [avatarUrl, setAvatarUrl] = useState("");
  const [profileColor, setProfileColor] = useState("violet");
  const [isAdmin, setIsAdmin] = useState(false);

  const loadProfile = useCallback(() => {
    if (!token) return;
    void fetch(`${apiBase()}/auth/me`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then((res) => (res.ok ? res.json() : null))
      .then(
        (
          data: {
            display_name?: string;
            identifier?: string;
            email?: string;
            roles?: string[];
            metadata?: { avatar_url?: string; profile_color?: string };
          } | null,
        ) => {
          if (!data) {
            setIsAdmin(false);
            return;
          }
          setIsAdmin(Array.isArray(data.roles) && data.roles.includes("admin"));
          setProfileLabel(data.display_name || data.identifier || userId);
          setProfileSubtitle(data.email || data.identifier || "");
          setAvatarUrl(data.metadata?.avatar_url || "");
          setProfileColor(data.metadata?.profile_color || "violet");
        },
      )
      .catch(() => {
        /* ignore */
      });
  }, [token, userId]);

  useEffect(() => {
    loadProfile();
  }, [loadProfile]);

  useEffect(() => {
    const onProfileUpdated = () => loadProfile();
    window.addEventListener("aion-profile-updated", onProfileUpdated);
    return () => window.removeEventListener("aion-profile-updated", onProfileUpdated);
  }, [loadProfile]);

  const query = searchQuery.trim().toLowerCase();
  const filteredItems = useMemo(() => {
    if (!query) return allConversations;
    return allConversations.filter(
      (c) =>
        (c.title || "").toLowerCase().includes(query) ||
        c.id.toLowerCase().includes(query),
    );
  }, [allConversations, query]);

  // Favorites win over folders: a favorite chat is listed only under Favorites
  // (its folder still counts it), the same way it is not repeated in Recent.
  const favoritedItems = useMemo(
    () => filteredItems.filter(isFavorite),
    [filteredItems],
  );
  const normalItems = useMemo(
    () => filteredItems.filter((c) => !isFavorite(c) && folderOf(c) === null),
    [filteredItems, folderOf],
  );
  const itemsByFolder = useMemo(() => {
    const map = new Map<string, ConversationSummary[]>();
    for (const c of filteredItems) {
      const fid = folderOf(c);
      if (!fid || isFavorite(c)) continue;
      map.set(fid, [...(map.get(fid) ?? []), c]);
    }
    return map;
  }, [filteredItems, folderOf]);
  // While searching, show only folders whose name or loaded chats match, opened.
  const visibleFolders = useMemo(
    () =>
      query
        ? folders.filter((f) => f.name.toLowerCase().includes(query) || (itemsByFolder.get(f.id)?.length ?? 0) > 0)
        : folders,
    [folders, query, itemsByFolder],
  );

  /** Folder-related props shared by every conversation row. */
  const rowFolderCtx = (c: ConversationSummary): RowFolderCtx => ({
    folders,
    folderId: folderOf(c),
    onMove: moveChat,
    onDragStart: setDraggingId,
    onDragEnd: () => {
      setDraggingId(null);
      setDropTarget(undefined);
    },
  });

  const renderRow = (c: ConversationSummary, favorite: boolean) => (
    <ConversationRow
      key={c.id}
      conv={c}
      currentId={currentId}
      isFavorite={favorite}
      onSelectConversation={onSelectConversation}
      onDeleteConversation={onDeleteConversation}
      onRenameConversation={onRenameConversation}
      onToggleFavorite={onToggleFavorite}
      onRefresh={onRefresh}
      userId={userId}
      autoExpand={c.id === activeParentId}
      folderCtx={rowFolderCtx(c)}
    />
  );

  const startNewChat = () => {
    const id = randomUUID();
    if (onSelectConversation) {
      onSelectConversation(id);
    } else {
      router.push(`/c/${id}`);
    }
  };

  const navItems: Array<{
    section: ShellSection;
    href: string;
    icon: typeof MessageSquare;
    label: string;
  }> = [
      { section: "chat", href: chatHomeHref, icon: MessageSquare, label: t("sidebar.chat") },
      { section: "integrations", href: "/integrations", icon: Plug, label: t("sidebar.integrations") },
      { section: "schedules", href: "/schedules", icon: Clock, label: t("sidebar.schedules") },
    ];

  // Sections are force-opened while searching (results must be visible) and
  // Folders while dragging a chat (its folders are the drop targets).
  const sectionOpen = (id: SidebarSectionId) =>
    Boolean(query) || (id === "folders" && Boolean(draggingId)) || !sections.isCollapsed(id);

  const conversationList = (
    <div className="space-y-1 pt-1">
      {favoritedItems.length > 0 ? (
        <SidebarSection
          id="favorites"
          icon={<Star className="h-3 w-3 fill-current" aria-hidden />}
          label={t("sidebar.favorites")}
          count={favoritedItems.length}
          open={sectionOpen("favorites")}
          onToggle={sections.toggle}
        >
          {favoritedItems.map((c) => renderRow(c, true))}
        </SidebarSection>
      ) : null}

      {/* Folders: "new folder" button in the header, then one collapsible row per folder. */}
      {!query || visibleFolders.length > 0 ? (
        <SidebarSection
          id="folders"
          icon={<Folders className="h-3 w-3" aria-hidden />}
          label={t("sidebar.folders.title")}
          count={folders.length}
          open={sectionOpen("folders")}
          onToggle={sections.toggle}
          action={
            <button
              type="button"
              onClick={() => {
                setFolderError(null);
                setCreatingFolder(true);
                // Creating inside a collapsed section would be invisible.
                if (sections.isCollapsed("folders")) sections.toggle("folders");
              }}
              className="rounded-md p-1 text-muted-foreground transition-colors hover:bg-muted/80 hover:text-foreground"
              title={t("sidebar.folders.new")}
              aria-label={t("sidebar.folders.new")}
            >
              <FolderPlus className="h-3.5 w-3.5" aria-hidden />
            </button>
          }
        >
          {creatingFolder ? (
            <FolderNameInput
              placeholder={t("sidebar.folders.name_placeholder")}
              onSubmit={async (name) => {
                const folder = await chatFolders.createFolder(name);
                chatFolders.toggleFolder(folder.id, true);
                setCreatingFolder(false);
              }}
              onCancel={() => setCreatingFolder(false)}
            />
          ) : null}
          {folderError ? (
            <p role="alert" className="px-2.5 pb-1 text-[11px] text-rose-500">
              {folderError}
            </p>
          ) : null}
          {visibleFolders.length === 0 && !creatingFolder ? (
            <p className="px-2.5 py-1 text-[11px] leading-snug text-muted-foreground/80">
              {t("sidebar.folders.no_folders")}
            </p>
          ) : null}
          {visibleFolders.map((f) => (
            <FolderRow
              key={f.id}
              folder={f}
              open={Boolean(query) || chatFolders.expanded.includes(f.id)}
              loaded={chatFolders.folderLoaded(f.id) || f.conversation_count === 0}
              isDropTarget={dropTarget === f.id}
              onToggle={() => chatFolders.toggleFolder(f.id)}
              onRename={(name) => chatFolders.renameFolder(f.id, name)}
              onDelete={() =>
                chatFolders.removeFolder(f.id).catch((err: unknown) => {
                  console.error("Error deleting folder:", err);
                  setFolderError(folderErrorMessage(err, t));
                })
              }
              dropProps={dropZoneProps(f.id, setDropTarget, moveChat)}
            >
              {(itemsByFolder.get(f.id) ?? []).map((c) => renderRow(c, false))}
            </FolderRow>
          ))}
        </SidebarSection>
      ) : null}

      {/* "Recent" = chats without a folder; the whole section (header included,
          also when collapsed) is the drop zone to take a chat out of its folder. */}
      {normalItems.length > 0 || draggingFromFolder ? (
        <SidebarSection
          id="recent"
          icon={<Clock className="h-3 w-3" aria-hidden />}
          label={t("sidebar.recent")}
          open={sectionOpen("recent")}
          onToggle={sections.toggle}
          isDropTarget={dropTarget === null}
          dropProps={dropZoneProps(null, setDropTarget, moveChat)}
        >
          {draggingFromFolder ? (
            <p className="mx-1 mb-1 rounded-lg border border-dashed border-primary/40 px-2 py-1.5 text-center text-[11px] text-muted-foreground">
              {t("sidebar.folders.drop_here")}
            </p>
          ) : null}
          {normalItems.map((c) => renderRow(c, false))}
        </SidebarSection>
      ) : null}

      {filteredItems.length === 0 && (query || folders.length === 0) ? (
        <p className="mt-4 px-2 text-xs text-muted-foreground">{t("sidebar.no_conversations")}</p>
      ) : null}
    </div>
  );

  const profileFooter = (
    <div className="border-t border-sidebar-border/60 p-3">
      <SidebarProfileMenu
        profileLabel={profileLabel}
        profileSubtitle={profileSubtitle}
        avatarUrl={avatarUrl}
        profileColor={profileColor}
        isLoggedIn={isLoggedIn}
        isAdmin={isAdmin}
        variant="expanded"
      />
    </div>
  );

  if (isCollapsed) {
    return (
      <aside className="flex h-full w-full flex-col items-center py-3">
        <button
          type="button"
          onClick={onToggleCollapse}
          className="mb-3 inline-flex h-9 w-9 items-center justify-center rounded-xl text-muted-foreground transition hover:bg-muted/60 hover:text-foreground"
          aria-label={t("sidebar.expand")}
          title={t("sidebar.expand")}
        >
          <PanelLeftOpen className="h-4 w-4" aria-hidden />
        </button>

        <button
          type="button"
          onClick={startNewChat}
          className="mb-3 inline-flex h-10 w-10 items-center justify-center rounded-2xl bg-gradient-to-r from-rose-500 to-brand-end hover:from-rose-600 hover:to-brand-end-hover text-white shadow-lg shadow-rose-500/25 border border-rose-400/30 backdrop-blur-xl transition-all duration-200 hover:scale-105 active:scale-95 cursor-pointer"
          aria-label={t("sidebar.new_conversation")}
          title={t("sidebar.new_conversation")}
        >
          <MessageSquarePlus className="h-4 w-4" aria-hidden />
        </button>

        <nav className="flex flex-col items-center gap-1.5">
          {navItems.map((item) => {
            const Icon = item.icon;
            const active = activeSection === item.section;
            return (
              <Link
                key={item.section}
                href={item.href}
                title={item.label}
                aria-label={item.label}
                className={cn(
                  "inline-flex h-9 w-9 items-center justify-center rounded-xl transition-all duration-200",
                  active
                    ? "bg-primary/10 text-primary dark:bg-white/10 dark:text-foreground border border-primary/15 dark:border-white/10"
                    : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
                )}
              >
                <Icon className="h-4 w-4" aria-hidden />
              </Link>
            );
          })}
        </nav>

        <div className="mt-auto flex flex-col items-center gap-2 border-t border-sidebar-border/45 pt-3">
          <SidebarProfileMenu
            profileLabel={profileLabel}
            profileSubtitle={profileSubtitle}
            avatarUrl={avatarUrl}
            profileColor={profileColor}
            isLoggedIn={isLoggedIn}
            isAdmin={isAdmin}
            variant="collapsed"
          />
        </div>
      </aside>
    );
  }

  return (
    <aside className="flex h-full min-h-0 w-full flex-col overflow-hidden">
      <div className="shrink-0">
        <div className="flex items-center justify-between px-4 pb-3 pt-3.5">
          <div className="min-w-0 flex-1">
            <ChatBrand className="max-w-[120px]" placement="sidebar" />
          </div>
          <button
            type="button"
            onClick={onToggleCollapse}
            className="rounded-xl p-1.5 text-muted-foreground transition hover:bg-muted/60 hover:text-foreground"
            aria-label={t("sidebar.collapse")}
            title={t("sidebar.collapse")}
          >
            <PanelLeftClose className="h-4 w-4" aria-hidden />
          </button>
        </div>

        <div className="px-3 pt-1">
          <button
            type="button"
            onClick={startNewChat}
            className="group relative flex w-full items-center justify-center gap-2 overflow-hidden rounded-2xl bg-gradient-to-r from-rose-500 to-brand-end hover:from-rose-600 hover:to-brand-end-hover py-2.5 px-3.5 text-sm font-bold text-white shadow-lg shadow-rose-500/25 border border-rose-400/30 backdrop-blur-xl transition-all duration-200 hover:scale-[1.01] active:scale-[0.98] cursor-pointer"
          >
            <div
              className="pointer-events-none absolute inset-0 -translate-x-full bg-gradient-to-r from-transparent via-white/25 to-transparent transition-transform duration-700 ease-out group-hover:translate-x-full"
              aria-hidden
            />
            <MessageSquarePlus className="h-4 w-4 transition-transform duration-300 group-hover:scale-110 shrink-0" aria-hidden />
            <span>{t("sidebar.new_conversation")}</span>
          </button>
        </div>

        <nav className="mt-3.5 flex flex-col gap-1 px-2.5">
          {navItems.map((item) => {
            const Icon = item.icon;
            const active = activeSection === item.section;
            const className = cn(
              "group flex items-center gap-2.5 rounded-xl px-3 py-2 text-sm font-medium transition-all duration-300 ease-out",
              active
                ? "bg-primary/10 text-primary dark:bg-white/10 dark:text-foreground border border-primary/15 dark:border-white/10 shadow-2xs font-semibold"
                : "text-muted-foreground hover:translate-x-1 hover:bg-muted/50 hover:text-foreground dark:hover:bg-white/[0.06]",
            );
            return (
              <Link
                key={item.section}
                href={item.href}
                className={className}
              >
                <Icon className="h-4 w-4 shrink-0 transition-transform duration-300 group-hover:scale-110" aria-hidden />
                <span className="truncate">{item.label}</span>
              </Link>
            );
          })}
        </nav>
      </div>

      <div className="mt-3 shrink-0 px-3 pb-2">
        <div className="flex items-center gap-2 rounded-xl border border-black/[0.08] dark:border-white/[0.08] bg-background/50 dark:bg-card/40 px-2.5 py-1.5 backdrop-blur-md transition-all duration-200 focus-within:border-primary/50 focus-within:shadow-[0_0_12px_rgba(var(--primary-rgb),0.12)]">
          <Search className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder={t("sidebar.search_placeholder")}
            className="min-w-0 flex-1 bg-transparent text-xs text-foreground placeholder:text-muted-foreground/70 focus:outline-none"
            aria-label={t("sidebar.search_placeholder")}
          />
          {searchQuery ? (
            <button
              type="button"
              onClick={() => setSearchQuery("")}
              className="rounded p-0.5 text-muted-foreground transition hover:bg-muted/60 hover:text-foreground"
              aria-label={t("btn.cancel")}
            >
              <X className="h-3 w-3" aria-hidden />
            </button>
          ) : null}
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-2 [scrollbar-width:thin]">{conversationList}</div>

      <div className="shrink-0">{profileFooter}</div>
    </aside>
  );
}

/**
 * Collapsible sidebar section (Favorites, Folders, Recent).
 *
 * The header is a toggle button with chevron, icon, label and optional count;
 * `action` renders extra controls on the right (e.g. "new folder"). When
 * `dropProps` is given the whole section, header included, accepts dragged
 * chats, so it still works as a drop zone while collapsed.
 */
function SidebarSection({
  id,
  icon,
  label,
  count,
  open,
  onToggle,
  action,
  isDropTarget = false,
  dropProps,
  children,
}: {
  id: SidebarSectionId;
  icon: React.ReactNode;
  label: string;
  count?: number;
  open: boolean;
  onToggle: (id: SidebarSectionId) => void;
  action?: React.ReactNode;
  isDropTarget?: boolean;
  dropProps?: ReturnType<typeof dropZoneProps>;
  children: React.ReactNode;
}) {
  const t = useT();
  const bodyId = `sidebar-section-${id}`;
  return (
    <section
      {...dropProps}
      className={cn(
        "rounded-xl transition-colors duration-200",
        isDropTarget && "bg-primary/5 ring-1 ring-primary/40",
      )}
    >
      {/* Divider-style header (label + hairline + count + chevron on the right):
          deliberately unlike a folder row, so "Folders" is not mistaken for a folder. */}
      <div className="group/section flex items-center gap-1 pr-1 pt-2 select-none">
        <button
          type="button"
          onClick={() => onToggle(id)}
          aria-expanded={open}
          aria-controls={bodyId}
          title={open ? t("sidebar.section_collapse") : t("sidebar.section_expand")}
          className="flex min-w-0 flex-1 items-center gap-2 rounded-lg px-2 py-1 text-[10px] font-bold uppercase tracking-[0.16em] text-muted-foreground/55 transition-colors hover:text-foreground/80"
        >
          <span className="shrink-0 opacity-80">{icon}</span>
          <span className="shrink-0">{label}</span>
          <span
            className="h-px min-w-4 flex-1 bg-gradient-to-r from-sidebar-border via-sidebar-border/60 to-transparent transition-colors group-hover/section:from-muted-foreground/40"
            aria-hidden
          />
          {count !== undefined && count > 0 ? (
            <span className="shrink-0 text-[10px] font-semibold tracking-normal tabular-nums text-muted-foreground/70">
              {count}
            </span>
          ) : null}
          <ChevronRight
            className={cn(
              "h-3 w-3 shrink-0 opacity-60 transition-transform duration-200 group-hover/section:opacity-100",
              open && "rotate-90",
            )}
            aria-hidden
          />
        </button>
        {action}
      </div>
      {open ? (
        <div id={bodyId} className="space-y-0.5 pb-1 animate-in fade-in-0 duration-150">
          {children}
        </div>
      ) : null}
    </section>
  );
}

/** Folder context of a conversation row: drag source + "Move to folder" menu. */
type RowFolderCtx = {
  folders: ChatFolder[];
  /** Current (optimistic) folder of the chat, null = none. */
  folderId: string | null;
  onMove: (convId: string, folderId: string | null) => void;
  onDragStart: (convId: string) => void;
  onDragEnd: () => void;
};

/** Width of the folder submenu (Tailwind `w-52`), used to flip it to the left. */
const MOVE_SUBMENU_WIDTH = 208;
/** Minimum distance kept between the submenu and the viewport edges. */
const MOVE_SUBMENU_MARGIN = 8;

/**
 * Folder picker shown beside the "Move to folder" item of a chat's "⋯" menu.
 *
 * Rendered in a portal with fixed positioning: the conversation list scrolls
 * (`overflow-y-auto`) and the menu is animated with a transform, so a submenu
 * positioned inside them would be clipped. It opens to the right of `anchor`
 * (the item's rect), flips to the left when there is no room and is kept inside
 * the viewport vertically. Closes itself on resize/scroll (the anchor rect
 * would go stale). `onPick(null)` removes the chat from its folder.
 */
function MoveToFolderSubmenu({
  anchor,
  folders,
  currentFolderId,
  onPick,
  onDismiss,
}: {
  anchor: DOMRect;
  folders: ChatFolder[];
  currentFolderId: string | null;
  onPick: (folderId: string | null) => void;
  onDismiss: () => void;
}) {
  const t = useT();
  const ref = useRef<HTMLDivElement>(null);
  const [top, setTop] = useState(anchor.top - 6);

  // Horizontal side: right of the item, or left of it when it would overflow.
  const fitsRight = anchor.right + 4 + MOVE_SUBMENU_WIDTH + MOVE_SUBMENU_MARGIN <= window.innerWidth;
  const left = fitsRight
    ? anchor.right + 4
    : Math.max(MOVE_SUBMENU_MARGIN, anchor.left - 4 - MOVE_SUBMENU_WIDTH);

  // Vertical clamp needs the rendered height, hence a layout effect (no flicker).
  useLayoutEffect(() => {
    const h = ref.current?.offsetHeight ?? 0;
    const maxTop = window.innerHeight - h - MOVE_SUBMENU_MARGIN;
    setTop(Math.max(MOVE_SUBMENU_MARGIN, Math.min(anchor.top - 6, maxTop)));
  }, [anchor, folders.length, currentFolderId]);

  useEffect(() => {
    // Capture: the sidebar list scrolls, not the window. Scrolling the
    // submenu's own folder list must not close it.
    const onScroll = (e: Event) => {
      if (e.target instanceof Node && ref.current?.contains(e.target)) return;
      onDismiss();
    };
    window.addEventListener("resize", onDismiss);
    window.addEventListener("scroll", onScroll, true);
    return () => {
      window.removeEventListener("resize", onDismiss);
      window.removeEventListener("scroll", onScroll, true);
    };
  }, [onDismiss]);

  const itemClass =
    "flex w-full items-center gap-2 rounded-xl px-2.5 py-2 text-left text-xs font-medium transition-colors";

  return createPortal(
    <div
      ref={ref}
      role="menu"
      style={{ top, left }}
      // Above the menu's click-catching backdrop (z-40) and the menu (z-50).
      className="fixed z-[60] w-52 rounded-2xl border border-border bg-popover p-1.5 text-popover-foreground shadow-2xl ring-1 ring-border/50 animate-in fade-in-0 zoom-in-95 duration-150"
      onClick={(e) => e.stopPropagation()}
    >
      <div className="max-h-56 overflow-y-auto [scrollbar-width:thin]">
        {folders.length === 0 ? (
          <p className="px-2.5 py-2 text-[11px] text-muted-foreground">{t("sidebar.folders.no_folders")}</p>
        ) : (
          folders.map((f) => {
            const current = f.id === currentFolderId;
            return (
              <button
                key={f.id}
                type="button"
                role="menuitem"
                disabled={current}
                onClick={() => onPick(f.id)}
                className={cn(
                  itemClass,
                  current
                    ? "cursor-default text-primary"
                    : "text-muted-foreground hover:bg-primary/10 hover:text-foreground dark:hover:bg-white/10",
                )}
              >
                <Folder size={13} className="shrink-0 fill-amber-400/80 text-amber-500" />
                <span className="min-w-0 flex-1 truncate">{f.name}</span>
                {current ? <Check size={13} className="shrink-0" /> : null}
              </button>
            );
          })
        )}
      </div>
      {currentFolderId ? (
        <button
          type="button"
          role="menuitem"
          onClick={() => onPick(null)}
          className={cn(
            itemClass,
            "mt-1 border-t border-border/60 text-muted-foreground hover:bg-primary/10 hover:text-foreground dark:hover:bg-white/10",
          )}
        >
          <FolderMinus size={13} className="shrink-0 opacity-80" />
          <span>{t("sidebar.folders.remove_from_folder")}</span>
        </button>
      ) : null}
    </div>,
    document.body,
  );
}

type ConversationRowProps = {
  conv: ConversationSummary;
  currentId: string;
  isFavorite: boolean;
  onSelectConversation?: (id: string) => void;
  onDeleteConversation?: (id: string) => void;
  onRenameConversation?: (id: string, newTitle: string) => void;
  onToggleFavorite?: (id: string, isFav: boolean) => void;
  onRefresh: () => void;
  folderCtx?: RowFolderCtx;
};

/**
 * Inline text field used to create or rename a folder.
 *
 * `onSubmit` may reject (e.g. duplicate name): the error is shown below the
 * field and the field stays open so the user can fix the name.
 */
function FolderNameInput({
  initial = "",
  placeholder,
  onSubmit,
  onCancel,
}: {
  initial?: string;
  placeholder?: string;
  onSubmit: (name: string) => Promise<void>;
  onCancel: () => void;
}) {
  const t = useT();
  const [value, setValue] = useState(initial);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    const name = value.trim();
    if (!name) {
      onCancel();
      return;
    }
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await onSubmit(name);
    } catch (err) {
      setError(folderErrorMessage(err, t));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mb-0.5">
      <div className="flex items-center gap-1.5 rounded-xl border border-primary/30 bg-muted/40 px-2.5 py-1.5 shadow-[0_0_12px_rgba(var(--primary-rgb),0.08)]">
        <Folder className="h-4 w-4 shrink-0 fill-amber-400/60 text-amber-500" aria-hidden />
        <input
          type="text"
          value={value}
          maxLength={128}
          placeholder={placeholder}
          aria-label={placeholder}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              void submit();
            } else if (e.key === "Escape") {
              e.preventDefault();
              onCancel();
            }
          }}
          className="min-w-0 flex-1 bg-transparent text-[0.929em] text-foreground placeholder:text-muted-foreground/70 focus:outline-none"
          autoFocus
          disabled={busy}
        />
        <button
          type="button"
          onClick={() => void submit()}
          className="rounded p-0.5 text-emerald-500 hover:text-emerald-400"
          title={t("btn.save")}
          aria-label={t("btn.save")}
        >
          <Check size={14} />
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="rounded p-0.5 text-muted-foreground hover:text-foreground"
          title={t("btn.cancel")}
          aria-label={t("btn.cancel")}
        >
          <X size={14} />
        </button>
      </div>
      {error ? (
        <p role="alert" className="px-3 pt-1 text-[11px] text-rose-500">
          {error}
        </p>
      ) : null}
    </div>
  );
}

/**
 * One sidebar folder: a collapsible header (also a drop target for dragged
 * chats) with count badge and rename/delete menu, and its chats below.
 */
function FolderRow({
  folder,
  open,
  loaded,
  isDropTarget,
  onToggle,
  onRename,
  onDelete,
  dropProps,
  children,
}: {
  folder: ChatFolder;
  open: boolean;
  /** False while an expanded folder's chats are being fetched for the first time. */
  loaded: boolean;
  isDropTarget: boolean;
  onToggle: () => void;
  onRename: (name: string) => Promise<void>;
  onDelete: () => void;
  dropProps: ReturnType<typeof dropZoneProps>;
  children: React.ReactNode;
}) {
  const t = useT();
  const [menuOpen, setMenuOpen] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const hasChildren = React.Children.count(children) > 0;
  const Icon = open ? FolderOpen : Folder;

  if (renaming) {
    return (
      <FolderNameInput
        initial={folder.name}
        placeholder={t("sidebar.folders.name_placeholder")}
        onSubmit={async (name) => {
          if (name !== folder.name) await onRename(name);
          setRenaming(false);
        }}
        onCancel={() => setRenaming(false)}
      />
    );
  }

  if (confirmDelete) {
    return (
      <div className="flex items-center justify-between gap-1 rounded-xl border border-rose-500/20 bg-rose-500/10 px-3 py-2 animate-in fade-in zoom-in-95 duration-150">
        <span className="min-w-0 text-xs font-medium text-rose-400/90">{t("sidebar.folders.delete_confirm")}</span>
        <div className="flex shrink-0 items-center gap-1">
          <button
            type="button"
            onClick={() => {
              setConfirmDelete(false);
              onDelete();
            }}
            className="rounded-lg bg-rose-500 px-2 py-0.5 text-[0.714em] font-semibold uppercase tracking-wider text-white shadow-sm transition-all duration-150 hover:bg-rose-600"
          >
            {t("btn.yes")}
          </button>
          <button
            type="button"
            onClick={() => setConfirmDelete(false)}
            className="rounded-lg bg-muted px-1.5 py-0.5 text-[0.714em] font-semibold uppercase tracking-wider text-foreground transition-all duration-150 hover:bg-muted/80"
          >
            {t("btn.no")}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div>
      {/* Windows-explorer look: yellow folder icon, amber gradient when open,
          on hover and (stronger) when a dragged chat hovers it. */}
      <div
        {...dropProps}
        className={cn(
          "group relative flex items-center gap-1 rounded-xl border px-1.5 py-1 text-xs sm:text-[13px] transition-all duration-200 ease-out",
          isDropTarget
            ? "border-amber-400/60 bg-gradient-to-r from-amber-400/30 via-amber-400/10 to-transparent text-foreground shadow-[0_0_14px_rgba(251,191,36,0.18)]"
            : menuOpen
              ? "border-amber-400/25 bg-gradient-to-r from-amber-400/20 to-transparent text-foreground"
              : open
                ? "border-transparent bg-gradient-to-r from-amber-400/12 to-transparent text-foreground hover:from-amber-400/20"
                : "border-transparent text-foreground/85 hover:bg-gradient-to-r hover:from-amber-400/15 hover:to-transparent hover:text-foreground",
        )}
      >
        <button
          type="button"
          onClick={onToggle}
          aria-expanded={open}
          className="flex min-w-0 flex-1 items-center gap-1.5 rounded-lg px-1 py-0.5 text-left"
          title={folder.name}
        >
          <ChevronRight
            className={cn("h-3 w-3 shrink-0 text-muted-foreground/70 transition-transform duration-200", open && "rotate-90")}
            aria-hidden
          />
          <Icon
            className={cn(
              "h-4 w-4 shrink-0 fill-amber-400/85 text-amber-500 drop-shadow-[0_1px_1px_rgba(0,0,0,0.25)] transition-transform duration-200",
              isDropTarget && "scale-110",
            )}
            aria-hidden
          />
          <span className="truncate font-medium">{folder.name}</span>
        </button>
        <span
          className="shrink-0 rounded-full bg-amber-400/10 px-1.5 text-[10.5px] font-semibold tabular-nums text-amber-600 dark:text-amber-300/90"
          title={t("sidebar.folders.count", { count: folder.conversation_count })}
        >
          {folder.conversation_count}
        </span>
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            setMenuOpen((v) => !v);
          }}
          className={cn(
            "rounded-lg p-1 transition-all",
            menuOpen
              ? "bg-muted/80 text-foreground opacity-100"
              : "text-muted-foreground opacity-0 hover:bg-muted/80 hover:text-foreground focus-visible:opacity-100 group-hover:opacity-100",
          )}
          title={t("sidebar.folders.options")}
          aria-label={t("sidebar.folders.options")}
        >
          <MoreHorizontal className="h-3.5 w-3.5" />
        </button>

        {menuOpen ? (
          <>
            <div
              className="fixed inset-0 z-40"
              aria-hidden
              onClick={(e) => {
                e.stopPropagation();
                setMenuOpen(false);
              }}
            />
            <div className="absolute right-0 top-full z-50 mt-1 w-44 rounded-2xl border border-border bg-popover p-1.5 text-popover-foreground shadow-2xl animate-in fade-in-0 zoom-in-95 duration-150">
              <button
                type="button"
                onClick={() => {
                  setMenuOpen(false);
                  setRenaming(true);
                }}
                className="flex w-full items-center gap-2 rounded-xl px-2.5 py-2 text-left text-xs font-medium text-muted-foreground transition-colors hover:bg-primary/10 hover:text-foreground dark:hover:bg-white/10"
              >
                <Edit3 size={13} className="shrink-0 opacity-80" />
                <span>{t("sidebar.rename")}</span>
              </button>
              <button
                type="button"
                onClick={() => {
                  setMenuOpen(false);
                  setConfirmDelete(true);
                }}
                className="flex w-full items-center gap-2 rounded-xl px-2.5 py-2 text-left text-xs font-medium text-rose-500 transition-colors hover:bg-rose-500/10 hover:text-rose-400"
              >
                <Trash2 size={13} className="shrink-0" />
                <span>{t("sidebar.folders.delete")}</span>
              </button>
            </div>
          </>
        ) : null}
      </div>

      {open ? (
        <div className="mb-1 ml-[18px] mt-0.5 space-y-0.5 border-l border-amber-400/25 pl-1.5">
          {hasChildren ? (
            children
          ) : (
            <p className="px-2 py-1 text-[11px] text-muted-foreground">
              {loaded ? t("sidebar.folders.empty") : "…"}
            </p>
          )}
        </div>
      ) : null}
    </div>
  );
}

/** A conversation row plus its side threads, lazily loaded and nested under a hairline guide. */
function ConversationRow({
  userId,
  autoExpand = false,
  ...props
}: ConversationRowProps & { userId: string; autoExpand?: boolean }) {
  const t = useT();
  const token = useStoredToken();
  const { conv, currentId, onSelectConversation } = props;
  const count = conv.thread_count ?? 0;
  // null = follow `autoExpand` (parent of the open thread); a click pins the user's choice.
  const [expandedChoice, setExpanded] = useState<boolean | null>(null);
  const expanded = expandedChoice ?? autoExpand;
  const [children, setChildren] = useState<SideThreadSummary[] | null>(null);

  // (Re)load when opened and whenever the parent's thread count or activity changes.
  useEffect(() => {
    if (!expanded || count === 0) return;
    const ac = new AbortController();
    void listSideThreads(conv.id, userId, token, ac.signal).then((rows) => {
      if (!ac.signal.aborted) setChildren(rows);
    });
    return () => ac.abort();
  }, [expanded, count, conv.id, conv.updated_at, userId, token]);

  const toggle =
    count > 0 ? (
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          setExpanded(!expanded);
        }}
        aria-expanded={expanded}
        aria-label={expanded ? t("sideThread.sidebar.collapse") : t("sideThread.sidebar.expand")}
        title={t("sideThread.sidebar.count", { count })}
        className="flex shrink-0 items-center gap-0.5 rounded-md px-1 py-0.5 text-[11px] font-medium tabular-nums text-muted-foreground transition-colors hover:bg-muted/80 hover:text-foreground"
      >
        {count}
        <ChevronRight
          className={cn("h-3 w-3 transition-transform duration-200", expanded && "rotate-90")}
          aria-hidden
        />
      </button>
    ) : null;

  return (
    <div>
      <ConversationRowInner {...props} threadToggle={toggle} />
      {expanded && count > 0 ? (
        <ul className="mb-1 ml-4 mt-0.5 space-y-0.5 border-l border-sidebar-border pl-2">
          {children === null ? (
            <li className="px-2 py-1 text-[11px] text-muted-foreground">…</li>
          ) : (
            children.map((th) => {
              const active = th.id === currentId;
              const label = th.title?.trim() || th.anchor.quote;
              return (
                <li key={th.id}>
                  <Link
                    href={`/c/${th.id}`}
                    onClick={(e) => {
                      if (onSelectConversation) {
                        e.preventDefault();
                        onSelectConversation(th.id);
                      }
                    }}
                    title={label}
                    aria-current={active ? "page" : undefined}
                    className={cn(
                      "flex items-center gap-1.5 rounded-lg px-2 py-1 text-xs transition-colors",
                      active
                        ? "bg-primary/10 font-semibold text-primary dark:bg-primary/15"
                        : "text-foreground/70 hover:bg-muted/60 hover:text-foreground dark:hover:bg-white/[0.06]",
                    )}
                  >
                    <MessageSquareQuote className="h-3 w-3 shrink-0 opacity-70" aria-hidden />
                    <span className="truncate">{label}</span>
                  </Link>
                </li>
              );
            })
          )}
        </ul>
      ) : null}
    </div>
  );
}

function ConversationRowInner({
  conv,
  currentId,
  isFavorite: favorite,
  onSelectConversation,
  onDeleteConversation,
  onRenameConversation,
  onToggleFavorite,
  onRefresh,
  threadToggle,
  folderCtx,
}: ConversationRowProps & { threadToggle?: React.ReactNode }) {
  const t = useT();
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState("");
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  /**
   * Bounding rect of the "Move to folder" item while its folder submenu is open
   * (null = closed). Opened on hover like the other nested menus (composer "+"
   * menu), or on click/tap as a touch and keyboard fallback.
   */
  const [moveAnchor, setMoveAnchor] = useState<DOMRect | null>(null);
  const closeMenu = () => {
    setMenuOpen(false);
    setMoveAnchor(null);
  };
  /** Hovering any other menu item closes the folder submenu. */
  const closeMoveSub = () => setMoveAnchor(null);

  const isActive = conv.id === currentId;
  const displayTitle = conv.title?.trim() || conv.id.slice(0, 16);
  const isEditing = editingId === conv.id;
  const isConfirmingDelete = confirmDeleteId === conv.id;

  if (isEditing) {
    return (
      <div className="flex items-center gap-1.5 rounded-xl border border-primary/30 bg-muted/40 px-2.5 py-1.5 shadow-[0_0_12px_rgba(var(--primary-rgb),0.08)]">
        <input
          type="text"
          value={editTitle}
          onChange={(e) => setEditTitle(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              if (editTitle.trim()) {
                onRenameConversation?.(conv.id, editTitle.trim());
                void onRefresh();
              }
              setEditingId(null);
            } else if (e.key === "Escape") {
              e.preventDefault();
              setEditingId(null);
            }
          }}
          className="min-w-0 flex-1 bg-transparent text-[0.929em] text-foreground focus:outline-none"
          autoFocus
        />
        <button
          type="button"
          onClick={() => {
            if (editTitle.trim()) {
              onRenameConversation?.(conv.id, editTitle.trim());
              void onRefresh();
            }
            setEditingId(null);
          }}
          className="rounded p-0.5 text-emerald-500 hover:text-emerald-400"
          title={t("btn.save")}
        >
          <Check size={14} />
        </button>
        <button
          type="button"
          onClick={() => setEditingId(null)}
          className="rounded p-0.5 text-muted-foreground hover:text-foreground"
          title={t("btn.cancel")}
        >
          <X size={14} />
        </button>
      </div>
    );
  }

  if (isConfirmingDelete) {
    return (
      <div className="flex items-center justify-between rounded-xl border border-rose-500/20 bg-rose-500/10 px-3 py-2 text-rose-200 animate-in fade-in zoom-in-95 duration-150">
        <span className="truncate pr-1 text-xs font-medium text-rose-400/90">
          {t("sidebar.delete_confirm")}
        </span>
        <div className="flex shrink-0 items-center gap-1">
          <button
            type="button"
            onClick={() => {
              onDeleteConversation?.(conv.id);
              setConfirmDeleteId(null);
              void onRefresh();
            }}
            className="rounded-lg bg-rose-500 px-2 py-0.5 text-[0.714em] font-semibold uppercase tracking-wider text-white shadow-sm transition-all duration-150 hover:bg-rose-600"
          >
            {t("btn.yes")}
          </button>
          <button
            type="button"
            onClick={() => setConfirmDeleteId(null)}
            className="rounded-lg bg-muted px-1.5 py-0.5 text-[0.714em] font-semibold uppercase tracking-wider text-foreground transition-all duration-150 hover:bg-muted/80"
          >
            {t("btn.no")}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div
      // Drag source for moving the chat into a folder (or back to Recent).
      draggable={Boolean(folderCtx) && !menuOpen}
      onDragStart={(e) => {
        if (!folderCtx) return;
        e.dataTransfer.setData(DRAG_MIME, conv.id);
        e.dataTransfer.effectAllowed = "move";
        folderCtx.onDragStart(conv.id);
      }}
      onDragEnd={() => folderCtx?.onDragEnd()}
      className={cn(
        "group relative flex items-center gap-2 rounded-xl px-2.5 py-1.5 text-xs sm:text-[13px] transition-all duration-200 ease-out",
        isActive || menuOpen
          ? "bg-primary/10 text-primary dark:bg-primary/15 dark:text-primary border border-primary/25 shadow-2xs font-semibold"
          : "text-foreground/80 hover:text-foreground hover:bg-muted/60 dark:hover:bg-white/[0.06]",
      )}
    >
      <Link
        href={`/c/${conv.id}`}
        // The row is the drag source: without this the browser would drag the URL.
        draggable={false}
        onClick={(e) => {
          if (onSelectConversation) {
            e.preventDefault();
            onSelectConversation(conv.id);
          }
        }}
        className="flex min-w-0 flex-1 items-center gap-2 text-left"
        title={displayTitle}
      >
        {favorite ? (
          <Star className="h-3.5 w-3.5 shrink-0 fill-amber-400 text-amber-400" aria-hidden />
        ) : null}
        {conv.metadata?.source === "whatsapp" ? (
          <span className="shrink-0" title={t("sidebar.from_whatsapp")}>
            <WhatsAppIcon label={t("sidebar.from_whatsapp")} />
          </span>
        ) : null}
        {conv.metadata?.source === "telegram" ? (
          <span className="shrink-0" title={t("sidebar.from_telegram")}>
            <TelegramIcon label={t("sidebar.from_telegram")} />
          </span>
        ) : null}
        <span className="truncate transition-transform duration-200 group-hover:translate-x-0.5">{displayTitle}</span>
      </Link>
      {threadToggle}

      <div className={cn("flex items-center gap-0.5 transition-opacity duration-200", menuOpen ? "opacity-100" : "opacity-0 group-hover:opacity-100")}>
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onToggleFavorite?.(conv.id, favorite);
            void onRefresh();
          }}
          className="rounded-lg p-1 hover:bg-muted/80 transition-colors"
          title={favorite ? t("sidebar.remove_favorite") : t("sidebar.add_favorite")}
          aria-label={favorite ? t("sidebar.remove_favorite") : t("sidebar.add_favorite")}
        >
          <Star
            className={cn(
              "h-3 w-3",
              favorite ? "fill-amber-400 text-amber-400" : "text-muted-foreground",
            )}
          />
        </button>
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            if (menuOpen) closeMenu();
            else setMenuOpen(true);
          }}
          className={cn(
            "rounded-lg p-1 transition-colors",
            menuOpen ? "bg-muted/80 text-foreground" : "text-muted-foreground hover:bg-muted/80 hover:text-foreground"
          )}
          title={t("sidebar.chat_options")}
          aria-label={t("sidebar.chat_options")}
        >
          <MoreHorizontal className="h-3.5 w-3.5" />
        </button>
      </div>

      {menuOpen ? (
        <>
          <div
            className="fixed inset-0 z-40"
            aria-hidden
            onClick={(e) => {
              e.stopPropagation();
              closeMenu();
            }}
          />
          <div className="absolute right-0 top-full z-50 mt-1 w-52 rounded-2xl border border-border bg-popover p-1.5 text-popover-foreground shadow-2xl animate-in fade-in-0 zoom-in-95 duration-150">
            <button
              type="button"
              onMouseEnter={closeMoveSub}
              onClick={(e) => {
                e.stopPropagation();
                setEditingId(conv.id);
                setEditTitle(conv.title || "");
                closeMenu();
              }}
              className="flex w-full items-center gap-2 rounded-xl px-2.5 py-2 text-left text-xs font-medium text-muted-foreground hover:bg-primary/10 hover:text-foreground dark:hover:bg-white/10 transition-colors"
            >
              <Edit3 size={13} className="shrink-0 opacity-80" />
              <span>{t("sidebar.rename")}</span>
            </button>
            {folderCtx ? (
              <button
                type="button"
                aria-haspopup="menu"
                aria-expanded={moveAnchor !== null}
                // Hover opens the folder submenu, as in the other nested menus.
                onMouseEnter={(e) => setMoveAnchor(e.currentTarget.getBoundingClientRect())}
                // Click/tap opens it too (touch and keyboard have no hover). It
                // never toggles closed: on desktop hover has already opened it.
                onClick={(e) => {
                  e.stopPropagation();
                  setMoveAnchor(e.currentTarget.getBoundingClientRect());
                }}
                className={cn(
                  "flex w-full items-center gap-2 rounded-xl px-2.5 py-2 text-left text-xs font-medium transition-colors hover:bg-primary/10 hover:text-foreground dark:hover:bg-white/10",
                  moveAnchor ? "bg-primary/10 text-foreground dark:bg-white/10" : "text-muted-foreground",
                )}
              >
                <FolderInput size={13} className="shrink-0 opacity-80" />
                <span className="flex-1">{t("sidebar.folders.move_to")}</span>
                <ChevronRight size={12} className="shrink-0 opacity-60" />
              </button>
            ) : null}
            <button
              type="button"
              onMouseEnter={closeMoveSub}
              onClick={(e) => {
                e.stopPropagation();
                setConfirmDeleteId(conv.id);
                closeMenu();
              }}
              className="flex w-full items-center gap-2 rounded-xl px-2.5 py-2 text-left text-xs font-medium text-rose-500 hover:bg-rose-500/10 hover:text-rose-400 transition-colors"
            >
              <Trash2 size={13} className="shrink-0" />
              <span>{t("sidebar.delete")}</span>
            </button>
          </div>
          {folderCtx && moveAnchor ? (
            <MoveToFolderSubmenu
              anchor={moveAnchor}
              folders={folderCtx.folders}
              currentFolderId={folderCtx.folderId}
              onPick={(folderId) => {
                closeMenu();
                folderCtx.onMove(conv.id, folderId);
              }}
              onDismiss={closeMoveSub}
            />
          ) : null}
        </>
      ) : null}
    </div>
  );
}
