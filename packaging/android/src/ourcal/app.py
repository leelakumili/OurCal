"""Android wrapper for OurCal.

Deliberately thin. Server, Google integration and the entire UI live in
``core`` — a byte-for-byte copy of the repo-root ``ourcal.py`` that the macOS
build also ships, dropped in here at build time by build-android.sh. This file
only does what a phone needs: start the in-process server and point a WebView at
it. The Android-specific behaviour (data dir, browser Intent, foreground token
exchange) already lives behind ``is_android()`` in ``core``.
"""
import toga

from ourcal import core


class OurCal(toga.App):
    def startup(self):
        # Bind on 127.0.0.1 and serve in-process, exactly as on desktop.
        self._server, url = core.start_server()

        self.main_window = toga.MainWindow(title=self.formal_name)
        self.web = toga.WebView(url=url, style=toga.style.Pack(flex=1))
        self.main_window.content = self.web
        self.main_window.show()

    def on_exit(self, *args, **kwargs):
        try:
            self._server.shutdown()
        except Exception:
            pass
        return True


def main():
    return OurCal("OurCal", "com.leelakumili.ourcal")
