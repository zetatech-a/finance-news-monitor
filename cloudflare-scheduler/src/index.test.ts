import { describe, expect, it, vi } from "vitest";

import wranglerText from "../wrangler.jsonc?raw";
import {
  ACTIVE_RUN_STATUSES,
  buildDispatchBody,
  buildDispatchUrl,
  buildMarkerUrl,
  buildWorkflowRunsUrl,
  classifyWorkflowRuns,
  computeRetryDelayMs,
  CRON_SLOTS,
  dispatchWorkflow,
  extractRunDetails,
  getKstReportDate,
  isRateLimitResponse,
  isTransientStatus,
  parseRetryAfterMs,
  readRateLimitSignals,
  resolveConfig,
  resolveCronSlot,
  resolveWaitBudget,
  runScheduled,
  SchedulerError,
  toUtcIso,
  UNKNOWN_CRON_SLOT,
  type DispatchBody,
  type Env,
  type ScheduledControllerLike,
  type SchedulerDeps,
} from "./index.js";

// Deliberately fake — no real PAT is used anywhere in this suite, and no test
// performs a real network call, a real sleep or a real clock read.
const FAKE_TOKEN = "test-token-not-a-real-pat";

/** 2026-08-04T00:00:00Z, the KST report day of every cron slot under test. */
const FIXED_NOW = Date.UTC(2026, 7, 4, 0, 0, 0);

function makeEnv(overrides: Partial<Env> = {}): Env {
  return {
    GITHUB_TOKEN: FAKE_TOKEN,
    GITHUB_OWNER: "zetatech-a",
    GITHUB_REPO: "finance-news-monitor",
    GITHUB_WORKFLOW: "daily.yml",
    GITHUB_REF: "main",
    DISPATCH_SEND_EMAIL: "false",
    ...overrides,
  };
}

function makeController(overrides: Partial<ScheduledControllerLike> = {}): ScheduledControllerLike {
  return {
    scheduledTime: Date.UTC(2026, 7, 3, 23, 59, 0),
    cron: "59 23 * * *",
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// Fetch / deps stubs
// ---------------------------------------------------------------------------

type RouteKey = "marker" | "runs" | "dispatch";
/** A canned reaction to one request. Returns a fresh Response or throws. */
type Step = () => Response;

interface RecordedCall {
  route: RouteKey;
  url: string;
  method: string;
  headers: Record<string, string>;
  body?: string;
}

function json(status: number, payload: unknown, headers: Record<string, string> = {}): Step {
  return () =>
    new Response(JSON.stringify(payload), {
      status,
      headers: { "content-type": "application/json", ...headers },
    });
}

function text(status: number, body: string, headers: Record<string, string> = {}): Step {
  return () => new Response(body, { status, headers });
}

function empty(status: number, headers: Record<string, string> = {}): Step {
  return () => new Response(null, { status, headers });
}

const noContent = (): Step => empty(204);

function netError(message = "connection reset"): Step {
  return () => {
    throw new TypeError(message);
  };
}

function timeoutError(): Step {
  return () => {
    const error = new Error("The operation was aborted due to timeout");
    error.name = "TimeoutError";
    throw error;
  };
}

const runsPayload = (...statuses: string[]): Step =>
  json(200, {
    total_count: statuses.length,
    workflow_runs: statuses.map((status, index) => ({
      id: 900 + index,
      status,
      html_url: `https://github.com/zetatech-a/finance-news-monitor/actions/runs/${900 + index}`,
      created_at: "2026-08-03T23:59:30Z",
    })),
  });

const noRuns = (): Step => json(200, { total_count: 0, workflow_runs: [] });

function classifyRoute(url: string): RouteKey {
  if (url.includes("/contents/")) return "marker";
  if (url.includes("/dispatches")) return "dispatch";
  return "runs";
}

/**
 * Routes each request by URL and replays the per-route steps in order. When a
 * route runs out of steps the last one repeats, so "always fails" scenarios stay
 * short; every test that cares asserts the exact call count instead.
 */
function makeFetch(routes: Partial<Record<RouteKey, Step[]>>) {
  const calls: RecordedCall[] = [];
  const counts: Record<RouteKey, number> = { marker: 0, runs: 0, dispatch: 0 };

  const impl = (async (url: string | URL | Request, init?: RequestInit) => {
    const asString = String(url);
    const route = classifyRoute(asString);
    counts[route] += 1;
    calls.push({
      route,
      url: asString,
      method: init?.method ?? "GET",
      headers: { ...((init?.headers ?? {}) as Record<string, string>) },
      body: init?.body === undefined || init?.body === null ? undefined : String(init.body),
    });

    const steps = routes[route];
    if (steps === undefined || steps.length === 0) {
      throw new Error(`unexpected ${route} request in this scenario`);
    }
    return steps[Math.min(counts[route] - 1, steps.length - 1)]();
  }) as unknown as typeof fetch;

  return { impl, calls, counts };
}

function makeDeps(routes: Partial<Record<RouteKey, Step[]>>, overrides: SchedulerDeps = {}) {
  const fetchStub = makeFetch(routes);
  const sleeps: number[] = [];
  const deps: SchedulerDeps = {
    fetchImpl: fetchStub.impl,
    sleep: async (ms: number) => {
      sleeps.push(ms);
    },
    random: () => 0,
    now: () => FIXED_NOW,
    ...overrides,
  };
  return { ...fetchStub, sleeps, deps };
}

interface LogEntry {
  message: string;
  [key: string]: unknown;
}

/** Captures console output as parsed JSON so assertions stay structural. */
function captureLogs() {
  const raw: string[] = [];
  const record = (...args: unknown[]) => {
    raw.push(args.map(String).join(" "));
  };
  const spies = [
    vi.spyOn(console, "log").mockImplementation(record),
    vi.spyOn(console, "warn").mockImplementation(record),
    vi.spyOn(console, "error").mockImplementation(record),
  ];
  return {
    raw,
    entries(): LogEntry[] {
      return raw.flatMap((line) => {
        try {
          return [JSON.parse(line) as LogEntry];
        } catch {
          return [];
        }
      });
    },
    find(message: string): LogEntry | undefined {
      return this.entries().find((entry) => entry.message === message);
    },
    all(message: string): LogEntry[] {
      return this.entries().filter((entry) => entry.message === message);
    },
    restore() {
      for (const spy of spies) spy.mockRestore();
    },
  };
}

/** Runs `body` with console captured, restoring the spies even on failure. */
async function withLogs<T>(body: (logs: ReturnType<typeof captureLogs>) => Promise<T>) {
  const logs = captureLogs();
  try {
    return { value: await body(logs), logs };
  } finally {
    logs.restore();
  }
}

function parseBody(init: RequestInit | RecordedCall): DispatchBody {
  const body = "body" in init ? init.body : undefined;
  return JSON.parse(String(body)) as DispatchBody;
}

/** The happy path: no marker, nothing running, dispatch accepted. */
function healthyRoutes(dispatch: Step[] = [noContent()]) {
  return { marker: [empty(404)], runs: [noRuns()], dispatch };
}

// ---------------------------------------------------------------------------
// Cron triggers and the KST report date
// ---------------------------------------------------------------------------

describe("cron triggers", () => {
  const configuredCrons = (): string[] => {
    const match = /"crons"\s*:\s*(\[[^\]]*\])/.exec(wranglerText);
    expect(match).not.toBeNull();
    return JSON.parse((match as RegExpExecArray)[1]) as string[];
  };

  it("declares exactly four cron expressions in wrangler.jsonc", () => {
    const crons = configuredCrons();
    expect(crons).toHaveLength(4);
    expect(new Set(crons).size).toBe(4);
    expect(crons).toEqual(["59 23 * * *", "14 0 * * *", "29 0 * * *", "44 0 * * *"]);
  });

  it("keeps wrangler.jsonc and CRON_SLOTS in sync", () => {
    expect(configuredCrons().slice().sort()).toEqual(Object.keys(CRON_SLOTS).slice().sort());
  });

  it.each([
    ["59 23 * * *", "primary"],
    ["14 0 * * *", "retry-1"],
    ["29 0 * * *", "retry-2"],
    ["44 0 * * *", "retry-3"],
  ])("maps %s to the %s slot", (cron, slot) => {
    expect(resolveCronSlot(cron)).toBe(slot);
  });

  it("labels an unrecognized cron expression unknown", () => {
    for (const cron of ["0 12 * * *", "", "not a cron", null, undefined]) {
      expect(resolveCronSlot(cron)).toBe(UNKNOWN_CRON_SLOT);
    }
  });

  it.each(["0 12 * * *", "", "not a cron"])(
    "fails closed on the unapproved cron %j without any GitHub request",
    async (cron) => {
      const { deps, calls, counts, sleeps } = makeDeps(healthyRoutes());

      const { logs } = await withLogs(async () => {
        await expect(runScheduled(makeController({ cron }), makeEnv(), deps)).rejects.toThrow(
          SchedulerError,
        );
      });

      expect(calls).toHaveLength(0);
      expect(counts).toEqual({ marker: 0, runs: 0, dispatch: 0 });
      expect(sleeps).toEqual([]);
      expect(logs.find("cron_rejected")?.reason).toBe("unknown_cron_expression");
    },
  );

  it("still calls noRetry() before rejecting an unapproved cron", async () => {
    const noRetry = vi.fn();
    const { deps, calls } = makeDeps(healthyRoutes());

    await withLogs(async () => {
      await expect(
        runScheduled(makeController({ cron: "0 12 * * *", noRetry }), makeEnv(), deps),
      ).rejects.toThrow(/Unrecognized cron expression/);
    });

    expect(noRetry).toHaveBeenCalledTimes(1);
    expect(calls).toHaveLength(0);
  });

  it.each(["59 23 * * *", "14 0 * * *", "29 0 * * *", "44 0 * * *"])(
    "still dispatches normally on the approved cron %j",
    async (cron) => {
      const { deps, counts } = makeDeps(healthyRoutes());

      await withLogs(async () => runScheduled(makeController({ cron }), makeEnv(), deps));

      expect(counts.dispatch).toBe(1);
    },
  );
});

describe("getKstReportDate", () => {
  it.each([
    ["2026-08-05T23:59:00Z", "2026-08-06"],
    ["2026-08-06T00:14:00Z", "2026-08-06"],
    ["2026-08-06T00:29:00Z", "2026-08-06"],
    ["2026-08-06T00:44:00Z", "2026-08-06"],
  ])("maps %s to the KST report date %s", (iso, expected) => {
    expect(getKstReportDate(Date.parse(iso))).toBe(expected);
  });

  it("rolls the date at the KST midnight boundary, not the UTC one", () => {
    expect(getKstReportDate(Date.parse("2026-08-05T14:59:59Z"))).toBe("2026-08-05");
    expect(getKstReportDate(Date.parse("2026-08-05T15:00:00Z"))).toBe("2026-08-06");
  });

  it("depends only on the argument, never on the host clock or timezone", () => {
    const scheduled = Date.parse("2026-08-05T23:59:00Z");
    expect(getKstReportDate(scheduled)).toBe(getKstReportDate(scheduled));
    expect(getKstReportDate(scheduled)).toBe("2026-08-06");
  });

  it("rejects a non-finite scheduled time", () => {
    expect(() => getKstReportDate(NaN)).toThrow(SchedulerError);
    expect(() => getKstReportDate(Number.POSITIVE_INFINITY)).toThrow(SchedulerError);
  });
});

// ---------------------------------------------------------------------------
// Configuration (unchanged behaviour)
// ---------------------------------------------------------------------------

describe("resolveConfig", () => {
  it("rejects each missing required environment variable by name", () => {
    for (const key of [
      "GITHUB_TOKEN",
      "GITHUB_OWNER",
      "GITHUB_REPO",
      "GITHUB_WORKFLOW",
      "GITHUB_REF",
      "DISPATCH_SEND_EMAIL",
    ] as const) {
      const env = makeEnv({ [key]: "" });
      expect(() => resolveConfig(env)).toThrow(SchedulerError);
      expect(() => resolveConfig(env)).toThrow(new RegExp(key));
    }
  });

  it("reports every missing variable at once and never echoes the token value", () => {
    expect(() => resolveConfig({})).toThrow(
      /GITHUB_TOKEN, GITHUB_OWNER, GITHUB_REPO, GITHUB_WORKFLOW, GITHUB_REF, DISPATCH_SEND_EMAIL/,
    );
    try {
      resolveConfig(makeEnv({ GITHUB_OWNER: "   " }));
      expect.unreachable("expected resolveConfig to throw");
    } catch (error) {
      expect((error as Error).message).not.toContain(FAKE_TOKEN);
    }
  });

  it('rejects DISPATCH_SEND_EMAIL values other than "true"/"false"', () => {
    for (const value of ["TRUE", "yes", "1", "0", "on", "False "]) {
      expect(() => resolveConfig(makeEnv({ DISPATCH_SEND_EMAIL: value }))).toThrow(
        /DISPATCH_SEND_EMAIL must be exactly/,
      );
    }
  });
});

// ---------------------------------------------------------------------------
// URL construction
// ---------------------------------------------------------------------------

describe("buildDispatchUrl", () => {
  it("targets the workflow dispatch endpoint", () => {
    expect(buildDispatchUrl(resolveConfig(makeEnv()))).toBe(
      "https://api.github.com/repos/zetatech-a/finance-news-monitor/actions/workflows/daily.yml/dispatches",
    );
  });

  it("URL-encodes owner, repo and workflow components", () => {
    const config = resolveConfig(
      makeEnv({ GITHUB_OWNER: "own er", GITHUB_REPO: "re/po", GITHUB_WORKFLOW: "a b.yml" }),
    );
    expect(buildDispatchUrl(config)).toBe(
      "https://api.github.com/repos/own%20er/re%2Fpo/actions/workflows/a%20b.yml/dispatches",
    );
  });
});

describe("buildMarkerUrl", () => {
  it("keeps the marker path separators literal and encodes each segment", () => {
    expect(buildMarkerUrl(resolveConfig(makeEnv()), "2026-08-04")).toBe(
      "https://api.github.com/repos/zetatech-a/finance-news-monitor/contents/reports/_sent/2026-08-04_email_sent.json?ref=main",
    );
  });

  it("never collapses the path separators into %2F", () => {
    const url = buildMarkerUrl(resolveConfig(makeEnv()), "2026-08-04");
    expect(url).toContain("/contents/reports/_sent/");
    expect(url).not.toContain("reports%2F_sent");
  });

  it("encodes owner, repo and the ref query parameter", () => {
    const config = resolveConfig(
      makeEnv({ GITHUB_OWNER: "own er", GITHUB_REPO: "re/po", GITHUB_REF: "feature/a b&c" }),
    );
    const url = buildMarkerUrl(config, "2026-08-04");
    expect(url).toContain("/repos/own%20er/re%2Fpo/contents/");
    expect(url).toContain("?ref=feature%2Fa+b%26c");
  });
});

describe("buildWorkflowRunsUrl", () => {
  it("queries the workflow runs of the configured branch without an event filter", () => {
    expect(buildWorkflowRunsUrl(resolveConfig(makeEnv()))).toBe(
      "https://api.github.com/repos/zetatech-a/finance-news-monitor/actions/workflows/daily.yml/runs?branch=main&per_page=20",
    );
    expect(buildWorkflowRunsUrl(resolveConfig(makeEnv()))).not.toContain("event=");
  });

  it("encodes the workflow path segment and a ref with special characters", () => {
    const config = resolveConfig(
      makeEnv({ GITHUB_WORKFLOW: "a b.yml", GITHUB_REF: "feature/a b&c" }),
    );
    const url = buildWorkflowRunsUrl(config);
    expect(url).toContain("/actions/workflows/a%20b.yml/runs?");
    expect(url).toContain("branch=feature%2Fa+b%26c");
  });
});

// ---------------------------------------------------------------------------
// Dispatch body / run details
// ---------------------------------------------------------------------------

describe("buildDispatchBody", () => {
  it("converts controller.scheduledTime to a UTC ISO-8601 string", () => {
    const body = buildDispatchBody(resolveConfig(makeEnv()), makeController());
    expect(body.inputs.scheduled_for).toBe("2026-08-03T23:59:00.000Z");
    expect(toUtcIso(0)).toBe("1970-01-01T00:00:00.000Z");
  });

  it("rejects a non-finite scheduledTime", () => {
    expect(() => buildDispatchBody(resolveConfig(makeEnv()), makeController({ scheduledTime: NaN })))
      .toThrow(SchedulerError);
  });

  it("omits the legacy return_run_details parameter", () => {
    const body = buildDispatchBody(resolveConfig(makeEnv()), makeController());
    expect(Object.keys(body).sort()).toEqual(["inputs", "ref"]);
    expect("return_run_details" in body).toBe(false);
  });
});

describe("extractRunDetails", () => {
  const RUN_URL = "https://github.com/zetatech-a/finance-news-monitor/actions/runs/987654321";
  const API_URL = "https://api.github.com/repos/zetatech-a/finance-news-monitor/actions/runs/987654321";

  // The exact 200 payload shape is not pinned down, so every plausible shape
  // must yield the same log fields.
  it.each([
    [
      "flat workflow_run_* fields",
      { workflow_run_id: 987654321, workflow_run_html_url: RUN_URL, workflow_run_url: API_URL },
    ],
    ["flat id/html_url fields", { id: 987654321, html_url: RUN_URL }],
    ["run envelope", { run: { id: 987654321, html_url: RUN_URL } }],
    ["workflow_run envelope", { workflow_run: { id: 987654321, html_url: RUN_URL } }],
    [
      "envelope using workflow_run_* names",
      { run: { workflow_run_id: 987654321, workflow_run_html_url: RUN_URL } },
    ],
  ])("reads the run id and HTML url from %s", (_label, payload) => {
    expect(extractRunDetails(payload)).toEqual({ id: 987654321, url: RUN_URL });
  });

  it("prefers the HTML url over the API url", () => {
    expect(extractRunDetails({ workflow_run_url: API_URL, workflow_run_html_url: RUN_URL }).url).toBe(
      RUN_URL,
    );
    // ...but still reports the API url when no HTML url is present.
    expect(extractRunDetails({ workflow_run_url: API_URL }).url).toBe(API_URL);
  });

  it("accepts a string run id and ignores empty or wrongly typed values", () => {
    expect(extractRunDetails({ workflow_run_id: "987654321" }).id).toBe("987654321");
    expect(extractRunDetails({ workflow_run_id: "", id: 42 }).id).toBe(42);
    expect(extractRunDetails({ workflow_run_html_url: { nested: true }, url: API_URL }).url).toBe(
      API_URL,
    );
  });

  it("reports the top-level key names when no known field matches", () => {
    expect(extractRunDetails({ something_else: 1, another: 2 })).toEqual({
      unrecognizedShapeKeys: ["something_else", "another"],
    });
  });

  it("returns nothing for a non-object or empty payload", () => {
    expect(extractRunDetails(null)).toEqual({});
    expect(extractRunDetails("not json")).toEqual({});
    expect(extractRunDetails({})).toEqual({});
  });
});

// ---------------------------------------------------------------------------
// Transient / rate-limit classification
// ---------------------------------------------------------------------------

describe("transient and rate-limit classification", () => {
  it("treats 408/429/5xx as transient and everything else as an answer", () => {
    for (const status of [408, 429, 500, 502, 503, 504, 599]) {
      expect(isTransientStatus(status)).toBe(true);
    }
    for (const status of [200, 204, 301, 400, 401, 403, 404, 422]) {
      expect(isTransientStatus(status)).toBe(false);
    }
  });

  const signalsFrom = (headers: Record<string, string>) =>
    readRateLimitSignals(new Headers(headers));

  it("always treats 429 as rate limiting", () => {
    expect(isRateLimitResponse(429, signalsFrom({}), "")).toBe(true);
  });

  it.each([
    ["a Retry-After header", { "Retry-After": "30" }, ""],
    ["X-RateLimit-Remaining: 0", { "X-RateLimit-Remaining": "0" }, ""],
    ["a primary rate-limit message", {}, "API rate limit exceeded for user"],
    ["a secondary rate-limit message", {}, "You have exceeded a secondary rate limit"],
    ["an abuse-detection message", {}, "triggered an abuse detection mechanism"],
  ])("recognizes a 403 as rate limiting from %s", (_label, headers, detail) => {
    expect(isRateLimitResponse(403, signalsFrom(headers), detail)).toBe(true);
  });

  it("treats an ordinary permission 403 as non-transient", () => {
    const detail = "Resource not accessible by personal access token";
    expect(isRateLimitResponse(403, signalsFrom({ "X-RateLimit-Remaining": "4999" }), detail)).toBe(
      false,
    );
    expect(isRateLimitResponse(404, signalsFrom({}), "")).toBe(false);
  });

  it("parses Retry-After only as a canonical non-negative decimal integer", () => {
    expect(parseRetryAfterMs("30")).toBe(30_000);
    expect(parseRetryAfterMs(" 5 ")).toBe(5_000);
    expect(parseRetryAfterMs("0")).toBe(0);
  });

  it.each([
    ["an empty string", ""],
    ["whitespace only", "   "],
    ["scientific notation", "1e1"],
    ["hexadecimal", "0x10"],
    ["a leading plus", "+10"],
    ["a negative value", "-1"],
    ["a fractional value", "1.5"],
    ["a trailing unit", "10s"],
    ["an HTTP-date", "Wed, 21 Oct 2026 07:28:00 GMT"],
    ["a non-numeric value", "soon"],
    ["Infinity", "Infinity"],
    ["a value beyond safe integers", "99999999999999999999"],
  ])("rejects Retry-After given as %s", (_label, value) => {
    expect(parseRetryAfterMs(value)).toBeUndefined();
  });

  it("rejects a missing Retry-After header", () => {
    expect(parseRetryAfterMs(null)).toBeUndefined();
    expect(parseRetryAfterMs(undefined)).toBeUndefined();
  });

  it("applies the same strictness to X-RateLimit-Reset", () => {
    expect(readRateLimitSignals(new Headers({ "X-RateLimit-Reset": "1780000000" }))
      .resetEpochSeconds).toBe(1780000000);
    for (const value of ["", "  ", "1e9", "0x10", "-1", "17.5", "later"]) {
      expect(
        readRateLimitSignals(new Headers({ "X-RateLimit-Reset": value })).resetEpochSeconds,
      ).toBeUndefined();
    }
  });

  it("never turns a malformed Retry-After into an immediate retry", () => {
    // The dangerous regression: Number("") === 0 would mean "retry right now".
    for (const value of ["", "   "]) {
      const signals = readRateLimitSignals(new Headers({ "Retry-After": value }));
      expect(signals.retryAfterMs).toBeUndefined();
      expect(signals.hasRetryAfterHeader).toBe(true);
      expect(resolveWaitBudget("rate_limit", signals, 1, () => 0, () => FIXED_NOW)).toEqual({
        kind: "defer",
        reason: "rate_limit_window_unknown",
      });
    }
  });

  it("defers when the quota is exhausted but the reset value is unusable", () => {
    const resetIn10 = String(Math.floor(FIXED_NOW / 1_000) + 10);
    const unusable: Record<string, string>[] = [
      { "X-RateLimit-Remaining": "0" },
      { "X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1e9" },
      { "X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "" },
      { "X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "not-a-time" },
    ];
    for (const headers of unusable) {
      expect(
        resolveWaitBudget("rate_limit", readRateLimitSignals(new Headers(headers)), 1, () => 0, () => FIXED_NOW),
      ).toEqual({ kind: "defer", reason: "rate_limit_reset_unusable" });
    }
    // ...and a well-formed reset with an exhausted quota is still honoured.
    expect(
      resolveWaitBudget(
        "rate_limit",
        readRateLimitSignals(
          new Headers({ "X-RateLimit-Remaining": "0", "X-RateLimit-Reset": resetIn10 }),
        ),
        1,
        () => 0,
        () => FIXED_NOW,
      ),
    ).toEqual({ kind: "wait", delayMs: 10_000 });
  });

  it("never shortens a Retry-After it cannot honour", () => {
    const random = () => 0;
    const now = () => FIXED_NOW;
    expect(resolveWaitBudget("rate_limit", signalsFrom({ "Retry-After": "10" }), 1, random, now)).toEqual(
      { kind: "wait", delayMs: 10_000 },
    );
    expect(resolveWaitBudget("rate_limit", signalsFrom({ "Retry-After": "15" }), 1, random, now)).toEqual(
      { kind: "wait", delayMs: 15_000 },
    );
    expect(resolveWaitBudget("rate_limit", signalsFrom({ "Retry-After": "16" }), 1, random, now)).toEqual(
      { kind: "defer", reason: "retry_after_exceeds_budget" },
    );
  });

  it("uses X-RateLimit-Reset only when the primary quota is provably exhausted", () => {
    const random = () => 0;
    const now = () => FIXED_NOW;
    const resetIn = (seconds: number) => String(Math.floor(FIXED_NOW / 1_000) + seconds);

    // remaining === "0": the reset time is trustworthy.
    expect(
      resolveWaitBudget(
        "rate_limit",
        signalsFrom({ "X-RateLimit-Remaining": "0", "X-RateLimit-Reset": resetIn(10) }),
        1,
        random,
        now,
      ),
    ).toEqual({ kind: "wait", delayMs: 10_000 });

    // remaining > 0 (secondary limit): a reset inside the budget must NOT be used.
    expect(
      resolveWaitBudget(
        "rate_limit",
        signalsFrom({ "X-RateLimit-Remaining": "4999", "X-RateLimit-Reset": resetIn(10) }),
        1,
        random,
        now,
      ),
    ).toEqual({ kind: "defer", reason: "rate_limit_window_unknown" });

    // remaining missing: same rule.
    expect(
      resolveWaitBudget(
        "rate_limit",
        signalsFrom({ "X-RateLimit-Reset": resetIn(10) }),
        1,
        random,
        now,
      ),
    ).toEqual({ kind: "defer", reason: "rate_limit_window_unknown" });

    // remaining === "0" but the window is longer than we may wait.
    expect(
      resolveWaitBudget(
        "rate_limit",
        signalsFrom({ "X-RateLimit-Remaining": "0", "X-RateLimit-Reset": resetIn(60) }),
        1,
        random,
        now,
      ),
    ).toEqual({ kind: "defer", reason: "rate_limit_reset_exceeds_budget" });
  });

  it("falls back to bounded exponential backoff for ordinary transient failures", () => {
    const random = () => 0;
    const now = () => FIXED_NOW;
    expect(resolveWaitBudget("transient", signalsFrom({}), 1, random, now)).toEqual({
      kind: "wait",
      delayMs: 1_000,
    });
    expect(resolveWaitBudget("transient", signalsFrom({}), 2, random, now)).toEqual({
      kind: "wait",
      delayMs: 2_000,
    });
    expect(computeRetryDelayMs(1, () => 0)).toBe(1_000);
    expect(computeRetryDelayMs(2, () => 0.5)).toBe(2_125);
    expect(computeRetryDelayMs(99, () => 0.99)).toBe(15_000);
  });
});

// ---------------------------------------------------------------------------
// Workflow-runs payload classification
// ---------------------------------------------------------------------------

describe("classifyWorkflowRuns", () => {
  it.each([...ACTIVE_RUN_STATUSES])("treats %s as an active run", (status) => {
    const result = classifyWorkflowRuns({ workflow_runs: [{ id: 7, status }] });
    expect(result.kind).toBe("active");
  });

  it("reports the id, status and html_url of the active run", () => {
    const result = classifyWorkflowRuns({
      workflow_runs: [
        { id: 1, status: "completed" },
        { id: 2, status: "IN_PROGRESS ", html_url: "https://example.test/2", created_at: "t" },
      ],
    });
    expect(result).toEqual({
      kind: "active",
      status: "in_progress",
      id: 2,
      url: "https://example.test/2",
      createdAt: "t",
    });
  });

  it("treats an empty list and completed-only runs as nothing running", () => {
    expect(classifyWorkflowRuns({ total_count: 0, workflow_runs: [] })).toEqual({ kind: "none" });
    expect(
      classifyWorkflowRuns({ workflow_runs: [{ status: "completed" }, { status: "completed" }] }),
    ).toEqual({ kind: "none" });
  });

  it("never reads a missing workflow_runs array as an empty one", () => {
    expect(classifyWorkflowRuns({ message: "Not Found" })).toEqual({
      kind: "malformed",
      reason: "missing_workflow_runs",
    });
  });

  it.each([
    ["a non-object payload", "not json", "unexpected_payload_type"],
    ["an array payload", [], "unexpected_payload_type"],
    ["null", null, "unexpected_payload_type"],
  ])("reports %s as malformed", (_label, payload, reason) => {
    expect(classifyWorkflowRuns(payload)).toEqual({ kind: "malformed", reason });
  });

  it("reports a run entry without a string status as malformed", () => {
    expect(classifyWorkflowRuns({ workflow_runs: [{ id: 1 }] })).toEqual({
      kind: "malformed",
      reason: "missing_run_status",
    });
    expect(classifyWorkflowRuns({ workflow_runs: ["queued"] })).toEqual({
      kind: "malformed",
      reason: "unexpected_run_entry",
    });
  });

  it("flags an unrecognized status instead of guessing that it is inactive", () => {
    expect(classifyWorkflowRuns({ workflow_runs: [{ status: "brand_new_state" }] })).toEqual({
      kind: "unknown_status",
      reason: "unknown_run_status_brand_new_state",
    });
  });

  it("ignores conclusion entirely", () => {
    expect(
      classifyWorkflowRuns({ workflow_runs: [{ status: "completed", conclusion: "failure" }] }),
    ).toEqual({ kind: "none" });
  });
});

// ---------------------------------------------------------------------------
// dispatchWorkflow (single-attempt entry point, unchanged semantics)
// ---------------------------------------------------------------------------

describe("dispatchWorkflow", () => {
  function stubFetch(response: Response) {
    const calls: { url: string; init: RequestInit }[] = [];
    const impl = (async (url: string | URL | Request, init?: RequestInit) => {
      calls.push({ url: String(url), init: init ?? {} });
      return response;
    }) as unknown as typeof fetch;
    return { impl, calls };
  }

  it("builds the exact URL, headers and body for a healthy environment", async () => {
    const { impl, calls } = stubFetch(new Response(null, { status: 204 }));

    await dispatchWorkflow(makeController(), makeEnv(), impl);

    expect(calls).toHaveLength(1);
    const [call] = calls;
    expect(call.url).toBe(
      "https://api.github.com/repos/zetatech-a/finance-news-monitor/actions/workflows/daily.yml/dispatches",
    );
    expect(call.init.method).toBe("POST");
    expect(call.init.headers).toEqual({
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${FAKE_TOKEN}`,
      "X-GitHub-Api-Version": "2026-03-10",
      "User-Agent": "finance-news-monitor-scheduler",
      "Content-Type": "application/json",
    });
    expect(call.init.signal).toBeInstanceOf(AbortSignal);
    expect(parseBody(call.init)).toEqual({
      ref: "main",
      inputs: {
        send_email: false,
        force_send: false,
        wait_until_target: false,
        trigger_source: "cloudflare-cron",
        scheduled_for: "2026-08-03T23:59:00.000Z",
        cron_expression: "59 23 * * *",
      },
    });
    expect("return_run_details" in parseBody(call.init)).toBe(false);
  });

  it('sends boolean false when DISPATCH_SEND_EMAIL is "false"', async () => {
    const { impl, calls } = stubFetch(new Response(null, { status: 204 }));

    await dispatchWorkflow(makeController(), makeEnv({ DISPATCH_SEND_EMAIL: "false" }), impl);

    expect(parseBody(calls[0].init).inputs.send_email).toBe(false);
  });

  it('sends boolean true when DISPATCH_SEND_EMAIL is "true"', async () => {
    const { impl, calls } = stubFetch(new Response(null, { status: 204 }));

    await dispatchWorkflow(makeController(), makeEnv({ DISPATCH_SEND_EMAIL: "true" }), impl);

    expect(parseBody(calls[0].init).inputs.send_email).toBe(true);
  });

  it("treats 204 as success", async () => {
    const { impl } = stubFetch(new Response(null, { status: 204 }));

    await expect(dispatchWorkflow(makeController(), makeEnv(), impl)).resolves.toBeUndefined();
  });

  it("treats 200 as success and logs run details without the token", async () => {
    const logs: string[] = [];
    const spy = vi.spyOn(console, "log").mockImplementation((...args: unknown[]) => {
      logs.push(args.map(String).join(" "));
    });
    const { impl } = stubFetch(
      Response.json(
        {
          workflow_run_id: 987654321,
          workflow_run_html_url:
            "https://github.com/zetatech-a/finance-news-monitor/actions/runs/987654321",
        },
        { status: 200 },
      ),
    );

    try {
      await expect(dispatchWorkflow(makeController(), makeEnv(), impl)).resolves.toBeUndefined();
    } finally {
      spy.mockRestore();
    }

    const joined = logs.join("\n");
    expect(joined).toContain("987654321");
    expect(joined).toContain("/actions/runs/987654321");
    expect(joined).not.toContain(FAKE_TOKEN);
    expect(joined).not.toContain("Bearer");
  });

  it("still succeeds on a 2xx with an unparseable body", async () => {
    const { impl } = stubFetch(new Response("not json", { status: 201 }));

    await expect(dispatchWorkflow(makeController(), makeEnv(), impl)).resolves.toBeUndefined();
  });

  it("still succeeds on a 2xx with no body at all", async () => {
    const { impl } = stubFetch(new Response(null, { status: 200 }));

    await expect(dispatchWorkflow(makeController(), makeEnv(), impl)).resolves.toBeUndefined();
  });

  it("fails on a non-2xx response, reporting status and a truncated body", async () => {
    const { impl } = stubFetch(
      new Response("x".repeat(5000), { status: 422, statusText: "Unprocessable Entity" }),
    );

    const error = await dispatchWorkflow(makeController(), makeEnv(), impl).catch((e) => e);

    expect(error).toBeInstanceOf(SchedulerError);
    expect(error.message).toContain("HTTP 422");
    expect(error.message).toContain("[truncated]");
    expect(error.message.length).toBeLessThan(700);
    expect(error.message).not.toContain(FAKE_TOKEN);
  });

  it("fails on a network error instead of reporting success", async () => {
    const impl = (async () => {
      throw new TypeError("connection reset");
    }) as unknown as typeof fetch;

    const error = await dispatchWorkflow(makeController(), makeEnv(), impl).catch((e) => e);

    expect(error).toBeInstanceOf(SchedulerError);
    expect(error.message).toContain("connection reset");
  });

  it("has no retry budget of its own: a 500 fails immediately", async () => {
    const { impl, calls } = stubFetch(new Response("boom", { status: 500 }));

    await expect(dispatchWorkflow(makeController(), makeEnv(), impl)).rejects.toThrow(
      /HTTP 500/,
    );
    expect(calls).toHaveLength(1);
  });

  it("does not dispatch at all when configuration is invalid", async () => {
    const { impl, calls } = stubFetch(new Response(null, { status: 204 }));

    await expect(
      dispatchWorkflow(makeController(), makeEnv({ GITHUB_REF: "" }), impl),
    ).rejects.toThrow(/GITHUB_REF/);
    expect(calls).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// runScheduled: sent-marker preflight
// ---------------------------------------------------------------------------

describe("runScheduled — sent-marker preflight", () => {
  it("skips the runs lookup and the dispatch when today's marker exists", async () => {
    const { deps, counts } = makeDeps({ marker: [json(200, { name: "marker" })] });

    const { logs } = await withLogs(async () => runScheduled(makeController(), makeEnv(), deps));

    expect(counts.marker).toBe(1);
    expect(counts.runs).toBe(0);
    expect(counts.dispatch).toBe(0);
    expect(logs.find("skip_marker_exists")).toBeDefined();
    expect(logs.find("skip_marker_exists")?.report_date).toBe("2026-08-04");
  });

  it("requests today's KST marker path", async () => {
    const { deps, calls } = makeDeps(healthyRoutes());

    await withLogs(async () => runScheduled(makeController(), makeEnv(), deps));

    expect(calls[0].url).toContain("/contents/reports/_sent/2026-08-04_email_sent.json?ref=main");
    expect(calls[0].method).toBe("GET");
  });

  it("continues to the runs lookup on 404", async () => {
    const { deps, counts } = makeDeps(healthyRoutes());

    await withLogs(async () => runScheduled(makeController(), makeEnv(), deps));

    expect(counts.marker).toBe(1);
    expect(counts.runs).toBe(1);
    expect(counts.dispatch).toBe(1);
  });

  it.each([401, 403, 400, 422])("fails without dispatching on HTTP %i", async (status) => {
    const detail = status === 403 ? "Resource not accessible by personal access token" : "nope";
    const { deps, counts } = makeDeps({ marker: [text(status, detail)] });

    await withLogs(async () => {
      await expect(runScheduled(makeController(), makeEnv(), deps)).rejects.toThrow(SchedulerError);
    });

    expect(counts.runs).toBe(0);
    expect(counts.dispatch).toBe(0);
  });

  it("retries a transient failure once and proceeds when the retry succeeds", async () => {
    const { deps, counts, sleeps } = makeDeps({
      marker: [text(500, "boom"), empty(404)],
      runs: [noRuns()],
      dispatch: [noContent()],
    });

    await withLogs(async () => runScheduled(makeController(), makeEnv(), deps));

    expect(counts.marker).toBe(2);
    expect(sleeps).toEqual([1_000]);
    expect(counts.dispatch).toBe(1);
  });

  it("degrades to a dispatch after two transient failures, without a third attempt", async () => {
    const { deps, counts, sleeps } = makeDeps({
      marker: [text(503, "unavailable")],
      runs: [noRuns()],
      dispatch: [noContent()],
    });

    const { logs } = await withLogs(async () => runScheduled(makeController(), makeEnv(), deps));

    expect(counts.marker).toBe(2);
    // Exactly one sleep: the last allowed attempt is never followed by a no-op wait.
    expect(sleeps).toEqual([1_000]);
    expect(logs.find("preflight_degraded")?.stage).toBe("sent_marker");
    expect(counts.dispatch).toBe(1);
  });

  it("degrades on an unexpected status without retrying", async () => {
    const { deps, counts, sleeps } = makeDeps({
      marker: [empty(204)],
      runs: [noRuns()],
      dispatch: [noContent()],
    });

    const { logs } = await withLogs(async () => runScheduled(makeController(), makeEnv(), deps));

    expect(counts.marker).toBe(1);
    expect(sleeps).toEqual([]);
    expect(logs.find("preflight_degraded")?.reason).toBe("unexpected_status_204");
    expect(counts.dispatch).toBe(1);
  });

  it("defers instead of dispatching when the marker lookup is rate limited", async () => {
    const { deps, counts, sleeps } = makeDeps({
      marker: [text(403, "API rate limit exceeded for user", { "X-RateLimit-Remaining": "0" })],
    });

    const { logs } = await withLogs(async () => {
      await expect(runScheduled(makeController(), makeEnv(), deps)).rejects.toThrow(
        /next cron slot will recover/,
      );
    });

    // No reset header we may trust -> defer immediately, no retry, no POST.
    expect(counts.marker).toBe(1);
    expect(counts.runs).toBe(0);
    expect(counts.dispatch).toBe(0);
    expect(sleeps).toEqual([]);
    // Quota provably exhausted (remaining "0") but no reset header to time the
    // wait from, so there is no safe delay to compute.
    expect(logs.find("preflight_request_deferred")?.defer_reason).toBe("rate_limit_reset_unusable");
    expect(logs.find("preflight_deferred")?.stage).toBe("sent_marker");
  });

  it("defers when a 429 asks for longer than the invocation budget", async () => {
    const { deps, counts, sleeps } = makeDeps({ marker: [text(429, "slow down", { "Retry-After": "60" })] });

    await withLogs(async () => {
      await expect(runScheduled(makeController(), makeEnv(), deps)).rejects.toThrow(SchedulerError);
    });

    expect(counts.marker).toBe(1);
    expect(counts.dispatch).toBe(0);
    expect(sleeps).toEqual([]);
  });

  it("honours a short Retry-After and retries once", async () => {
    const resetIn10 = String(Math.floor(FIXED_NOW / 1_000) + 10);
    const { deps, counts, sleeps } = makeDeps({
      marker: [
        text(403, "API rate limit exceeded", {
          "X-RateLimit-Remaining": "0",
          "X-RateLimit-Reset": resetIn10,
        }),
        empty(404),
      ],
      runs: [noRuns()],
      dispatch: [noContent()],
    });

    await withLogs(async () => runScheduled(makeController(), makeEnv(), deps));

    expect(sleeps).toEqual([10_000]);
    expect(counts.marker).toBe(2);
    expect(counts.dispatch).toBe(1);
  });

  it("defers, never degrades, when both attempts are rate limited", async () => {
    const resetIn5 = String(Math.floor(FIXED_NOW / 1_000) + 5);
    const { deps, counts, sleeps } = makeDeps({
      marker: [
        text(403, "API rate limit exceeded", {
          "X-RateLimit-Remaining": "0",
          "X-RateLimit-Reset": resetIn5,
        }),
      ],
    });

    const { logs } = await withLogs(async () => {
      await expect(runScheduled(makeController(), makeEnv(), deps)).rejects.toThrow(SchedulerError);
    });

    expect(counts.marker).toBe(2);
    expect(sleeps).toEqual([5_000]);
    expect(counts.runs).toBe(0);
    expect(counts.dispatch).toBe(0);
    expect(String(logs.find("preflight_deferred")?.reason)).toContain("attempts_exhausted");
  });

  it("degrades when a rate-limited attempt is followed by an ordinary transient one", async () => {
    const resetIn5 = String(Math.floor(FIXED_NOW / 1_000) + 5);
    const { deps, counts } = makeDeps({
      marker: [
        text(429, "slow down", { "X-RateLimit-Remaining": "0", "X-RateLimit-Reset": resetIn5 }),
        text(500, "boom"),
      ],
      runs: [noRuns()],
      dispatch: [noContent()],
    });

    const { logs } = await withLogs(async () => runScheduled(makeController(), makeEnv(), deps));

    // The last failure was not rate limiting, so degrading is allowed again.
    expect(counts.marker).toBe(2);
    expect(logs.find("preflight_degraded")?.stage).toBe("sent_marker");
    expect(counts.dispatch).toBe(1);
  });
});

// ---------------------------------------------------------------------------
// runScheduled: active-run preflight
// ---------------------------------------------------------------------------

describe("runScheduled — active-run preflight", () => {
  it.each([...ACTIVE_RUN_STATUSES])("does not dispatch while a run is %s", async (status) => {
    const { deps, counts } = makeDeps({ marker: [empty(404)], runs: [runsPayload(status)] });

    const { logs } = await withLogs(async () => runScheduled(makeController(), makeEnv(), deps));

    expect(counts.dispatch).toBe(0);
    const skip = logs.find("skip_workflow_active");
    expect(skip?.active_run_status).toBe(status);
    expect(skip?.run_id).toBe(900);
    expect(skip?.run_url).toContain("/actions/runs/900");
  });

  it("dispatches when only completed runs exist", async () => {
    const { deps, counts } = makeDeps({
      marker: [empty(404)],
      runs: [runsPayload("completed", "completed")],
      dispatch: [noContent()],
    });

    await withLogs(async () => runScheduled(makeController(), makeEnv(), deps));

    expect(counts.dispatch).toBe(1);
  });

  it("dispatches when the run list is empty", async () => {
    const { deps, counts } = makeDeps(healthyRoutes());

    await withLogs(async () => runScheduled(makeController(), makeEnv(), deps));

    expect(counts.dispatch).toBe(1);
  });

  it("queries the runs endpoint without an event filter", async () => {
    const { deps, calls } = makeDeps(healthyRoutes());

    await withLogs(async () => runScheduled(makeController(), makeEnv(), deps));

    const runsCall = calls.find((call) => call.route === "runs");
    expect(runsCall?.url).toContain("branch=main&per_page=20");
    expect(runsCall?.url).not.toContain("event=");
  });

  it("warns and degrades to a dispatch on a malformed payload", async () => {
    const { deps, counts } = makeDeps({
      marker: [empty(404)],
      runs: [json(200, { message: "Not Found" })],
      dispatch: [noContent()],
    });

    const { logs } = await withLogs(async () => runScheduled(makeController(), makeEnv(), deps));

    expect(logs.find("preflight_degraded")?.reason).toBe("missing_workflow_runs");
    expect(counts.dispatch).toBe(1);
  });

  it("degrades to a dispatch on an unparseable body", async () => {
    const { deps, counts } = makeDeps({
      marker: [empty(404)],
      runs: [text(200, "not json")],
      dispatch: [noContent()],
    });

    const { logs } = await withLogs(async () => runScheduled(makeController(), makeEnv(), deps));

    expect(logs.find("preflight_degraded")?.reason).toBe("unparseable_body");
    expect(counts.dispatch).toBe(1);
  });

  it("retries a transient failure once and proceeds when the retry succeeds", async () => {
    const { deps, counts, sleeps } = makeDeps({
      marker: [empty(404)],
      runs: [netError("socket hang up"), noRuns()],
      dispatch: [noContent()],
    });

    await withLogs(async () => runScheduled(makeController(), makeEnv(), deps));

    expect(counts.runs).toBe(2);
    expect(sleeps).toEqual([1_000]);
    expect(counts.dispatch).toBe(1);
  });

  it("degrades to a dispatch after two transient failures", async () => {
    const { deps, counts } = makeDeps({
      marker: [empty(404)],
      runs: [text(502, "bad gateway")],
      dispatch: [noContent()],
    });

    const { logs } = await withLogs(async () => runScheduled(makeController(), makeEnv(), deps));

    expect(counts.runs).toBe(2);
    expect(logs.find("preflight_degraded")?.stage).toBe("workflow_runs");
    expect(counts.dispatch).toBe(1);
  });

  it.each([400, 401, 403, 404, 422])("fails without dispatching on HTTP %i", async (status) => {
    const detail = status === 403 ? "Resource not accessible by personal access token" : "nope";
    const { deps, counts } = makeDeps({ marker: [empty(404)], runs: [text(status, detail)] });

    await withLogs(async () => {
      await expect(runScheduled(makeController(), makeEnv(), deps)).rejects.toThrow(SchedulerError);
    });

    expect(counts.dispatch).toBe(0);
  });

  it("defers instead of degrading when the runs lookup is rate limited", async () => {
    const { deps, counts } = makeDeps({
      marker: [empty(404)],
      runs: [text(429, "slow down", { "Retry-After": "120" })],
    });

    const { logs } = await withLogs(async () => {
      await expect(runScheduled(makeController(), makeEnv(), deps)).rejects.toThrow(SchedulerError);
    });

    expect(counts.dispatch).toBe(0);
    expect(logs.find("preflight_deferred")?.stage).toBe("workflow_runs");
  });

  it("does not dispatch on an unknown run status only after a degraded warning", async () => {
    const { deps, counts } = makeDeps({
      marker: [empty(404)],
      runs: [runsPayload("brand_new_state")],
      dispatch: [noContent()],
    });

    const { logs } = await withLogs(async () => runScheduled(makeController(), makeEnv(), deps));

    expect(logs.find("preflight_degraded")?.reason).toBe("unknown_run_status_brand_new_state");
    expect(counts.dispatch).toBe(1);
  });
});

// ---------------------------------------------------------------------------
// runScheduled: dispatch, retries and reconciliation
// ---------------------------------------------------------------------------

describe("runScheduled — dispatch contract", () => {
  it.each([204, 200])("treats HTTP %i as success on the first POST", async (status) => {
    const step = status === 204 ? noContent() : json(200, { workflow_run_id: 5, html_url: "u" });
    const { deps, counts } = makeDeps(healthyRoutes([step]));

    const { logs } = await withLogs(async () => runScheduled(makeController(), makeEnv(), deps));

    expect(counts.dispatch).toBe(1);
    expect(logs.find("workflow_dispatch_accepted")?.status).toBe(status);
  });

  it("logs the run details of a 200 response", async () => {
    const { deps } = makeDeps(
      healthyRoutes([
        json(200, {
          workflow_run_id: 987654321,
          workflow_run_html_url:
            "https://github.com/zetatech-a/finance-news-monitor/actions/runs/987654321",
        }),
      ]),
    );

    const { logs } = await withLogs(async () => runScheduled(makeController(), makeEnv(), deps));

    const accepted = logs.find("workflow_dispatch_accepted");
    expect(accepted?.run_id).toBe(987654321);
    expect(accepted?.run_url).toContain("/actions/runs/987654321");
  });

  it.each([
    ["an empty body", empty(200)],
    ["an unparseable body", text(200, "not json")],
  ])("treats a 2xx with %s as success", async (_label, step) => {
    const { deps, counts } = makeDeps(healthyRoutes([step]));

    await withLogs(async () => runScheduled(makeController(), makeEnv(), deps));

    expect(counts.dispatch).toBe(1);
  });

  it("sends the documented inputs and no return_run_details", async () => {
    const { deps, calls } = makeDeps(healthyRoutes());

    await withLogs(async () =>
      runScheduled(makeController(), makeEnv({ DISPATCH_SEND_EMAIL: "true" }), deps),
    );

    const dispatchCall = calls.find((call) => call.route === "dispatch");
    expect(dispatchCall?.method).toBe("POST");
    const body = parseBody(dispatchCall as RecordedCall);
    expect(body).toEqual({
      ref: "main",
      inputs: {
        send_email: true,
        force_send: false,
        wait_until_target: false,
        trigger_source: "cloudflare-cron",
        scheduled_for: "2026-08-03T23:59:00.000Z",
        cron_expression: "59 23 * * *",
      },
    });
    expect("return_run_details" in body).toBe(false);
  });

  it("derives scheduled_for and cron_expression from the controller", async () => {
    const { deps, calls } = makeDeps(healthyRoutes());

    await withLogs(async () =>
      runScheduled(
        makeController({ scheduledTime: Date.UTC(2026, 7, 4, 0, 44, 0), cron: "44 0 * * *" }),
        makeEnv(),
        deps,
      ),
    );

    const body = parseBody(calls.find((call) => call.route === "dispatch") as RecordedCall);
    expect(body.inputs.scheduled_for).toBe("2026-08-04T00:44:00.000Z");
    expect(body.inputs.cron_expression).toBe("44 0 * * *");
  });

  it.each([400, 401, 404, 422])("never retries a POST that failed with HTTP %i", async (status) => {
    const { deps, counts, sleeps } = makeDeps(healthyRoutes([text(status, "nope")]));

    await withLogs(async () => {
      await expect(runScheduled(makeController(), makeEnv(), deps)).rejects.toThrow(
        new RegExp(`HTTP ${status}`),
      );
    });

    expect(counts.dispatch).toBe(1);
    expect(counts.runs).toBe(1); // the preflight lookup only
    expect(sleeps).toEqual([]);
  });

  it("never retries a POST rejected by an ordinary permission 403", async () => {
    const { deps, counts } = makeDeps(
      healthyRoutes([
        text(403, "Resource not accessible by personal access token", {
          "X-RateLimit-Remaining": "4999",
        }),
      ]),
    );

    await withLogs(async () => {
      await expect(runScheduled(makeController(), makeEnv(), deps)).rejects.toThrow(/HTTP 403/);
    });

    expect(counts.dispatch).toBe(1);
  });

  it("truncates a long HTML error body instead of logging it whole", async () => {
    const html = `<html><body>${"x".repeat(5000)}</body></html>`;
    const { deps } = makeDeps(healthyRoutes([text(422, html)]));

    const { logs } = await withLogs(async () => {
      const error = await runScheduled(makeController(), makeEnv(), deps).catch((e) => e);
      expect((error as Error).message).toContain("[truncated]");
      expect((error as Error).message.length).toBeLessThan(700);
      return error;
    });

    for (const line of logs.raw) {
      expect(line.length).toBeLessThan(1_500);
      expect(line).not.toContain("x".repeat(1_000));
    }
  });
});

describe("runScheduled — dispatch retry and reconciliation", () => {
  it.each([
    ["a 429 with a short Retry-After", text(429, "slow down", { "Retry-After": "5" }), 5_000],
    ["a 500", text(500, "boom"), 1_000],
    ["a network error", netError(), 1_000],
    ["a timeout", timeoutError(), 1_000],
  ])("retries after %s once reconciliation reports nothing running", async (_l, step, delay) => {
    const { deps, counts, sleeps } = makeDeps({
      marker: [empty(404)],
      runs: [noRuns()],
      dispatch: [step, noContent()],
    });

    await withLogs(async () => runScheduled(makeController(), makeEnv(), deps));

    expect(sleeps).toEqual([delay]);
    expect(counts.dispatch).toBe(2);
    expect(counts.runs).toBe(2); // preflight + one reconciliation
  });

  it("reconciles before the second and the third POST, and never sends a fourth", async () => {
    const { deps, counts, sleeps } = makeDeps({
      marker: [empty(404)],
      runs: [noRuns()],
      dispatch: [netError()],
    });

    const { logs } = await withLogs(async () => {
      await expect(runScheduled(makeController(), makeEnv(), deps)).rejects.toThrow(SchedulerError);
    });

    expect(counts.dispatch).toBe(3);
    // preflight + one reconciliation per ambiguous failure, including the last.
    expect(counts.runs).toBe(4);
    expect(sleeps).toEqual([1_000, 2_000, 4_000]);
    expect(logs.all("dispatch_reconciliation_check").map((entry) => entry.attempt)).toEqual([
      1, 2, 3,
    ]);
    expect(logs.all("dispatch_attempt")).toHaveLength(3);
    expect(logs.find("workflow_dispatch_failed")?.reason).toBe("attempts_exhausted");
  });

  it.each([
    ["a network error", netError()],
    ["a timeout", timeoutError()],
    ["a 5xx", text(503, "unavailable")],
  ])("stops after %s once reconciliation finds an active run", async (_label, step) => {
    const { deps, counts } = makeDeps({
      marker: [empty(404)],
      runs: [noRuns(), runsPayload("in_progress")],
      dispatch: [step],
    });

    const { logs } = await withLogs(async () => runScheduled(makeController(), makeEnv(), deps));

    expect(counts.dispatch).toBe(1);
    const observed = logs.find("dispatch_observed_after_ambiguous_failure");
    expect(observed?.active_run_status).toBe("in_progress");
    expect(observed?.run_id).toBe(900);
  });

  it("treats an active run found after the FINAL POST as an accepted dispatch", async () => {
    const { deps, counts } = makeDeps({
      marker: [empty(404)],
      // preflight, recon-1, recon-2 clean; recon-3 (after the last POST) sees the run.
      runs: [noRuns(), noRuns(), noRuns(), runsPayload("queued")],
      dispatch: [netError()],
    });

    const { logs } = await withLogs(async () => runScheduled(makeController(), makeEnv(), deps));

    expect(counts.dispatch).toBe(3);
    expect(logs.find("dispatch_observed_after_ambiguous_failure")?.attempt).toBe(3);
  });

  it("fails without a fourth POST when the final reconciliation is clean", async () => {
    const { deps, counts } = makeDeps({
      marker: [empty(404)],
      runs: [noRuns()],
      dispatch: [timeoutError()],
    });

    await withLogs(async () => {
      await expect(runScheduled(makeController(), makeEnv(), deps)).rejects.toThrow(SchedulerError);
    });

    expect(counts.dispatch).toBe(3);
  });

  it("fails without a fourth POST when the final reconciliation is inconclusive", async () => {
    const { deps, counts } = makeDeps({
      marker: [empty(404)],
      runs: [noRuns(), noRuns(), noRuns(), text(500, "boom")],
      dispatch: [netError()],
    });

    const { logs } = await withLogs(async () => {
      await expect(runScheduled(makeController(), makeEnv(), deps)).rejects.toThrow(
        /reconciliation was inconclusive/,
      );
    });

    expect(counts.dispatch).toBe(3);
    expect(logs.find("dispatch_reconciliation_deferred")?.attempt).toBe(3);
  });

  it("never sends a blind retry when reconciliation keeps failing transiently", async () => {
    const { deps, counts } = makeDeps({
      marker: [empty(404)],
      runs: [noRuns(), text(503, "unavailable")],
      dispatch: [netError()],
    });

    const { logs } = await withLogs(async () => {
      await expect(runScheduled(makeController(), makeEnv(), deps)).rejects.toThrow(
        /no blind retry was sent/,
      );
    });

    expect(counts.dispatch).toBe(1);
    expect(logs.find("dispatch_reconciliation_deferred")).toBeDefined();
  });

  it("never sends a blind retry when reconciliation returns a malformed payload", async () => {
    const { deps, counts } = makeDeps({
      marker: [empty(404)],
      runs: [noRuns(), json(200, { message: "Not Found" })],
      dispatch: [netError()],
    });

    const { logs } = await withLogs(async () => {
      await expect(runScheduled(makeController(), makeEnv(), deps)).rejects.toThrow(SchedulerError);
    });

    expect(counts.dispatch).toBe(1);
    expect(logs.find("dispatch_reconciliation_deferred")?.reason).toBe("missing_workflow_runs");
  });

  it("stops without any further GitHub request when a POST is rate limited beyond budget", async () => {
    const { deps, counts, sleeps } = makeDeps({
      marker: [empty(404)],
      runs: [noRuns()],
      dispatch: [text(429, "slow down", { "Retry-After": "120" })],
    });

    const { logs } = await withLogs(async () => {
      await expect(runScheduled(makeController(), makeEnv(), deps)).rejects.toThrow(
        /exceeds this invocation's budget/,
      );
    });

    expect(counts.dispatch).toBe(1);
    expect(counts.runs).toBe(1); // the preflight lookup only — no reconciliation GET
    expect(sleeps).toEqual([]);
    expect(logs.find("dispatch_deferred")?.reason).toBe("retry_after_exceeds_budget");
  });

  it("stops when a secondary rate limit gives no usable retry window", async () => {
    const resetIn10 = String(Math.floor(FIXED_NOW / 1_000) + 10);
    const { deps, counts } = makeDeps({
      marker: [empty(404)],
      runs: [noRuns()],
      dispatch: [
        text(403, "You have exceeded a secondary rate limit", {
          "X-RateLimit-Remaining": "4321",
          "X-RateLimit-Reset": resetIn10,
        }),
      ],
    });

    const { logs } = await withLogs(async () => {
      await expect(runScheduled(makeController(), makeEnv(), deps)).rejects.toThrow(SchedulerError);
    });

    expect(counts.dispatch).toBe(1);
    expect(counts.runs).toBe(1);
    expect(logs.find("dispatch_deferred")?.reason).toBe("rate_limit_window_unknown");
  });

  it.each([
    ["an empty Retry-After", ""],
    ["a whitespace Retry-After", "   "],
  ])(
    "defers on a 429 with %s and an unusable reset, without treating it as 0ms",
    async (_label, retryAfter) => {
      const resetIn10 = String(Math.floor(FIXED_NOW / 1_000) + 10);
      const { deps, counts, sleeps } = makeDeps({
        marker: [empty(404)],
        runs: [noRuns()],
        dispatch: [
          // remaining is absent, so the reset must not be used either.
          text(429, "slow down", { "Retry-After": retryAfter, "X-RateLimit-Reset": resetIn10 }),
        ],
      });

      const { logs } = await withLogs(async () => {
        await expect(runScheduled(makeController(), makeEnv(), deps)).rejects.toThrow(
          SchedulerError,
        );
      });

      expect(counts.dispatch).toBe(1); // no second POST
      expect(counts.runs).toBe(1); // preflight only — no reconciliation GET
      expect(sleeps).toEqual([]); // and above all: no 0ms "retry immediately"
      expect(logs.find("dispatch_deferred")?.reason).toBe("rate_limit_window_unknown");
    },
  );

  it("defers when the quota is exhausted but the reset header is malformed", async () => {
    const { deps, counts, sleeps } = makeDeps({
      marker: [empty(404)],
      runs: [noRuns()],
      dispatch: [
        text(403, "API rate limit exceeded", {
          "X-RateLimit-Remaining": "0",
          "X-RateLimit-Reset": "1e9",
        }),
      ],
    });

    const { logs } = await withLogs(async () => {
      await expect(runScheduled(makeController(), makeEnv(), deps)).rejects.toThrow(SchedulerError);
    });

    expect(counts.dispatch).toBe(1);
    expect(counts.runs).toBe(1);
    expect(sleeps).toEqual([]);
    expect(logs.find("dispatch_deferred")?.reason).toBe("rate_limit_reset_unusable");
  });

  it.each([
    ["1e1", "scientific notation"],
    ["0x10", "hexadecimal"],
  ])("does not honour Retry-After %j (%s) as a wait", async (retryAfter, _label) => {
    const { deps, counts, sleeps } = makeDeps({
      marker: [empty(404)],
      runs: [noRuns()],
      dispatch: [text(429, "slow down", { "Retry-After": retryAfter })],
    });

    const { logs } = await withLogs(async () => {
      await expect(runScheduled(makeController(), makeEnv(), deps)).rejects.toThrow(SchedulerError);
    });

    // 10 and 16 are both inside/outside the budget respectively, but neither
    // value may be read out of a non-canonical syntax in the first place.
    expect(sleeps).toEqual([]);
    expect(counts.dispatch).toBe(1);
    expect(logs.find("dispatch_deferred")?.reason).toBe("rate_limit_window_unknown");
  });

  it("waits for the reset only when the primary quota is exhausted", async () => {
    const resetIn10 = String(Math.floor(FIXED_NOW / 1_000) + 10);
    const { deps, counts, sleeps } = makeDeps({
      marker: [empty(404)],
      runs: [noRuns()],
      dispatch: [
        text(403, "API rate limit exceeded", {
          "X-RateLimit-Remaining": "0",
          "X-RateLimit-Reset": resetIn10,
        }),
        noContent(),
      ],
    });

    await withLogs(async () => runScheduled(makeController(), makeEnv(), deps));

    expect(sleeps).toEqual([10_000]);
    expect(counts.dispatch).toBe(2);
  });
});

// ---------------------------------------------------------------------------
// Invocation-level guarantees
// ---------------------------------------------------------------------------

describe("runScheduled — invocation guarantees", () => {
  it("calls controller.noRetry() before any GitHub request", async () => {
    const order: string[] = [];
    const noRetry = vi.fn(() => {
      order.push("noRetry");
    });
    const { deps } = makeDeps(healthyRoutes(), {
      fetchImpl: (async (url: string | URL | Request) => {
        order.push(classifyRoute(String(url)));
        return classifyRoute(String(url)) === "runs"
          ? new Response(JSON.stringify({ workflow_runs: [] }), { status: 200 })
          : new Response(null, { status: classifyRoute(String(url)) === "marker" ? 404 : 204 });
      }) as unknown as typeof fetch,
    });

    await withLogs(async () => runScheduled(makeController({ noRetry }), makeEnv(), deps));

    expect(noRetry).toHaveBeenCalledTimes(1);
    expect(order).toEqual(["noRetry", "marker", "runs", "dispatch"]);
  });

  it("uses the global fetch when no dependency is injected", async () => {
    const order: string[] = [];
    const noRetry = vi.fn(() => {
      order.push("noRetry");
    });
    const original = globalThis.fetch;
    globalThis.fetch = (async (url: string | URL | Request) => {
      const route = classifyRoute(String(url));
      order.push(route);
      if (route === "marker") return new Response(null, { status: 404 });
      if (route === "runs") {
        return new Response(JSON.stringify({ workflow_runs: [] }), { status: 200 });
      }
      return new Response(null, { status: 204 });
    }) as unknown as typeof fetch;

    try {
      await withLogs(async () => runScheduled(makeController({ noRetry }), makeEnv()));
    } finally {
      globalThis.fetch = original;
    }

    expect(noRetry).toHaveBeenCalledTimes(1);
    expect(order).toEqual(["noRetry", "marker", "runs", "dispatch"]);
  });

  it("calls noRetry() even when the environment is broken, then fails", async () => {
    const noRetry = vi.fn();

    await expect(runScheduled(makeController({ noRetry }), {})).rejects.toThrow(SchedulerError);
    expect(noRetry).toHaveBeenCalledTimes(1);
  });

  it("performs no GitHub request at all when the configuration is invalid", async () => {
    const { deps, calls } = makeDeps(healthyRoutes());

    await expect(
      runScheduled(makeController(), makeEnv({ GITHUB_TOKEN: " " }), deps),
    ).rejects.toThrow(/GITHUB_TOKEN/);
    expect(calls).toHaveLength(0);
  });

  it("sends the token only in the Authorization header, never in a log", async () => {
    const scenarios: Partial<Record<RouteKey, Step[]>>[] = [
      healthyRoutes(),
      { marker: [json(200, { name: "marker" })] },
      { marker: [empty(404)], runs: [runsPayload("queued")] },
      { marker: [text(500, "boom")], runs: [noRuns()], dispatch: [noContent()] },
      { marker: [empty(404)], runs: [noRuns()], dispatch: [text(422, "x".repeat(3000))] },
      { marker: [empty(404)], runs: [noRuns()], dispatch: [netError()] },
      { marker: [empty(404)], runs: [noRuns(), text(500, "boom")], dispatch: [netError()] },
      { marker: [text(429, "slow", { "Retry-After": "600" })] },
    ];

    for (const routes of scenarios) {
      const { deps, calls } = makeDeps(routes);
      const { logs } = await withLogs(async () =>
        runScheduled(makeController(), makeEnv(), deps).catch(() => undefined),
      );

      const joined = logs.raw.join("\n");
      expect(joined).not.toContain(FAKE_TOKEN);
      expect(joined).not.toContain("Bearer");
      expect(joined).not.toContain("Authorization");
      // ...while every request really did carry the header.
      for (const call of calls) {
        expect(call.headers.Authorization).toBe(`Bearer ${FAKE_TOKEN}`);
      }
    }
  });

  it("sends the shared headers and a timeout signal on every request", async () => {
    const seen: RequestInit[] = [];
    const { deps } = makeDeps(healthyRoutes(), {
      fetchImpl: (async (url: string | URL | Request, init?: RequestInit) => {
        seen.push(init ?? {});
        const route = classifyRoute(String(url));
        if (route === "marker") return new Response(null, { status: 404 });
        if (route === "runs") {
          return new Response(JSON.stringify({ workflow_runs: [] }), { status: 200 });
        }
        return new Response(null, { status: 204 });
      }) as unknown as typeof fetch,
    });

    await withLogs(async () => runScheduled(makeController(), makeEnv(), deps));

    expect(seen).toHaveLength(3);
    for (const init of seen) {
      const headers = init.headers as Record<string, string>;
      expect(headers.Accept).toBe("application/vnd.github+json");
      expect(headers["X-GitHub-Api-Version"]).toBe("2026-03-10");
      expect(headers["User-Agent"]).toBe("finance-news-monitor-scheduler");
      expect(init.signal).toBeInstanceOf(AbortSignal);
    }
  });

  it("labels every log line with the invocation context", async () => {
    const { deps } = makeDeps(healthyRoutes());

    const { logs } = await withLogs(async () => runScheduled(makeController(), makeEnv(), deps));

    expect(logs.entries().length).toBeGreaterThan(0);
    for (const entry of logs.entries()) {
      expect(entry.cron).toBe("59 23 * * *");
      expect(entry.slot).toBe("primary");
      expect(entry.report_date).toBe("2026-08-04");
      expect(entry.scheduled_for).toBe("2026-08-03T23:59:00.000Z");
      expect(entry.owner).toBe("zetatech-a");
      expect(entry.repo).toBe("finance-news-monitor");
      expect(entry.workflow).toBe("daily.yml");
      expect(entry.ref).toBe("main");
    }
    expect(logs.find("cron_received")).toBeDefined();
  });
});
