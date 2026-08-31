"""`client_ip` writes to the login throttle and the security audit, so it must
not trust a header a caller can forge.

nginx replaces X-Forwarded-For with the real peer, so behind the intended
deployment the first token is the client. But the value still lands in
`login_attempts.ip_address` and the control-plane `audit_log`, and without the
proxy -- a deployment the platform explicitly warns against but cannot prevent --
the header is whatever the caller sent. Validating it as an IP address stops a
caller forging the platform's own incident record with markup or free text, and
falls back to the socket peer when the header is not a plain address.
"""

from __future__ import annotations

from backend.services.auth_service import client_ip


class _Request:
    def __init__(self, xff=None, peer="203.0.113.9"):
        self.headers = {} if xff is None else {"x-forwarded-for": xff}

        class _Client:
            host = peer

        self.client = _Client()


def test_a_valid_forwarded_address_is_trusted():
    assert client_ip(_Request("1.2.3.4")) == "1.2.3.4"
    assert client_ip(_Request("1.2.3.4, 5.6.7.8")) == "1.2.3.4"
    assert client_ip(_Request("2001:db8::1")) == "2001:db8::1"


def test_a_forged_forwarded_header_falls_back_to_the_peer():
    assert client_ip(_Request("1.2.3.4 (admin OK) <script>")) == "203.0.113.9"
    assert client_ip(_Request("not-an-ip-at-all")) == "203.0.113.9"
    assert client_ip(_Request("'; DROP TABLE audit_log; --")) == "203.0.113.9"


def test_no_header_uses_the_peer():
    assert client_ip(_Request(None)) == "203.0.113.9"
