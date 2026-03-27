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

export function PortalUiProvider({ children }: { children: ReactNode }) {
  const { resolvedTheme } = useTheme();
  const themeMode: ThemeMode = resolvedTheme === "dark" ? "dark" : "light";

  const [paletteId, setPaletteId] = useState<string>(() => {
    if (typeof window === "undefined") return DEFAULT_PORTAL_PALETTE_ID;
    return localStorage.getItem(PORTAL_PALETTE_STORAGE_KEY) ?? DEFAULT_PORTAL_PALETTE_ID;
  });

  const palette = useMemo(() => getPortalPaletteById(paletteId), [paletteId]);

  useEffect(() => {
    try {
      localStorage.setItem(PORTAL_PALETTE_STORAGE_KEY, paletteId);
    } catch {
      // ignore
    }
  }, [paletteId]);

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
