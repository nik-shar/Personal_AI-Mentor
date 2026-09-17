"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  Route,
  Briefcase,
  Dna,
  CalendarClock,
  ArrowRight,
  MessagesSquare,
  Network,
  TrendingUp,
} from "lucide-react";
import { Card, CardHeader, ErrorNotice, Spinner, Stat } from "@/components/ui";
import {
  getApplications,
  getHealth,
  getMemories,
  getRoadmaps,
  getSchedule,
} from "@/lib/api";
import type { HealthResponse } from "@/lib/types";

interface Overview {
  health: HealthResponse | null;
  roadmaps: number;
  completedTopics: number;
  totalTopics: number;
  activeApplications: number;
  responseRate: number;
  memories: number;
  pendingMemories: number;
  todayEvents: number;
}

export default function OverviewPage() {
  const [data, setData] = useState<Overview | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    setError(null);
    try {
      const [health, roadmaps, apps, memories, schedule] = await Promise.all([
        getHealth().catch(() => null),
        getRoadmaps().catch(() => ({ roadmaps: [], vault_path: "" })),
        getApplications().catch(() => null),
        getMemories().catch(() => ({ count: 0, memories: [] })),
        getSchedule().catch(() => ({ count: 0, events: [] })),
      ]);

      const completedTopics = roadmaps.roadmaps.reduce((s, r) => s + r.completed, 0);
      const totalTopics = roadmaps.roadmaps.reduce((s, r) => s + r.total_nodes, 0);
      const pending = memories.memories.filter(
        (m) => !m.user_confirmed && m.source === "mentor_inferred",
      ).length;

      setData({
        health,
        roadmaps: roadmaps.roadmaps.length,
        completedTopics,
        totalTopics,
        activeApplications: apps?.stats?.active ?? 0,
        responseRate: apps?.stats?.response_rate ?? 0,
        memories: memories.count,
        pendingMemories: pending,
        todayEvents: schedule.count,
      });
    } catch (err) {
      setError((err as Error).message);
    }
  };

  useEffect(() => {
    load();
  }, []);

  if (error) return <ErrorNotice message={error} onRetry={load} />;
  if (!data) return <Spinner label="Gathering your dashboard…" />;

  return (
    <div className="space-y-5">
      <div className="glass rounded-[var(--radius-xl)] p-6">
        <h1 className="text-2xl font-bold" style={{ fontFamily: "var(--font-display)" }}>
          Welcome back, Nik.
        </h1>
        <p className="text-sm text-[var(--color-fg-muted)] mt-1 max-w-2xl">
          Your mentor keeps a persistent picture of your learning, job hunt and
          memory. Start a conversation, review a roadmap, or audit what it knows
          about you.
        </p>
        <div className="flex flex-wrap gap-2 mt-4">
          <Link href="/chat" className="btn btn-primary btn-sm">
            <MessagesSquare size={15} /> Start chatting
          </Link>
          <Link href="/memory" className="btn btn-secondary btn-sm">
            <Dna size={15} /> Review memory
          </Link>
          <Link href="/jobs" className="btn btn-secondary btn-sm">
            <Briefcase size={15} /> Job board
          </Link>
        </div>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <Stat
          label="Roadmaps"
          value={data.roadmaps}
          hint={`${data.completedTopics}/${data.totalTopics} topics complete`}
          icon={<Route size={18} />}
        />
        <Stat
          label="Active applications"
          value={data.activeApplications}
          hint={`${data.responseRate}% response rate`}
          accent="var(--color-info)"
          icon={<Briefcase size={18} />}
        />
        <Stat
          label="Memories"
          value={data.memories}
          hint={`${data.pendingMemories} awaiting validation`}
          accent="var(--color-accent-2)"
          icon={<Dna size={18} />}
        />
        <Stat
          label="Scheduled events"
          value={data.todayEvents}
          hint={data.health?.obsidian_vault ? "Vault connected" : "Vault not configured"}
          accent="var(--color-warning)"
          icon={<CalendarClock size={18} />}
        />
      </div>

      <div className="grid lg:grid-cols-2 gap-5">
        <Card>
          <CardHeader
            title="Learning"
            subtitle="Roadmaps live as markdown in your Obsidian vault"
            icon={<Route size={18} />}
            action={
              <Link href="/roadmaps" className="btn btn-ghost btn-sm">
                Open <ArrowRight size={14} />
              </Link>
            }
          />
          <div className="space-y-3">
            <div className="flex items-center justify-between text-sm">
              <span className="text-[var(--color-fg-muted)]">Topics completed</span>
              <span className="font-semibold">
                {data.completedTopics} / {data.totalTopics}
              </span>
            </div>
            <div className="h-2 rounded-full bg-[var(--color-surface-2)] overflow-hidden">
              <div
                className="h-full rounded-full bg-gradient-to-r from-[var(--color-accent)] to-[var(--color-accent-2)]"
                style={{
                  width: `${
                    data.totalTopics
                      ? Math.round((data.completedTopics / data.totalTopics) * 100)
                      : 0
                  }%`,
                }}
              />
            </div>
            <Link href="/graph" className="btn btn-secondary btn-sm w-full">
              <Network size={15} /> Visualize dependency graph
            </Link>
          </div>
        </Card>

        <Card>
          <CardHeader
            title="Job Hunt"
            subtitle="Pipeline state, refreshes and follow-ups"
            icon={<TrendingUp size={18} />}
            action={
              <Link href="/jobs" className="btn btn-ghost btn-sm">
                Open <ArrowRight size={14} />
              </Link>
            }
          />
          <div className="space-y-3 text-sm">
            <div className="flex items-center justify-between">
              <span className="text-[var(--color-fg-muted)]">Active</span>
              <span className="font-semibold">{data.activeApplications}</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-[var(--color-fg-muted)]">Response rate</span>
              <span className="font-semibold">{data.responseRate}%</span>
            </div>
            <p className="text-xs text-[var(--color-fg-dim)]">
              Ask the mentor: “find me AI Engineer jobs” or paste a JD to tailor
              your resume.
            </p>
          </div>
        </Card>
      </div>
    </div>
  );
}
