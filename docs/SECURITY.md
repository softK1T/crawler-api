# Security boundaries

[Documentation](README.md) · [API](API.md) · [Deployment](runbook.md)

## Access and isolation

| Boundary | Current behavior | Operator action |
|---|---|---|
| API keys | Database-backed, Argon2-verified, explicitly scoped | Issue least-privilege client keys; revoke leaked credentials |
| Jobs and batches | Reads check application ownership | Keep application identities separate |
| Policies and proxy pools | Global configuration | Restrict administration to trusted operators |
| Archives | Queries lack application filtering | Grant `archive` only to trusted clients; fix ownership before untrusted multi-tenancy |
| Local S3 | Dedicated server, shared administrative credential pair | Protect its secret volume; introduce scoped IAM before sharing access |
| Diagnostics/docs | No API-key dependency | Restrict health, metrics (API and worker port), and documentation when exposing the API |

`admin` does not implicitly grant other scopes. Application deactivation is not a substitute for key revocation. Treat host/Docker access and backup files as privileged; shell variables do not isolate credentials on a shared host.

## Network, browsers, and proxies

The canonical API and S3 console bind to loopback, and the S3 API remains inside Docker. Public deployment additionally needs TLS, access controls, restricted CORS, egress policy, resource budgets, and a security review. Canonical Compose does not impose explicit CPU/memory limits; response-size checks do not bound browser RSS.

Keep SSRF checks enabled for untrusted URLs. Software URL checks/browser interception are defense in depth, not complete egress isolation. Redirects, DNS rebinding, subresources, and optional callbacks require independent review. Browser sandbox compatibility settings are not hardened browser isolation. A policy's robots flag is not proof of complete enforcement by every engine.

A successful proxy check or country code is not a trust/anonymity certificate. Never send sensitive sessions through untrusted public proxies. Use only authorized proxies and permitted targets; respect refusals and rate limits.

## Secrets, archives, and evidence

URLs, query strings, HTML, exceptions, and archives can contain personal data or tokens. Redact before sharing; never publish raw credentials, dumps, cookies, profiles, or proxy URLs. A leak requires exposure restriction, credential revocation/rotation, and access review.

Back up database, S3 objects, credentials, and pending journal entries consistently and off-host. Readiness is a point-in-time dependency check, not proof of valid target content, write durability, sustained stability, or disaster recovery. Validate archive round trips and isolated restoration separately.

The recovered Camoufox artifact is checksum-checked before extraction. A checksum is not an upstream signature or vulnerability audit; preserve license material and review redistribution obligations. Current browser packaging supports Linux x86_64, not native ARM64. Other dependencies are not made immutable by that checksum.
