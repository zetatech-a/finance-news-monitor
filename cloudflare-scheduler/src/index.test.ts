import { describe, expect, it, vi } from "vitest";

import {
  buildDispatchBody,
  buildDispatchUrl,
  dispatchWorkflow,
  extractRunDetails,
  resolveConfig,
  runScheduled,
  SchedulerError,
  toUtcIso,
  type DispatchBody,
  type Env,
  type ScheduledControllerLike,
} from "./index.js";

// Deliberately fake — no real PAT is used anywhere in this suite, and no test
// performs a real network call (fetch is always injected as a stub).
const FAKE_TOKEN = "test-token-not-a-real-pat";

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

/** Records the single fetch call and returns the given response. */
function stubFetch(response: Response) {
  const calls: { url: string; init: RequestInit }[] = [];
  const impl = (async (url: string | URL | Request, init?: RequestInit) => {
    calls.push({ url: String(url), init: init ?? {} });
    return response;
  }) as unknown as typeof fetch;
  return { impl, calls };
}

function parseBody(init: RequestInit): DispatchBody {
  return JSON.parse(init.body as string) as DispatchBody;
}

function noContent(): Response {
  return new Response(null, { status: 204 });
}

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
});

describe("extractRunDetails", () => {
  const RUN_URL = "https://github.com/zetatech-a/finance-news-monitor/actions/runs/987654321";
  const API_URL = "https://api.github.com/repos/zetatech-a/finance-news-monitor/actions/runs/987654321";

  // The exact `return_run_details` payload shape is not pinned down, so every
  // plausible shape must yield the same log fields.
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

describe("dispatchWorkflow", () => {
  it("builds the exact URL, headers and body for a healthy environment", async () => {
    const { impl, calls } = stubFetch(noContent());

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
      return_run_details: true,
    });
  });

  it('sends boolean false when DISPATCH_SEND_EMAIL is "false"', async () => {
    const { impl, calls } = stubFetch(noContent());

    await dispatchWorkflow(makeController(), makeEnv({ DISPATCH_SEND_EMAIL: "false" }), impl);

    expect(parseBody(calls[0].init).inputs.send_email).toBe(false);
  });

  it('sends boolean true when DISPATCH_SEND_EMAIL is "true"', async () => {
    const { impl, calls } = stubFetch(noContent());

    await dispatchWorkflow(makeController(), makeEnv({ DISPATCH_SEND_EMAIL: "true" }), impl);

    expect(parseBody(calls[0].init).inputs.send_email).toBe(true);
  });

  it("treats 204 as success", async () => {
    const { impl } = stubFetch(noContent());

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

  it("does not dispatch at all when configuration is invalid", async () => {
    const { impl, calls } = stubFetch(noContent());

    await expect(
      dispatchWorkflow(makeController(), makeEnv({ GITHUB_REF: "" }), impl),
    ).rejects.toThrow(/GITHUB_REF/);
    expect(calls).toHaveLength(0);
  });
});

describe("runScheduled", () => {
  it("calls controller.noRetry() before dispatching", async () => {
    const noRetry = vi.fn();
    const order: string[] = [];
    noRetry.mockImplementation(() => order.push("noRetry"));
    const fetchSpy = (async () => {
      order.push("fetch");
      return noContent();
    }) as unknown as typeof fetch;
    const original = globalThis.fetch;
    globalThis.fetch = fetchSpy;

    try {
      await runScheduled(makeController({ noRetry }), makeEnv());
    } finally {
      globalThis.fetch = original;
    }

    expect(noRetry).toHaveBeenCalledTimes(1);
    expect(order).toEqual(["noRetry", "fetch"]);
  });

  it("calls noRetry() even when the environment is broken, then fails", async () => {
    const noRetry = vi.fn();

    await expect(runScheduled(makeController({ noRetry }), {})).rejects.toThrow(SchedulerError);
    expect(noRetry).toHaveBeenCalledTimes(1);
  });
});
