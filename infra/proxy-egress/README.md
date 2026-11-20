# Container egress policy generator

This component renders rules only. It does not change the host, Compose, the fetcher, or proxy selection. Do not deploy it as a complete proxy-only solution.

Input on stdin is a JSON object with two lists: `proxy_urls` (trusted proxy endpoints with public literal IPs and explicit TCP ports) and `internal_services` (explicit private service IP/port pairs as URLs). Both lists may be empty. Supply only endpoints approved for the workload, independent of commercial proxy type. Hostnames are rejected rather than resolved outside the policy. Credentials are not emitted. Input containing credentials must not be logged.

The generated inet output chain is for an isolated application network namespace only, never the host. Responses to inbound connections and localhost traffic are allowed. Other outbound traffic defaults to drop, including UDP and direct DNS to Docker's embedded resolver. Local services must not act as unrestricted relays. The caller must arrange internal name resolution and an approved DNS path without introducing a general-purpose egress bypass.

Deployment still requires installing the policy before application startup, refreshing allowed endpoints atomically, and testing both IPv4 and IPv6 in a disposable namespace. Do not append successive generations or give the application NET_ADMIN. This file alone does not guarantee that a running deployment is isolated.
