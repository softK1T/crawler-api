"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { selectPlan, run } = require("./ci-plan.cjs");
const context = (extra = {}) => ({ eventName: "pull_request", ref: "refs/pull/19/merge",
  payload: { action: "synchronize", pull_request: { draft: true } }, ...extra });
const cases = [
  ["draft docs", context(), ["docs/runbook.md"], [false, false, false]],
  ["draft Python policy", context(), ["app/services/proxy_candidates.py"], [true, false, false]],
  ["draft model", context(), ["app/models/proxy.py"], [true, true, false]],
  ["draft integration test", context(), ["tests/integration/test_proxy_candidates.py"], [true, true, false]],
  ["draft legacy mixed tests", context(), ["tests/unit/test_rate_limiter.py"], [true, true, false]],
  ["draft deployment", context(), ["Dockerfile"], [true, true, true]],
  ["draft dependency", context(), ["pyproject.toml"], [true, true, true]],
  ["CI policy update", context(), [".github/workflows/ci.yml", "scripts/ci_test_selection.py"], [true, false, false]],
  ["ready transition", context({ payload: { action: "ready_for_review", pull_request: { draft: false } } }), ["README.md"], [true, true, true]],
  ["ready code update", context({ payload: { action: "synchronize", pull_request: { draft: false } } }), ["app/services/proxy_candidates.py"], [true, true, true]],
  ["ready docs update", context({ payload: { action: "synchronize", pull_request: { draft: false } } }), ["README.md"], [false, false, false]],
  ["back to draft", context({ payload: { action: "converted_to_draft", pull_request: { draft: true } } }), ["Dockerfile"], [false, false, false]],
  ["manual full", context({ eventName: "workflow_dispatch", payload: { inputs: { mode: "full" } } }), [], [true, true, true]],
  ["manual fast", context({ eventName: "workflow_dispatch", payload: { inputs: { mode: "fast" } } }), [], [true, false, false]],
  ["release tag", context({ eventName: "push", ref: "refs/tags/v1.2.0", payload: {} }), [], [true, true, true]],
  ["master code", context({ eventName: "push", ref: "refs/heads/master", payload: {} }), ["app/services/proxy_candidates.py"], [true, true, true]],
  ["master docs", context({ eventName: "push", ref: "refs/heads/master", payload: {} }), ["README.md"], [false, false, false]]
];
for (const [name, ctx, paths, expected] of cases) {
  test(name, () => {
    const p = selectPlan(ctx, paths);
    assert.deepEqual([p.code, p.integration, p.docker], expected);
  });
}

test("synchronize uses previous and new head", async () => {
  const outputs = {};
  let args;
  await run({
    context: context({ payload: { action: "synchronize", before: "old-head",
      pull_request: { draft: true, base: { sha: "master" }, head: { sha: "new-head" } } } }),
    github: {},
    core: { setOutput: (k, v) => { outputs[k] = v; }, notice() {}, warning() {} },
    git: (command, value) => { args = value; return "README.md\n"; }
  });
  assert.deepEqual(args, ["diff", "--name-only", "old-head", "new-head"]);
  assert.equal(outputs.docker, "false");
});
test("unknown scope fails safe to full validation", async () => {
  const outputs = {};
  await run({ context: context(), github: {},
    core: { setOutput: (k, v) => { outputs[k] = v; }, notice() {}, warning() {} },
    git: () => { throw Error("missing commit"); } });
  assert.deepEqual(outputs, { code: "true", integration: "true", docker: "true" });
});
test("open PR skips duplicate push without reading diff", async () => {
  const outputs = {};
  await run({
    context: context({ eventName: "push", ref: "refs/heads/dev",
      repo: { owner: "softK1T", repo: "crawler-api" }, payload: {} }),
    github: { rest: { pulls: { list: async () => ({ data: [{}] }) } } },
    core: { setOutput: (k, v) => { outputs[k] = v; }, notice() {}, warning() {} },
    git: () => { throw Error("must not be called"); } });
  assert.deepEqual(outputs, { code: "false", integration: "false", docker: "false" });
});
