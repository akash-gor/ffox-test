"""Control jlesage/firefox: RFB for chrome, BiDi for page DOM."""

from __future__ import annotations
from argparse import Namespace
import asyncio

from typing import Any, Optional

from ffox_test._async_utils import run_sync
from ffox_test.bidi import (
    DEFAULT_BIDI_HOST,
    DEFAULT_BIDI_PORT,
    DEFAULT_CONTAINER,
    BiDiError,
    BiDiSession,
    ensure_bidi_proxy,
)
from ffox_test.rfb import DEFAULT_VNC_HOST, DEFAULT_VNC_PORT, RFBSession, normalize_url

__all__ = [
    "BiDiError",
    "BiDiSession",
    "FirefoxSession",
    "RFBSession",
    "click",
    "ensure_bidi_proxy",
    "evaluate",
    "fill",
    "navigate",
    "navigate_async",
    "wait_for_selector",
]


class FirefoxSession:
    """RFB drives the browser window; BiDi only touches the page DOM.

    - **Chrome** (address bar / navigate): RFB/noVNC — connected on enter.
    - **DOM** (``click``, ``fill``, ``evaluate``, …): BiDi — connected lazily
      on first DOM call, so it never runs until you need the page.
    """

    def __init__(
        self,
        *,
        host: str = DEFAULT_BIDI_HOST,
        bidi_port: int = DEFAULT_BIDI_PORT,
        vnc_host: str = DEFAULT_VNC_HOST,
        vnc_port: int = DEFAULT_VNC_PORT,
        container: str = DEFAULT_CONTAINER,
        auto_proxy: bool = True,
    ) -> None:
        self.bidi = BiDiSession(
            host=host,
            port=bidi_port,
            container=container,
            auto_proxy=auto_proxy,
        )
        self.rfb = RFBSession(host=vnc_host, port=vnc_port)

    async def __aenter__(self) -> "FirefoxSession":
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def connect(self) -> None:
        """Connect chrome control only (RFB). BiDi waits until a DOM call."""
        await self.rfb.connect()

    async def close(self) -> None:
        await self.bidi.close()
        await self.rfb.close()

    async def _ensure_bidi(self) -> BiDiSession:
        """Connect BiDi on first DOM use; refresh context after RFB navigations."""
        if not self.bidi.connected:
            await self.bidi.connect()
        else:
            await self.bidi.refresh_context()
        return self.bidi

    # --- chrome (RFB) ---

    async def navigate(
        self,
        url: str,
        *,
        settle: float = 1.0,
        ready: Optional[str] = None,
        timeout: float = 30.0,
    ) -> str:
        """Open ``url`` via the address bar (RFB).

        If ``ready`` is set, BiDi then waits for that CSS selector before returning.
        """
        if not self.rfb.connected:
            await self.rfb.connect()
        opened = await self.rfb.navigate(url, settle=settle)
        if ready:
            await self.wait_for_selector(ready, timeout=timeout)
        return opened

    # --- page DOM (BiDi, lazy) ---

    async def click(self, selector: str, *, timeout: float = 10.0) -> None:
        bidi = await self._ensure_bidi()
        await bidi.click(selector, timeout=timeout)

    async def fill(self, selector: str, text: str, *, timeout: float = 10.0) -> None:
        bidi = await self._ensure_bidi()
        await bidi.fill(selector, text, timeout=timeout)

    async def type_text(self, text: str, *, selector: Optional[str] = None) -> None:
        bidi = await self._ensure_bidi()
        await bidi.type_text(text, selector=selector)

    async def wait_for_selector(
        self,
        selector: str,
        *,
        timeout: float = 10.0,
        locator_type: str = "css",
    ) -> dict[str, Any]:
        bidi = await self._ensure_bidi()
        return await bidi.wait_for_selector(
            selector, timeout=timeout, locator_type=locator_type
        )

    async def evaluate(self, expression: str, *, await_promise: bool = True) -> Any:
        bidi = await self._ensure_bidi()
        return await bidi.evaluate(expression, await_promise=await_promise)

    async def text_content(self, selector: str, *, timeout: float = 10.0) -> str:
        bidi = await self._ensure_bidi()
        return await bidi.text_content(selector, timeout=timeout)

    async def current_url(self) -> str:
        bidi = await self._ensure_bidi()
        return await bidi.current_url()


async def navigate_async(url: str, **kwargs: Any) -> str:
    async with FirefoxSession(**kwargs) as session:
        return await session.navigate(url)


def navigate(url: str, **kwargs: Any) -> str:
    return run_sync(navigate_async(url, **kwargs))


async def _with_session(method: str, *args: Any, **kwargs: Any) -> Any:
    session_kwargs = {
        key: kwargs.pop(key)
        for key in ("host", "bidi_port", "vnc_host", "vnc_port", "container", "auto_proxy")
        if key in kwargs
    }
    if "port" in kwargs:
        session_kwargs["bidi_port"] = kwargs.pop("port")
    async with FirefoxSession(**session_kwargs) as session:
        return await getattr(session, method)(*args, **kwargs)


def click(selector: str, **kwargs: Any) -> None:
    run_sync(_with_session("click", selector, **kwargs))


def fill(selector: str, text: str, **kwargs: Any) -> None:
    run_sync(_with_session("fill", selector, text, **kwargs))


def wait_for_selector(selector: str, **kwargs: Any) -> dict[str, Any]:
    return run_sync(_with_session("wait_for_selector", selector, **kwargs))


def evaluate(expression: str, **kwargs: Any) -> Any:
    return run_sync(_with_session("evaluate", expression, **kwargs))


async def run(args: Namespace) -> None:
    async with FirefoxSession() as ff:
        await ff.navigate("https://airtable.com/login", ready='input[type="email"], input[name="email"]')
        await ff.fill('input[type="email"], input[name="email"]', "devops@gordian.bio")
        await ff.click("#sign-in-form-fields-root button[type=submit]")  # Continue
        await ff.wait_for_selector('input[type="password"]', timeout=60)
        await ff.fill('input[type="password"]', "test123")
        # await ff.click('button[type=submit]')  # tighten once you see Sign in markup
        # then poll current_url until "login" not in it

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Open a URL in jlesage/firefox via the address bar (RFB)"
    )
    parser.add_argument("url", help="URL to open")
    parser.add_argument("--vnc-host", default=DEFAULT_VNC_HOST)
    parser.add_argument("--vnc-port", type=int, default=DEFAULT_VNC_PORT)
    args = parser.parse_args()
    
    asyncio.run(run(args))
    
    opened = navigate(args.url, vnc_host=args.vnc_host, vnc_port=args.vnc_port)
    print(f"navigated to {opened}")
