# Per-request options and domain-policy switches

## Request options

`POST /v1/fetch` accepts optional `headers`, `cookies`, `user_agent`,
`impersonate`, `wait_for` and `use_policy`. `POST /batches/` accepts the same
keys inside `options`.

    {
      "url": "https://www.example.com/",
      "mode": "stealth",
      "use_proxy": false,
      "use_policy": false,
      "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
      "impersonate": "chrome124",
      "headers": {"Referer": "https://www.google.com/"},
      "cookies": {"session": "value"}
    }

Request values win over the built-in defaults and over a domain policy.

| Engine | headers | cookies | user_agent | impersonate |
| :-- | :-- | :-- | :-- | :-- |
| httpx (`static`) | merged into the request | `Cookie` header | applied | ignored |
| curl_cffi (`stealth`) | merged into the request | `Cookie` header | replaces the pinned Chrome 120 UA | selects the TLS profile |
| Playwright (`browser`) | same-site top-level navigations only | browser cookie jar for the target URL | browser context UA | ignored |
| Camoufox | same-site top-level navigations only | browser cookie jar for the target URL | ignored (Firefox identity) | ignored |

A `user_agent` that disagrees with the TLS profile is a detectable
inconsistency; pair it with a matching `impersonate` value. With `impersonate`
and no `user_agent`, the pinned Chrome 120 UA is not sent. An `impersonate`
value the installed curl_cffi does not know fails the job.

## Waiting for content

`wait_for` is a CSS selector of at most 512 characters, for pages that fill in
their content after load. A browser engine keeps the page open until a matching
element is in the DOM, visible or not, and only then returns the HTML.

    {"url": "https://www.example.com/", "mode": "browser", "wait_for": ".product-name"}

- The wait shares the 60-second budget of one browser attempt.
- If nothing matches in time, or the selector is not valid, the job fails with
  `wait_for failed: ...` after that one attempt: no retry, and the proxy is
  not penalized.
- Block pages and responses with status 400 or higher are returned as before,
  without waiting.
- httpx and curl_cffi ignore `wait_for`.

## Safety rules

- Cookies and `Authorization` are dropped when a redirect leaves the registered
  domain or downgrades from HTTPS to HTTP.
- `Host`, `Content-Length`, `Transfer-Encoding`, `Connection`, `Upgrade`,
  `Accept-Encoding` and `Proxy-*` cannot be set. `Cookie` and `User-Agent` are
  accepted only through their dedicated fields.
- Values must be printable ASCII; at most 40 headers, 150 cookies and 32 KiB.
- A batch that carries cookies or `Authorization` must keep all URLs on one
  registered domain; otherwise it is rejected with 422.
- `use_policy` must be a JSON boolean.
- Values are never logged and are absent from `request_log`. They stay in the
  Redis job payload until the job finishes. WARC stores response
  records only, but an archived page can still contain account data.

Cookies are credentials. Use sessions you are allowed to use.

## Domain-policy switches

Domain policies are on by default.

- `"use_policy": false` in a request skips the domain policy for that job only:
  the engine follows `mode`, no policy headers, proxy pool or learned tier are
  applied, and the policy learner does not record the outcome. The API-side
  domain rate limit still uses the policy value.
- `ENABLE_DOMAIN_POLICIES=false` turns policies off for the whole stack without
  deleting them: lookups return nothing, the learner does not write, and the
  admin endpoints keep working but have no effect on fetches.

Without a policy an explicit engine mode starts at its ladder tier, so
`stealth` without `use_proxy=false` still requires a datacenter proxy.
