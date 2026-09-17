"use client";

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { Menu, X } from "lucide-react";
import { Sidebar } from "./Sidebar";
import { getHealth } from "@/lib/api";

const PAGE_META: Record<string, { title: string; subtitle: string }> = {
  "/": { title: "Overview", subtitle: "Your mentor, roadmaps, job hunt and memory at a glance" },
  "/chat": { title: "Mentor Chat", subtitle: "Multi-agent orchestration & real-time goal decomposition" },
  "/flow": { title: "Architecture", subtitle: "Watch the nodes & tools activate for each message" },
  "/roadmaps": { title: "Roadmaps", subtitle: "Obsidian vault curriculum index" },
  "/graph": { title: "Topic Graph", subtitle: "Interactive dependency DAG of a roadmap" },
  "/schedule": { title: "Schedule", subtitle: "Calendar events, day grid & free windows" },
  "/jobs": { title: "Job Hunt", subtitle: "Application pipeline, tailoring & follow-ups" },
  "/profile": { title: "Profile Facts", subtitle: "Learner identity, goals & habits" },
  "/memory": { title: "Memory DNA", subtitle: "Everything your mentor knows — inspect, correct, forget" },
};

function usePageMeta(pathname: string) {
  if (PAGE_META[pathname]) return PAGE_META[pathname];
  const match = Object.keys(PAGE_META)
    .filter((k) => k !== "/" && pathname.startsWith(k))
    .sort((a, b) => b.length - a.length)[0];
  return match ? PAGE_META[match] : { title: "AI Mentor", subtitle: "" };
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const meta = usePageMeta(pathname);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [online, setOnline] = useState<boolean | null>(null);

  useEffect(() => {
    let alive = true;
    getHealth()
      .then(() => alive && setOnline(true))
      .catch(() => alive && setOnline(false));
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    setMobileOpen(false);
  }, [pathname]);

  return (
    <div className="flex h-screen w-full overflow-hidden">
      {/* Desktop sidebar */}
      <div className="hidden md:flex">
        <Sidebar />
      </div>

      {/* Mobile sidebar */}
      {mobileOpen ? (
        <div className="fixed inset-0 z-40 md:hidden">
          <div className="absolute inset-0 bg-black/60" onClick={() => setMobileOpen(false)} />
          <div className="absolute left-0 top-0 h-full">
            <Sidebar onNavigate={() => setMobileOpen(false)} />
          </div>
        </div>
      ) : null}

      <main className="flex-1 flex flex-col min-w-0">
        <header className="flex items-center justify-between gap-4 px-5 py-4 border-b border-[var(--color-border-soft)] bg-[var(--color-bg)]/60 backdrop-blur">
          <div className="flex items-center gap-3 min-w-0">
            <button
              className="btn btn-ghost btn-sm md:hidden"
              onClick={() => setMobileOpen((v) => !v)}
              aria-label="Toggle navigation"
            >
              {mobileOpen ? <X size={18} /> : <Menu size={18} />}
            </button>
            <div className="min-w-0">
              <h2
                className="text-lg font-semibold leading-tight truncate"
                style={{ fontFamily: "var(--font-display)" }}
              >
                {meta.title}
              </h2>
              <p className="text-xs text-[var(--color-fg-muted)] truncate">{meta.subtitle}</p>
            </div>
          </div>

          <div className="flex items-center gap-3 shrink-0">
            <span
              className="badge"
              style={{
                color: online === false ? "var(--color-danger)" : "var(--color-success)",
                borderColor:
                  online === false ? "var(--color-danger)" : "var(--color-success)",
              }}
              title="FastAPI backend status"
            >
              <span
                className="h-1.5 w-1.5 rounded-full"
                style={{
                  background:
                    online === false ? "var(--color-danger)" : "var(--color-success)",
                }}
              />
              {online === null ? "Checking…" : online ? "Backend online" : "Backend offline"}
            </span>
            <div
              className="h-8 w-8 grid place-items-center rounded-full bg-gradient-to-br from-[var(--color-accent)] to-[var(--color-accent-2)] text-[#08111f] text-xs font-semibold"
              title="Nik"
            >
              NK
            </div>
          </div>
        </header>

        <div className="flex-1 overflow-y-auto px-5 py-5">{children}</div>
      </main>
    </div>
  );
}
