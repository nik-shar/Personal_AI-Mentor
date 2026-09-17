"use client";

import { useEffect } from "react";
import { X } from "lucide-react";
import { Markdown } from "./Markdown";

export function Drawer({
  open,
  onClose,
  title,
  tag,
  children,
  loading,
  error,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  tag?: string;
  children?: React.ReactNode;
  loading?: boolean;
  error?: string | null;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex justify-end bg-black/55 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="h-full w-full max-w-2xl bg-[var(--color-bg-elev)] border-l border-[var(--color-border)] shadow-2xl flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-3 px-5 py-4 border-b border-[var(--color-border-soft)]">
          <div className="min-w-0">
            {tag ? <span className="badge mb-1.5 inline-flex">{tag}</span> : null}
            <h3
              className="text-base font-semibold truncate"
              style={{ fontFamily: "var(--font-display)" }}
            >
              {title}
            </h3>
          </div>
          <button
            className="btn btn-ghost btn-sm"
            onClick={onClose}
            aria-label="Close"
          >
            <X size={16} />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto px-5 py-4">
          {loading ? (
            <div className="text-sm text-[var(--color-fg-muted)]">
              Loading from Obsidian vault…
            </div>
          ) : error ? (
            <div className="text-sm text-[var(--color-danger)]">{error}</div>
          ) : (
            children
          )}
        </div>
      </div>
    </div>
  );
}

/** Convenience: a drawer that renders markdown body. */
export function MarkdownDrawer({
  open,
  onClose,
  title,
  tag = "Obsidian Note",
  body,
  loading,
  error,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  tag?: string;
  body: string;
  loading?: boolean;
  error?: string | null;
}) {
  return (
    <Drawer open={open} onClose={onClose} title={title} tag={tag} loading={loading} error={error}>
      <Markdown content={body} />
    </Drawer>
  );
}
