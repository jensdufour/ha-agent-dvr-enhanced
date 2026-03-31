"""In-memory media token store for Agent DVR Enhanced.

Tokens grant time-limited access to recording and thumbnail proxy endpoints,
allowing the HA Companion App's native media player to fetch media without
a full authentication session.
"""

import secrets
import time

# Token lifetime in seconds (5 minutes)
TOKEN_TTL = 300


class MediaTokenStore:
    """Store for short-lived media access tokens."""

    def __init__(self) -> None:
        """Initialize the token store."""
        # {token: (allowed_path_prefix, expiry_timestamp)}
        self._tokens: dict[str, tuple[str, float]] = {}

    def create(self, path_prefix: str) -> str:
        """Create a token that grants access to URLs starting with path_prefix."""
        self._purge_expired()
        token = secrets.token_urlsafe(32)
        self._tokens[token] = (path_prefix, time.monotonic() + TOKEN_TTL)
        return token

    def validate(self, token: str, request_path: str) -> bool:
        """Return True if the token is valid and covers the requested path."""
        entry = self._tokens.get(token)
        if entry is None:
            return False
        path_prefix, expiry = entry
        if time.monotonic() > expiry:
            del self._tokens[token]
            return False
        return request_path.startswith(path_prefix)

    def _purge_expired(self) -> None:
        """Remove expired tokens to prevent unbounded growth."""
        now = time.monotonic()
        expired = [t for t, (_, exp) in self._tokens.items() if now > exp]
        for t in expired:
            del self._tokens[t]
