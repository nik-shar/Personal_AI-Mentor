"use client";

import { useEffect, useRef, useState } from "react";
import { Bot, User, Send, Plus, Sparkles, Workflow } from "lucide-react";
import { Markdown } from "@/components/Markdown";
import { Button } from "@/components/ui";
import { endSession, sendChat } from "@/lib/api";
import { cn } from "@/lib/utils";

interface ChatMessage {
  id: string;
  role: "user" | "mentor";
  content: string;
  pipeline?: string[];
  error?: boolean;
}

const CHIPS: { label: string; prompt: string; fill?: boolean }[] = [
  { label: "💡 3-Day Rust Roadmap", prompt: "Create a 3-day roadmap for Rust programming" },
  { label: "📅 Plan My Day (6h)", prompt: "Plan my day for 6 hours focusing on FastAPI and SQL" },
  { label: "🗺️ List Vault Roadmaps", prompt: "Show my obsidian roadmaps" },
  { label: "💼 Job Hunt Status", prompt: "How is my job hunt going?" },
  {
    label: "📄 Tailor Resume to a JD",
    prompt: "Tailor my resume for this job:\n\n[paste the job description here]",
    fill: true,
  },
];

let idCounter = 0;
const nextId = () => `m${Date.now()}-${idCounter++}`;

export default function ChatPage() {
  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      id: "welcome",
      role: "mentor",
      content:
        "Welcome back, Nik. I'm your persistent mentor — I can decompose learning goals into Obsidian roadmaps, generate deep tutorial notes, plan your day, or work your job hunt with you.\n\n**What would you like to focus on today?**",
    },
  ]);
  const [input, setInput] = useState("");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [status] = useState("Reasoning & routing turn…");
  const threadRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    threadRef.current?.scrollTo({
      top: threadRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [messages, sending]);

  const submit = async (raw?: string) => {
    const message = (raw ?? input).trim();
    if (!message || sending) return;
    setInput("");
    setSending(true);
    setMessages((prev) => [...prev, { id: nextId(), role: "user", content: message }]);

    try {
      const res = await sendChat(message, sessionId);
      setSessionId(res.session_id);
      setMessages((prev) => [
        ...prev,
        {
          id: nextId(),
          role: "mentor",
          content: res.response_text || "(no response)",
          pipeline: res.agent_pipeline,
        },
      ]);
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        {
          id: nextId(),
          role: "mentor",
          content: `⚠️ **Communication error** — ${(err as Error).message}`,
          error: true,
        },
      ]);
    } finally {
      setSending(false);
    }
  };

  const newSession = async () => {
    if (sessionId) {
      try {
        await endSession(sessionId);
      } catch {
        /* session may already be gone */
      }
    }
    setSessionId(null);
    setMessages([
      {
        id: nextId(),
        role: "mentor",
        content: "Fresh session started. What's on your mind?",
      },
    ]);
    inputRef.current?.focus();
  };

  return (
    <div className="flex flex-col h-[calc(100vh-8.5rem)]">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2 text-xs text-[var(--color-fg-dim)]">
          <Workflow size={14} />
          {sessionId ? `session · ${sessionId.slice(0, 8)}` : "no active session"}
        </div>
        <Button size="sm" variant="secondary" onClick={newSession}>
          <Plus size={14} /> New session
        </Button>
      </div>

      <div ref={threadRef} className="flex-1 overflow-y-auto space-y-5 pr-1 pb-4">
        {messages.map((m) => (
          <MessageBubble key={m.id} message={m} />
        ))}
        {sending ? <TypingRow status={status} /> : null}
      </div>

      <div className="pt-3 border-t border-[var(--color-border-soft)]">
        <div className="flex flex-wrap gap-2 mb-2.5">
          {CHIPS.map((c) => (
            <button
              key={c.label}
              className="badge hover:border-[var(--color-accent)] hover:text-[var(--color-accent)] transition-colors"
              onClick={() => {
                if (c.fill) {
                  setInput(c.prompt);
                  inputRef.current?.focus();
                } else {
                  submit(c.prompt);
                }
              }}
            >
              {c.label}
            </button>
          ))}
        </div>

        <form
          onSubmit={(e) => {
            e.preventDefault();
            submit();
          }}
          className="flex items-end gap-2"
        >
          <textarea
            ref={inputRef}
            className="input resize-none min-h-[48px] max-h-40"
            rows={1}
            placeholder="Ask your mentor anything…  (Shift+Enter for a new line)"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }}
          />
          <Button type="submit" variant="primary" loading={sending} className="h-[48px] px-4">
            <Send size={16} />
          </Button>
        </form>
      </div>
    </div>
  );
}

function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user";
  return (
    <div className={cn("flex gap-3", isUser && "flex-row-reverse")}>
      <div
        className={cn(
          "h-8 w-8 shrink-0 grid place-items-center rounded-full",
          isUser
            ? "bg-[var(--color-surface-2)] text-[var(--color-fg-muted)]"
            : "bg-gradient-to-br from-[var(--color-accent)] to-[var(--color-accent-2)] text-[#08111f]",
        )}
      >
        {isUser ? <User size={16} /> : <Bot size={16} />}
      </div>
      <div className={cn("min-w-0 max-w-[80%]", isUser && "text-right")}>
        <div className="text-[0.7rem] text-[var(--color-fg-dim)] mb-1">
          {isUser ? "You" : "AI Mentor"}
        </div>
        <div
          className={cn(
            "rounded-[var(--radius-lg)] px-4 py-3 text-left",
            isUser
              ? "bg-[var(--color-surface-2)] border border-[var(--color-border)]"
              : message.error
                ? "bg-[rgba(248,113,113,0.08)] border border-[rgba(248,113,113,0.3)]"
                : "glass",
          )}
        >
          <Markdown content={message.content} />
          {message.pipeline && message.pipeline.length > 0 ? (
            <div className="flex flex-wrap items-center gap-1.5 mt-3 pt-3 border-t border-[var(--color-border-soft)]">
              <Sparkles size={13} className="text-[var(--color-accent)]" />
              {message.pipeline.map((p, i) => (
                <span key={`${p}-${i}`} className="badge">
                  {p}
                </span>
              ))}
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function TypingRow({ status }: { status: string }) {
  return (
    <div className="flex gap-3">
      <div className="h-8 w-8 shrink-0 grid place-items-center rounded-full bg-gradient-to-br from-[var(--color-accent)] to-[var(--color-accent-2)] text-[#08111f]">
        <Bot size={16} />
      </div>
      <div className="glass rounded-[var(--radius-lg)] px-4 py-3 flex items-center gap-3">
        <div className="flex gap-1">
          {[0, 1, 2].map((i) => (
            <span
              key={i}
              className="typing-dot h-1.5 w-1.5 rounded-full bg-[var(--color-accent)]"
              style={{ animationDelay: `${i * 0.15}s` }}
            />
          ))}
        </div>
        <span className="text-xs text-[var(--color-fg-muted)]">{status}</span>
      </div>
    </div>
  );
}
