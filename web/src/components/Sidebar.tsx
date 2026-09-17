"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  BrainCircuit,
  LayoutDashboard,
  MessagesSquare,
  Route,
  Network,
  CalendarClock,
  Briefcase,
  UserCog,
  Dna,
  Github,
  Workflow,
} from "lucide-react";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/", label: "Overview", icon: LayoutDashboard, subtitle: "System at a glance" },
  { href: "/chat", label: "Mentor Chat", icon: MessagesSquare, subtitle: "Multi-agent orchestration" },
  { href: "/flow", label: "Architecture", icon: Workflow, subtitle: "Live pipeline visualizer" },
  { href: "/roadmaps", label: "Roadmaps", icon: Route, subtitle: "Obsidian curriculum index" },
  { href: "/graph", label: "Topic Graph", icon: Network, subtitle: "Dependency DAG visualizer" },
  { href: "/schedule", label: "Schedule", icon: CalendarClock, subtitle: "48-slot day grid & events" },
  { href: "/jobs", label: "Job Hunt", icon: Briefcase, subtitle: "Pipeline, tailoring & follow-ups" },
  { href: "/profile", label: "Profile", icon: UserCog, subtitle: "Learner facts & identity" },
  { href: "/memory", label: "Memory DNA", icon: Dna, subtitle: "Inspect, correct, forget" },
] as const;

export function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();

  return (
    <aside className="h-full w-[248px] shrink-0 flex flex-col border-r border-[var(--color-border-soft)] bg-[var(--color-bg-elev)]/80 backdrop-blur">
      <div className="flex items-center gap-2.5 px-4 py-5">
        <div className="grid place-items-center h-9 w-9 rounded-[var(--radius)] bg-gradient-to-br from-[var(--color-accent)] to-[var(--color-accent-2)] text-[#08111f]">
          <BrainCircuit size={20} />
        </div>
        <div className="min-w-0">
          <h1
            className="text-[0.95rem] font-semibold leading-tight"
            style={{ fontFamily: "var(--font-display)" }}
          >
            AI Mentor
          </h1>
          <span className="text-[0.65rem] text-[var(--color-fg-dim)]">
            Local-first companion
          </span>
        </div>
      </div>

      <nav className="flex-1 px-2.5 space-y-1 overflow-y-auto">
        {NAV.map(({ href, label, icon: Icon }) => {
          const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
          return (
            <Link
              key={href}
              href={href}
              onClick={onNavigate}
              className={cn("nav-item", active && "active")}
            >
              <Icon size={17} />
              <span className="truncate">{label}</span>
            </Link>
          );
        })}
      </nav>

      <div className="px-2.5 py-3">
        <div className="glass rounded-[var(--radius)] px-3 py-2.5">
          <div className="flex items-center gap-2">
            <span className="h-2 w-2 rounded-full bg-[var(--color-success)] shadow-[0_0_8px_var(--color-success)]" />
            <span className="text-xs font-medium">Nebius LLM Tier</span>
          </div>
          <p className="text-[0.65rem] text-[var(--color-fg-dim)] mt-0.5">
            Qwen3-235B · MiniMax-M3
          </p>
        </div>
        <a
          href="https://github.com/nik-shar/Personal_AI-Mentor"
          target="_blank"
          rel="noopener noreferrer"
          className="nav-item mt-2 text-xs"
        >
          <Github size={15} />
          <span>Repository</span>
        </a>
      </div>
    </aside>
  );
}
