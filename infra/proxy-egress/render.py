"""Render nftables rules for ONE application network namespace. Never applies rules."""

import argparse
import ipaddress
import json
import sys
from urllib.parse import urlsplit


def endpoint(url: str, *, public: bool) -> str:
    parsed = urlsplit(url)
    if parsed.hostname is None or parsed.port is None or not 1 <= parsed.port <= 65535:
        raise ValueError('an explicit IP literal and TCP port are required')
    addr = ipaddress.ip_address(parsed.hostname)
    if '%' in str(addr) or addr.is_multicast or addr.is_unspecified:
        raise ValueError('invalid endpoint address')
    if public and not addr.is_global:
        raise ValueError('proxy must have a public address')
    if not public and (not addr.is_private or addr.is_loopback or addr.is_link_local):
        raise ValueError('internal service must have a private network address')
    family = 'ip' if addr.version == 4 else 'ip6'
    return f'{family} daddr {addr} tcp dport {parsed.port} accept'


def render(config: dict) -> str:
    if set(config) != {'proxy_urls', 'internal_services'}:
        raise ValueError('expected proxy_urls and internal_services')
    rules = set()
    for key in ('proxy_urls', 'internal_services'):
        if not isinstance(config[key], list):
            raise ValueError('expected lists')
        for value in config[key]:
            rules.add(endpoint(value, public=key == 'proxy_urls'))
    lines = ['table inet crawler_egress {', ' chain output {',
             '  type filter hook output priority -150; policy drop;',
             '  ct direction reply accept',
             '  ip daddr 127.0.0.1 oifname "lo" accept',
             '  ip6 daddr ::1 oifname "lo" accept']
    lines.extend('  ' + rule for rule in sorted(rules))
    return '\n'.join([*lines, ' }', '}', ''])


if __name__ == '__main__':
    argparse.ArgumentParser(description=__doc__).parse_args()
    try:
        print(render(json.load(sys.stdin)), end='')
    except (ValueError, TypeError, AttributeError):
        sys.exit('Invalid egress configuration; no rules generated.')
