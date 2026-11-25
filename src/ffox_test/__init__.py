"""Control jlesage/firefox: RFB for chrome, BiDi for page DOM."""

from __future__ import annotations

import asyncio
import json
import os
from argparse import Namespace
from typing import Any, Self

import requests
from dotenv import load_dotenv

from ffox_test._async_utils import run_sync
from ffox_test.bidi import (
    DEFAULT_BIDI_HOST,
    DEFAULT_BIDI_PORT,
    DEFAULT_CONTAINER,
    BiDiError,
    BiDiSession,
    ensure_bidi_proxy,
)
from ffox_test.rfb import DEFAULT_VNC_HOST, DEFAULT_VNC_PORT, RFBSession

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

    async def __aenter__(self) -> Self:
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
        ready: str | None = None,
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

    async def type_text(self, text: str, *, selector: str | None = None) -> None:
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

    async def has_selector(self, selector: str, *, locator_type: str = "css") -> bool:
        """Return True if ``selector`` matches something right now. Does not wait."""
        bidi = await self._ensure_bidi()
        return bool(await bidi.locate(selector, locator_type=locator_type, max_count=1))

    async def evaluate(self, expression: str, *, await_promise: bool = True) -> Any:
        bidi = await self._ensure_bidi()
        return await bidi.evaluate(expression, await_promise=await_promise)

    async def text_content(self, selector: str, *, timeout: float = 10.0) -> str:
        bidi = await self._ensure_bidi()
        return await bidi.text_content(selector, timeout=timeout)

    async def current_url(self) -> str:
        bidi = await self._ensure_bidi()
        return await bidi.current_url()

    async def get_cookies(
        self,
        *,
        domain: str | None = None,
        name: str | None = None,
    ) -> list[dict[str, Any]]:
        """Browser cookies (incl. HttpOnly), sorted by domain then name."""
        bidi = await self._ensure_bidi()
        return await bidi.get_cookies(domain=domain, name=name)


async def navigate_async(url: str, **kwargs: Any) -> str:
    async with FirefoxSession(**kwargs) as session:
        return await session.navigate(url)


def navigate(url: str, **kwargs: Any) -> str:
    return run_sync(navigate_async(url, **kwargs))


async def _with_session(method: str, *args: Any, **kwargs: Any) -> Any:
    session_kwargs = {
        key: kwargs.pop(key)
        for key in (
            "host",
            "bidi_port",
            "vnc_host",
            "vnc_port",
            "container",
            "auto_proxy",
        )
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


async def run(args: Namespace) -> dict[str, str]:
    """
    Generates User-Agent and Cookie headers for a read request to Airtable.
    """
    read_request_cookie_names = {
        "AWSALBTG",
        "AWSALBTGCORS",
        "AWSALBAPP-0",
        "AWSALBAPP-1",
        "AWSALBAPP-2",
        "AWSALBAPP-3",
        "brw",
        "brwConsent",
        "__Host-airtable-session",
        "__Host-airtable-session.sig",
        "marketing-homepage-e4-segment",
        "googleOneTapNonce",
        "_gcl_au",
        "mv",
        "_pxhd",
        "_ga_VJY8J9RFZM",
        "_ga",
        "g_state",
        "_px3",
        "pxcts",
        "_pxvid",
        "_pxff_fp",
        "localePref",
        "deploymentTier",
        "userSignature",
        "userSignature.sig",
    }

    async with FirefoxSession() as ff:
        await ff.navigate(
            "https://airtable.com",
            ready='[class*="ActionGroup-module_actionGroupWrap"] a[href*="login"]',
        )
        print("navigated to airtable home page")

        if await ff.has_selector("#close"):
            await ff.click("#close")
            print("closed google airtable login pop-up")

        # Scope to the desktop navbar: a plain a[href*="login"] matches a hidden
        # mobile-menu link first, which BiDi refuses to click.
        print("clicking login link...")
        await ff.click('[class*="ActionGroup-module_actionGroupWrap"] a[href*="login"]')
        await asyncio.sleep(1)

        print("filling email...")
        await ff.fill(
            'input[type="email"], input[name="email"]',
            os.getenv("READONLY_AIRTABLE_EMAIL") or "devops@gordian.bio",
        )
        print("filled email")
        await asyncio.sleep(1)
        await ff.click("#sign-in-form-fields-root button[type=submit]")  # Continue
        print("clicked continue")
        await asyncio.sleep(1)
        await ff.wait_for_selector('input[type="password"]', timeout=60)
        print("waited for password input")
        await ff.fill(
            'input[type="password"]', os.getenv("READONLY_AIRTABLE_PWD") or ""
        )
        print("filled password")
        await asyncio.sleep(1)
        print("logging in...")
        await ff.click("button[type=submit]")  # tighten once you see Sign in markup
        await asyncio.sleep(1)

        print("getting cookies...")
        all_cookies = await ff.get_cookies()
        res = [c for c in all_cookies if c["name"] in read_request_cookie_names]
        found = {c["name"] for c in res}
        print(
            f"kept {len(res)} of {len(read_request_cookie_names)} read-request cookies"
        )
        if missing := sorted(read_request_cookie_names - found):
            print("missing from browser (names only):", missing)

        user_agent = (
            await ff.evaluate("navigator.userAgent")
            or "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:160.0) Gecko/20100101 Firefox/160.0"
        )
        cookie_str = assemble_cookies_for_read_request(res)
        assert isinstance(user_agent, str), "user_agent is not a string"
        assert isinstance(cookie_str, str), "cookie_str is not a string"

        return {"User-Agent": user_agent, "Cookie": cookie_str}


def assemble_cookies_for_read_request(cookies: list[dict]) -> str:
    return "; ".join([f"{c['name']}={c['value']}" for c in cookies])


def main() -> None:
    import argparse

    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Open a URL in jlesage/firefox via the address bar (RFB)"
    )
    parser.add_argument("url", help="URL to open")
    parser.add_argument("--vnc-host", default=DEFAULT_VNC_HOST)
    parser.add_argument("--vnc-port", type=int, default=DEFAULT_VNC_PORT)
    args = parser.parse_args()

    generated_headers = asyncio.run(run(args))

    base_ids_to_names = {
        "appgPC6hb4QiY8a1I": "Aerarium-MolBio",
        "app1AZTpDqMLQ5RGN": "Aerarium-AAV",
        "appr2Y3rKYdxr3lVd": "Aerarium-SingleCell",
        "appmMrBxRHGCnMKHW": "Aerarium-Animals",
        "app4TX4qkidGYhxuT": "Aerarium-Projects",
        "appZVgmv7HyI7xh7z": "Progressus",
        "appJLRWejhNYia6TQ": "Aerarium-CellrangerDualIndices",
        "app63IUVxGT6oQ6fh": "Aerarium-Constants",
        "appGWRGZiYl9skgIv": "Aerarium-Delivery",
        "appjl8PViUuMuGyq0": "Aerarium-SingleCell (Copy)",
        "app2G2yx4FevGfIoE": "(UNDER CONSTRUCTION) Aerarium-DiseaseLeads",
        "app1CB8LBslk0ZJY5": "NovaCaelum-Delivery (Development Only)",
        "app8rAY6QXFQzNtiT": "Aerarium-MASH",
        "appQV8A4SkaHaXHGf": "NovaCaelum-IPF (Development Only)",
        "appgNIFffz9a3eqfm": "NovaCaelum-MASH (Development Only)",
        "appAVGslXTrVnj8iV": "NovaCaelum-OA (Development Only)",
        "appndWS2RD2RSViAo": "Aerarium-HFpEF",
        "appqPFkzZHkIctDmF": "Aerarium-OA",
        "appmM1jzvHnJwsYiE": "Aerarium-PF",
        "appjUVa9Vo73WjcDv": "NovaCaelum-HFpEF (Development Only)",
        "apptbapbjPgwqN4cC": "Concord JoP - Comparing Screens",
        "appn8iUb4L8pOpGWW": "CRM Single cell screening conference",
        "appHupMbZcaj7Myis": "NovaCaelum-ExVivo-OA (Development Only)",
        "appbAoKXOZEgKTPAs": "NovaCaelum-AAV (Development Only)",
        "appfPuRyKWssUbGcZ": "Equipment Assets and Software DB",
        "appOI9M4l534c9gR2": "Aerarium-Molecules",
        "app7rhks0CudClxMc": "Long Read Sequencing",
        "appRQz79VPvL5DFhV": "Aerarium-CDD",
    }

    rollup_formulas: dict[str, str] = {}

    for base_id in base_ids_to_names:
        headers = {
            "User-Agent": generated_headers["User-Agent"],
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Referer": f"https://airtable.com/{base_id}?",
            "Connection": "keep-alive",
            "Cookie": generated_headers["Cookie"],
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "no-cors",
            "Sec-Fetch-Site": "same-origin",
            "TE": "trailers",
            "x-airtable-application-id": base_id,
            "x-airtable-client-document-visibility-state": "visible",
            "x-airtable-client-queue-time": "2",
            "x-airtable-inter-service-client": "webClient",
            "x-requested-with": "XMLHttpRequest",
            "x-time-zone": "Africa/Abidjan",
            "x-user-locale": "en",
            "Priority": "u=4",
            "Pragma": "no-cache",
            "Cache-Control": "no-cache",
        }
        response = requests.get(
            f"https://airtable.com/v0.3/application/{base_id}/read",
            headers=headers,
        )
        print(f"got response for {base_id}, {response.status_code=}")

        data = response.json()["data"]
        table_schemas = data["tableSchemas"]

        for table_schema in table_schemas:
            columns = table_schema["columns"]
            for column in columns:
                field_id = column["id"]
                if column["type"] == "rollup":
                    opts = column["typeOptions"]
                    formula = opts["formulaTextParsed"]
                    rollup_formulas[field_id] = formula

    with open("rollup_formulas.json", "w") as f:
        json.dump(rollup_formulas, f, indent=2)
    print("rollup formulas saved to rollup_formulas.json")
