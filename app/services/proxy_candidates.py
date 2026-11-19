"""Pure import, result and eligibility policy. No network or database side effects."""

from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from app.services.proxy_geo_policy import country_code, parse_exit_ip, pin_proxy, selection_matches

PROVIDER = "public-candidates"


def normalize_candidate(value: str) -> str:
    value = value.strip()
    if not value or len(value) > 1024 or any(char.isspace() for char in value):
        raise ValueError("invalid_candidate")
    parsed = urlsplit(value)
    if not parsed.hostname:
        raise ValueError("invalid_candidate")
    # Candidates accept public IP literals only; DNS gateways use provider integrations.
    return pin_proxy(value, [parsed.hostname])


def trace_country(body: bytes) -> str | None:
    parse_exit_ip(body)
    values = [
        line[4:].strip() for line in body.decode("ascii").splitlines() if line.startswith("loc=")
    ]
    if len(values) != 1 or values[0] in {"XX", "ZZ"}:
        return None
    try:
        return country_code(values[0])
    except ValueError:
        return None


def candidate_matches(proxy, **filters) -> bool:
    if not selection_matches(proxy, **filters):
        return False
    if not getattr(proxy, "check_managed", False):
        return True
    now = filters.get("now") or datetime.now(UTC)
    return bool(
        proxy.check_auto_activate
        and proxy.check_status == "ready"
        and proxy.check_last_at is not None
        and proxy.check_due_at is not None
        and proxy.check_due_at > now
    )


def record_check(
    proxy,
    *,
    now,
    ttl_s,
    retry_s,
    exit_ip=None,
    country=None,
    source=None,
    error=None,
    geo_error=None,
) -> None:
    # Only records explicitly enrolled by the candidate importer may be mutated.
    if not proxy.check_managed or not proxy.check_auto_activate:
        return
    proxy.check_last_at = now
    proxy.check_error = error or geo_error
    if error:
        proxy.is_active = False
        proxy.check_status = "failed"
        proxy.check_due_at = now + timedelta(seconds=retry_s)
        return
    from app.services.proxy_geo_policy import public_ip

    code = country_code(country) if country else None
    if proxy.country != code:
        proxy.city = None
    proxy.exit_ip = public_ip(exit_ip)
    proxy.country = code
    proxy.country_source = source if code else None
    proxy.check_status = "ready"
    proxy.check_due_at = now + timedelta(seconds=ttl_s)
    proxy.is_active = True
