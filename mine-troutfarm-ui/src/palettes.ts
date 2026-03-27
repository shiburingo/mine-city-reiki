export type ThemeMode = "light" | "dark";

export interface PaletteColors {
  primary: string;
  primaryForeground: string;
  accent: string;
  accentForeground: string;
  background: string;
  card: string;
}

export interface PortalPalette {
  id: string;
  name: string;
  description: string;
  colors: PaletteColors;
  darkColors: PaletteColors;
}

export const PORTAL_PALETTE_STORAGE_KEY = "colorPalette";
export const DEFAULT_PORTAL_PALETTE_ID = "beppu-bentenike";

export const PORTAL_PALETTES: PortalPalette[] = [
  {
    id: "beppu-bentenike",
    name: "別府弁天池",
    description: "透き通った青い水",
    colors: {
      primary: "#1e88e5",
      primaryForeground: "#ffffff",
      accent: "#e3f2fd",
      accentForeground: "#1565c0",
      background: "#f8fafb",
      card: "#ffffff",
    },
    darkColors: {
      primary: "#4aa3ff",
      primaryForeground: "#081321",
      accent: "#0f2a4a",
      accentForeground: "#bfdbfe",
      background: "#07121f",
      card: "#0b1a2c",
    },
  },
  {
    id: "akiyoshidai",
    name: "秋吉台",
    description: "カルスト台地の緑",
    colors: {
      primary: "#66bb6a",
      primaryForeground: "#ffffff",
      accent: "#e8f5e9",
      accentForeground: "#2e7d32",
      background: "#f1f8f4",
      card: "#ffffff",
    },
    darkColors: {
      primary: "#34d399",
      primaryForeground: "#071a12",
      accent: "#0b2a1c",
      accentForeground: "#a7f3d0",
      background: "#06130e",
      card: "#0b1f15",
    },
  },
  {
    id: "akiyoshido",
    name: "秋芳洞",
    description: "神秘的な鍾乳洞",
    colors: {
      primary: "#5e35b1",
      primaryForeground: "#ffffff",
      accent: "#ede7f6",
      accentForeground: "#4527a0",
      background: "#f5f3f7",
      card: "#ffffff",
    },
    darkColors: {
      primary: "#a78bfa",
      primaryForeground: "#160b28",
      accent: "#1f1636",
      accentForeground: "#e9d5ff",
      background: "#0b0614",
      card: "#150b28",
    },
  },
  {
    id: "taishodo",
    name: "大正洞",
    description: "清らかな地下水",
    colors: {
      primary: "#26c6da",
      primaryForeground: "#ffffff",
      accent: "#e0f7fa",
      accentForeground: "#00838f",
      background: "#f0f9fb",
      card: "#ffffff",
    },
    darkColors: {
      primary: "#22d3ee",
      primaryForeground: "#041a1f",
      accent: "#062a32",
      accentForeground: "#a5f3fc",
      background: "#041418",
      card: "#062028",
    },
  },
  {
    id: "kagekiyodo",
    name: "景清洞",
    description: "深い闇と静寂",
    colors: {
      primary: "#1565c0",
      primaryForeground: "#ffffff",
      accent: "#e1f5fe",
      accentForeground: "#0d47a1",
      background: "#f3f7fa",
      card: "#ffffff",
    },
    darkColors: {
      primary: "#3b82f6",
      primaryForeground: "#081321",
      accent: "#0a2042",
      accentForeground: "#bfdbfe",
      background: "#050f1e",
      card: "#091a33",
    },
  },
  {
    id: "sakura",
    name: "美祢の桜",
    description: "春の桜色",
    colors: {
      primary: "#ec407a",
      primaryForeground: "#ffffff",
      accent: "#fce4ec",
      accentForeground: "#c2185b",
      background: "#fef5f8",
      card: "#ffffff",
    },
    darkColors: {
      primary: "#f472b6",
      primaryForeground: "#2a0a16",
      accent: "#33111d",
      accentForeground: "#fecdd3",
      background: "#16060b",
      card: "#240a12",
    },
  },
  {
    id: "koyo",
    name: "秋の紅葉",
    description: "秋の紅葉色",
    colors: {
      primary: "#ef6c00",
      primaryForeground: "#ffffff",
      accent: "#fff3e0",
      accentForeground: "#e65100",
      background: "#fffaf5",
      card: "#ffffff",
    },
    darkColors: {
      primary: "#fb923c",
      primaryForeground: "#1f1008",
      accent: "#331a09",
      accentForeground: "#fed7aa",
      background: "#140a05",
      card: "#221008",
    },
  },
  {
    id: "sunset",
    name: "秋吉台の夕焼け",
    description: "夕暮れのグラデーション",
    colors: {
      primary: "#f4511e",
      primaryForeground: "#ffffff",
      accent: "#fbe9e7",
      accentForeground: "#d84315",
      background: "#fff8f6",
      card: "#ffffff",
    },
    darkColors: {
      primary: "#fb7185",
      primaryForeground: "#1f0a0f",
      accent: "#321106",
      accentForeground: "#fed7aa",
      background: "#120805",
      card: "#1f1009",
    },
  },
  {
    id: "gobo",
    name: "美東ゴボウ",
    description: "大地の恵み",
    colors: {
      primary: "#6d4c41",
      primaryForeground: "#ffffff",
      accent: "#efebe9",
      accentForeground: "#4e342e",
      background: "#faf9f8",
      card: "#ffffff",
    },
    darkColors: {
      primary: "#d1a77a",
      primaryForeground: "#20130b",
      accent: "#2a1d14",
      accentForeground: "#f1dfcf",
      background: "#100b07",
      card: "#1b120c",
    },
  },
  {
    id: "naganobori",
    name: "長登銅山",
    description: "歴史ある銅の色",
    colors: {
      primary: "#8d6e63",
      primaryForeground: "#ffffff",
      accent: "#d7ccc8",
      accentForeground: "#5d4037",
      background: "#f5f3f2",
      card: "#ffffff",
    },
    darkColors: {
      primary: "#f59e0b",
      primaryForeground: "#1c1206",
      accent: "#2a1e0a",
      accentForeground: "#fde68a",
      background: "#100b05",
      card: "#1c1308",
    },
  },
];

export function getPortalPaletteById(id: string | null | undefined): PortalPalette {
  const key = String(id ?? "").trim();
  return PORTAL_PALETTES.find((p) => p.id === key) ?? PORTAL_PALETTES[0];
}

function hexToRgbString(hex: string): string | null {
  const raw = String(hex ?? "").trim().replace(/^#/, "");
  if (!/^[0-9a-fA-F]{6}$/.test(raw)) return null;
  const r = Number.parseInt(raw.slice(0, 2), 16);
  const g = Number.parseInt(raw.slice(2, 4), 16);
  const b = Number.parseInt(raw.slice(4, 6), 16);
  return `${r} ${g} ${b}`;
}

export function applyPortalPalette(themeMode: ThemeMode, palette: PortalPalette): void {
  const colors = themeMode === "dark" ? palette.darkColors : palette.colors;
  const root = document.documentElement;

  root.style.setProperty("--primary", colors.primary);
  root.style.setProperty("--primary-foreground", colors.primaryForeground);
  root.style.setProperty("--accent", colors.accent);
  root.style.setProperty("--accent-foreground", colors.accentForeground);
  root.style.setProperty("--background", colors.background);
  root.style.setProperty("--card", colors.card);

  const primaryRgb = hexToRgbString(colors.primary);
  const accentRgb = hexToRgbString(colors.accent);
  const accentFgRgb = hexToRgbString(colors.accentForeground);

  if (primaryRgb) {
    root.style.setProperty("--brand-500", primaryRgb);
    root.style.setProperty("--brand-600", primaryRgb);
    root.style.setProperty("--brand-700", primaryRgb);
    root.style.setProperty("--brand-800", primaryRgb);
    root.style.setProperty("--brand-900", primaryRgb);
    root.style.setProperty("--theme-key", palette.id);
  }
  if (accentRgb) {
    root.style.setProperty("--brand-50", accentRgb);
    root.style.setProperty("--brand-100", accentRgb);
    root.style.setProperty("--brand-200", accentRgb);
    root.style.setProperty("--brand-300", accentRgb);
    root.style.setProperty("--brand-400", accentRgb);
  }
  if (accentFgRgb) {
    root.style.setProperty("--brand-fg", accentFgRgb);
  }
}
