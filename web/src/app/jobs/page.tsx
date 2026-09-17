"use client";

import { useCallback, useEffect, useState } from "react";
import { Briefcase, Plus, Trash2, FileText, Link2, StickyNote, History, ExternalLink, X } from "lucide-react";
import {
  Badge,
  Button,
  Card,
  CardHeader,
  EmptyState,
  ErrorNotice,
  Spinner,
  Stat,
} from "@/components/ui";
import { Drawer } from "@/components/Drawer";
import { Markdown } from "@/components/Markdown";
import {
  addApplicationNote,
  attachJobDescription,
  createApplication,
  deleteApplication,
  getApplicationHistory,
  getApplications,
  getArtifact,
  updateApplicationStage,
} from "@/lib/api";
import type { Application, PipelineStats } from "@/lib/types";
import { formatDate, relativeTime, stageColor, stageLabel, STAGE_ORDER } from "@/lib/utils";

type HistoryEvent = {
  id: string;
  occurred_at: string;
  event_type: string;
  content: string;
};

export default function JobsPage() {
  const [apps, setApps] = useState<Application[]>([]);
  const [stats, setStats] = useState<PipelineStats | null>(null);
  const [history, setHistory] = useState<HistoryEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const [showAdd, setShowAdd] = useState(false);
  const [jdTarget, setJdTarget] = useState<Application | null>(null);
  const [jdText, setJdText] = useState("");
  const [artifact, setArtifact] = useState<{ open: boolean; title: string; body: string }>({
    open: false,
    title: "",
    body: "",
  });
  const [artifactLoading, setArtifactLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [appsRes, histRes] = await Promise.all([
        getApplications(),
        getApplicationHistory().catch(() => ({ count: 0, events: [] })),
      ]);
      setApps(appsRes.applications);
      setStats(appsRes.stats);
      setHistory(histRes.events);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const keyOf = (a: Application) => `${a.company}::${a.role_title || ""}`;

  const changeStage = async (a: Application, stage: string) => {
    setBusy(keyOf(a));
    try {
      await updateApplicationStage(a.company, a.role_title || null, stage);
      await load();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const note = async (a: Application) => {
    const text = window.prompt(`Add a note for ${a.company} (${a.role_title || "role"}):`);
    if (!text || !text.trim()) return;
    setBusy(keyOf(a));
    try {
      await addApplicationNote(a.company, a.role_title || null, text.trim());
      await load();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const remove = async (a: Application) => {
    if (!window.confirm(`Remove ${a.company} (${a.role_title || "role"}) from the board?`)) return;
    setBusy(keyOf(a));
    try {
      await deleteApplication(a.company, a.role_title || undefined);
      await load();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const submitJd = async () => {
    if (!jdTarget || !jdText.trim()) return;
    setBusy(keyOf(jdTarget));
    try {
      await attachJobDescription(jdTarget.company, jdTarget.role_title || null, jdText.trim());
      setJdTarget(null);
      setJdText("");
      await load();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const viewArtifact = async (a: Application, kind: "jd" | "resume" | "fit") => {
    setArtifact({ open: true, title: `${stageLabel(a.stage)} · ${a.company}`, body: "" });
    setArtifactLoading(true);
    try {
      const res = await getArtifact(a.company, a.role_title || "Role", kind);
      setArtifact({ open: true, title: res.filename, body: res.body });
    } catch (err) {
      setArtifact({ open: true, title: "Artifact", body: `_Could not load: ${(err as Error).message}_` });
    } finally {
      setArtifactLoading(false);
    }
  };

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-xs text-[var(--color-fg-dim)]">
          <Briefcase size={14} /> Pipeline board
        </div>
        <Button size="sm" variant="primary" onClick={() => setShowAdd(true)}>
          <Plus size={15} /> Add application
        </Button>
      </div>

      {error ? <ErrorNotice message={error} onRetry={load} /> : null}

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <Stat label="Total" value={stats?.total ?? 0} icon={<Briefcase size={18} />} />
        <Stat label="Active" value={stats?.active ?? 0} accent="var(--color-info)" />
        <Stat label="Stale" value={stats?.stale ?? 0} accent="var(--color-warning)" />
        <Stat
          label="Response rate"
          value={`${stats?.response_rate ?? 0}%`}
          accent="var(--color-success)"
        />
      </div>

      <Card className="!p-3">
        {loading ? (
          <Spinner label="Loading job pipeline…" />
        ) : apps.length === 0 ? (
          <EmptyState
            icon={<Briefcase size={28} />}
            title="Your job board is empty"
            hint='Ask the mentor "find me AI Engineer jobs", or add one manually.'
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm border-collapse">
              <thead>
                <tr className="text-left text-xs uppercase tracking-wide text-[var(--color-fg-dim)]">
                  <th className="px-3 py-2">Company</th>
                  <th className="px-3 py-2">Role</th>
                  <th className="px-3 py-2">Stage</th>
                  <th className="px-3 py-2">Updated</th>
                  <th className="px-3 py-2 text-right">Actions</th>
                </tr>
              </thead>
              <tbody>
                {apps.map((a) => {
                  const k = keyOf(a);
                  return (
                    <tr
                      key={k}
                      className="border-t border-[var(--color-border-soft)] hover:bg-[var(--color-surface-2)]/40"
                    >
                      <td className="px-3 py-2.5">
                        <div className="flex items-center gap-2">
                          <span className="font-medium">{a.company}</span>
                          {a.is_stale ? (
                            <Badge color="var(--color-warning)">stale</Badge>
                          ) : null}
                        </div>
                      </td>
                      <td className="px-3 py-2.5 text-[var(--color-fg-muted)]">
                        {a.role_title || "—"}
                      </td>
                      <td className="px-3 py-2.5">
                        <select
                          className="input !w-auto !py-1 text-xs"
                          value={a.stage}
                          disabled={busy === k}
                          onChange={(e) => changeStage(a, e.target.value)}
                          style={{ color: stageColor(a.stage) }}
                        >
                          {STAGE_ORDER.map((s) => (
                            <option key={s} value={s}>
                              {stageLabel(s)}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td className="px-3 py-2.5 text-[var(--color-fg-muted)] text-xs">
                        {a.days_since_update != null
                          ? `${a.days_since_update}d ago`
                          : formatDate(a.applied_date)}
                      </td>
                      <td className="px-3 py-2.5">
                        <div className="flex items-center justify-end gap-1">
                          {a.job_url ? (
                            <a
                              className="btn btn-ghost btn-sm"
                              href={a.job_url}
                              target="_blank"
                              rel="noopener noreferrer"
                              title="Open posting"
                            >
                              <ExternalLink size={14} />
                            </a>
                          ) : null}
                          <Button
                            size="sm"
                            variant="ghost"
                            disabled={busy === k}
                            onClick={() => note(a)}
                            title="Add note"
                          >
                            <StickyNote size={14} />
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            disabled={busy === k}
                            onClick={() => {
                              setJdTarget(a);
                              setJdText("");
                            }}
                            title="Attach JD"
                          >
                            <Link2 size={14} />
                          </Button>
                          {a.has_tailored_resume ? (
                            <Button
                              size="sm"
                              variant="ghost"
                              onClick={() => viewArtifact(a, "resume")}
                              title="View tailored resume"
                            >
                              <FileText size={14} />
                            </Button>
                          ) : null}
                          <Button
                            size="sm"
                            variant="ghost"
                            disabled={busy === k}
                            onClick={() => remove(a)}
                            title="Remove"
                          >
                            <Trash2 size={14} />
                          </Button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card>
        <CardHeader
          title="Timeline"
          subtitle="Append-only application history"
          icon={<History size={18} />}
        />
        {history.length === 0 ? (
          <EmptyState title="No history yet" />
        ) : (
          <div className="space-y-2.5 max-h-[320px] overflow-y-auto">
            {history.map((h) => (
              <div key={h.id} className="flex gap-3 text-sm">
                <span className="text-[0.7rem] text-[var(--color-fg-dim)] w-20 shrink-0 pt-0.5">
                  {relativeTime(h.occurred_at)}
                </span>
                <span className="text-[var(--color-fg-muted)]">{h.content}</span>
              </div>
            ))}
          </div>
        )}
      </Card>

      {showAdd ? <AddApplicationModal onClose={() => setShowAdd(false)} onSaved={load} /> : null}

      {jdTarget ? (
        <Modal onClose={() => setJdTarget(null)} title={`Attach JD — ${jdTarget.company}`}>
          <textarea
            className="input min-h-[200px] resize-y font-mono text-xs"
            placeholder="Paste the full job description…"
            value={jdText}
            onChange={(e) => setJdText(e.target.value)}
            autoFocus
          />
          <div className="flex justify-end gap-2 mt-4">
            <Button variant="ghost" onClick={() => setJdTarget(null)}>
              Cancel
            </Button>
            <Button variant="primary" loading={busy === keyOf(jdTarget)} onClick={submitJd}>
              Attach JD
            </Button>
          </div>
        </Modal>
      ) : null}

      <Drawer
        open={artifact.open}
        onClose={() => setArtifact({ open: false, title: "", body: "" })}
        title={artifact.title}
        tag="Vault artifact"
        loading={artifactLoading}
      >
        <Markdown content={artifact.body} />
      </Drawer>
    </div>
  );
}

function Modal({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/60 p-4" onClick={onClose}>
      <div className="card w-full max-w-xl p-5" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-base font-semibold" style={{ fontFamily: "var(--font-display)" }}>
            {title}
          </h3>
          <button className="btn btn-ghost btn-sm" onClick={onClose} aria-label="Close">
            <X size={16} />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

function AddApplicationModal({
  onClose,
  onSaved,
}: {
  onClose: () => void;
  onSaved: () => void;
}) {
  const [form, setForm] = useState({
    company: "",
    role_title: "",
    stage: "applied",
    applied_date: "",
    job_url: "",
    location: "",
    referral_contact: "",
    notes: "",
    jd_text: "",
  });
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const set =
    (k: keyof typeof form) =>
    (
      e: React.ChangeEvent<
        HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement
      >,
    ) =>
      setForm((f) => ({ ...f, [k]: e.target.value }));

  const save = async () => {
    if (!form.company.trim() || !form.role_title.trim()) {
      setErr("Company and role are required.");
      return;
    }
    setSaving(true);
    setErr(null);
    try {
      await createApplication({
        ...form,
        applied_date: form.applied_date || null,
        job_url: form.job_url || null,
        location: form.location || null,
        referral_contact: form.referral_contact || null,
        notes: form.notes || null,
        jd_text: form.jd_text || null,
      });
      onSaved();
      onClose();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal onClose={onClose} title="Add application">
      <div className="space-y-3 max-h-[65vh] overflow-y-auto pr-1">
        <div className="grid grid-cols-2 gap-3">
          <label className="text-xs text-[var(--color-fg-muted)]">
            Company *
            <input className="input mt-1" value={form.company} onChange={set("company")} autoFocus />
          </label>
          <label className="text-xs text-[var(--color-fg-muted)]">
            Role *
            <input className="input mt-1" value={form.role_title} onChange={set("role_title")} />
          </label>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <label className="text-xs text-[var(--color-fg-muted)]">
            Stage
            <select className="input mt-1" value={form.stage} onChange={set("stage")}>
              {STAGE_ORDER.map((s) => (
                <option key={s} value={s}>
                  {stageLabel(s)}
                </option>
              ))}
            </select>
          </label>
          <label className="text-xs text-[var(--color-fg-muted)]">
            Applied date
            <input
              type="date"
              className="input mt-1"
              value={form.applied_date}
              onChange={set("applied_date")}
            />
          </label>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <label className="text-xs text-[var(--color-fg-muted)]">
            Job URL
            <input className="input mt-1" value={form.job_url} onChange={set("job_url")} />
          </label>
          <label className="text-xs text-[var(--color-fg-muted)]">
            Location
            <input className="input mt-1" value={form.location} onChange={set("location")} />
          </label>
        </div>
        <label className="text-xs text-[var(--color-fg-muted)] block">
          Referral contact
          <input
            className="input mt-1"
            value={form.referral_contact}
            onChange={set("referral_contact")}
          />
        </label>
        <label className="text-xs text-[var(--color-fg-muted)] block">
          Job description
          <textarea
            className="input mt-1 min-h-[100px] resize-y"
            value={form.jd_text}
            onChange={set("jd_text")}
          />
        </label>
        <label className="text-xs text-[var(--color-fg-muted)] block">
          Notes
          <textarea
            className="input mt-1 min-h-[60px] resize-y"
            value={form.notes}
            onChange={set("notes")}
          />
        </label>
        {err ? <p className="text-xs text-[var(--color-danger)]">{err}</p> : null}
      </div>
      <div className="flex justify-end gap-2 mt-4">
        <Button variant="ghost" onClick={onClose}>
          Cancel
        </Button>
        <Button variant="primary" loading={saving} onClick={save}>
          Add to pipeline
        </Button>
      </div>
    </Modal>
  );
}
