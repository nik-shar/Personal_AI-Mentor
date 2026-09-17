"use client";

import React from "react";
import { Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";

/* ------------------------------- Card ------------------------------ */

export function Card({
  className,
  children,
  ...rest
}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={cn("card p-5", className)} {...rest}>
      {children}
    </div>
  );
}

export function CardHeader({
  title,
  subtitle,
  icon,
  action,
  className,
}: {
  title: string;
  subtitle?: string;
  icon?: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex items-start justify-between gap-3 mb-4", className)}>
      <div className="flex items-start gap-2.5 min-w-0">
        {icon ? <span className="text-[var(--color-accent)] mt-0.5">{icon}</span> : null}
        <div className="min-w-0">
          <h3
            className="text-[0.95rem] font-semibold truncate"
            style={{ fontFamily: "var(--font-display)" }}
          >
            {title}
          </h3>
          {subtitle ? (
            <p className="text-xs text-[var(--color-fg-muted)] mt-0.5">{subtitle}</p>
          ) : null}
        </div>
      </div>
      {action}
    </div>
  );
}

/* ------------------------------ Badge ------------------------------ */

export function Badge({
  children,
  color,
  className,
}: {
  children: React.ReactNode;
  color?: string;
  className?: string;
}) {
  return (
    <span
      className={cn("badge", className)}
      style={color ? { color, borderColor: color } : undefined}
    >
      {children}
    </span>
  );
}

/* ------------------------------ Button ----------------------------- */

type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost" | "danger";
  size?: "sm" | "md";
  loading?: boolean;
};

export function Button({
  variant = "secondary",
  size = "md",
  loading,
  className,
  children,
  disabled,
  ...rest
}: ButtonProps) {
  return (
    <button
      className={cn(
        "btn",
        `btn-${variant}`,
        size === "sm" && "btn-sm",
        className,
      )}
      disabled={disabled || loading}
      {...rest}
    >
      {loading ? <Loader2 size={15} className="animate-spin" /> : null}
      {children}
    </button>
  );
}

/* ------------------------------ Spinner ---------------------------- */

export function Spinner({ label }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-2 py-10 text-[var(--color-fg-muted)] text-sm">
      <Loader2 size={18} className="animate-spin" />
      {label ?? "Loading…"}
    </div>
  );
}

/* ---------------------------- EmptyState --------------------------- */

export function EmptyState({
  icon,
  title,
  hint,
}: {
  icon?: React.ReactNode;
  title: string;
  hint?: string;
}) {
  return (
    <div className="flex flex-col items-center justify-center text-center py-12 px-4">
      {icon ? (
        <div className="text-[var(--color-fg-dim)] mb-3">{icon}</div>
      ) : null}
      <p className="text-sm font-medium text-[var(--color-fg-muted)]">{title}</p>
      {hint ? (
        <p className="text-xs text-[var(--color-fg-dim)] mt-1 max-w-sm">{hint}</p>
      ) : null}
    </div>
  );
}

/* --------------------------- ErrorNotice --------------------------- */

export function ErrorNotice({
  message,
  onRetry,
}: {
  message: string;
  onRetry?: () => void;
}) {
  return (
    <div className="rounded-[var(--radius)] border border-[rgba(248,113,113,0.35)] bg-[rgba(248,113,113,0.08)] px-4 py-3 text-sm text-[var(--color-danger)] flex items-center justify-between gap-3">
      <span className="min-w-0 break-words">{message}</span>
      {onRetry ? (
        <Button size="sm" variant="secondary" onClick={onRetry}>
          Retry
        </Button>
      ) : null}
    </div>
  );
}

/* ------------------------------- Stat ------------------------------ */

export function Stat({
  label,
  value,
  hint,
  accent,
  icon,
}: {
  label: string;
  value: React.ReactNode;
  hint?: string;
  accent?: string;
  icon?: React.ReactNode;
}) {
  return (
    <div className="card p-4">
      <div className="flex items-center justify-between">
        <span className="text-xs uppercase tracking-wide text-[var(--color-fg-dim)]">
          {label}
        </span>
        {icon ? <span style={{ color: accent ?? "var(--color-accent)" }}>{icon}</span> : null}
      </div>
      <div
        className="text-2xl font-semibold mt-1.5"
        style={{ fontFamily: "var(--font-display)", color: accent ?? "var(--color-fg)" }}
      >
        {value}
      </div>
      {hint ? <p className="text-xs text-[var(--color-fg-muted)] mt-1">{hint}</p> : null}
    </div>
  );
}
