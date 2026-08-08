"""How the app sees the outside world: the address it is reached at, and the
address a request actually came from.

Both answers are wrong by default once a reverse proxy is in front, and both
matter. The app runs behind Zoraxy (and, since 2026-08-03, the Cloudflare
proxy), so every public request arrives from the proxy's IP over plain HTTP:

* `request.client.host` is the PROXY for all of them. `ratelimit`'s per-address
  bucket then holds one counter for the whole internet — five wrong passwords
  by anybody locks every user out of login for fifteen minutes. The bucket that
  was supposed to be the safety net is instead a one-line denial of service.
* `request.url.scheme` is "http", so the session cookie never gets `Secure`
  even though the browser is on https.
* `request.base_url` is the internal host:port, which is not a URL anyone else
  can open — so links we hand out (invite links, the device-pairing payload)
  have to come from configuration, not from the request.

`PUBLIC_BASE_URL` is the one place the public address is stated. It is separate
from wherever the route happens to point today: the hostname is the product,
the box behind it is an implementation detail that moves.
"""

import ipaddress
import os

DEFAULT_PUBLIC_BASE_URL = "https://eq2advanced.com"

# Hosts whose forwarding headers we believe. Only Zoraxy talks to this app from
# outside the box; anything else is a direct LAN client speaking for itself.
DEFAULT_TRUSTED_PROXIES = "10.1.1.4,127.0.0.1,::1"

# Cloudflare's free-plan ceiling on a single request body. The edge answers 413
# with its own HTML error page before the app is asked anything, so for anyone
# arriving through the front door this — not `upload_max_bytes` — is the real
# upload limit, and the app can only warn about it in advance.
CLOUDFLARE_MAX_BODY_BYTES = 100 * (1 << 20)


def public_base_url() -> str:
    """Scheme + host the site is reached at from outside, no trailing slash."""
    return (os.environ.get("PUBLIC_BASE_URL") or DEFAULT_PUBLIC_BASE_URL).rstrip("/")


def _trusted_networks() -> list:
    raw = os.environ.get("TRUSTED_PROXIES", DEFAULT_TRUSTED_PROXIES)
    nets = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            nets.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            continue
    return nets


def _is_trusted(host: str) -> bool:
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(addr in net for net in _trusted_networks())


def _first_valid(raw: str) -> str:
    """Leftmost syntactically valid address in a comma list, or ""."""
    for part in raw.split(","):
        part = part.strip()
        if part.startswith("[") and "]" in part:          # [::1]:1234
            part = part[1:part.index("]")]
        elif part.count(":") == 1:                        # 1.2.3.4:1234
            part = part.split(":", 1)[0]
        try:
            return str(ipaddress.ip_address(part))
        except ValueError:
            continue
    return ""


def client_ip(request) -> str:
    """The address to hold a rate-limit bucket against.

    Forwarding headers are read ONLY when the immediate peer is a proxy we
    trust — a direct LAN client cannot invent an address for itself. Behind
    Cloudflare, `CF-Connecting-IP` is the visitor and is rewritten by the edge
    on every request; `X-Forwarded-For` is the fallback for a Zoraxy-only path,
    where the leftmost entry is the original client.

    A forged header can still buy an attacker a fresh address bucket, and that
    is fine: the per-USERNAME bucket is what actually guards a password, and it
    is keyed on something they cannot rename. The address bucket exists to make
    spraying many usernames from one place expensive, and a value we can't
    trust at all — one shared counter for the entire internet — was worse than
    useless, because anyone could spend it and lock the site out.
    """
    peer = request.client.host if request.client else ""
    if not peer or not _is_trusted(peer):
        return peer
    cf = _first_valid(request.headers.get("cf-connecting-ip", ""))
    if cf:
        return cf
    return _first_valid(request.headers.get("x-forwarded-for", "")) or peer


def edge_max_bytes(request) -> int:
    """Largest body that can reach the app on this request's path, 0 if nothing
    in front caps it.

    A request that came through the Cloudflare proxy carries `CF-Ray`, so the
    answer is per-request: the same app tells a browser on the LAN there is no
    limit and a friend on the internet that there is one. Trusted-peer gated
    like every other forwarded fact — a direct client claiming `CF-Ray` would
    only be talking itself into a smaller limit, but the rule stays the rule.
    """
    peer = request.client.host if request.client else ""
    if not peer or not _is_trusted(peer):
        return 0
    if request.headers.get("cf-ray") or request.headers.get("cf-connecting-ip"):
        return CLOUDFLARE_MAX_BODY_BYTES
    return 0


def is_secure(request) -> bool:
    """True when the BROWSER is on https, which is what decides whether the
    session cookie may be marked `Secure`. TLS terminates at the proxy, so the
    request we see is plain http and the header is the only evidence."""
    if request.url.scheme == "https":
        return True
    peer = request.client.host if request.client else ""
    if not peer or not _is_trusted(peer):
        return False
    proto = request.headers.get("x-forwarded-proto", "")
    return proto.split(",")[0].strip().lower() == "https"
