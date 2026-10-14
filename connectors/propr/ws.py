"""Read-only authenticated Propr account WebSocket with bounded recovery.

Uses provider ping/pong, conservatively refreshes full REST inventory on each
state event, and never claims sequence replay is available. No order commands.
"""
import asyncio
import hashlib
import json
from dataclasses import dataclass

from .config import PRODUCTION_WS_URL
from .reconcile import AccountReconciler
from .state import UnknownOutcome


@dataclass(frozen=True)
class WSHealth:
    authenticated: bool
    connected: bool
    reconciled: bool
    restarts: int


class AccountWebSocket:
    def __init__(self, *, ws_url, api_key, reconciler: AccountReconciler,
                 connector=None, handshake_timeout=10.0, idle_timeout=65.0):
        if ws_url != PRODUCTION_WS_URL or not api_key:
            raise ValueError("Authenticated Propr production WebSocket required")
        if handshake_timeout <= 0 or idle_timeout <= 0:
            raise ValueError("WebSocket timeout must be positive")
        if connector is None:
            import websockets
            connector = websockets.connect
        self.connector = connector
        self.url = ws_url
        self.key = api_key
        self.reconciler = reconciler
        self.handshake_timeout = handshake_timeout
        self.idle_timeout = idle_timeout
        self.authenticated = False
        self.restarts = 0
        self.stopped = False

    @property
    def health(self):
        return WSHealth(self.authenticated, self.reconciler.connected,
                        self.reconciler.ready, self.restarts)

    @staticmethod
    def _decode(raw):
        try:
            message = json.loads(raw)
        except (ValueError, TypeError) as exc:
            raise UnknownOutcome("Invalid provider WebSocket JSON") from exc
        if not isinstance(message, dict) or not isinstance(message.get("data"), dict):
            raise UnknownOutcome("Invalid provider WebSocket envelope")
        return message

    async def run_once(self, *, max_events=None):
        """One authenticated connection; caller owns bounded reconnect policy."""
        self.reconciler.disconnected()
        self.authenticated = False
        try:
            async with self.connector(
                self.url, additional_headers={"X-API-Key": self.key},
                ping_interval=20, ping_timeout=10, open_timeout=10, close_timeout=5,
                max_size=1_000_000,
            ) as socket:
                raw = await asyncio.wait_for(socket.recv(), self.handshake_timeout)
                hello = self._decode(raw)
                if hello.get("type") != "connected" or not hello["data"].get("userId"):
                    raise UnknownOutcome("WebSocket authentication not confirmed")
                self.authenticated = True
                connected_user = hello["data"]["userId"]
                self.reconciler.connected_unverified()
                await self.reconciler.refresh()
                count = 0
                while not self.stopped and (max_events is None or count < max_events):
                    raw = await asyncio.wait_for(socket.recv(), self.idle_timeout)
                    message = self._decode(raw)
                    event_type = message.get("type")
                    if event_type == "connected":
                        raise UnknownOutcome("Unexpected WebSocket reauthentication")
                    if not isinstance(event_type, str) or not event_type.startswith(
                        ("account.", "order.", "position.", "trade.")
                    ):
                        raise UnknownOutcome("Unrecognized WebSocket event type")
                    if message.get("userId") not in (None, connected_user):
                        raise UnknownOutcome("WebSocket user identity mismatch")
                    # Provider supplies no guaranteed monotonically increasing sequence.
                    # Exact envelope identity allows dedup but NOT gap-proof replay.
                    if message.get("timestamp") is None:
                        raise UnknownOutcome("No event timestamp; cannot deduplicate safely")
                    event_hash = hashlib.sha256(
                        json.dumps(message, sort_keys=True, separators=(",", ":")).encode()
                    ).hexdigest()
                    self.reconciler.event(event_hash, event_type, message["data"])
                    self.reconciler.heartbeat()
                    await self.reconciler.refresh()
                    count += 1
        except (TimeoutError, ConnectionError, OSError, ValueError, UnknownOutcome, asyncio.TimeoutError):
            self.reconciler.disconnected()
            self.authenticated = False
            raise
        finally:
            self.reconciler.disconnected()
            self.authenticated = False

    async def run(self, *, max_restarts=3, base_delay=1.0):
        """Retry finite times and remain halted if recovery never succeeds."""
        if not 0 <= max_restarts <= 20 or base_delay <= 0:
            raise ValueError("Invalid reconnect budget")
        failures = 0
        while not self.stopped:
            try:
                await self.run_once()
                return
            except asyncio.CancelledError:
                raise
            except Exception:
                failures += 1
                self.restarts += 1
                if failures > max_restarts:
                    raise UnknownOutcome("WebSocket recovery budget exhausted") from None
                await asyncio.sleep(min(base_delay * (2 ** (failures - 1)), 30.0))

    def stop(self):
        self.stopped = True
        self.reconciler.disconnected()
