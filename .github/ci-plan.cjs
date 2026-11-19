"use strict";

const { execFileSync } = require("node:child_process");

function selectPlan(context, paths) {
  const no = reason => ({ code: false, integration: false, docker: false, reason });
  const full = reason => ({ code: true, integration: true, docker: true, reason });
  const pr = context.payload.pull_request;
  if (context.eventName === "workflow_dispatch") {
    return context.payload.inputs?.mode === "full"
      ? full("Explicit full validation")
      : { code: true, integration: false, docker: false, reason: "Explicit fast validation" };
  }
  if (context.payload.action === "ready_for_review") return full("PR ready for review");
  if (context.payload.action === "converted_to_draft") return no("PR returned to draft");
  if (context.ref.startsWith("refs/tags/v")) return full("Release tag");
  const docsOnly = paths.length > 0 && paths.every(path =>
    path.startsWith("docs/") || /\.md$/i.test(path) || /^(LICENSE|LICENSE\.txt|COPYING)$/.test(path));
  if (docsOnly || paths.length === 0) return no("Documentation-only or empty change");
  if ((pr && !pr.draft) || ["refs/heads/master", "refs/heads/main"].includes(context.ref)) {
    return full("Code on a release branch or ready PR");
  }
  const docker = paths.some(path =>
    /(^|\/)Dockerfile[^/]*$/.test(path) || path === ".dockerignore" ||
    /^(docker-compose|compose)([.-][^/]*)?\.ya?ml$/.test(path) ||
    path === "pyproject.toml" || /^requirements[^/]*\.txt$/.test(path) ||
    ["scripts/stack_runtime.py", "scripts/browser_smoke.py", "app/main.py",
     "app/core/config.py", "app/core/db.py", "app/core/proxy_check_config.py",
     "app/worker/arq_worker.py", "app/worker/browser_pool.py",
     "app/worker/camoufox_check.py", "app/worker/tasks/fetch_task.py"].includes(path));
  if (docker) return full("Runtime, dependency or deployment input changed");
  const pure = new Set([
    "app/services/block_detector.py", "app/services/content_decoder.py",
    "app/services/proxy_geo_policy.py", "app/services/proxy_candidates.py",
    "tests/test_ci_selection.py", "tests/test_proxy_geo_unit.py",
    "tests/test_proxy_candidates_unit.py", "tests/test_stack_runtime.py",
    "tests/test_logging_redaction.py", "tests/test_url_guard.py",
    "tests/unit/test_block_detector.py", "tests/unit/test_block_reason.py",
    "tests/unit/test_content_decoder.py", "tests/unit/test_logging_context.py",
    "tests/unit/test_request_attempt_log.py", "tests/unit/test_security.py",
    "tests/unit/test_vendor_detection.py"
  ]);
  const integration = paths.some(path => !pure.has(path) &&
    (path.startsWith("app/") || path.startsWith("alembic/") || path.startsWith("tests/")));
  return { code: true, integration, docker: false,
    reason: integration ? "Service-boundary changes" : "Fast development checks" };
}

async function run({ context, github, core, git = execFileSync }) {
  if (context.eventName === "push" && context.ref.startsWith("refs/heads/") &&
      !["refs/heads/master", "refs/heads/main"].includes(context.ref)) {
    try {
      const { data } = await github.rest.pulls.list({ ...context.repo, state: "open",
        head: `${context.repo.owner}:${context.ref.slice(11)}`, per_page: 1 });
      if (data.length) {
        for (const key of ["code", "integration", "docker"]) core.setOutput(key, "false");
        core.notice("PR owns validation; duplicate push skipped");
        return;
      }
    } catch (error) {
      core.warning("PR lookup unavailable; proceeding with validation");
    }
  }
  const pr = context.payload.pull_request;
  // Synchronize compares previous PR head to new PR head, not the full PR base diff.
  const base = context.payload.action === "synchronize"
    ? context.payload.before : (pr?.base?.sha || context.payload.before);
  const head = pr?.head?.sha || context.sha || "HEAD";
  let paths;
  try {
    paths = git("git", base && !/^0+$/.test(base)
      ? ["diff", "--name-only", base, head] : ["ls-files"], { encoding: "utf8" })
      .trim().split("\n").filter(Boolean);
  } catch (error) {
    for (const key of ["code", "integration", "docker"]) core.setOutput(key, "true");
    core.warning("Cannot establish change scope; full validation required");
    return;
  }
  const plan = selectPlan(context, paths);
  for (const key of ["code", "integration", "docker"]) core.setOutput(key, String(plan[key]));
  core.notice(plan.reason);
}

module.exports = { selectPlan, run };
