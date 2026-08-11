/**
 * Vite/Vitest `?raw` imports. Used by the test suite to read wrangler.jsonc as
 * text and assert that its cron triggers match CRON_SLOTS in index.ts, without
 * adding a dependency (node:fs would need @types/node).
 */
declare module "*?raw" {
  const contents: string;
  export default contents;
}
