import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useTheme } from "next-themes";
import {
  applyPortalPalette,
  DEFAULT_PORTAL_PALETTE_ID,
  getPortalPaletteById,
  PORTAL_PALETTES,
  PORTAL_PALETTE_STORAGE_KEY,
  type PortalPalette,
  type ThemeMode,
} from "./palettes";

type PortalUiContextValue = {
  themeMode: ThemeMode;
  paletteId: string;
  palette: PortalPalette;
  palettes: PortalPalette[];
  setPaletteId: (next: string) => void;
};

const PortalUiContext = createContext<PortalUiContextValue | null>(null);

type PortalUiProviderProps = {
  children: ReactNode;
  storageKey?: string;
  defaultPaletteId?: string;
};

export function PortalUiProvider({
  children,
  storageKey = PORTAL_PALETTE_STORAGE_KEY,
  defaultPaletteId = DEFAULT_PORTAL_PALETTE_ID,
}: PortalUiProviderProps) {
  const { resolvedTheme } = useTheme();
  const themeMode: ThemeMode = resolvedTheme === "dark" ? "dark" : "light";

  const [paletteId, setPaletteId] = useState<string>(() => {
    if (typeof window === "undefined") return getPortalPaletteById(defaultPaletteId).id;
    return getPortalPaletteById(localStorage.getItem(storageKey) ?? defaultPaletteId).id;
  });

  const palette = useMemo(() => getPortalPaletteById(paletteId), [paletteId]);

  useEffect(() => {
    try {
      localStorage.setItem(storageKey, paletteId);
    } catch {
      // ignore
    }
  }, [paletteId, storageKey]);

  useEffect(() => {
    applyPortalPalette(themeMode, palette);
  }, [themeMode, palette]);

  const value = useMemo<PortalUiContextValue>(
    () => ({
      themeMode,
      paletteId,
      palette,
      palettes: PORTAL_PALETTES,
      setPaletteId,
    }),
    [themeMode, paletteId, palette],
  );

  return <PortalUiContext.Provider value={value}>{children}</PortalUiContext.Provider>;
}

export function usePortalUi(): PortalUiContextValue {
  const ctx = useContext(PortalUiContext);
  if (!ctx) {
    throw new Error("usePortalUi must be used within PortalUiProvider");
  }
  return ctx;
}
