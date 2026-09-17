"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Dna, ShieldAlert, Filter, Check, PencilLine, Trash2 } from "lucide-react";
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
  confirmMemory,
  correctMemory,
  deleteMemory,
  getMemories,
  getPendingMemories,
} from "@/lib/api";
import type { DNAMemory } from "@/lib/types";
import { confidenceMeta, formatDate, titleCase } from "@/lib/utils";

const TYPES = ["fact", "observation", "insight", "preference", "goal", "reflection", "context"];
const SOURCES = ["user_stated", "mentor_inferred", "data_derived", "seeded"];

export default function MemoryPage() {
  const [all, setAll] = useState<DNAMemory[]>([]);
  const [pending, setPending] = useState<DNAMemory[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [typeFilter, setTypeFilter] = useState("");
  const [sourceFilter, setSourceFilter] = useState("");
  const [correcting, setCorrecting] = useState<DNAMemory | null>(null);
  const [correctionText, setCorrectionText] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [allRes, pendingRes] = await Promise.all([
        getMemories({
          type: typeFilter || undefined,
          source: sourceFilter || undefined,
        }),
        getPendingMemories(),
      ]);
      setAll(allRes.memories);
      setPending(pendingRes.memories);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }, [typeFilter, sourceFilter]);

  useEffect(() => {
    load();
  }, [load]);

  const stats = useMemo(() => {
    const unconfirmed = all.filter((m) => !m.user_confirmed).length;
    const inferred = all.filter((m) => m.source === "mentor_inferred").length;
    const inferredUnconfirmed = all.filter(
      (m) => m.source === "mentor_inferred" && !m.user_confirmed,
    ).length;
    return { total: all.length, unconfirmed, inferred, inferredUnconfirmed };
  }, [all]);

  const act = async (id: string, fn: () => Promise<unknown>) => {
    setBusyId(id);
    try {
      await fn();
      await load();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusyId(null);
    }
  };

  const submitCorrection = async () => {
    if (!correcting || !correctionText.trim()) return;
    await act(correcting.id, () =>
      correctMemory(correcting.id, correctionText.trim(), "user correction"),
    );
    setCorrecting(null);
    setCorrectionText("");
  };

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatMini label="Total memories" value={stats.total} />
        <StatMini label="Unconfirmed" value={stats.unconfirmed} accent="var(--color-warning)" />
        <StatMini label="Mentor-inferred" value={stats.inferred} accent="var(--color-accent-2)" />
        <StatMini
          label="Inferred · unconfirmed"
          value={stats.inferredUnconfirmed}
          accent="var(--color-danger)"
        />
      </div>

      {error ? <ErrorNotice message={error} onRetry={load} /> : null}

      {pending.length > 0 ? (
        <Card>
          <CardHeader
            title="Worth validating"
            subtitle="Unconfirmed inferences — confirm to unlock trust, or correct"
            icon={<ShieldAlert size={18} />}
          />
          <div className="space-y-3">
            {pending.map((m) => (
              <MemoryCard
                key={m.id}
                memory={m}
                busy={busyId === m.id}
                onConfirm={() => act(m.id, () => confirmMemory(m.id))}
                onCorrect={() => {
                  setCorrecting(m);
                  setCorrectionText(m.content);
                }}
                onDelete={() => act(m.id, () => deleteMemory(m.id))}
              />
            ))}
          </div>
        </Card>
      ) : null}

      <Card>
        <CardHeader
          title="All memories"
          subtitle="Everything your mentor believes about you"
          icon={<Dna size={18} />}
          action={
            <div className="flex items-center gap-2">
              <Filter size={14} className="text-[var(--color-fg-dim)]" />
              <select
                className="input !w-auto !py-1.5 text-xs"
                value={typeFilter}
                onChange={(e) => setTypeFilter(e.target.value)}
              >
                <option value="">all types</option>
                {TYPES.map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </select>
              <select
                className="input !w-auto !py-1.5 text-xs"
                value={sourceFilter}
                onChange={(e) => setSourceFilter(e.target.value)}
              >
                <option value="">all sources</option>
                {SOURCES.map((s) => (
                  <option key={s} value={s}>
                    {titleCase(s)}
                  </option>
                ))}
              </select>
            </div>
          }
        />
        {loading ? (
          <Spinner label="Loading memories…" />
        ) : all.length === 0 ? (
          <EmptyState
            icon={<Dna size={28} />}
            title="No memories yet"
            hint="They form as you chat with your mentor."
          />
        ) : (
          <div className="space-y-3">
            {all.map((m) => (
              <MemoryCard
                key={m.id}
                memory={m}
                busy={busyId === m.id}
                onConfirm={
                  m.user_confirmed ? undefined : () => act(m.id, () => confirmMemory(m.id))
                }
                onCorrect={() => {
                  setCorrecting(m);
                  setCorrectionText(m.content);
                }}
                onDelete={() => act(m.id, () => deleteMemory(m.id))}
              />
            ))}
          </div>
        )}
      </Card>

      {correcting ? (
        <CorrectModal
          memory={correcting}
          text={correctionText}
          onChange={setCorrectionText}
          onCancel={() => setCorrecting(null)}
          onSave={submitCorrection}
        />
      ) : null}
    </div>
  );
}

function StatMini({
  label,
  value,
  accent = "var(--color-fg)",
}: {
  label: string;
  value: number;
  accent?: string;
}) {
  return (
    <div className="card p-4">
      <div className="text-xs uppercase tracking-wide text-[var(--color-fg-dim)]">{label}</div>
      <div className="text-2xl font-semibold mt-1" style={{ color: accent }}>
        {value}
      </div>
    </div>
  );
}

function MemoryCard({
  memory,
  busy,
  onConfirm,
  onCorrect,
  onDelete,
}: {
  memory: DNAMemory;
  busy: boolean;
  onConfirm?: () => void;
  onCorrect: () => void;
  onDelete: () => void;
}) {
  const conf = confidenceMeta(memory.confidence, memory.user_confirmed);
  return (
    <div className="rounded-[var(--radius)] border border-[var(--color-border)] bg-[var(--color-bg-elev)] p-3.5">
      <div className="flex flex-wrap items-center gap-2 mb-2">
        <Badge>{memory.memory_type}</Badge>
        <Badge>{titleCase(memory.source)}</Badge>
        <Badge color={conf.color}>
          {conf.label}
          {memory.user_confirmed ? "" : " · unconfirmed"}
        </Badge>
        {memory.due_at ? (
          <Badge color="var(--color-warning)">⏰ {formatDate(memory.due_at)}</Badge>
        ) : null}
        <span className="text-[0.65rem] text-[var(--color-fg-dim)] ml-auto">
          conf {memory.confidence.toFixed(2)}
          {memory.confirmation_count > 1 ? ` · ×${memory.confirmation_count}` : ""}
        </span>
      </div>
      <p className="text-sm leading-relaxed">{memory.content}</p>
      <div className="flex flex-wrap gap-2 mt-3">
        {onConfirm ? (
          <Button size="sm" variant="secondary" disabled={busy} onClick={onConfirm}>
            <Check size={14} /> Confirm
          </Button>
        ) : null}
        <Button size="sm" variant="secondary" disabled={busy} onClick={onCorrect}>
          <PencilLine size={14} /> Correct
        </Button>
        <Button size="sm" variant="danger" disabled={busy} onClick={onDelete}>
          <Trash2 size={14} /> Forget
        </Button>
      </div>
    </div>
  );
}

function CorrectModal({
  memory,
  text,
  onChange,
  onCancel,
  onSave,
}: {
  memory: DNAMemory;
  text: string;
  onChange: (v: string) => void;
  onCancel: () => void;
  onSave: () => void;
}) {
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/60 p-4" onClick={onCancel}>
      <div className="card w-full max-w-lg p-5" onClick={(e) => e.stopPropagation()}>
        <h3 className="text-base font-semibold mb-1" style={{ fontFamily: "var(--font-display)" }}>
          Correct this memory
        </h3>
        <p className="text-xs text-[var(--color-fg-muted)] mb-3">
          Your correction supersedes the old belief (it stays in the audit trail).
        </p>
        <textarea
          className="input min-h-[120px] resize-y"
          value={text}
          onChange={(e) => onChange(e.target.value)}
          autoFocus
        />
        <div className="flex justify-end gap-2 mt-4">
          <Button variant="ghost" onClick={onCancel}>
            Cancel
          </Button>
          <Button variant="primary" onClick={onSave} disabled={!text.trim()}>
            Save correction
          </Button>
        </div>
      </div>
    </div>
  );
}
