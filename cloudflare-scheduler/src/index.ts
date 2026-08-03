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
 */

const GITHUB_API_ORIGIN = "https://api.github.com";
const GITHUB_API_VERSION = "2026-03-10";
const USER_AGENT = "finance-news-monitor-scheduler";
const REQUEST_TIMEOUT_MS = 15_000;
/** Error bodies are truncated so a large HTML error page cannot flood the logs. */
const MAX_ERROR_BODY_CHARS = 500;

/**
 * Worker configuration. GITHUB_TOKEN must be provided as a *secret*
 * (`wrangler secret put GITHUB_TOKEN`), never as a plain var in wrangler.jsonc.
 */
export interface Env {
  /** Fine-grained PAT with Actions: read & write on the target repository. */
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
  return_run_details: boolean;
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

/** Every path component is URL-encoded so odd repo/workflow names cannot alter the path. */
export function buildDispatchUrl(config: SchedulerConfig): string {
  const owner = encodeURIComponent(config.owner);
  const repo = encodeURIComponent(config.repo);
  const workflow = encodeURIComponent(config.workflow);
  return `${GITHUB_API_ORIGIN}/repos/${owner}/${repo}/actions/workflows/${workflow}/dispatches`;
}

/** Converts the controller's epoch-ms fire time to a UTC ISO-8601 string. */
export function toUtcIso(scheduledTime: number): string {
  if (!Number.isFinite(scheduledTime)) {
    throw new SchedulerError(`Invalid controller.scheduledTime: ${String(scheduledTime)}.`);
  }
  return new Date(scheduledTime).toISOString();
}

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
    return_run_details: true,
  };
}

function truncate(text: string): string {
  const collapsed = text.replace(/\s+/g, " ").trim();
  return collapsed.length > MAX_ERROR_BODY_CHARS
    ? `${collapsed.slice(0, MAX_ERROR_BODY_CHARS)}…[truncated]`
    : collapsed;
}

/** Pulls run id/url out of a 200 response body, tolerating an unexpected shape. */
function extractRunDetails(payload: unknown): { id?: number | string; url?: string } {
  if (typeof payload !== "object" || payload === null) return {};
  const record = payload as Record<string, unknown>;
  const run =
    typeof record.run === "object" && record.run !== null
      ? (record.run as Record<string, unknown>)
      : record;

  const rawId = run.id ?? run.run_id;
  const rawUrl = run.html_url ?? run.url;
  return {
    id: typeof rawId === "number" || typeof rawId === "string" ? rawId : undefined,
    url: typeof rawUrl === "string" ? rawUrl : undefined,
  };
}

/**
 * Sends the workflow_dispatch request.
 *
 * No retry loop lives here: cron reliability is Cloudflare's job, and the
 * daily workflow is additionally protected by the sent-marker, so a silent
 * in-Worker retry would only produce duplicate runs.
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

  let response: Response;
  try {
    response = await fetchImpl(url, {
      method: "POST",
      headers: {
        Accept: "application/vnd.github+json",
        Authorization: `Bearer ${config.token}`,
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
        "User-Agent": USER_AGENT,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
  } catch (error) {
    // Network/timeout failures must surface — never treated as a successful dispatch.
    const reason = error instanceof Error ? error.message : String(error);
    throw new SchedulerError(`GitHub workflow dispatch request failed: ${reason}`);
  }

  if (!response.ok) {
    let detail = "";
    try {
      detail = truncate(await response.text());
    } catch {
      detail = "<unreadable response body>";
    }
    throw new SchedulerError(
      `GitHub workflow dispatch failed with HTTP ${response.status}: ${detail}`,
    );
  }

  // 204 (classic) and 200 (with return_run_details) are both success.
  if (response.status === 204) {
    console.log(JSON.stringify({ message: "workflow dispatch accepted", status: 204 }));
    return;
  }

  let details: { id?: number | string; url?: string } = {};
  try {
    details = extractRunDetails(await response.json());
  } catch {
    // A 2xx with an unparseable body is still an accepted dispatch.
  }
  console.log(
    JSON.stringify({
      message: "workflow dispatch accepted",
      status: response.status,
      run_id: details.id,
      run_url: details.url,
    }),
  );
}

export async function runScheduled(
  controller: ScheduledControllerLike,
  env: Partial<Env> | null | undefined,
): Promise<void> {
  // Requested up front: a failed dispatch should not be silently replayed.
  controller.noRetry?.();
  await dispatchWorkflow(controller, env);
}

export default {
  async scheduled(controller: ScheduledControllerLike, env: Env): Promise<void> {
    await runScheduled(controller, env);
  },
};
