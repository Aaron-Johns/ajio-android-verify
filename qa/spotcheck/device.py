"""Thin Appium/UiAutomator2 wrapper for driving the AJIO app on the emulator (CLAUDE.md section 10).

Requires an Appium server (default http://127.0.0.1:4723, uiautomator2 driver) and a running
emulator with the app installed. The app's home carousel never lets the screen go idle, so idle
waiting is disabled.
"""
from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from qa.spotcheck import landing as lp

_BOUNDS = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")
HOME_ACTIVITY_SUFFIX = "AjioHomeActivity"
APPIUM_SERVER = "http://127.0.0.1:4723"


class DeviceError(RuntimeError):
    pass


def parse_bounds(text: str) -> tuple[int, int, int, int] | None:
    m = _BOUNDS.fullmatch(text or "")
    return tuple(int(g) for g in m.groups()) if m else None


def clickable_descs(page_source: str, width: int, height: int) -> dict[str, list[tuple[int, int, int, int]]]:
    """Clickable elements with a content-desc that lie fully inside the scrollable content area."""
    top, bottom = int(height * 0.06), int(height * 0.90)  # below the status bar, above the bottom tab bar
    found: dict[str, list] = {}
    for n in ET.fromstring(page_source).iter():
        desc, box = n.get("content-desc"), parse_bounds(n.get("bounds") or "")
        if not desc or n.get("clickable") != "true" or box is None:
            continue
        x1, y1, x2, y2 = box
        if x1 >= 0 and x2 <= width and y1 >= top and y2 <= bottom and x2 > x1 and y2 > y1:
            found.setdefault(desc, []).append(box)
    return found


def looks_like_home(package: str, activity: str, page_source: str) -> bool:
    return (package == lp.APP_PACKAGE and activity.endswith(HOME_ACTIVITY_SUFFIX)
            and 'content-desc="Right Now"' in page_source
            and "plp_sort_by_view" not in page_source and "toolbar_title_tv" not in page_source)


class Device:
    def __init__(self, server: str = APPIUM_SERVER, udid: str = "emulator-5554"):
        self.server, self.udid, self.driver = server, udid, None

    def __enter__(self) -> "Device":
        from appium import webdriver
        from appium.options.android import UiAutomator2Options

        opts = UiAutomator2Options()
        opts.platform_name = "Android"
        opts.udid = self.udid
        opts.no_reset = True
        opts.set_capability("appium:appPackage", lp.APP_PACKAGE)
        opts.set_capability("appium:appActivity", f"com.ril.ajio.home.{HOME_ACTIVITY_SUFFIX}")
        opts.set_capability("appium:autoLaunch", False)
        opts.set_capability("appium:newCommandTimeout", 300)
        try:
            self.driver = webdriver.Remote(self.server, options=opts)
        except Exception as exc:
            raise DeviceError(f"could not start an Appium session at {self.server}: {exc}") from exc
        self.driver.update_settings({"waitForIdleTimeout": 0, "waitForSelectorTimeout": 0})
        size = self.driver.get_window_size()
        self.width, self.height = size["width"], size["height"]
        return self

    def __exit__(self, *exc) -> None:
        if self.driver:
            self.driver.quit()

    def state(self) -> tuple[str, str, str]:
        return self.driver.current_package, self.driver.current_activity, self.driver.page_source

    def screenshot(self, path: Path) -> str:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.driver.get_screenshot_as_file(str(path))
        return str(path)

    def screenshot_png(self) -> bytes:
        return self.driver.get_screenshot_as_png()

    def scroll(self, direction: str = "down", percent: float = 0.6) -> None:
        self.driver.execute_script("mobile: scrollGesture", {
            "left": 100, "top": int(self.height * 0.25), "width": self.width - 200,
            "height": int(self.height * 0.5), "direction": direction, "percent": percent})

    def scroll_to_top(self, swipes: int = 5) -> None:
        for _ in range(swipes):
            self.scroll("up", 0.9)
        time.sleep(0.5)

    def tap(self, box: tuple[int, int, int, int]) -> None:
        x1, y1, x2, y2 = box
        self.driver.execute_script("mobile: clickGesture", {"x": (x1 + x2) // 2, "y": (y1 + y2) // 2})

    def type_into(self, resource_id: str, text: str) -> None:
        from appium.webdriver.common.appiumby import AppiumBy
        el = self.driver.find_element(AppiumBy.ID, f"{lp.APP_PACKAGE}:id/{resource_id}")
        el.click()
        el.clear()
        el.send_keys(text)

    def restart_app(self, settle: float = 15.0) -> None:
        self.driver.terminate_app(lp.APP_PACKAGE)
        self.driver.activate_app(lp.APP_PACKAGE)
        time.sleep(settle)

    def return_home(self) -> None:
        for _ in range(4):
            package, activity, source = self.state()
            if looks_like_home(package, activity, source):
                return
            self.driver.back()
            time.sleep(1.5)
        self.restart_app()

    def capture_after_tap(self, before_source: str, timeout: float = 10.0, settle: float = 3.0) -> tuple[str, str, str]:
        """Wait for the screen to change (or the timeout), let it render, then return package/activity/source."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(1.0)
            package, activity, source = self.state()
            if (package != lp.APP_PACKAGE or "WebView" in activity
                    or lp.similarity(before_source, source) < lp.NAVIGATION_SIMILARITY):
                break
        time.sleep(settle)
        return self.state()
