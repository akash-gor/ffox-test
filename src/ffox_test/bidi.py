"""Minimal WebDriver BiDi client for Firefox remote debugging."""

from __future__ import annotations

import asyncio
import json
import subprocess
from typing import Any, Self

import websockets
from websockets.asyncio.client import ClientConnection

DEFAULT_BIDI_HOST = "127.0.0.1"
DEFAULT_BIDI_PORT = 9222
DEFAULT_CONTAINER = "firefox"
DEFAULT_PROXY_LISTEN = 9223
DEFAULT_PROXY_TARGET = 9222


class BiDiError(RuntimeError):
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        super().__init__(
            f"{payload.get('error', 'bidi error')}: {payload.get('message', payload)}"
        )


def ensure_bidi_proxy(
    *,
    container: str = DEFAULT_CONTAINER,
    listen_port: int = DEFAULT_PROXY_LISTEN,
    target_port: int = DEFAULT_PROXY_TARGET,
) -> None:
    """Publish Firefox's localhost BiDi port on 0.0.0.0 inside the container.

    jlesage/firefox binds WebDriver BiDi to 127.0.0.1, so Docker port maps need
    a socat forwarder (install with ``INSTALL_PACKAGES=socat``).
    """
    subprocess.run(
        [
            "docker",
            "exec",
            "-d",
            container,
            "socat",
            f"TCP-LISTEN:{listen_port},fork,reuseaddr,bind=0.0.0.0",
            f"TCP:127.0.0.1:{target_port}",
        ],
        check=False,
        capture_output=True,
    )


def _unwrap_remote_value(value: Any) -> Any:
    if not isinstance(value, dict) or "type" not in value:
        return value
    t = value["type"]
    if t in {"string", "number", "boolean", "bigint"}:
        return value.get("value")
    if t == "null" or t == "undefined":
        return None
    if t == "array":
        return [_unwrap_remote_value(v) for v in value.get("value", [])]
    if t == "object":
        out: dict[str, Any] = {}
        for item in value.get("value", []):
            if isinstance(item, (list, tuple)) and len(item) == 2:
                out[str(item[0])] = _unwrap_remote_value(item[1])
        return out
    if t == "node":
        return value
    return value.get("value", value)


class BiDiSession:
    """Page-DOM control via WebDriver BiDi (selectors, fill, click, JS).

    Not used for browser chrome — navigation belongs on RFB.
    """

    def __init__(
        self,
        host: str = DEFAULT_BIDI_HOST,
        port: int = DEFAULT_BIDI_PORT,
        *,
        container: str = DEFAULT_CONTAINER,
        auto_proxy: bool = True,
    ) -> None:
        self.host = host
        self.port = port
        self.container = container
        self.auto_proxy = auto_proxy
        self._ws: ClientConnection | None = None
        self._next_id = 1
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._reader_task: asyncio.Task[None] | None = None
        self.context: str | None = None

    @property
    def uri(self) -> str:
        return f"ws://{self.host}:{self.port}/session"

    @property
    def connected(self) -> bool:
        # Deliberately "socket object exists", not "socket is open": if the socket drops
        # without session.end, Firefox keeps the old session alive, so reconnecting
        # fails with "Maximum number of active sessions". Better to surface the
        # ConnectionClosed error than to hide it behind a reconnect that cannot work.
        return self._ws is not None

    async def __aenter__(self) -> Self:
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def connect(self) -> None:
        if self._ws is not None:
            return
        if self.auto_proxy:
            await self._ensure_reachable()
        ws = await websockets.connect(self.uri, open_timeout=10)
        self._ws = ws
        self._reader_task = asyncio.create_task(self._read_loop())
        try:
            await self.call("session.new", {"capabilities": {}})
            await self.refresh_context()
        except Exception:
            await self.close()
            raise

    async def close(self) -> None:
        if self._ws is not None:
            try:
                await self.call("session.end", {})
            except BiDiError, websockets.ConnectionClosed, TimeoutError, OSError:
                pass  # best-effort: the browser may already have dropped the session
            await self._ws.close()
            self._ws = None
        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
            self._reader_task = None
        for fut in self._pending.values():
            if not fut.done():
                fut.cancel()
        self._pending.clear()
        self.context = None

    async def _ensure_reachable(self) -> None:
        for attempt in range(3):
            try:
                _reader, writer = await asyncio.open_connection(self.host, self.port)
                writer.close()
                await writer.wait_closed()
                return
            except OSError:
                if attempt == 0:
                    await asyncio.to_thread(ensure_bidi_proxy, container=self.container)
                await asyncio.sleep(0.4)
        # Final attempt left to websockets.connect for a clearer error.

    async def _read_loop(self) -> None:
        assert self._ws is not None
        try:
            async for raw in self._ws:
                if isinstance(raw, bytes):
                    raw = raw.decode()
                msg = json.loads(raw)
                msg_id = msg.get("id")
                if msg_id in self._pending:
                    fut = self._pending.pop(msg_id)
                    if not fut.done():
                        fut.set_result(msg)
        except websockets.ConnectionClosed:
            pass

    async def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        if self._ws is None:
            raise RuntimeError("BiDi session is not connected")
        msg_id = self._next_id
        self._next_id += 1
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[msg_id] = fut
        await self._ws.send(
            json.dumps({"id": msg_id, "method": method, "params": params or {}})
        )
        try:
            msg = await asyncio.wait_for(fut, timeout=60)
        except Exception:
            self._pending.pop(msg_id, None)
            raise
        if msg.get("type") == "error":
            raise BiDiError(msg)
        return msg.get("result")

    async def refresh_context(self) -> str:
        """Pick up the active tab (e.g. after RFB navigated the address bar)."""
        tree = await self.call("browsingContext.getTree", {})
        contexts = tree.get("contexts") or []
        if not contexts:
            raise RuntimeError("no browsing contexts available")
        self.context = contexts[0]["context"]
        return self.context

    def _require_context(self) -> str:
        if not self.context:
            raise RuntimeError("no browsing context; call connect() first")
        return self.context

    async def locate(
        self,
        selector: str,
        *,
        locator_type: str = "css",
        max_count: int | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "context": self._require_context(),
            "locator": {"type": locator_type, "value": selector},
        }
        if max_count is not None:
            params["maxNodeCount"] = max_count
        result = await self.call("browsingContext.locateNodes", params)
        return list(result.get("nodes") or [])

    async def wait_for_selector(
        self,
        selector: str,
        *,
        timeout: float = 10.0,
        locator_type: str = "css",
        poll: float = 0.2,
    ) -> dict[str, Any]:
        deadline = asyncio.get_running_loop().time() + timeout
        last_error: Exception | None = None
        while asyncio.get_running_loop().time() < deadline:
            try:
                nodes = await self.locate(
                    selector, locator_type=locator_type, max_count=1
                )
                if nodes:
                    return nodes[0]
            except BiDiError as exc:
                last_error = exc
            await asyncio.sleep(poll)
        raise TimeoutError(
            f"selector {selector!r} not found within {timeout}s"
            + (f" ({last_error})" if last_error else "")
        )

    async def click(self, selector: str, *, timeout: float = 10.0) -> None:
        node = await self.wait_for_selector(selector, timeout=timeout)
        element = {"sharedId": node["sharedId"]}
        await self.call(
            "input.performActions",
            {
                "context": self._require_context(),
                "actions": [
                    {
                        "type": "pointer",
                        "id": "mouse",
                        "actions": [
                            {
                                "type": "pointerMove",
                                "x": 0,
                                "y": 0,
                                "origin": {"type": "element", "element": element},
                            },
                            {"type": "pointerDown", "button": 0},
                            {"type": "pointerUp", "button": 0},
                        ],
                    }
                ],
            },
        )
        await self.call("input.releaseActions", {"context": self._require_context()})

    async def fill(self, selector: str, text: str, *, timeout: float = 10.0) -> None:
        """Clear a field and type ``text`` (works for typical login inputs)."""
        await self.wait_for_selector(selector, timeout=timeout)
        # Prefer DOM property updates + input/change events so frameworks see the value.
        js = """
        (selector, text) => {
          const el = document.querySelector(selector);
          if (!el) throw new Error('element not found: ' + selector);
          el.focus();
          const proto = el instanceof HTMLTextAreaElement
            ? HTMLTextAreaElement.prototype
            : HTMLInputElement.prototype;
          const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
          if (setter) setter.call(el, text);
          else el.value = text;
          el.dispatchEvent(new Event('input', { bubbles: true }));
          el.dispatchEvent(new Event('change', { bubbles: true }));
          return true;
        }
        """
        await self.call(
            "script.callFunction",
            {
                "functionDeclaration": js,
                "arguments": [
                    {"type": "string", "value": selector},
                    {"type": "string", "value": text},
                ],
                "target": {"context": self._require_context()},
                "awaitPromise": True,
            },
        )

    async def type_text(self, text: str, *, selector: str | None = None) -> None:
        """Send key events, optionally focusing ``selector`` first."""
        if selector:
            await self.click(selector)
        actions = []
        for ch in text:
            actions.append({"type": "keyDown", "value": ch})
            actions.append({"type": "keyUp", "value": ch})
        await self.call(
            "input.performActions",
            {
                "context": self._require_context(),
                "actions": [{"type": "key", "id": "keyboard", "actions": actions}],
            },
        )
        await self.call("input.releaseActions", {"context": self._require_context()})

    async def evaluate(self, expression: str, *, await_promise: bool = True) -> Any:
        result = await self.call(
            "script.evaluate",
            {
                "expression": expression,
                "target": {"context": self._require_context()},
                "awaitPromise": await_promise,
            },
        )
        if result.get("type") == "success":
            return _unwrap_remote_value(result.get("result"))
        raise BiDiError(result)

    async def text_content(self, selector: str, *, timeout: float = 10.0) -> str:
        await self.wait_for_selector(selector, timeout=timeout)
        value = await self.evaluate(
            f"document.querySelector({selector!r})?.textContent ?? ''"
        )
        return str(value)

    async def current_url(self) -> str:
        return str(await self.evaluate("location.href", await_promise=False))

    async def get_cookies(
        self,
        *,
        domain: str | None = None,
        name: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return browser cookies, including HttpOnly ones, sorted by domain then name.

        Covers every site in the browser profile, not just the current page.
        ``domain`` / ``name`` are exact-match filters (``domain=".airtable.com"``).
        Each cookie is returned once; the DevTools Storage Inspector may list a
        ``.example.com`` cookie under several hosts, BiDi does not.
        """
        flt = {
            key: value
            for key, value in (("domain", domain), ("name", name))
            if value is not None
        }
        result = await self.call("storage.getCookies", {"filter": flt} if flt else {})
        cookies = []
        for raw in result.get("cookies") or []:
            cookie = dict(raw)
            value = cookie.get("value")
            if isinstance(value, dict) and value.get("type") == "string":
                cookie["value"] = value[
                    "value"
                ]  # unwrap {"type": "string", "value": ...}
            cookies.append(cookie)
        cookies.sort(key=lambda c: (c["domain"], c["name"]))
        return cookies
