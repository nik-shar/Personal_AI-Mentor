import { clsx, type ClassValue } from "clsx";

/** Tailwind-friendly class combiner. */
export function cn(...inputs: ClassValue[]): string {
  return clsx(inputs);
}

export function formatDate(value?: string | null): string {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value);
  return d.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

export function formatDateTime(value?: string | null): string {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value);
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function relativeTime(value?: string | null): string {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value);
  const diff = Date.now() - d.getTime();
  const mins = Math.round(diff / 60000);
  if (Math.abs(mins) < 1) return "just now";
  if (Math.abs(mins) < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (Math.abs(hours) < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (Math.abs(days) < 30) return `${days}d ago`;
  return formatDate(value);
}

export function titleCase(value: string): string {
  return value
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

export const STAGE_ORDER = [
  "wishlist",
  "applied",
  "referral_requested",
  "screening",
  "interviewing",
  "offer",
  "rejected",
  "withdrawn",
] as const;

export function stageLabel(stage: string): string {
  return titleCase(stage || "applied");
}

export function stageColor(stage: string): string {
  switch (stage) {
    case "offer":
      return "var(--color-success)";
    case "interviewing":
    case "screening":
      return "var(--color-info)";
    case "rejected":
    case "withdrawn":
      return "var(--color-danger)";
    case "referral_requested":
      return "var(--color-accent-2)";
    case "applied":
      return "var(--color-accent)";
    default:
      return "var(--color-fg-muted)";
  }
}

export function statusColor(status: string): string {
  switch (status) {
    case "done":
    case "completed":
      return "var(--color-success)";
    case "in_progress":
      return "var(--color-accent)";
    default:
      return "var(--color-fg-dim)";
  }
}

export function confidenceMeta(confidence: number, confirmed: boolean) {
  const label = confidence >= 0.8 ? "high" : confidence >= 0.5 ? "medium" : "low";
  const color =
    confidence >= 0.8
      ? "var(--color-success)"
      : confidence >= 0.5
        ? "var(--color-warning)"
        : "var(--color-fg-dim)";
  return { label, color, confirmed };
}

export function truncate(text: string, n = 160): string {
  const t = (text || "").replace(/\s+/g, " ").trim();
  return t.length <= n ? t : t.slice(0, n - 1) + "…";
}
