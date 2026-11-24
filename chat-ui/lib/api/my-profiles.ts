/**
 * Client for the user's private agent profiles (`/v1/me/profiles`, access
 * control Phase 3). Used by the "I miei profili" page
 * (`components/my-profiles/*`). Same auth headers as the rest of
 * `lib/api/aion.ts`; the backend checks ownership and the user's access
 * rules on every call, the client only mirrors them in the UI.
 */

import { apiBase } from "@/lib/config";
import { baseUserHeaders, jsonHeaders } from "@/lib/api/aion";

/** The three lists a private profile is made of (ids from `building-blocks`). */
export type ProfilePart = "skills" | "mcp_servers" | "native_tool_groups";
export const PROFILE_PARTS: ProfilePart[] = [
  "skills",
  "mcp_servers",
  "native_tool_groups",
];

/** Private profile as returned by the API. */
export type MyProfile = {
  slug: string;
  name: string;
  description: string;
  instructions: string;
  skills: string[];
  mcp_servers: string[];
  native_tool_groups: string[];
  /** Stored items the user may no longer use (revoked by an admin): ignored at runtime, dropped on the next save. */
  unavailable: Record<ProfilePart, string[]>;
  updated_at?: string | null;
};

/** Editable fields sent on create / update. */
export type MyProfileInput = Pick<
  MyProfile,
  "name" | "description" | "instructions" | ProfilePart
>;

export type MyProfilesList = {
  items: MyProfile[];
  can_create: boolean;
  limits: { max_profiles: number; max_instructions: number };
};

/** One item the user may add (skill, MCP server or native tool group). */
export type BuildingBlock = { id: string; name: string; description?: string };
export type BuildingBlocks = Record<ProfilePart, BuildingBlock[]>;

/** Error with the backend `detail.code` (e.g. `items_not_allowed`, `private_profiles_limit`). */
export class MyProfilesError extends Error {
  constructor(
    message: string,
    public code: string,
    public status: number,
  ) {
    super(message);
  }
}

async function check<T>(res: Response): Promise<T> {
  if (res.ok) return (await res.json()) as T;
  let code = "";
  let message = String(res.status);
  try {
    const data = await res.json();
    const d = data?.detail;
    if (d && typeof d === "object") {
      code = String(d.code || "");
      message = String(d.message || d.code || message);
    } else if (d) {
      message = String(d);
    }
  } catch {
    /* non-JSON error body */
  }
  throw new MyProfilesError(message, code, res.status);
}

const base = () => `${apiBase()}/v1/me/profiles`;

export async function listMyProfiles(
  userId: string,
  token?: string | null,
): Promise<MyProfilesList> {
  return check(
    await fetch(base(), { headers: baseUserHeaders(userId, token) }),
  );
}

export async function fetchBuildingBlocks(
  userId: string,
  token?: string | null,
): Promise<BuildingBlocks> {
  return check(
    await fetch(`${base()}/building-blocks`, {
      headers: baseUserHeaders(userId, token),
    }),
  );
}

export async function createMyProfile(
  userId: string,
  body: MyProfileInput,
  token?: string | null,
): Promise<MyProfile> {
  return check(
    await fetch(base(), {
      method: "POST",
      headers: jsonHeaders(userId, token),
      body: JSON.stringify(body),
    }),
  );
}

export async function updateMyProfile(
  userId: string,
  slug: string,
  body: MyProfileInput,
  token?: string | null,
): Promise<MyProfile> {
  return check(
    await fetch(`${base()}/${encodeURIComponent(slug)}`, {
      method: "PUT",
      headers: jsonHeaders(userId, token),
      body: JSON.stringify(body),
    }),
  );
}

/** Returns how many scheduled jobs were paused because they used the profile. */
export async function deleteMyProfile(
  userId: string,
  slug: string,
  token?: string | null,
): Promise<{ paused_jobs: number }> {
  return check(
    await fetch(`${base()}/${encodeURIComponent(slug)}`, {
      method: "DELETE",
      headers: baseUserHeaders(userId, token),
    }),
  );
}

/** New private profile copied from any profile the user may use (global or own). */
export async function duplicateProfile(
  userId: string,
  sourceSlug: string,
  name: string,
  token?: string | null,
): Promise<MyProfile> {
  return check(
    await fetch(`${base()}/${encodeURIComponent(sourceSlug)}/duplicate`, {
      method: "POST",
      headers: jsonHeaders(userId, token),
      body: JSON.stringify({ name }),
    }),
  );
}

/**
 * AI wizard (same generator as the admin-ui one, limited to what the user may
 * add). Both calls return an unsaved draft: create / update save it.
 */
export async function wizardGenerate(
  userId: string,
  prompt: string,
  token?: string | null,
): Promise<MyProfileInput> {
  return check(
    await fetch(`${base()}/wizard-generate`, {
      method: "POST",
      headers: jsonHeaders(userId, token),
      body: JSON.stringify({ prompt }),
    }),
  );
}

export async function wizardRefine(
  userId: string,
  prompt: string,
  current: MyProfileInput,
  token?: string | null,
): Promise<MyProfileInput> {
  return check(
    await fetch(`${base()}/wizard-refine`, {
      method: "POST",
      headers: jsonHeaders(userId, token),
      body: JSON.stringify({ prompt, current_profile: current }),
    }),
  );
}
