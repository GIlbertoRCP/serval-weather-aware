"""Minimal pure-Python stand-in for the `fastlogging` package.

Serval's simulator only uses LogInit/INFO/Logger.cbFormatter and the logger's
info/flush/stop/shutdown methods. The real package is a C extension that does
not build on current macOS/Python, so this shim writes the same tab-separated
lines with the standard library. It is placed on PYTHONPATH by our run scripts.
"""

INFO = 20


class Logger:
    cbFormatter = None

    def __init__(self, pathName, level=INFO, encoding="ascii", **_):
        self.level = level
        self._fh = open(pathName, "w", encoding=encoding, errors="replace")

    def info(self, msg):
        self._fh.write(f"{msg}\n")

    def flush(self):
        self._fh.flush()

    def stop(self, now=False):
        self.flush()

    def shutdown(self):
        if not self._fh.closed:
            self._fh.close()


def LogInit(pathName, level=INFO, encoding="ascii", useThreads=False, **kwargs):
    return Logger(pathName, level=level, encoding=encoding)
