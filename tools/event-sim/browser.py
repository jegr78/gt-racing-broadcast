"""A shared headless Chromium for the roles, through the optional Playwright library.

`Browser.get()` starts it on first use and returns None when Playwright or its Chromium
is missing; the caller then records a soft finding instead of a screenshot."""


class Browser:
    def __init__(self, headless=True, executable=None):
        self.headless = headless
        self.executable = executable
        self._pw = None
        self._browser = None
        self.problem = ""

    def get(self):
        if self._browser is not None or self.problem:
            return self._browser
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.problem = "Playwright is not installed (see tools/CLAUDE.md, e2e visual run)"
            return None
        try:
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=self.headless, executable_path=self.executable)
        except Exception as exc:  # a missing Chromium download is the common case
            self.problem = f"Chromium did not start: {exc}"
            self.close()
            return None
        return self._browser

    def close(self):
        for obj, method in ((self._browser, "close"), (self._pw, "stop")):
            if obj is not None:
                try:
                    getattr(obj, method)()
                except Exception:
                    pass  # closing a half-started browser is best effort
        self._browser = self._pw = None
