"use client";

import { useCallback, useEffect, useState } from "react";
import { CalendarClock, RefreshCw, Trash2, CheckCircle2, Clock } from "lucide-react";
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
import {
  deleteScheduleEvent,
  getAvailability,
  getSchedule,
  updateScheduleEvent,
} from "@/lib/api";
import type { AvailabilityResponse, ScheduleEvent } from "@/lib/types";
import { titleCase } from "@/lib/utils";

const todayISO = () => new Date().toISOString().slice(0, 10);

function slotColor(state: string): string {
  switch (state) {
    case "busy":
    case "event":
      return "var(--color-accent)";
    case "anchor":
      return "var(--color-accent-2)";
    case "free":
      return "var(--color-surface-2)";
    default:
      return "var(--color-border)";
  }
}

export default function SchedulePage() {
  const [date, setDate] = useState(todayISO());
  const [grid, setGrid] = useState<AvailabilityResponse | null>(null);
  const [events, setEvents] = useState<ScheduleEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [avail, sched] = await Promise.all([getAvailability(date), getSchedule({ date })]);
      setGrid(avail);
      setEvents(sched.events);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }, [date]);

  useEffect(() => {
    load();
  }, [load]);

  const completeEvent = async (id: string) => {
    setBusy(id);
    try {
      await updateScheduleEvent(id, { status: "completed" });
      await load();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const removeEvent = async (id: string) => {
    setBusy(id);
    try {
      await deleteScheduleEvent(id);
      await load();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const freeMinutes = grid?.free_minutes ?? 0;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-3">
        <input
          type="date"
          className="input !w-auto"
          value={date}
          onChange={(e) => setDate(e.target.value)}
        />
        <Button size="sm" variant="ghost" onClick={load}>
          <RefreshCw size={14} /> Refresh
        </Button>
      </div>

      {error ? <ErrorNotice message={error} onRetry={load} /> : null}

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <Stat label="Events" value={events.length} icon={<CalendarClock size={18} />} />
        <Stat
          label="Free time"
          value={`${Math.floor(freeMinutes / 60)}h ${freeMinutes % 60}m`}
          accent="var(--color-success)"
          icon={<Clock size={18} />}
        />
        <Stat label="Free windows" value={grid?.free_windows.length ?? 0} accent="var(--color-info)" />
        <Stat
          label="Completed"
          value={events.filter((e) => e.status === "completed").length}
          accent="var(--color-accent)"
        />
      </div>

      <div className="grid lg:grid-cols-[320px_1fr] gap-5">
        <Card>
          <CardHeader title="Day grid" subtitle="48 × 30-minute slots" icon={<Clock size={18} />} />
          {loading ? (
            <Spinner label="Reading the day grid…" />
          ) : !grid ? (
            <EmptyState title="No grid available" />
          ) : (
            <>
              <div className="grid grid-cols-6 gap-1 mb-3">
                {grid.slots.map((s) => (
                  <div
                    key={s.slot}
                    title={`${s.start} · ${s.state}${s.title ? ` · ${s.title}` : ""}`}
                    className="h-5 rounded-sm border border-[var(--color-border-soft)]"
                    style={{
                      background:
                        s.state === "free" ? "var(--color-surface-2)" : slotColor(s.state),
                    }}
                  />
                ))}
              </div>
              <div className="flex flex-wrap gap-2">
                <Badge color="var(--color-accent)">busy</Badge>
                <Badge color="var(--color-accent-2)">anchor</Badge>
                <Badge color="var(--color-fg-dim)">free</Badge>
              </div>
            </>
          )}
        </Card>

        <Card>
          <CardHeader title="Events" subtitle={date} icon={<CalendarClock size={18} />} />
          {loading ? (
            <Spinner />
          ) : events.length === 0 ? (
            <EmptyState
              icon={<CalendarClock size={26} />}
              title="No events scheduled"
              hint='Ask the mentor to "plan my day" and blocks will show here.'
            />
          ) : (
            <div className="space-y-2.5">
              {events.map((e) => (
                <div
                  key={e.id}
                  className="rounded-[var(--radius)] border border-[var(--color-border)] bg-[var(--color-bg-elev)] p-3 flex items-start justify-between gap-3"
                >
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-medium truncate">{e.title}</span>
                      <Badge
                        color={e.status === "completed" ? "var(--color-success)" : undefined}
                      >
                        {titleCase(e.status || "scheduled")}
                      </Badge>
                      {e.category ? <Badge>{e.category}</Badge> : null}
                    </div>
                    <p className="text-xs text-[var(--color-fg-muted)] mt-1">
                      {e.start_time ? new Date(e.start_time).toLocaleString() : "unscheduled"} ·{" "}
                      {e.duration_min ?? 30}m
                    </p>
                  </div>
                  <div className="flex gap-1.5 shrink-0">
                    {e.status !== "completed" ? (
                      <Button
                        size="sm"
                        variant="ghost"
                        disabled={busy === e.id}
                        onClick={() => completeEvent(e.id)}
                        title="Mark complete"
                      >
                        <CheckCircle2 size={15} />
                      </Button>
                    ) : null}
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={busy === e.id}
                      onClick={() => removeEvent(e.id)}
                      title="Delete event"
                    >
                      <Trash2 size={15} />
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
