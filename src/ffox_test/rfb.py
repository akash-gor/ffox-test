"""RFB (VNC) client for jlesage/firefox noVNC — browser chrome only (e.g. address bar)."""

from __future__ import annotations

import asyncio
import struct
from typing import Self
from urllib.parse import urlparse

import websockets
from websockets.asyncio.client import ClientConnection
from websockets.protocol import State
from websockets.typing import Subprotocol

DEFAULT_VNC_HOST = "127.0.0.1"
DEFAULT_VNC_PORT = 5770

_KEY_CONTROL_L = 0xFFE3
_KEY_RETURN = 0xFF0D
_KEY_A = ord("a")
_KEY_L = ord("l")
_KEY_V = ord("v")


def _key_event(down: bool, keysym: int) -> bytes:
    return struct.pack(">BBHI", 4, 1 if down else 0, 0, keysym)


def _client_cut_text(text: str) -> bytes:
    data = text.encode("latin-1", errors="replace")
    return struct.pack(">B3sI", 6, b"\x00\x00\x00", len(data)) + data


def normalize_url(url: str) -> str:
    url = url.strip()
    if not url:
        raise ValueError("url must be non-empty")
    if not urlparse(url).scheme:
        url = "https://" + url
    return url


class RFBSession:
    """Drive Firefox chrome over noVNC (navigate via address bar, etc.)."""

    def __init__(
        self, host: str = DEFAULT_VNC_HOST, port: int = DEFAULT_VNC_PORT
    ) -> None:
        self.host = host
        self.port = port
        self._ws: ClientConnection | None = None

    @property
    def uri(self) -> str:
        return f"ws://{self.host}:{self.port}/websockify"

    async def __aenter__(self) -> Self:
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def connect(self) -> None:
        if self.connected:
            return
        await self.close()  # drop a stale socket that has already closed
        # ping_interval=None: noVNC's websockify never answers websocket pings, so the
        # client's default keepalive would close this socket after ~40s of idleness
        # (e.g. while BiDi does DOM work) with "keepalive ping timeout".
        ws = await websockets.connect(
            self.uri,
            subprotocols=[Subprotocol("binary")],
            ping_interval=None,
        )
        try:
            await self._handshake(ws)
        except Exception:
            await ws.close()
            raise
        self._ws = ws

    @property
    def connected(self) -> bool:
        """True only while the socket is actually open (not just once created)."""
        return self._ws is not None and self._ws.state is State.OPEN

    async def close(self) -> None:
        if self._ws is not None:
            await self._ws.close()
            self._ws = None

    async def _handshake(self, ws: ClientConnection) -> None:
        version = await ws.recv()
        if not isinstance(version, (bytes, bytearray)) or not version.startswith(
            b"RFB "
        ):
            raise RuntimeError(f"unexpected RFB version greeting: {version!r}")
        await ws.send(b"RFB 003.008\n")

        security = await ws.recv()
        if not isinstance(security, (bytes, bytearray)) or len(security) < 1:
            raise RuntimeError(f"unexpected security-types message: {security!r}")
        count = security[0]
        types = list(security[1 : 1 + count])
        if 1 not in types:
            raise RuntimeError(
                f"server does not offer unauthenticated RFB (types={types})"
            )
        await ws.send(bytes([1]))

        result = await ws.recv()
        if (
            not isinstance(result, (bytes, bytearray))
            or result[:4] != b"\x00\x00\x00\x00"
        ):
            raise RuntimeError(f"RFB security handshake failed: {result!r}")

        await ws.send(bytes([1]))
        init = await ws.recv()
        if not isinstance(init, (bytes, bytearray)) or len(init) < 24:
            raise RuntimeError(f"unexpected ServerInit: {init!r}")

    async def _send_key(self, keysym: int, *, delay: float = 0.03) -> None:
        assert self._ws is not None
        await self._ws.send(_key_event(True, keysym))
        await asyncio.sleep(delay)
        await self._ws.send(_key_event(False, keysym))
        await asyncio.sleep(delay)

    async def _chord(self, modifier: int, key: int) -> None:
        assert self._ws is not None
        await self._ws.send(_key_event(True, modifier))
        await asyncio.sleep(0.03)
        await self._send_key(key)
        await self._ws.send(_key_event(False, modifier))
        await asyncio.sleep(0.05)

    async def navigate(self, url: str, *, settle: float = 0.5) -> str:
        if self._ws is None:
            raise RuntimeError("session is not connected; call connect() first")
        url = normalize_url(url)
        await self._ws.send(_client_cut_text(url))
        await asyncio.sleep(0.15)
        await self._chord(_KEY_CONTROL_L, _KEY_L)
        await asyncio.sleep(0.15)
        await self._chord(_KEY_CONTROL_L, _KEY_A)
        await self._chord(_KEY_CONTROL_L, _KEY_V)
        await asyncio.sleep(0.1)
        await self._send_key(_KEY_RETURN)
        await asyncio.sleep(settle)
        return url
