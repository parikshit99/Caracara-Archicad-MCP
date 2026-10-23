import hmac


class BearerTokenMiddleware:
    """
    Minimal ASGI middleware that rejects HTTP requests without a valid
    'Authorization: Bearer <token>' header. Used to protect the HTTP-based
    transports; stdio does not need it.
    """

    def __init__(self, app, token: str):
        self.app = app
        self.token = token

    async def __call__(self, scope, receive, send):
        # Server startup and shutdown messages carry no request to check.
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        # Compared as bytes, so a header with any characters is simply wrong rather than an error.
        authorization = headers.get(b"authorization", b"")
        expected = f"Bearer {self.token}".encode()

        if hmac.compare_digest(authorization, expected):
            await self.app(scope, receive, send)
            return

        if scope["type"] == "websocket":
            # Closing before accepting refuses the connection (HTTP 403).
            await send({"type": "websocket.close", "code": 1008})
            return

        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"www-authenticate", b"Bearer"),
                ],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": b'{"error": "Unauthorized: missing or invalid bearer token."}',
            }
        )
