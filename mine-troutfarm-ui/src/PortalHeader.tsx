import { BookOpen, LayoutGrid, ListTree, LogOut, Settings } from "lucide-react";
import { useTheme } from "next-themes";
import { ThemeToggle } from "./ThemeToggle";

type Mode = "simple" | "normal" | "detail";

function BeginnerMarkIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" className={className} aria-hidden="true" focusable="false">
      <path fill="#22c55e" d="M12 4C7.6 4 4 7.6 4 12s3.6 8 8 8V4z" />
      <path fill="#facc15" d="M12 4v16c4.4 0 8-3.6 8-8s-3.6-8-8-8z" />
    </svg>
  );
}

export function PortalHeader({
  title,
  subtitle,
  syncStatusText,
  syncStatusTone = "neutral",
  onOpenSettings,
  onOpenOpsRules,
  mode,
  onChangeMode,
  user,
  onLogout,
  authEnabled,
}: {
  title: string;
  subtitle?: string;
  syncStatusText?: string;
  syncStatusTone?: "ok" | "error" | "neutral";
  onOpenSettings: () => void;
  onOpenOpsRules?: () => void;
  mode?: Mode;
  onChangeMode?: (m: Mode) => void;
  user?: { username: string; roles: string[] } | null;
  onLogout?: () => void;
  authEnabled?: boolean;
}) {
  const { resolvedTheme } = useTheme();
  const isDark = resolvedTheme === "dark";
  const iconSrc = isDark ? "/top/title-icon-dark.svg" : "/top/title-icon-light.svg";
  const roleLabel = user?.roles?.includes("admin")
    ? "管理者"
    : user?.roles?.includes("staff")
      ? "養鱒場職員"
      : user?.roles?.includes("guest")
        ? "ゲスト"
        : "";

  const syncClass =
    syncStatusTone === "ok"
      ? "border-emerald-200 bg-emerald-50 text-emerald-700"
      : syncStatusTone === "error"
        ? "border-red-200 bg-red-50 text-red-700"
        : "border-border bg-background text-muted-foreground";

  return (
    <header className="border-b bg-card sticky top-0 z-10">
      <div className="container mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex items-center justify-between py-3">
          <div className="flex items-center gap-3 min-w-0">
            <a
              href="/top/"
              className="size-10 rounded-lg shrink-0 overflow-hidden inline-flex items-center justify-center"
              aria-label="ポータルへ移動"
            >
              <img
                src={iconSrc}
                width={40}
                height={40}
                className="size-10"
                alt=""
                aria-hidden="true"
              />
            </a>
            <div className="min-w-0">
              <div className="flex items-center gap-2 min-w-0">
                <h1 className="text-lg sm:text-xl truncate">{title}</h1>
                {syncStatusText ? (
                  <span className={`shrink-0 rounded-full border px-2 py-0.5 text-xs ${syncClass}`}>
                    {syncStatusText}
                  </span>
                ) : null}
              </div>
              {subtitle ? <p className="text-sm text-muted-foreground truncate">{subtitle}</p> : null}
            </div>
          </div>

          <div className="flex items-center gap-2">
            {onOpenOpsRules ? (
              <button
                type="button"
                onClick={onOpenOpsRules}
                className="inline-flex h-9 items-center rounded-md px-2 hover:bg-accent text-foreground"
                aria-label="運用ルール"
              >
                <BookOpen className="size-4" />
              </button>
            ) : null}
            <ThemeToggle />
            {authEnabled && user && (
              <div className="hidden items-center gap-2 sm:flex">
                <span className="rounded-full border px-2 py-0.5 text-xs text-foreground">
                  {roleLabel || "ユーザー"}
                </span>
                <span className="text-sm text-muted-foreground">{user.username}</span>
                {onLogout && (
                  <button
                    type="button"
                    onClick={onLogout}
                    className="inline-flex h-9 items-center gap-2 rounded-md px-2 hover:bg-accent text-foreground"
                    aria-label="ログアウト"
                  >
                    <LogOut className="size-4" />
                  </button>
                )}
              </div>
            )}
            {mode !== "simple" ? (
              <button
                type="button"
                onClick={onOpenSettings}
                className="inline-flex h-9 items-center gap-2 rounded-md px-2 hover:bg-accent text-foreground"
                aria-label="設定"
              >
                <Settings className="size-4" />
                <span className="hidden sm:inline">設定</span>
              </button>
            ) : null}
          </div>
        </div>

        {mode && onChangeMode ? (
          <div className="pb-3">
            <nav className="grid grid-cols-3 gap-2 rounded-lg border border-border bg-background/40 p-1">
              <button
                type="button"
                onClick={() => onChangeMode("simple")}
                className={`inline-flex h-9 items-center justify-center gap-1 rounded-md px-2 text-sm font-semibold transition-colors ${
                  mode === "simple"
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:text-foreground hover:bg-accent/40"
                }`}
                aria-label="シンプル"
                title="シンプル"
              >
                <BeginnerMarkIcon className="size-4" />
                <span>シンプル</span>
              </button>
              <button
                type="button"
                onClick={() => onChangeMode("normal")}
                className={`inline-flex h-9 items-center justify-center gap-1 rounded-md px-2 text-sm font-semibold transition-colors ${
                  mode === "normal"
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:text-foreground hover:bg-accent/40"
                }`}
                aria-label="ノーマル"
                title="ノーマル"
              >
                <LayoutGrid className="size-4" aria-hidden="true" />
                <span>ノーマル</span>
              </button>
              <button
                type="button"
                onClick={() => onChangeMode("detail")}
                className={`inline-flex h-9 items-center justify-center gap-1 rounded-md px-2 text-sm font-semibold transition-colors ${
                  mode === "detail"
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:text-foreground hover:bg-accent/40"
                }`}
                aria-label="詳細"
                title="詳細"
              >
                <ListTree className="size-4" aria-hidden="true" />
                <span>詳細</span>
              </button>
            </nav>
          </div>
        ) : null}
      </div>
    </header>
  );
}
