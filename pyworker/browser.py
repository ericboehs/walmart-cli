"""Browser lifecycle: one dedicated, persistent, mode-restricted profile.

Rules from DESIGN.md enforced here:
  * we never touch the user's everyday browser profile;
  * only one process may own the profile at a time, and contention is reported
    rather than allowed to corrupt session state;
  * login is visible, normal commands are headless;
  * every navigation passes through the guard in safety.py.
"""

from __future__ import annotations

import errno
import os
import re
import shutil
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import safety

WALMART_ORIGIN = "https://www.walmart.com"

# Why a browser lookup failed, when it failed for a reason worth telling the
# user about. Reset per process; the worker is one-shot.
LOOKUP_ERRORS: dict[str, str] = {}

# Where an installed Chrome usually lives. Playwright's bundled Chromium is the
# fallback; recon decides which one Walmart's bot protection tolerates.
# Playwright's own on-disk layout, per platform, under chromium-<build>/.
CHROMIUM_RELATIVE_PATHS = {
    "darwin": ["chrome-mac/Chromium.app/Contents/MacOS/Chromium"],
    "linux": ["chrome-linux/chrome", "chrome-linux/headless_shell"],
}

CHROME_CANDIDATES = {
    "darwin": [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
    ],
    "linux": [
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
    ],
}


class ProfileLocked(Exception):
    """Another walmart command already owns the browser profile."""


class BrowserUnavailable(Exception):
    """No usable Chrome/Chromium, or Playwright is not installed."""


def warn(message: str) -> None:
    """Diagnostics for the human, on stderr.

    stdout belongs to the NDJSON protocol, and Ruby keeps a bounded tail of
    stderr, so this reaches the user without corrupting a JSON document.
    """
    print(f"warning: {message}", file=sys.stderr, flush=True)


def platform_key() -> str:
    if sys.platform.startswith("darwin"):
        return "darwin"
    if sys.platform.startswith("linux"):
        return "linux"
    return sys.platform


def find_browsers() -> dict[str, str | None]:
    """Local discovery only. Never launches anything."""
    LOOKUP_ERRORS.clear()
    found: dict[str, str | None] = {}
    for path in CHROME_CANDIDATES.get(platform_key(), []):
        if os.path.exists(path):
            found.setdefault("chrome" if "chrome" in path.lower() else "chromium", path)
    for name in ("google-chrome", "chromium", "chromium-browser"):
        path = shutil.which(name)
        if path:
            found.setdefault("chrome" if "chrome" in name else "chromium", path)

    playwright_path = _playwright_chromium_path()
    if playwright_path:
        found["playwright-chromium"] = playwright_path
    return found


def _playwright_chromium_path() -> str | None:
    """Playwright's bundled Chromium, or None. Records why on failure.

    "Playwright is broken" and "no browser is installed" call for opposite
    fixes, so the distinction is preserved in ``LOOKUP_ERRORS`` rather than
    flattened into a None that makes doctor say "install a browser".

    The registry directory is read directly. Asking Playwright for the path
    means *starting* Playwright -- a driver process and an event loop, just to
    learn a filename -- which also warns when anything else is already inside an
    asyncio loop. Discovery should not need a subprocess.
    """
    try:
        import playwright  # noqa: F401
    except ImportError:
        return None

    path = _chromium_from_registry()
    if path:
        return path
    return _chromium_from_driver()


def _registry_root() -> Path:
    """Where Playwright unpacks browsers, per its own documented layout."""
    override = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if override and override != "0":
        return Path(override)
    if platform_key() == "darwin":
        return Path.home() / "Library" / "Caches" / "ms-playwright"
    return Path.home() / ".cache" / "ms-playwright"


def _chromium_from_registry() -> str | None:
    """Newest installed chromium build, found by looking, not by launching."""
    root = _registry_root()
    if not root.is_dir():
        return None

    builds: list[tuple[int, str]] = []
    try:
        entries = sorted(root.iterdir())
    except OSError as exc:
        LOOKUP_ERRORS["playwright-chromium"] = f"could not read {root}: {exc}"
        return None

    for entry in entries:
        if not entry.name.startswith("chromium-"):
            continue
        for relative in CHROMIUM_RELATIVE_PATHS.get(platform_key(), []):
            candidate = entry / relative
            if candidate.exists():
                builds.append((_build_number(entry.name), str(candidate)))
                break

    if not builds:
        return None
    return max(builds)[1]


def _build_number(name: str) -> int:
    """Sort chromium-1187 above chromium-999 without lexicographic surprises."""
    digits = re.sub(r"\D", "", name)
    return int(digits) if digits else 0


def _chromium_from_driver() -> str | None:
    """Last resort: ask Playwright, accepting the cost of starting it.

    Only reached when the registry lookup found nothing, so an unusual install
    layout still works instead of reporting a missing browser.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    try:
        with sync_playwright() as pw:
            path = pw.chromium.executable_path
    except Exception as exc:  # noqa: BLE001 - discovery must never crash doctor
        LOOKUP_ERRORS["playwright-chromium"] = f"{type(exc).__name__}: {exc}"
        warn(f"playwright chromium lookup failed: {exc}")
        return None
    if path and os.path.exists(path):
        return path
    if path:
        LOOKUP_ERRORS["playwright-chromium"] = f"executable path does not exist: {path}"
        warn(f"playwright reports a chromium at {path}, but it is not there")
    return None


def playwright_version() -> str | None:
    try:
        import playwright  # noqa: F401
    except ImportError:
        return None
    try:
        from importlib.metadata import version

        return version("playwright")
    except Exception:  # noqa: BLE001 - version metadata is a nicety, not a gate
        return "installed"


@contextmanager
def profile_lock(lock_path: str):
    """Exclusive, advisory, and self-explanatory when it fails."""
    import fcntl

    os.makedirs(os.path.dirname(lock_path) or ".", exist_ok=True)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EAGAIN):
                raise ProfileLocked(
                    "another walmart command is using the browser profile; wait for it to finish or kill it"
                ) from exc
            raise
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode())
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


@dataclass(frozen=True)
class BrowserOptions:
    profile_dir: str
    profile_lock: str
    headless: bool = True
    browser_channel: str | None = None
    slow_mo: int = 0
    viewport: tuple[int, int] = (1440, 900)

    @classmethod
    def from_request(cls, options: dict[str, Any], headless: bool | None = None) -> BrowserOptions:
        """Build options from a worker request.

        ``headless`` overrides the request, which is how login states outright
        that it is visible rather than mutating a shared object afterwards.
        """
        requested = bool(options.get("headless", True)) if headless is None else headless
        return cls(
            profile_dir=options["profile_dir"],
            profile_lock=options.get("profile_lock") or (options["profile_dir"] + ".lock"),
            headless=requested,
            browser_channel=options.get("browser_channel"),
        )


class Session:
    """A live persistent context plus the guarded operations we allow on it.

    The Playwright context is deliberately private. Everything that touches the
    page goes through goto() or click(), because a guard that can be bypassed by
    reaching for the raw object is not a guard.
    """

    def __init__(self, context, options: BrowserOptions):
        self._context = context
        self.options = options

    @property
    def page(self):
        pages = self._context.pages
        return pages[0] if pages else self._context.new_page()

    def on_response(self, handler) -> None:
        """Subscribe to responses. Used by reconnaissance recording."""
        self._context.on("response", handler)

    def close(self) -> None:
        self._context.close()

    def goto(self, url: str, **kwargs):
        """The only navigation entry point. Refuses forbidden destinations."""
        safety.assert_navigable(url)
        return self.page.goto(url, **kwargs)

    def click(self, locator, label: str | None = None, **kwargs):
        """The only click entry point, and it fails closed.

        If we cannot establish what a control says, we do not click it. An
        unreadable label is exactly the case where an unknown button might be
        the one that submits an order, so silence is refusal, not consent.

        A caller that already knows what a control is may pass ``label``; it is
        still checked against the forbidden patterns.
        """
        text = label if label is not None else self._read_label(locator)
        if text is None or not str(text).strip():
            raise safety.UnsafeNavigation(
                "refusing to click a control whose label could not be read; "
                "pass an explicit label only when the caller knows what the control is"
            )
        safety.assert_clickable(text)
        return locator.click(**kwargs)

    def _read_label(self, locator) -> str | None:
        """Best-effort label from the DOM. Returns None when nothing is readable."""
        readers = (
            lambda: locator.inner_text(timeout=1000),
            lambda: locator.get_attribute("aria-label"),
            lambda: locator.get_attribute("value"),
            lambda: locator.get_attribute("title"),
        )
        for reader in readers:
            try:
                value = reader()
            except Exception:  # noqa: BLE001 - one unreadable attribute is not fatal on its own
                continue
            if value and str(value).strip():
                return str(value)
        return None


@contextmanager
def open_session(options: BrowserOptions):
    """Launch (or reuse) the dedicated persistent profile."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - environment problem
        raise BrowserUnavailable(
            "playwright is not installed; run: cd pyworker && uv sync && uv run playwright install chromium"
        ) from exc

    os.makedirs(options.profile_dir, exist_ok=True)
    os.chmod(options.profile_dir, 0o700)

    with profile_lock(options.profile_lock), sync_playwright() as pw:
        context = launch_context(pw, options)
        try:
            yield Session(context, options)
        finally:
            context.close()


def launch_context(pw, options: BrowserOptions):
    """Launch the persistent context, falling back off an unusable channel.

    A silent fallback would hide the single most useful fact when Walmart's bot
    protection behaves differently than expected: which browser we are actually
    driving. So the fallback warns, loudly, on stderr.
    """
    launch_kwargs: dict[str, Any] = {
        "user_data_dir": options.profile_dir,
        "headless": options.headless,
        "viewport": {"width": options.viewport[0], "height": options.viewport[1]},
        "args": ["--disable-blink-features=AutomationControlled"],
    }
    channel = options.browser_channel or _default_channel()
    if channel:
        launch_kwargs["channel"] = channel

    try:
        return pw.chromium.launch_persistent_context(**launch_kwargs)
    except Exception as exc:  # noqa: BLE001 - one retry, without the channel
        if "channel" not in launch_kwargs:
            raise BrowserUnavailable(f"could not launch a browser: {exc}") from exc
        failed_channel = launch_kwargs.pop("channel")
        warn(
            f"browser channel {failed_channel!r} failed to launch ({exc}); "
            "falling back to Playwright's bundled Chromium"
        )
        try:
            return pw.chromium.launch_persistent_context(**launch_kwargs)
        except Exception as fallback_exc:  # noqa: BLE001
            raise BrowserUnavailable(f"could not launch a browser: {fallback_exc}") from fallback_exc


def _default_channel() -> str | None:
    browsers = find_browsers()
    return "chrome" if "chrome" in browsers else None
