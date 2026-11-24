"use client";

/**
 * Remembers which chat-ui sidebar sections (Favorites, Folders, Recent) the
 * user has collapsed, per browser, in localStorage.
 *
 * Uses `useSyncExternalStore` with an empty server snapshot so the SSR markup
 * (every section open) never mismatches hydration, and so several components
 * reading the same key stay in sync. Storage failures (private mode, blocked
 * site data) just mean the choice is not remembered.
 */

import { useCallback, useSyncExternalStore } from "react";

export type SidebarSectionId = "favorites" | "folders" | "recent";

const STORAGE_KEY = "aion-chat-sidebar-collapsed-sections";
/** Same-tab notification: the native `storage` event only fires in other tabs. */
const CHANGE_EVENT = "aion-sidebar-sections-changed";

function readRaw(): string {
  try {
    return localStorage.getItem(STORAGE_KEY) ?? "";
  } catch {
    return "";
  }
}

function parse(raw: string): SidebarSectionId[] {
  try {
    const v: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(v) ? (v.filter((x) => typeof x === "string") as SidebarSectionId[]) : [];
  } catch {
    return [];
  }
}

function subscribe(onChange: () => void): () => void {
  window.addEventListener("storage", onChange);
  window.addEventListener(CHANGE_EVENT, onChange);
  return () => {
    window.removeEventListener("storage", onChange);
    window.removeEventListener(CHANGE_EVENT, onChange);
  };
}

/** Returns `isCollapsed(section)` and `toggle(section)`. */
export function useCollapsedSections() {
  // The snapshot is the raw string: stable identity between renders.
  const raw = useSyncExternalStore(subscribe, readRaw, () => "");
  const collapsed = parse(raw);

  const isCollapsed = (id: SidebarSectionId) => collapsed.includes(id);

  const toggle = useCallback((id: SidebarSectionId) => {
    const current = parse(readRaw());
    const next = current.includes(id) ? current.filter((x) => x !== id) : [...current, id];
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    } catch {
      return;
    }
    window.dispatchEvent(new Event(CHANGE_EVENT));
  }, []);

  return { isCollapsed, toggle };
}
