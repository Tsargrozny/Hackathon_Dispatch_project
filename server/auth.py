"""Optional HTTP basic auth for the whole app, so an exposed ngrok URL isn't an open control panel.

Enabled when BASIC_AUTH_USER and BASIC_AUTH_PASS are both set. `/voice/*` is exempt because Twilio
can't send credentials; those routes verify Twilio's request signature instead.
"""
from __future__ import annotations

import base64
import secrets

EXEMPT_PREFIXES = ("/voice/",)
EXEMPT_PATHS = ("/vapi",)


class BasicAuth:
    def __init__(self, app, user: str, password: str):
        self.app = app
        self.expected = (user, password)

    def _ok(self, scope) -> bool:
        header = dict(scope["headers"]).get(b"authorization", b"").decode()
        if not header.lower().startswith("basic "):
            return False
        try:
            user, _, password = base64.b64decode(header[6:]).decode().partition(":")
        except Exception:
            return False
        return (secrets.compare_digest(user, self.expected[0])
                and secrets.compare_digest(password, self.expected[1]))

    async def __call__(self, scope, receive, send):
        path = scope["path"]
        if (scope["type"] not in ("http", "websocket") or path.startswith(EXEMPT_PREFIXES)
                or path.rstrip("/") in EXEMPT_PATHS or self._ok(scope)):
            return await self.app(scope, receive, send)
        if scope["type"] == "websocket":
            await receive()  # the connect message
            return await send({"type": "websocket.close", "code": 1008})
        body = b"Authentication required"
        await send({"type": "http.response.start", "status": 401,
                    "headers": [(b"www-authenticate", b'Basic realm="Organ Relay"'),
                                (b"content-type", b"text/plain"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})
