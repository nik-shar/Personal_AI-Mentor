"use client";

import { useCallback, useEffect, useState } from "react";
import { UserCog, PencilLine, Trash2, Save, X, Braces, Database } from "lucide-react";
import {
  Badge,
  Button,
  Card,
  CardHeader,
  EmptyState,
  ErrorNotice,
  Spinner,
} from "@/components/ui";
import {
  deleteProfileFact,
  getProfile,
  getProfileFacts,
  updateProfileFact,
} from "@/lib/api";
import type { ProfileFactRow } from "@/lib/types";
import { relativeTime } from "@/lib/utils";

export default function ProfilePage() {
  const [facts, setFacts] = useState<ProfileFactRow[]>([]);
  const [raw, setRaw] = useState<Record<string, unknown>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [editValue, setEditValue] = useState("");
  const [showRaw, setShowRaw] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [factsRes, profileRes] = await Promise.all([
        getProfileFacts(),
        getProfile().catch(() => ({ profile: {} })),
      ]);
      const sorted = [...factsRes.facts].sort((a, b) =>
        `${a.category}.${a.key}`.localeCompare(`${b.category}.${b.key}`),
      );
      setFacts(sorted);
      setRaw(profileRes.profile || {});
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const startEdit = (row: ProfileFactRow) => {
    setEditingKey(row.key);
    setEditValue(
      typeof row.value === "string" ? row.value : JSON.stringify(row.value, null, 2),
    );
  };

  const saveEdit = async () => {
    if (!editingKey) return;
    let parsed: unknown = editValue;
    try {
      parsed = JSON.parse(editValue);
    } catch {
      /* keep as plain string */
    }
    try {
      await updateProfileFact(editingKey, parsed);
      setEditingKey(null);
      await load();
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const removeFact = async (key: string) => {
    try {
      await deleteProfileFact(key);
      await load();
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const renderValue = (value: unknown) => {
    if (value === null || value === undefined) return "—";
    if (typeof value === "object") {
      const s = JSON.stringify(value);
      return s.length > 220 ? s.slice(0, 219) + "…" : s;
    }
    const s = String(value);
    return s.length > 220 ? s.slice(0, 219) + "…" : s;
  };

  return (
    <div className="space-y-5">
      {error ? <ErrorNotice message={error} onRetry={load} /> : null}

      <Card>
        <CardHeader
          title="Profile facts"
          subtitle="Structured, code-read keys (goals, roles, skills, preferences…)"
          icon={<Database size={18} />}
          action={
            <Button size="sm" variant="ghost" onClick={() => setShowRaw((v) => !v)}>
              <Braces size={14} /> {showRaw ? "Hide raw" : "Raw profile"}
            </Button>
          }
        />

        {loading ? (
          <Spinner label="Loading profile facts…" />
        ) : facts.length === 0 ? (
          <EmptyState
            icon={<UserCog size={28} />}
            title="No profile facts yet"
            hint="The mentor learns these through conversation."
          />
        ) : (
          <div className="space-y-2">
            {facts.map((row) => {
              const isEditing = editingKey === row.key;
              return (
                <div
                  key={`${row.category}.${row.key}`}
                  className="rounded-[var(--radius)] border border-[var(--color-border)] bg-[var(--color-bg-elev)] p-3"
                >
                  <div className="flex flex-wrap items-center gap-2 mb-1.5">
                    <Badge color="var(--color-accent)">{row.category}</Badge>
                    <span className="text-sm font-medium">{row.key}</span>
                    {row.source ? <Badge className="ml-auto">{row.source}</Badge> : null}
                    <span className="text-[0.65rem] text-[var(--color-fg-dim)]">
                      {relativeTime(row.updated_at)}
                    </span>
                  </div>

                  {isEditing ? (
                    <div className="space-y-2">
                      <textarea
                        className="input font-mono text-xs min-h-[80px] resize-y"
                        value={editValue}
                        onChange={(e) => setEditValue(e.target.value)}
                      />
                      <div className="flex gap-2">
                        <Button size="sm" variant="primary" onClick={saveEdit}>
                          <Save size={14} /> Save
                        </Button>
                        <Button size="sm" variant="ghost" onClick={() => setEditingKey(null)}>
                          <X size={14} /> Cancel
                        </Button>
                      </div>
                    </div>
                  ) : (
                    <div className="flex items-start gap-3">
                      <pre className="text-xs text-[var(--color-fg-muted)] whitespace-pre-wrap break-words flex-1 font-mono">
                        {renderValue(row.value)}
                      </pre>
                      <div className="flex gap-1.5 shrink-0">
                        <Button size="sm" variant="ghost" onClick={() => startEdit(row)}>
                          <PencilLine size={14} />
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => removeFact(row.key)}
                          title="Delete fact"
                        >
                          <Trash2 size={14} />
                        </Button>
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </Card>

      {showRaw ? (
        <Card>
          <CardHeader
            title="Raw /api/profile"
            subtitle="Full nested profile object"
            icon={<Braces size={18} />}
          />
          <pre className="text-xs font-mono whitespace-pre-wrap break-words max-h-[420px] overflow-auto text-[var(--color-fg-muted)]">
            {JSON.stringify(raw, null, 2)}
          </pre>
        </Card>
      ) : null}
    </div>
  );
}
