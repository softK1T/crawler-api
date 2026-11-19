# Security and supported boundaries

[Documentation](README.md) · [API](API.md) · [Runbook](runbook.md)

## Deployment boundary

Default loopback single-host operation is not a completed public-production audit.
Treat Docker/host access, administrator keys and backup archives as privileged.
Before public use establish TLS, access controls, restricted CORS, secret rotation,
recovery, CPU/memory budgets and network-level egress policy. Canonical Compose
has no explicit memory/CPU cap; response-size checks do not bound browser RSS.

Keys are DB-backed and Argon2-verified. Scopes are explicit; job/batch reads check
application ownership. Policies and proxy pools are global. A tenant/application
is not a separate database, network, proxy universe or archive boundary. Do not
issue normal clients admin/keys scope. Application deactivation is not a substitute
for revoking keys. Bootstrap credentials are persisted; protect them with DB backups.
A shell variable is not complete credential/process isolation on a shared host.

## Network and browser behavior

SSRF checks and browser interception are defense in depth, not complete network
isolation. DNS rebinding, redirects/subresources and optional callback egress need
independent review. Browser paths restrict service workers/downloads/WebSockets;
Firefox's compatibility sandbox setting is not hardened browser isolation.
A robots flag is not proof of complete robots enforcement on every engine.
Use permitted targets and authorized proxies; stop/diagnose refusals and rate limits.

A successful proxy probe, TLS response or country code is not a trust/anonymity
certification. Never send sensitive sessions through untrusted public proxies.
Location does not establish commercial proxy type or future rotating exit location.

## Optional boundaries

WARC/S3 and callbacks are disabled by default. Archive handlers currently query
WarcIndex without an application filter: archive scope is not a tenant ownership
boundary. Review/fix that before untrusted multi-tenant archive exposure. Configure
durable DLQ storage and real archive write/read tests before relying on durability.
Callback URL/redirect/egress and delivery behaviour need separate acceptance.
Do not expose operational health/metrics or pass deployment secrets to CI casually.

## Supply chain and data

The pinned recovered Camoufox archive is checked before filtered extraction and
version/executable validation. SHA256 is not an upstream signature, provenance
review, vulnerability scan or future availability guarantee. Keep license material;
review redistribution obligations. Never overwrite the asset or add profiles,
secrets or separately licensed GeoIP databases. New versions/platforms need reviewed
artifacts and real launch tests. Native ARM64 is not supported. Other dependencies
and OS images are not fully immutable merely because this archive has a fixed hash.

URLs, query strings, exception text, HTML and archives can contain tokens/personal
data despite intended proxy-credential redaction. Redact before sharing; do not
attach raw dumps, profiles, cookies, proxy URLs or bootstrap output to public issues.
For a leak, restrict exposure, preserve safe audit IDs, revoke/rotate and inspect
access. Readiness proves point-in-time capabilities, not valid target content,
sustained stability or complete disaster recovery. CI's basic restore drill checks
schema/selected counts in a disposable DB, not the whole production recovery plan.
