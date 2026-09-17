/**
 * Sidecar client — the single door between the PI agent core (TypeScript) and
 * the mentor's Python data layer.
 *
 * Boundary rule: TypeScript never opens a Postgres, Chroma, or vault
 * connection. Reads go through intent-shaped tools; Python stays the only
 * writer. See docs/PI-Mentor Boundary.md.
 *
 * Fail-open by design: a dead or slow sidecar returns a structured error
 * instead of throwing into the agent loop, so one broken tool can never crash
 * a mentor turn — the same property `orchestrator/harness.py` guaranteed.
 */

const DEFAULT_BASE_URL = "http://127.0.0.1:8000";
const DEFAULT_TIMEOUT_MS = 15000;

export interface SidecarConfig {
  baseUrl: string;
  timeoutMs: number;
}

/** Resolve the sidecar endpoint from the environment, with dev defaults. */
export function resolveSidecarConfig(): SidecarConfig {
  const env = typeof process === "undefined" ? undefined : process.env;
  const rawBaseUrl = env?.MENTOR_SERVICE_URL?.trim();
  const baseUrl = (rawBaseUrl && rawBaseUrl.length > 0 ? rawBaseUrl : DEFAULT_BASE_URL).replace(/\/+$/, "");
  const parsedTimeout = Number(env?.MENTOR_SERVICE_TIMEOUT_MS);
  const timeoutMs = Number.isFinite(parsedTimeout) && parsedTimeout > 0 ? parsedTimeout : DEFAULT_TIMEOUT_MS;
  return { baseUrl, timeoutMs };
}

export type SidecarResult<T> = { ok: true; data: T } | { ok: false; error: string };

export interface CallToolOptions {
  /**
   * Override the default request timeout for this call.
   *
   * The 15s default is right for reads: a slow memory lookup should degrade to a
   * readable error rather than stall a turn. Specialist runs are the exception —
   * goal decomposition generates tutorials and legitimately takes minutes, which
   * is why `PI_TURN_TIMEOUT_SECONDS` is 420s. Cutting one off at 15s would fail
   * the exact turns that matter most.
   */
  timeoutMs?: number;
}

/**
 * POST to one intent endpoint on the sidecar.
 *
 * `name` is an intent name (e.g. "get_profile"), never a table name. The
 * response contract is generated from the Python pydantic models by
 * `scripts/export_mentor_tools_schema.py`, so the two runtimes cannot drift.
 */
export async function callTool<T>(
  name: string,
  params: unknown,
  options: CallToolOptions = {},
): Promise<SidecarResult<T>> {
  const { baseUrl, timeoutMs } = resolveSidecarConfig();
  const effectiveTimeout = options.timeoutMs ?? timeoutMs;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), effectiveTimeout);

  try {
    const response = await fetch(`${baseUrl}/tools/${name}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(params ?? {}),
      signal: controller.signal,
    });
    if (!response.ok) {
      return { ok: false, error: `mentor sidecar rejected ${name}: HTTP ${response.status}` };
    }
    return { ok: true, data: (await response.json()) as T };
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    const timedOut = error instanceof Error && error.name === "AbortError";
    if (timedOut) {
      return {
        ok: false,
        error: `${name} did not finish within ${Math.round(effectiveTimeout / 1000)}s and was abandoned. Nothing was reported back — do not assume it failed or succeeded.`,
      };
    }
    return {
      ok: false,
      error: `mentor sidecar unreachable at ${baseUrl} (${message}) — is the FastAPI service running?`,
    };
  } finally {
    clearTimeout(timer);
  }
}
