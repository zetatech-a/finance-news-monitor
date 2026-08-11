/**
 * Cloudflare Worker cron scheduler for finance-news-monitor.
 *
 * Cron Trigger -> scheduled() -> GitHub workflow_dispatch -> daily.yml
 *
 * This Worker only *requests* a run. News collection, report generation and
 * email delivery all stay inside the existing GitHub Actions pipeline.
 *
 * There is intentionally no fetch() handler: the Worker must not be reachable
 * over HTTP, only via its cron trigger.
 *
 * Recovery is layered, and the two layers must never be conflated:
 *
 *   A. A missed cron *invocation* is recovered by four independent Cron
 *      Triggers 15 minutes apart (primary, retry-1, retry-2, retry-3).
 *   B. A transient GitHub API failure *inside* one invocation is recovered by
 *      the short, seconds-scale bounded retries in this file.
 *
 * Before dispatching, an invocation checks today's sent-marker and then any
 * active run of the same workflow, so a later slot stays quiet when the day is
 * already handled. Those checks are duplicate-suppression guards, not absolute
 * gates: an ordinary transient failure while checking degrades to "dispatch
 * anyway", because missing the day entirely is worse than a duplicate that
 * GitHub Actions concurrency and the workflow's own sent-marker already defend
 * against.
 *
 * Two situations are deliberately *not* degraded that way:
 *   - being rate limited (a degraded POST would just add load and might not be
 *     accepted anyway), and
 *   - an ambiguous dispatch, where a blind retry could duplicate a run.
 * Both defer to the next independent cron slot instead.
 */

const GITHUB_API_ORIGIN = "https://api.github.com";
const GITHUB_API_VERSION = "2026-03-10";
const USER_AGENT = "finance-news-monitor-scheduler";
const REQUEST_TIMEOUT_MS = 15_000;
/** Error bodies are truncated so a large HTML error page cannot flood the logs. */
const MAX_ERROR_BODY_CHARS = 500;

/** Preflight GETs (sent-marker, active runs) get one retry — two attempts total. */
const MAX_PREFLIGHT_ATTEMPTS = 2;
/** workflow_dispatch POSTs get two retries — three attempts total. */
const MAX_DISPATCH_ATTEMPTS = 3;
const BASE_RETRY_DELAY_MS = 1_000;
/**
 * Ceiling for the backoff this Worker computes itself, and the budget a wait
 * demanded by the server has to fit into. It is never used to *shorten* a
 * server-supplied Retry-After: waiting less than the server asked for is worse
 * than not retrying at all, so an over-budget Retry-After defers the whole
 * invocation to the next cron slot instead.
 */
const MAX_RETRY_DELAY_MS = 15_000;
const RETRY_JITTER_MS = 250;
const WORKFLOW_RUNS_PER_PAGE = 20;

/**
 * Worst case for one invocation, if every request burns its full timeout:
 * marker 45s + runs 45s + 3 POSTs 45s + 3 reconciliations 135s + backoff 30s
 * ≈ 5m, essentially all I/O wait (no CPU). Under normal conditions that is
 * comfortably shorter than the 15-minute spacing between cron slots, which is
 * why a slot usually finishes long before the next one fires.
 *
 * This is a budget, NOT a mutual-exclusion guarantee: Cloudflare may fire a
 * slot late, and nothing here prevents two invocations from being in flight at
 * once. Overlap is handled by the checks each invocation performs (sent-marker,
 * active run, post-dispatch reconciliation) and, below this Worker, by GitHub
 * Actions concurrency and the workflow's own sent-marker. Anyone raising
 * REQUEST_TIMEOUT_MS or the attempt counts must re-check this budget.
 */

/**
 * Worker configuration. GITHUB_TOKEN must be provided as a *secret*
 * (`wrangler secret put GITHUB_TOKEN`), never as a plain var in wrangler.jsonc.
 */
export interface Env {
  /**
   * Fine-grained PAT with Actions: read & write (workflow_dispatch + run
   * lookup) and Contents: read-only (sent-marker existence check).
   */
  GITHUB_TOKEN: string;
  GITHUB_OWNER: string;
  GITHUB_REPO: string;
  /** Workflow file name or numeric workflow id, e.g. "daily.yml". */
  GITHUB_WORKFLOW: string;
  /** Git ref the workflow runs on, e.g. "main". */
  GITHUB_REF: string;
  /** Exactly "true" or "false" — canary runs use "false". */
  DISPATCH_SEND_EMAIL: string;
}

const REQUIRED_ENV_KEYS = [
  "GITHUB_TOKEN",
  "GITHUB_OWNER",
  "GITHUB_REPO",
  "GITHUB_WORKFLOW",
  "GITHUB_REF",
  "DISPATCH_SEND_EMAIL",
] as const satisfies readonly (keyof Env)[];

/**
 * Minimal structural view of the runtime ScheduledController. Declared locally
 * so the dispatch logic can be unit tested without a Workers runtime.
 */
export interface ScheduledControllerLike {
  /** Scheduled fire time in epoch milliseconds. */
  readonly scheduledTime: number;
  /** The cron expression that fired, e.g. "59 23 * * *". */
  readonly cron: string;
  /** Tells the runtime not to retry this invocation. */
  noRetry?: () => void;
}

/**
 * Injectable side effects. Everything has a production default; tests replace
 * them so no test performs a network call, sleeps for real, or reads the clock.
 */
export interface SchedulerDeps {
  fetchImpl?: typeof fetch;
  sleep?: (ms: number) => Promise<void>;
  random?: () => number;
  /** Only used to turn X-RateLimit-Reset into a delay. */
  now?: () => number;
}

/** Thrown for configuration and GitHub API failures; never carries secrets. */
export class SchedulerError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "SchedulerError";
  }
}

export interface SchedulerConfig {
  token: string;
  owner: string;
  repo: string;
  workflow: string;
  ref: string;
  sendEmail: boolean;
}

export interface DispatchInputs {
  send_email: boolean;
  force_send: boolean;
  wait_until_target: boolean;
  trigger_source: string;
  scheduled_for: string;
  cron_expression: string;
}

export interface DispatchBody {
  ref: string;
  inputs: DispatchInputs;
}

/**
 * Validates and normalizes the Worker environment.
 *
 * Error messages list only *names* of missing variables — never their values —
 * so a misconfiguration can never leak the token into the logs.
 */
export function resolveConfig(env: Partial<Env> | null | undefined): SchedulerConfig {
  const missing: string[] = [];
  const values: Partial<Record<keyof Env, string>> = {};

  for (const key of REQUIRED_ENV_KEYS) {
    const raw = env?.[key];
    const value = typeof raw === "string" ? raw.trim() : "";
    if (value === "") {
      missing.push(key);
    } else {
      values[key] = value;
    }
  }

  if (missing.length > 0) {
    throw new SchedulerError(
      `Missing required environment configuration: ${missing.join(", ")}. ` +
        "Set GITHUB_TOKEN with `wrangler secret put GITHUB_TOKEN` and the rest " +
        "as vars in wrangler.jsonc (or .dev.vars for local runs).",
    );
  }

  const sendEmailRaw = values.DISPATCH_SEND_EMAIL as string;
  if (sendEmailRaw !== "true" && sendEmailRaw !== "false") {
    throw new SchedulerError(
      `DISPATCH_SEND_EMAIL must be exactly "true" or "false", got ${JSON.stringify(sendEmailRaw)}.`,
    );
  }

  return {
    token: values.GITHUB_TOKEN as string,
    owner: values.GITHUB_OWNER as string,
    repo: values.GITHUB_REPO as string,
    workflow: values.GITHUB_WORKFLOW as string,
    ref: values.GITHUB_REF as string,
    sendEmail: sendEmailRaw === "true",
  };
}

/**
 * Cron expression -> slot name. These four must stay in sync with
 * `triggers.crons` in wrangler.jsonc; a test asserts that they do.
 */
export const CRON_SLOTS: Readonly<Record<string, string>> = Object.freeze({
  "59 23 * * *": "primary",
  "14 0 * * *": "retry-1",
  "29 0 * * *": "retry-2",
  "44 0 * * *": "retry-3",
});

export const UNKNOWN_CRON_SLOT = "unknown";

/**
 * Maps a cron expression to its slot label, or UNKNOWN_CRON_SLOT.
 *
 * This function itself never throws — it is a pure lookup. Enforcement lives in
 * runScheduled(), which refuses to act on an unknown expression: the four slots
 * above are the whole approved contract, and an invocation from outside it has
 * no reviewed behaviour to fall back on.
 */
export function resolveCronSlot(cron: string | null | undefined): string {
  if (typeof cron !== "string") return UNKNOWN_CRON_SLOT;
  const key = cron.trim();
  return Object.hasOwn(CRON_SLOTS, key) ? CRON_SLOTS[key] : UNKNOWN_CRON_SLOT;
}

const KST_OFFSET_MS = 9 * 60 * 60 * 1_000;

/**
 * The report date the daily pipeline will use, derived from the controller's
 * fire time — never from Date.now() and never from the host timezone, so the
 * 23:59 UTC slot and the three 00:xx UTC slots all agree on one KST date.
 */
export function getKstReportDate(scheduledTime: number): string {
  if (!Number.isFinite(scheduledTime)) {
    throw new SchedulerError(`Invalid controller.scheduledTime: ${String(scheduledTime)}.`);
  }
  const kst = new Date(scheduledTime + KST_OFFSET_MS);
  const year = kst.getUTCFullYear();
  const month = String(kst.getUTCMonth() + 1).padStart(2, "0");
  const day = String(kst.getUTCDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

/** Every path component is URL-encoded so odd repo/workflow names cannot alter the path. */
export function buildDispatchUrl(config: SchedulerConfig): string {
  const owner = encodeURIComponent(config.owner);
  const repo = encodeURIComponent(config.repo);
  const workflow = encodeURIComponent(config.workflow);
  return `${GITHUB_API_ORIGIN}/repos/${owner}/${repo}/actions/workflows/${workflow}/dispatches`;
}

/** Directory of the delivery markers written by scripts/phase5_delivery.py. */
const MARKER_DIR_SEGMENTS = ["reports", "_sent"] as const;

/**
 * Contents-API path of today's sent-marker.
 *
 * Each segment is encoded on its own and the separators stay literal: encoding
 * the whole path in one call would turn the slashes into %2F, and the resulting
 * permanent 404 would read as "no marker" and re-dispatch on every slot.
 */
export function buildMarkerPath(reportDate: string): string {
  return [...MARKER_DIR_SEGMENTS, `${reportDate}_email_sent.json`]
    .map((segment) => encodeURIComponent(segment))
    .join("/");
}

export function buildMarkerUrl(config: SchedulerConfig, reportDate: string): string {
  const owner = encodeURIComponent(config.owner);
  const repo = encodeURIComponent(config.repo);
  const query = new URLSearchParams({ ref: config.ref });
  return `${GITHUB_API_ORIGIN}/repos/${owner}/${repo}/contents/${buildMarkerPath(reportDate)}?${query.toString()}`;
}

/**
 * Recent runs of the daily workflow on the target branch.
 *
 * Deliberately unfiltered by `event`: a manual recovery run and a run this
 * Worker requested are equally good reasons not to dispatch again.
 */
export function buildWorkflowRunsUrl(config: SchedulerConfig): string {
  const owner = encodeURIComponent(config.owner);
  const repo = encodeURIComponent(config.repo);
  const workflow = encodeURIComponent(config.workflow);
  const query = new URLSearchParams({
    branch: config.ref,
    per_page: String(WORKFLOW_RUNS_PER_PAGE),
  });
  return `${GITHUB_API_ORIGIN}/repos/${owner}/${repo}/actions/workflows/${workflow}/runs?${query.toString()}`;
}

/** Converts the controller's epoch-ms fire time to a UTC ISO-8601 string. */
export function toUtcIso(scheduledTime: number): string {
  if (!Number.isFinite(scheduledTime)) {
    throw new SchedulerError(`Invalid controller.scheduledTime: ${String(scheduledTime)}.`);
  }
  return new Date(scheduledTime).toISOString();
}

/**
 * `return_run_details` is intentionally absent: it is a legacy parameter at
 * API version 2026-03-10. Success is a 2xx; a 200 may carry run details, which
 * are used for logging only (see extractRunDetails).
 */
export function buildDispatchBody(
  config: SchedulerConfig,
  controller: ScheduledControllerLike,
): DispatchBody {
  return {
    ref: config.ref,
    inputs: {
      send_email: config.sendEmail,
      force_send: false,
      wait_until_target: false,
      trigger_source: "cloudflare-cron",
      scheduled_for: toUtcIso(controller.scheduledTime),
      cron_expression: controller.cron,
    },
  };
}

function truncate(text: string): string {
  const collapsed = text.replace(/\s+/g, " ").trim();
  return collapsed.length > MAX_ERROR_BODY_CHARS
    ? `${collapsed.slice(0, MAX_ERROR_BODY_CHARS)}…[truncated]`
    : collapsed;
}

/** Error -> short message. The error object itself is never serialized: it can carry request internals. */
function describeError(error: unknown): string {
  return truncate(error instanceof Error ? error.message : String(error));
}

// ---------------------------------------------------------------------------
// Structured logging
// ---------------------------------------------------------------------------

/** The only invocation-level fields any log line carries. Never holds the token. */
export interface LogContext {
  cron: string;
  slot: string;
  scheduled_for: string;
  report_date: string;
  owner: string;
  repo: string;
  workflow: string;
  ref: string;
}

export type LogLevel = "info" | "warn" | "error";
export type Logger = (level: LogLevel, message: string, fields?: Record<string, unknown>) => void;

/**
 * One-line JSON logger over a frozen, explicitly whitelisted context.
 *
 * Call sites pass individual primitives, never the config object, so there is
 * no code path that could put the token or an Authorization header in a log.
 */
export function makeLogger(context: LogContext): Logger {
  const base = Object.freeze({ ...context });
  return (level, message, fields) => {
    const line = JSON.stringify({ message, ...base, ...(fields ?? {}) });
    if (level === "error") {
      console.error(line);
    } else if (level === "warn") {
      console.warn(line);
    } else {
      console.log(line);
    }
  };
}

// ---------------------------------------------------------------------------
// Transient / rate-limit classification and wait budgets
// ---------------------------------------------------------------------------

/** 408/429/5xx are worth another attempt; every other status is an answer. */
export function isTransientStatus(status: number): boolean {
  return status === 408 || status === 429 || (status >= 500 && status <= 599);
}

export interface RateLimitSignals {
  /** Parsed Retry-After in ms. NOT capped — the cap is a policy decision, not a parse step. */
  retryAfterMs?: number;
  /** Raw X-RateLimit-Remaining, compared as a string ("0" is the meaningful value). */
  remaining?: string;
  /** X-RateLimit-Reset as epoch *seconds*. */
  resetEpochSeconds?: number;
  /** Whether a Retry-After header was present at all, even if unparseable. */
  hasRetryAfterHeader: boolean;
}

/**
 * Canonical non-negative decimal integer, and nothing else.
 *
 * `Number()` alone is far too permissive for a header that decides how long we
 * stop talking to GitHub: it turns "" and "   " into 0 (retry immediately —
 * exactly the wrong reaction to a rate limit), and accepts "1e1", "0x10",
 * "-5" and "1.5". A malformed header is not a number we may act on, so it is
 * treated as absent and the caller falls through to its defer rules.
 */
const DECIMAL_INTEGER = /^\d+$/;

function parseHeaderInteger(headerValue: string | null | undefined): number | undefined {
  if (typeof headerValue !== "string") return undefined;
  const trimmed = headerValue.trim();
  if (!DECIMAL_INTEGER.test(trimmed)) return undefined;
  const value = Number(trimmed);
  return Number.isSafeInteger(value) ? value : undefined;
}

/** Retry-After in delta-seconds. HTTP-date form is ignored rather than guessed at. */
export function parseRetryAfterMs(headerValue: string | null | undefined): number | undefined {
  const seconds = parseHeaderInteger(headerValue);
  return seconds === undefined ? undefined : seconds * 1_000;
}

export function readRateLimitSignals(headers: Headers): RateLimitSignals {
  const retryAfter = headers.get("Retry-After");
  const remaining = headers.get("X-RateLimit-Remaining");
  const reset = parseHeaderInteger(headers.get("X-RateLimit-Reset"));
  return {
    retryAfterMs: parseRetryAfterMs(retryAfter),
    remaining: remaining === null ? undefined : remaining.trim(),
    resetEpochSeconds: reset !== undefined && reset > 0 ? reset : undefined,
    hasRetryAfterHeader: retryAfter !== null,
  };
}

const RATE_LIMIT_MESSAGE = /rate limit|secondary rate|abuse detection/i;

/**
 * GitHub reports rate limiting as 429 *or* 403, so a 403 is not automatically a
 * permission problem. `detail` is the already-truncated error body; the raw
 * body is never logged.
 */
export function isRateLimitResponse(
  status: number,
  signals: RateLimitSignals,
  detail: string,
): boolean {
  if (status === 429) return true;
  if (status !== 403) return false;
  if (signals.hasRetryAfterHeader) return true;
  if (signals.remaining === "0") return true;
  return RATE_LIMIT_MESSAGE.test(detail);
}

export type WaitBudget =
  | { kind: "wait"; delayMs: number }
  | { kind: "defer"; reason: string };

/**
 * Bounded exponential backoff with a little jitter. `attempt` is the 1-based
 * number of the attempt that just failed.
 */
export function computeRetryDelayMs(attempt: number, random: () => number): number {
  const exponential = BASE_RETRY_DELAY_MS * 2 ** Math.max(0, attempt - 1);
  const jitter = Math.floor(random() * RETRY_JITTER_MS);
  return Math.min(exponential + jitter, MAX_RETRY_DELAY_MS);
}

/**
 * How long to wait before the next GitHub request — or whether to stop asking.
 *
 * Rules, in order:
 *  - An explicit Retry-After is authoritative and is never shortened. Over
 *    budget means we defer, not that we retry early.
 *  - X-RateLimit-Reset is only trusted when X-RateLimit-Remaining is exactly
 *    "0", i.e. the *primary* quota is provably exhausted. A secondary rate
 *    limit with quota left needs a minimum wait of about a minute, which no
 *    15-second budget can honour, so it defers.
 *  - Everything else transient uses our own backoff.
 */
export function resolveWaitBudget(
  failure: "rate_limit" | "transient",
  signals: RateLimitSignals,
  attempt: number,
  random: () => number,
  now: () => number,
): WaitBudget {
  if (signals.retryAfterMs !== undefined) {
    return signals.retryAfterMs <= MAX_RETRY_DELAY_MS
      ? { kind: "wait", delayMs: signals.retryAfterMs }
      : { kind: "defer", reason: "retry_after_exceeds_budget" };
  }

  if (failure === "rate_limit") {
    if (signals.remaining === "0") {
      // Missing or malformed reset: we know the quota is gone but not for how
      // long, so there is no safe wait to compute.
      if (signals.resetEpochSeconds === undefined) {
        return { kind: "defer", reason: "rate_limit_reset_unusable" };
      }
      const delayMs = signals.resetEpochSeconds * 1_000 - now();
      return delayMs <= MAX_RETRY_DELAY_MS
        ? { kind: "wait", delayMs: Math.max(delayMs, 0) }
        : { kind: "defer", reason: "rate_limit_reset_exceeds_budget" };
    }
    return { kind: "defer", reason: "rate_limit_window_unknown" };
  }

  return { kind: "wait", delayMs: computeRetryDelayMs(attempt, random) };
}

function defaultSleep(ms: number): Promise<void> {
  // Cloudflare's awaitable timer. The guard keeps the module importable outside
  // workerd (vitest never reaches either branch — sleep is always injected).
  if (typeof scheduler !== "undefined" && typeof scheduler.wait === "function") {
    return scheduler.wait(ms);
  }
  return new Promise<void>((resolve) => {
    setTimeout(resolve, ms);
  });
}

// ---------------------------------------------------------------------------
// Shared GitHub request handling
// ---------------------------------------------------------------------------

type RequestResult = { ok: true; response: Response } | { ok: false; reason: string };

/**
 * Single place for headers, timeout and network-error capture. The caller owns
 * the returned Response and must read its body at most once.
 */
async function githubRequest(
  url: string,
  config: SchedulerConfig,
  deps: SchedulerDeps,
  options: { method: "GET" | "POST"; body?: string },
): Promise<RequestResult> {
  const fetchImpl = deps.fetchImpl ?? fetch;
  const headers: Record<string, string> = {
    Accept: "application/vnd.github+json",
    Authorization: `Bearer ${config.token}`,
    "X-GitHub-Api-Version": GITHUB_API_VERSION,
    "User-Agent": USER_AGENT,
  };
  if (options.body !== undefined) headers["Content-Type"] = "application/json";

  try {
    const response = await fetchImpl(url, {
      method: options.method,
      headers,
      body: options.body,
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
    return { ok: true, response };
  } catch (error) {
    // Network failures and AbortSignal timeouts land here. Both are retryable,
    // and after a POST both are *ambiguous*: GitHub may have accepted it.
    return { ok: false, reason: describeError(error) };
  }
}

async function readErrorDetail(response: Response): Promise<string> {
  try {
    return truncate(await response.text());
  } catch {
    return "<unreadable response body>";
  }
}

type GetResult =
  /** A non-retryable status; the body has NOT been read yet. */
  | { kind: "response"; status: number; response: Response }
  /** A 403 that is not rate limiting; the body was read for classification. */
  | { kind: "client_error"; status: number; detail: string }
  /** Ordinary transient failure that survived the attempt budget — degradable. */
  | { kind: "transient_exhausted"; reason: string }
  /** Rate limited, or a wait we are not allowed to honour — NOT degradable. */
  | { kind: "deferred"; reason: string };

/**
 * GET with the shared preflight retry budget (MAX_PREFLIGHT_ATTEMPTS).
 *
 * Only network errors, timeouts and 408/429/5xx are retried, plus 403s that
 * turn out to be rate limiting. Every other status is returned to the caller
 * on the first attempt — a 404 or a permission 403 does not improve on a
 * second ask. The loop never sleeps after its last allowed attempt: a sleep
 * that no request follows is pure waste.
 */
async function getWithRetry(
  url: string,
  config: SchedulerConfig,
  deps: SchedulerDeps,
  log: Logger,
  stage: string,
): Promise<GetResult> {
  const sleep = deps.sleep ?? defaultSleep;
  const random = deps.random ?? Math.random;
  const now = deps.now ?? Date.now;
  let reason = "unknown";
  /** Whether the *last* attempt failed because of rate limiting. */
  let lastWasRateLimited = false;

  for (let attempt = 1; attempt <= MAX_PREFLIGHT_ATTEMPTS; attempt += 1) {
    const result = await githubRequest(url, config, deps, { method: "GET" });

    let failure: "rate_limit" | "transient";
    let signals: RateLimitSignals;

    if (result.ok) {
      const { response } = result;
      const status = response.status;
      signals = readRateLimitSignals(response.headers);

      if (status === 403 || status === 429) {
        const detail = await readErrorDetail(response);
        if (!isRateLimitResponse(status, signals, detail)) {
          return { kind: "client_error", status, detail };
        }
        failure = "rate_limit";
        reason = `rate_limited_http_${status}`;
      } else if (isTransientStatus(status)) {
        failure = "transient";
        reason = `http_${status}: ${await readErrorDetail(response)}`;
      } else {
        return { kind: "response", status, response };
      }
    } else {
      signals = { hasRetryAfterHeader: false };
      failure = "transient";
      reason = `request_failed: ${result.reason}`;
    }

    lastWasRateLimited = failure === "rate_limit";

    const budget = resolveWaitBudget(failure, signals, attempt, random, now);
    if (budget.kind === "defer") {
      // Per-request event. The invocation-level decision is logged by the
      // caller as `preflight_deferred`; keeping the messages distinct means a
      // log query for one never silently matches the other.
      log("warn", "preflight_request_deferred", {
        stage,
        attempt,
        reason,
        defer_reason: budget.reason,
      });
      return { kind: "deferred", reason: `${reason} (${budget.reason})` };
    }

    if (attempt >= MAX_PREFLIGHT_ATTEMPTS) break;

    log("warn", "preflight_retry", {
      stage,
      attempt,
      max_attempts: MAX_PREFLIGHT_ATTEMPTS,
      retry_delay_ms: budget.delayMs,
      reason,
    });
    await sleep(budget.delayMs);
  }

  // Rate limiting that outlives the attempt budget must not degrade into an
  // immediate POST: that is exactly the load the limit is asking us to shed.
  if (lastWasRateLimited) {
    return { kind: "deferred", reason: `${reason} (attempts_exhausted)` };
  }
  return { kind: "transient_exhausted", reason };
}

// ---------------------------------------------------------------------------
// Step 1: today's sent-marker
// ---------------------------------------------------------------------------

export type MarkerResult =
  | { kind: "exists" }
  | { kind: "absent" }
  | { kind: "degraded"; reason: string };

/**
 * Existence check for reports/_sent/{date}_email_sent.json — status only; the
 * body is never downloaded or parsed.
 *
 * The marker is a *suppression* signal, so an inconclusive answer must never
 * suppress: ordinary transient exhaustion and unexpected statuses both degrade
 * to "carry on". A hard 4xx (bad token, missing Contents permission, wrong
 * repo) stops the invocation because dispatching would be equally broken, and
 * a rate-limited lookup defers to the next slot.
 */
export async function checkSentMarker(
  config: SchedulerConfig,
  reportDate: string,
  deps: SchedulerDeps,
  log: Logger,
): Promise<MarkerResult> {
  const url = buildMarkerUrl(config, reportDate);
  const result = await getWithRetry(url, config, deps, log, "sent_marker");

  if (result.kind === "deferred") {
    log("error", "preflight_deferred", { stage: "sent_marker", reason: result.reason });
    throw new SchedulerError(
      `GitHub sent-marker check could not complete (${result.reason}); no dispatch was sent. ` +
        "The next cron slot will recover.",
    );
  }

  if (result.kind === "transient_exhausted") {
    log("warn", "sent_marker_check", { outcome: "transient_exhausted", reason: result.reason });
    return { kind: "degraded", reason: result.reason };
  }

  if (result.kind === "client_error") {
    log("error", "sent_marker_check", { status: result.status, outcome: "client_error" });
    throw new SchedulerError(
      `GitHub sent-marker check failed with HTTP ${result.status}: ${result.detail}`,
    );
  }

  const { status, response } = result;
  log("info", "sent_marker_check", { status });

  if (status === 200) return { kind: "exists" };
  if (status === 404) return { kind: "absent" };

  if (status >= 400 && status <= 499) {
    throw new SchedulerError(
      `GitHub sent-marker check failed with HTTP ${status}: ${await readErrorDetail(response)}`,
    );
  }

  // 3xx or an unexpected 2xx: not an error, but not an answer either.
  return { kind: "degraded", reason: `unexpected_status_${status}` };
}

// ---------------------------------------------------------------------------
// Step 2: an already-active run of the same workflow
// ---------------------------------------------------------------------------

export const ACTIVE_RUN_STATUSES: ReadonlySet<string> = new Set([
  "queued",
  "in_progress",
  "requested",
  "waiting",
  "pending",
]);

/** "completed" is the only status that positively means "not running". */
const INACTIVE_RUN_STATUSES: ReadonlySet<string> = new Set(["completed"]);

export interface ActiveRunInfo {
  status: string;
  id?: number | string;
  url?: string;
  createdAt?: string;
}

export type WorkflowRunsClassification =
  | ({ kind: "active" } & ActiveRunInfo)
  | { kind: "none" }
  | { kind: "unknown_status"; reason: string }
  | { kind: "malformed"; reason: string };

const isId = (value: unknown): value is number | string =>
  typeof value === "number" || (typeof value === "string" && value !== "");
const isNonEmptyString = (value: unknown): value is string =>
  typeof value === "string" && value !== "";
const isUrl = isNonEmptyString;

/**
 * Pure classifier for a workflow-runs payload.
 *
 * Strict on purpose. A missing `workflow_runs` array is "malformed", never
 * "empty" — an error envelope such as {"message":"Not Found"} would otherwise
 * read as "nothing is running" and license a duplicate dispatch. Likewise a run
 * entry without a string status makes the whole payload untrustworthy: being
 * wrong here costs a duplicate run, while being cautious only costs a degrade
 * (preflight) or a deferral to the next slot (reconciliation).
 *
 * `conclusion` is deliberately ignored — whether the email actually went out is
 * the sent-marker's job, not something to infer from a run's outcome.
 */
export function classifyWorkflowRuns(payload: unknown): WorkflowRunsClassification {
  if (typeof payload !== "object" || payload === null || Array.isArray(payload)) {
    return { kind: "malformed", reason: "unexpected_payload_type" };
  }
  const runs = (payload as Record<string, unknown>).workflow_runs;
  if (!Array.isArray(runs)) {
    return { kind: "malformed", reason: "missing_workflow_runs" };
  }

  let unknownStatus: string | undefined;
  for (const entry of runs) {
    if (typeof entry !== "object" || entry === null || Array.isArray(entry)) {
      return { kind: "malformed", reason: "unexpected_run_entry" };
    }
    const record = entry as Record<string, unknown>;
    if (typeof record.status !== "string") {
      return { kind: "malformed", reason: "missing_run_status" };
    }
    const status = record.status.trim().toLowerCase();

    if (ACTIVE_RUN_STATUSES.has(status)) {
      return {
        kind: "active",
        status,
        id: isId(record.id) ? record.id : undefined,
        url: isUrl(record.html_url) ? record.html_url : undefined,
        createdAt: isNonEmptyString(record.created_at) ? record.created_at : undefined,
      };
    }
    if (!INACTIVE_RUN_STATUSES.has(status) && unknownStatus === undefined) {
      unknownStatus = status;
    }
  }

  if (unknownStatus !== undefined) {
    return { kind: "unknown_status", reason: `unknown_run_status_${unknownStatus}` };
  }
  return { kind: "none" };
}

/**
 * "preflight" runs before the first dispatch, "reconciliation" after an
 * ambiguous one. The lookup is identical; only the caller's reaction to an
 * inconclusive answer differs, which is why the mode lives at the call site and
 * not in a shared mutable flag.
 */
export type RunCheckMode = "preflight" | "reconciliation";

export type RunCheckResult =
  | ({ kind: "active" } & ActiveRunInfo)
  | { kind: "none" }
  | { kind: "inconclusive"; reason: string; degradable: boolean };

export async function findActiveWorkflowRun(
  config: SchedulerConfig,
  deps: SchedulerDeps,
  log: Logger,
  mode: RunCheckMode,
): Promise<RunCheckResult> {
  const url = buildWorkflowRunsUrl(config);
  const result = await getWithRetry(url, config, deps, log, `workflow_runs_${mode}`);

  if (result.kind === "deferred") {
    log("warn", "workflow_runs_check", { mode, outcome: "deferred", reason: result.reason });
    return { kind: "inconclusive", reason: result.reason, degradable: false };
  }

  if (result.kind === "transient_exhausted") {
    log("warn", "workflow_runs_check", {
      mode,
      outcome: "transient_exhausted",
      reason: result.reason,
    });
    return { kind: "inconclusive", reason: result.reason, degradable: true };
  }

  if (result.kind === "client_error") {
    throw new SchedulerError(
      `GitHub workflow runs lookup failed with HTTP ${result.status}: ${result.detail}`,
    );
  }

  const { status, response } = result;

  if (status >= 400 && status <= 499) {
    throw new SchedulerError(
      `GitHub workflow runs lookup failed with HTTP ${status}: ${await readErrorDetail(response)}`,
    );
  }

  if (status !== 200) {
    log("warn", "workflow_runs_check", { mode, status, outcome: "unexpected_status" });
    return { kind: "inconclusive", reason: `unexpected_status_${status}`, degradable: true };
  }

  let classification: WorkflowRunsClassification;
  try {
    classification = classifyWorkflowRuns(await response.json());
  } catch {
    classification = { kind: "malformed", reason: "unparseable_body" };
  }

  if (classification.kind === "active") {
    log("info", "workflow_runs_check", {
      mode,
      status,
      outcome: "active",
      run_id: classification.id,
      active_run_status: classification.status,
      run_url: classification.url,
      created_at: classification.createdAt,
    });
    return classification;
  }

  if (classification.kind === "none") {
    log("info", "workflow_runs_check", { mode, status, outcome: "none" });
    return { kind: "none" };
  }

  log("warn", "workflow_runs_check", {
    mode,
    status,
    outcome: classification.kind,
    reason: classification.reason,
  });
  return { kind: "inconclusive", reason: classification.reason, degradable: true };
}

// ---------------------------------------------------------------------------
// Step 3: workflow_dispatch
// ---------------------------------------------------------------------------

/**
 * Candidate field names for the run id / run URL in a dispatch response, in
 * priority order.
 *
 * The exact shape is not pinned down here on purpose: the response is accepted
 * either flat (`workflow_run_id`) or nested under `run`, so a naming difference
 * degrades to a missing log field rather than a failed dispatch. HTML URLs are
 * preferred over API URLs because the log is read by humans.
 */
const RUN_ID_KEYS = ["workflow_run_id", "run_id", "id"] as const;
const RUN_URL_KEYS = ["workflow_run_html_url", "html_url", "workflow_run_url", "url"] as const;
/** Cap on the key names logged when no known field matches. */
const MAX_LOGGED_SHAPE_KEYS = 20;

export interface RunDetails {
  id?: number | string;
  url?: string;
  /** Top-level key *names* of an unrecognized payload, to aid canary debugging. */
  unrecognizedShapeKeys?: string[];
}

function pickField<T>(
  sources: readonly Record<string, unknown>[],
  keys: readonly string[],
  accept: (value: unknown) => value is T,
): T | undefined {
  for (const key of keys) {
    for (const source of sources) {
      const value = source[key];
      if (accept(value)) return value;
    }
  }
  return undefined;
}

/** Pulls run id/url out of a 2xx response body, tolerating an unexpected shape. */
export function extractRunDetails(payload: unknown): RunDetails {
  if (typeof payload !== "object" || payload === null) return {};
  const record = payload as Record<string, unknown>;

  const sources: Record<string, unknown>[] = [record];
  for (const envelope of ["run", "workflow_run"]) {
    const nested = record[envelope];
    if (typeof nested === "object" && nested !== null) {
      sources.push(nested as Record<string, unknown>);
    }
  }

  const id = pickField(sources, RUN_ID_KEYS, isId);
  const url = pickField(sources, RUN_URL_KEYS, isUrl);
  if (id !== undefined || url !== undefined) return { id, url };

  // Nothing matched. Log the key *names* only (never values) so the first real
  // canary run tells us the actual field names instead of staying silent.
  const keys = Object.keys(record);
  return keys.length > 0 ? { unrecognizedShapeKeys: keys.slice(0, MAX_LOGGED_SHAPE_KEYS) } : {};
}

export type DispatchOutcome =
  | { kind: "accepted"; status: number; details: RunDetails }
  | { kind: "fatal"; status: number; detail: string }
  | { kind: "transient"; status: number; detail: string; signals: RateLimitSignals }
  | { kind: "rate_limited"; status: number; detail: string; signals: RateLimitSignals }
  | { kind: "network"; reason: string };

export type DispatchFailure = Exclude<DispatchOutcome, { kind: "accepted" }>;

/**
 * One POST. Returns an outcome instead of throwing, and fully consumes the
 * response body, so a caller can never read it twice or leak a live Response.
 */
async function attemptDispatch(
  config: SchedulerConfig,
  url: string,
  body: DispatchBody,
  deps: SchedulerDeps,
): Promise<DispatchOutcome> {
  const result = await githubRequest(url, config, deps, {
    method: "POST",
    body: JSON.stringify(body),
  });

  if (!result.ok) return { kind: "network", reason: result.reason };
  const { response } = result;

  if (!response.ok) {
    const signals = readRateLimitSignals(response.headers);
    const detail = await readErrorDetail(response);
    const status = response.status;

    if (isRateLimitResponse(status, signals, detail)) {
      return { kind: "rate_limited", status, detail, signals };
    }
    if (isTransientStatus(status)) {
      return { kind: "transient", status, detail, signals };
    }
    return { kind: "fatal", status, detail };
  }

  // 204 (classic) and 200 (with run details) are both success.
  if (response.status === 204) return { kind: "accepted", status: 204, details: {} };

  let details: RunDetails = {};
  try {
    details = extractRunDetails(await response.json());
  } catch {
    // A 2xx with an unparseable body is still an accepted dispatch.
  }
  return { kind: "accepted", status: response.status, details };
}

/** Single source of the failure wording, shared by both dispatch entry points. */
function describeDispatchFailure(outcome: DispatchFailure): string {
  if (outcome.kind === "network") {
    return `GitHub workflow dispatch request failed: ${outcome.reason}`;
  }
  return `GitHub workflow dispatch failed with HTTP ${outcome.status}: ${outcome.detail}`;
}

/**
 * Sends a single workflow_dispatch request, with no preflight and no retry.
 *
 * Retained for compatibility and for focused testing of one request. The
 * production path is runScheduled() -> dispatchWorkflowWithRetry(), where
 * retries are safe because each one is gated on an active-run reconciliation.
 */
export async function dispatchWorkflow(
  controller: ScheduledControllerLike,
  env: Partial<Env> | null | undefined,
  fetchImpl: typeof fetch = fetch,
): Promise<void> {
  const config = resolveConfig(env);
  const url = buildDispatchUrl(config);
  const body = buildDispatchBody(config, controller);

  console.log(
    JSON.stringify({
      message: "dispatching workflow",
      owner: config.owner,
      repo: config.repo,
      workflow: config.workflow,
      ref: config.ref,
      send_email: config.sendEmail,
      scheduled_for: body.inputs.scheduled_for,
      cron: body.inputs.cron_expression,
    }),
  );

  const outcome = await attemptDispatch(config, url, body, { fetchImpl });

  // Anything that is not "accepted" — including a retryable status — is an
  // error here: this entry point has no retry budget to fall back on.
  if (outcome.kind !== "accepted") {
    throw new SchedulerError(describeDispatchFailure(outcome));
  }

  if (outcome.status === 204) {
    console.log(JSON.stringify({ message: "workflow dispatch accepted", status: 204 }));
    return;
  }

  console.log(
    JSON.stringify({
      message: "workflow dispatch accepted",
      status: outcome.status,
      run_id: outcome.details.id,
      run_url: outcome.details.url,
      unrecognized_shape_keys: outcome.details.unrecognizedShapeKeys,
    }),
  );
}

/**
 * The production dispatch path: at most MAX_DISPATCH_ATTEMPTS POSTs, with an
 * active-run reconciliation after *every* ambiguous failure.
 *
 * A POST that fails with a network error or a timeout is ambiguous — GitHub may
 * well have accepted it. So the invocation sleeps (long enough for an accepted
 * run to surface) and then asks whether a run is now active:
 *
 *   active       -> the POST landed; stop, report success.
 *   none         -> nothing was created; another POST is safe, if any are left.
 *   inconclusive -> we do not know. Never POST blind: fail this invocation and
 *                   let the next independent cron slot recover.
 *
 * The reconciliation after the *last* allowed POST exists purely to observe
 * acceptance. It can turn a failure into a success, but never into a 4th POST.
 *
 * This is the deliberate asymmetry with the preflight checks: once a POST has
 * been sent, avoiding a duplicate run outranks recovering today's delivery,
 * because further slots are still queued behind us.
 */
export async function dispatchWorkflowWithRetry(
  config: SchedulerConfig,
  controller: ScheduledControllerLike,
  deps: SchedulerDeps,
  log: Logger,
): Promise<void> {
  const url = buildDispatchUrl(config);
  const body = buildDispatchBody(config, controller);
  const sleep = deps.sleep ?? defaultSleep;
  const random = deps.random ?? Math.random;
  const now = deps.now ?? Date.now;

  for (let attempt = 1; attempt <= MAX_DISPATCH_ATTEMPTS; attempt += 1) {
    log("info", "dispatch_attempt", { attempt, max_attempts: MAX_DISPATCH_ATTEMPTS });
    const outcome = await attemptDispatch(config, url, body, deps);

    if (outcome.kind === "accepted") {
      log("info", "workflow_dispatch_accepted", {
        attempt,
        status: outcome.status,
        run_id: outcome.details.id,
        run_url: outcome.details.url,
        unrecognized_shape_keys: outcome.details.unrecognizedShapeKeys,
      });
      return;
    }

    if (outcome.kind === "fatal") {
      log("error", "workflow_dispatch_failed", {
        attempt,
        status: outcome.status,
        reason: "non_transient_status",
      });
      throw new SchedulerError(describeDispatchFailure(outcome));
    }

    const failureKind = outcome.kind === "rate_limited" ? "rate_limit" : "transient";
    const signals = outcome.kind === "network" ? { hasRetryAfterHeader: false } : outcome.signals;
    const budget = resolveWaitBudget(failureKind, signals, attempt, random, now);

    if (budget.kind === "defer") {
      // No further GitHub request this invocation — not even a reconciliation
      // GET, since we were explicitly asked to back off for longer than our
      // budget allows.
      log("error", "dispatch_deferred", {
        attempt,
        status: outcome.kind === "network" ? undefined : outcome.status,
        reason: budget.reason,
      });
      throw new SchedulerError(
        `Workflow dispatch attempt ${attempt} failed and the required wait exceeds this ` +
          `invocation's budget (${budget.reason}); no retry was sent. ` +
          "The next cron slot will recover.",
      );
    }

    const finalAttempt = attempt === MAX_DISPATCH_ATTEMPTS;
    log("warn", "dispatch_retry_scheduled", {
      attempt,
      max_attempts: MAX_DISPATCH_ATTEMPTS,
      retry_delay_ms: budget.delayMs,
      final_attempt: finalAttempt,
      status: outcome.kind === "network" ? undefined : outcome.status,
      reason: describeDispatchFailure(outcome),
    });
    // Sleep first: reconciling immediately would race a run GitHub has accepted
    // but not yet surfaced, and read it as "safe to POST again".
    await sleep(budget.delayMs);

    log("info", "dispatch_reconciliation_check", { attempt, final_attempt: finalAttempt });
    const recon = await findActiveWorkflowRun(config, deps, log, "reconciliation");

    if (recon.kind === "active") {
      log("info", "dispatch_observed_after_ambiguous_failure", {
        attempt,
        run_id: recon.id,
        active_run_status: recon.status,
        run_url: recon.url,
        created_at: recon.createdAt,
      });
      return;
    }

    if (recon.kind === "inconclusive") {
      log("error", "dispatch_reconciliation_deferred", { attempt, reason: recon.reason });
      throw new SchedulerError(
        `Workflow dispatch outcome is ambiguous after attempt ${attempt} and the active-run ` +
          `reconciliation was inconclusive (${recon.reason}); no blind retry was sent. ` +
          "The next cron slot will recover.",
      );
    }

    if (finalAttempt) {
      log("error", "workflow_dispatch_failed", {
        attempt,
        max_attempts: MAX_DISPATCH_ATTEMPTS,
        status: outcome.kind === "network" ? undefined : outcome.status,
        reason: "attempts_exhausted",
      });
      throw new SchedulerError(describeDispatchFailure(outcome));
    }
  }
}

/**
 * One cron invocation.
 *
 * Order matters and is asserted by tests: noRetry() before anything can fail,
 * the sent-marker before the runs lookup (a finished day must not cost an
 * Actions API call), and the runs lookup before any POST.
 */
export async function runScheduled(
  controller: ScheduledControllerLike,
  env: Partial<Env> | null | undefined,
  deps: SchedulerDeps = {},
): Promise<void> {
  // Requested up front: a failed dispatch must not be silently replayed.
  // Recovery is the next cron slot's job, not the runtime's.
  controller.noRetry?.();

  const config = resolveConfig(env);
  const scheduledFor = toUtcIso(controller.scheduledTime);
  const reportDate = getKstReportDate(controller.scheduledTime);
  const slot = resolveCronSlot(controller.cron);

  const log = makeLogger({
    cron: controller.cron,
    slot,
    scheduled_for: scheduledFor,
    report_date: reportDate,
    owner: config.owner,
    repo: config.repo,
    workflow: config.workflow,
    ref: config.ref,
  });

  log("info", "cron_received", { send_email: config.sendEmail });

  if (slot === UNKNOWN_CRON_SLOT) {
    // Fail closed. The four slots in CRON_SLOTS are the entire approved
    // contract; an expression outside it means the deployed triggers and this
    // code disagree, and dispatching anyway would run the daily pipeline on a
    // schedule nobody reviewed. Nothing is sent to GitHub.
    log("error", "cron_rejected", { reason: "unknown_cron_expression" });
    throw new SchedulerError(
      `Unrecognized cron expression ${JSON.stringify(controller.cron)}; no GitHub request was ` +
        "sent. Update triggers.crons in wrangler.jsonc and CRON_SLOTS together.",
    );
  }

  const marker = await checkSentMarker(config, reportDate, deps, log);
  if (marker.kind === "exists") {
    log("info", "skip_marker_exists");
    return;
  }
  if (marker.kind === "degraded") {
    log("warn", "preflight_degraded", { stage: "sent_marker", reason: marker.reason });
  }

  const activeRun = await findActiveWorkflowRun(config, deps, log, "preflight");
  if (activeRun.kind === "active") {
    log("info", "skip_workflow_active", {
      run_id: activeRun.id,
      active_run_status: activeRun.status,
      run_url: activeRun.url,
      created_at: activeRun.createdAt,
    });
    return;
  }
  if (activeRun.kind === "inconclusive") {
    if (!activeRun.degradable) {
      // Rate limited (or an over-budget Retry-After): dispatching now would add
      // load GitHub just asked us to shed, and might not be accepted anyway.
      log("error", "preflight_deferred", { stage: "workflow_runs", reason: activeRun.reason });
      throw new SchedulerError(
        `GitHub workflow runs lookup could not complete (${activeRun.reason}); no dispatch was ` +
          "sent. The next cron slot will recover.",
      );
    }
    log("warn", "preflight_degraded", { stage: "workflow_runs", reason: activeRun.reason });
  }

  await dispatchWorkflowWithRetry(config, controller, deps, log);
}

export default {
  async scheduled(controller: ScheduledControllerLike, env: Env): Promise<void> {
    await runScheduled(controller, env);
  },
};
