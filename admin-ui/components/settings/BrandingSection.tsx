"use client";

/**
 * Settings → Brand: tenant logo (light + optional dark), favicon and primary
 * colours for chat-ui, admin-ui and the transactional emails.
 *
 * Self-saving section (like SMTP): uploads and removals are applied at once,
 * colours have their own Save button. Backend: `/admin/branding*`
 * (`src/api/admin_branding.py`). After every change the shared branding store
 * is refreshed so the admin sees the result immediately in this tab.
 * "Powered by AION" is a fixed product rule, shown here as a note only.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { ImageUp, Loader2, RotateCcw, Save, Trash2 } from "lucide-react";
import { apiFetch } from "@/lib/api/headers";
import { apiBase } from "@/lib/api";
import { refreshBranding, brandingAssetUrl } from "@/lib/branding/branding";
import { contrastRatio, isHexColor } from "@/lib/branding/colors";
import { inputCls, outlineBtn, solidBtn, ghostBtn } from "@/components/ui/classes";
import { cn } from "@/lib/cn";
import { Field, FieldGrid, Group, Note, SectionHead } from "./ui";
import { K, type SectionCtx } from "./types";

type AssetKind = "logo_light" | "logo_dark" | "favicon";

type AdminAsset = { url: string; mime: string; size: number; updated_at: string | null; sha256: string };

/** Short format label for an asset MIME type ("image/svg+xml" → "SVG", "image/x-icon" → "ICO"). */
function formatMime(mime: string): string {
  const sub = mime.replace(/^image\//, "").replace(/\+xml$/, "");
  if (sub === "x-icon" || sub === "vnd.microsoft.icon") return "ICO";
  return sub.toUpperCase();
}

type BrandingState = {
  primary_color_light: string | null;
  primary_color_dark: string | null;
  logo_light: AdminAsset | null;
  logo_dark: AdminAsset | null;
  favicon: AdminAsset | null;
  custom_logo: boolean;
  /** True when the logo used in emails is PNG/JPEG (SVG/WebP are not embedded). */
  email_logo: boolean;
  updated_at: string | null;
};

const m = `${K}.branding`;

/** AION defaults from `styles/theme-tokens.css`, shown when no colour is set. */
const AION_LIGHT = "#ca2121";
const AION_DARK = "#db2424";
/** Page backgrounds the colours are checked against (light and dark theme). */
const BG_LIGHT = "#ffffff";
const BG_DARK = "#161618";
/** WCAG minimum for UI components and large text: below it buttons/links are hard to see. */
const MIN_CONTRAST = 3;

const ACCEPT: Record<AssetKind, string> = {
  logo_light: "image/png,image/jpeg,image/webp,image/svg+xml",
  logo_dark: "image/png,image/jpeg,image/webp,image/svg+xml",
  favicon: "image/png,image/x-icon,image/vnd.microsoft.icon,image/svg+xml,.ico",
};

/** Error codes from /admin/branding map to localized messages; unknown codes fall back. */
function errorText(t: SectionCtx["t"], detail: { code?: string; max_bytes?: number } | undefined, fallback: string) {
  if (!detail?.code) return fallback;
  const key = `${m}.errors.${detail.code}`;
  const msg = t(key, { max: detail.max_bytes ? `${Math.round(detail.max_bytes / 1024)} KB` : "" });
  return msg === key ? fallback : msg;
}

const formatSize = (n: number) => (n < 1024 ? `${n} B` : `${Math.round(n / 102.4) / 10} KB`);

export function BrandingSection({ t, notify }: Pick<SectionCtx, "t" | "notify">) {
  const [state, setState] = useState<BrandingState | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [light, setLight] = useState("");
  const [dark, setDark] = useState("");
  const [savingColors, setSavingColors] = useState(false);
  const [busy, setBusy] = useState<AssetKind | null>(null);

  /** Applies a fresh admin view to the form and the live branding of this tab. */
  const apply = useCallback((data: BrandingState, refresh: boolean) => {
    setState(data);
    setLight(data.primary_color_light ?? "");
    setDark(data.primary_color_dark ?? "");
    if (refresh) void refreshBranding(true);
  }, []);

  const load = useCallback(async () => {
    try {
      const res = await apiFetch(`${apiBase()}/admin/branding`);
      if (!res.ok) throw new Error(String(res.status));
      apply((await res.json()) as BrandingState, false);
      setLoadFailed(false);
    } catch {
      setLoadFailed(true);
    }
  }, [apply]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- fetch on mount
    void load();
  }, [load]);

  const upload = async (kind: AssetKind, file: File) => {
    setBusy(kind);
    try {
      const body = new FormData();
      body.append("file", file);
      // No Content-Type header: the browser sets the multipart boundary.
      const res = await apiFetch(`${apiBase()}/admin/branding/assets/${kind}`, { method: "PUT", body });
      if (res.ok) {
        apply((await res.json()) as BrandingState, true);
        notify(t(`${m}.uploaded`), "success");
      } else {
        const err = await res.json().catch(() => ({}));
        notify(errorText(t, err?.detail, t(`${m}.err_upload`)), "error");
      }
    } catch {
      notify(t(`${m}.err_network`), "error");
    } finally {
      setBusy(null);
    }
  };

  const remove = async (kind: AssetKind) => {
    setBusy(kind);
    try {
      const res = await apiFetch(`${apiBase()}/admin/branding/assets/${kind}`, { method: "DELETE" });
      if (!res.ok) throw new Error(String(res.status));
      apply((await res.json()) as BrandingState, true);
      notify(t(`${m}.removed`), "success");
    } catch {
      notify(t(`${m}.err_save`), "error");
    } finally {
      setBusy(null);
    }
  };

  const lightValid = light === "" || isHexColor(light);
  const darkValid = dark === "" || isHexColor(dark);
  const colorsDirty =
    !!state && (light.toLowerCase() !== (state.primary_color_light ?? "") || dark.toLowerCase() !== (state.primary_color_dark ?? ""));

  const saveColors = async () => {
    if (!lightValid || !darkValid) return notify(t(`${m}.errors.invalid_color`), "error");
    setSavingColors(true);
    try {
      const res = await apiFetch(`${apiBase()}/admin/branding`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ primary_color_light: light || null, primary_color_dark: dark || null }),
      });
      if (res.ok) {
        apply((await res.json()) as BrandingState, true);
        notify(t(`${m}.saved`), "success");
      } else {
        const err = await res.json().catch(() => ({}));
        notify(errorText(t, err?.detail, t(`${m}.err_save`)), "error");
      }
    } catch {
      notify(t(`${m}.err_network`), "error");
    } finally {
      setSavingColors(false);
    }
  };

  if (loadFailed) {
    return (
      <div className="flex flex-col gap-5">
        <SectionHead title={t(`${m}.title`)} description={t(`${m}.description`)} />
        <Note tone="warn">{t(`${m}.load_failed`)}</Note>
      </div>
    );
  }

  if (!state) {
    return (
      <div className="flex items-center gap-2 px-1 text-sm text-muted-foreground" role="status">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
        {t(`${m}.loading`)}
      </div>
    );
  }

  // Dark logo falls back to the light one, so the dark preview shows what users will see.
  const darkPreview = state.logo_dark ?? state.logo_light;
  const effectiveDark = dark || light;

  return (
    <div className="flex flex-col gap-5">
      <SectionHead title={t(`${m}.title`)} description={t(`${m}.description`)} />

      <Note>{t(`${m}.powered_by_note`)}</Note>

      <Group title={t(`${m}.logo`)} description={t(`${m}.logo_hint`)}>
        <div className="grid gap-4 px-5 py-5 md:grid-cols-2">
          <AssetCard
            t={t}
            kind="logo_light"
            title={t(`${m}.logo_light`)}
            asset={state.logo_light}
            preview={state.logo_light}
            surface="light"
            busy={busy}
            onUpload={upload}
            onRemove={remove}
          />
          <AssetCard
            t={t}
            kind="logo_dark"
            title={t(`${m}.logo_dark`)}
            hint={!state.logo_dark && state.logo_light ? t(`${m}.logo_dark_fallback`) : t(`${m}.optional`)}
            asset={state.logo_dark}
            preview={darkPreview}
            surface="dark"
            busy={busy}
            onUpload={upload}
            onRemove={remove}
          />
        </div>
        {state.custom_logo && !state.email_logo && (
          <div className="px-5 pb-5">
            <Note tone="warn">{t(`${m}.email_svg_note`)}</Note>
          </div>
        )}
      </Group>

      <Group title={t(`${m}.favicon`)} description={t(`${m}.favicon_hint`)}>
        <div className="grid gap-4 px-5 py-5 md:grid-cols-2">
          <AssetCard
            t={t}
            kind="favicon"
            title={t(`${m}.favicon`)}
            asset={state.favicon}
            preview={state.favicon}
            surface="split"
            busy={busy}
            onUpload={upload}
            onRemove={remove}
          />
        </div>
      </Group>

      <Group title={t(`${m}.colors`)} description={t(`${m}.colors_hint`)}>
        <FieldGrid>
          <ColorField
            t={t}
            label={t(`${m}.color_light`)}
            value={light}
            onChange={setLight}
            fallback={AION_LIGHT}
            background={BG_LIGHT}
            valid={lightValid}
            hint={light ? undefined : t(`${m}.color_default`)}
          />
          <ColorField
            t={t}
            label={t(`${m}.color_dark`)}
            value={dark}
            onChange={setDark}
            fallback={light || AION_DARK}
            background={BG_DARK}
            valid={darkValid}
            hint={dark ? undefined : light ? t(`${m}.color_dark_fallback`) : t(`${m}.color_default`)}
          />
          <div className="md:col-span-2">
            <span className="text-sm font-medium">{t(`${m}.preview`)}</span>
            <div className="mt-1.5 grid overflow-hidden rounded-xl border border-border sm:grid-cols-2">
              <ColorPreview t={t} color={isHexColor(light) ? light : AION_LIGHT} background={BG_LIGHT} dark={false} />
              <ColorPreview t={t} color={isHexColor(effectiveDark) ? effectiveDark : AION_DARK} background={BG_DARK} dark />
            </div>
          </div>
        </FieldGrid>
        <div className="flex flex-wrap items-center justify-end gap-3 border-t border-border px-5 py-3.5">
          {(light || dark) && (
            <button
              type="button"
              className={cn(ghostBtn, "mr-auto")}
              onClick={() => {
                setLight("");
                setDark("");
              }}
            >
              <RotateCcw className="h-3.5 w-3.5" aria-hidden />
              {t(`${m}.reset_colors`)}
            </button>
          )}
          {colorsDirty && <span className="text-sm text-muted-foreground" role="status">{t(`${m}.unsaved`)}</span>}
          <button
            type="button"
            onClick={() => void saveColors()}
            disabled={savingColors || !colorsDirty || !lightValid || !darkValid}
            className={solidBtn}
          >
            {savingColors ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> : <Save className="h-3.5 w-3.5" aria-hidden />}
            {savingColors ? t(`${m}.saving`) : t(`${m}.save`)}
          </button>
        </div>
      </Group>
    </div>
  );
}

/**
 * One uploadable image: preview on the surface it will be shown on, file
 * picker and remove button. `surface="split"` previews on both themes (favicon).
 */
function AssetCard({
  t,
  kind,
  title,
  hint,
  asset,
  preview,
  surface,
  busy,
  onUpload,
  onRemove,
}: {
  t: SectionCtx["t"];
  kind: AssetKind;
  title: string;
  hint?: string;
  asset: AdminAsset | null;
  preview: AdminAsset | null;
  surface: "light" | "dark" | "split";
  busy: AssetKind | null;
  onUpload: (kind: AssetKind, file: File) => Promise<void>;
  onRemove: (kind: AssetKind) => Promise<void>;
}) {
  const input = useRef<HTMLInputElement>(null);
  const working = busy === kind;
  const src = preview ? brandingAssetUrl(preview) : null;
  const img = (bg: string) => (
    <div className={cn("flex h-24 flex-1 items-center justify-center p-4", bg)}>
      {src ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={src} alt="" className={cn("max-w-full object-contain", kind === "favicon" ? "h-8 w-8" : "max-h-14")} />
      ) : (
        <span className={cn("text-xs", bg === "bg-white" ? "text-zinc-400" : "text-zinc-500")}>{t(`${K}.branding.no_image`)}</span>
      )}
    </div>
  );

  return (
    <div className="flex min-w-0 flex-col gap-2">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-sm font-medium">{title}</span>
        {hint && <span className="truncate text-xs text-muted-foreground">{hint}</span>}
      </div>
      <div className="flex overflow-hidden rounded-xl border border-border">
        {(surface === "light" || surface === "split") && img("bg-white")}
        {(surface === "dark" || surface === "split") && img("bg-[#161618]")}
      </div>
      <p className="text-xs text-muted-foreground">
        {asset
          ? `${formatMime(asset.mime)} · ${formatSize(asset.size)}`
          : t(kind === "favicon" ? `${K}.branding.favicon_formats` : `${K}.branding.logo_formats`)}
      </p>
      <div className="flex flex-wrap gap-2">
        <input
          ref={input}
          type="file"
          accept={ACCEPT[kind]}
          className="sr-only"
          tabIndex={-1}
          aria-hidden
          onChange={(e) => {
            const file = e.target.files?.[0];
            e.target.value = ""; // allow re-selecting the same file after an error
            if (file) void onUpload(kind, file);
          }}
        />
        <button type="button" className={outlineBtn} disabled={busy !== null} onClick={() => input.current?.click()}>
          {working ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> : <ImageUp className="h-3.5 w-3.5" aria-hidden />}
          {t(asset ? `${K}.branding.replace` : `${K}.branding.upload`)}
        </button>
        {asset && (
          <button type="button" className={ghostBtn} disabled={busy !== null} onClick={() => void onRemove(kind)}>
            <Trash2 className="h-3.5 w-3.5" aria-hidden />
            {t(`${K}.branding.remove`)}
          </button>
        )}
      </div>
    </div>
  );
}

/** Native colour picker + hex field, with a warning when the colour is hard to see on the theme background. */
function ColorField({
  t,
  label,
  value,
  onChange,
  fallback,
  background,
  valid,
  hint,
}: {
  t: SectionCtx["t"];
  label: string;
  value: string;
  onChange: (v: string) => void;
  /** Colour shown in the picker while the field is empty (the effective default). */
  fallback: string;
  background: string;
  valid: boolean;
  hint?: string;
}) {
  const lowContrast = valid && isHexColor(value) && contrastRatio(value, background) < MIN_CONTRAST;
  return (
    <Field
      label={label}
      hint={!valid ? t(`${K}.branding.errors.invalid_color`) : lowContrast ? t(`${K}.branding.low_contrast`) : hint}
    >
      <div className="flex items-center gap-2">
        <input
          type="color"
          value={isHexColor(value) ? value.toLowerCase() : fallback}
          onChange={(e) => onChange(e.target.value)}
          className="focus-ring h-10 w-12 shrink-0 cursor-pointer rounded-lg border border-border bg-background p-1"
          aria-label={label}
        />
        <input
          type="text"
          value={value}
          onChange={(e) => onChange(e.target.value.trim())}
          placeholder={fallback}
          maxLength={7}
          spellCheck={false}
          className={cn(inputCls, "font-mono", !valid && "border-destructive")}
          aria-invalid={!valid}
        />
      </div>
    </Field>
  );
}

/** Sample button and link on a theme background, so the admin sees contrast before saving. */
function ColorPreview({ t, color, background, dark }: { t: SectionCtx["t"]; color: string; background: string; dark: boolean }) {
  const fg = contrastRatio(color, "#ffffff") >= contrastRatio(color, "#171717") ? "#ffffff" : "#171717";
  return (
    <div className="flex flex-wrap items-center gap-3 px-4 py-4" style={{ background }}>
      <span className="rounded-lg px-3 py-1.5 text-sm font-semibold" style={{ background: color, color: fg }}>
        {t(`${K}.branding.preview_button`)}
      </span>
      <span className="text-sm font-medium underline underline-offset-2" style={{ color }}>
        {t(`${K}.branding.preview_link`)}
      </span>
      <span className={cn("ml-auto text-xs", dark ? "text-zinc-400" : "text-zinc-500")}>
        {t(dark ? `${K}.branding.theme_dark` : `${K}.branding.theme_light`)}
      </span>
    </div>
  );
}
