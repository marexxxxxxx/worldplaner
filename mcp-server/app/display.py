"""Display-Steuerung (Spezifikation J.2).

Fuehrt exakt die in J.2 dokumentierten Befehle aus, mit ``DISPLAY`` und
``DISPLAY_OUTPUT`` aus der Umgebung. Ist X11 nicht erreichbar oder fehlen
``xset``/``xrandr``, wird ``DisplayUnavailable`` geworfen — der Tool-Handler
macht daraus den Fehlercode ``DISPLAY_UNAVAILABLE`` (F.2).
"""

from __future__ import annotations

import asyncio
import os

# Fehlertexte exakt wie in F.2 fuer `screen_off`/`screen_on` vorgegeben.
XSET_MESSAGE = "xset nicht ausführbar oder X11 nicht erreichbar"
XRANDR_MESSAGE = "xrandr nicht ausführbar oder X11 nicht erreichbar"


class DisplayUnavailable(Exception):
    """X11/Display nicht erreichbar oder benoetigtes Werkzeug fehlt."""


class DisplayController:
    def __init__(self, display: str = ":0", output: str = "eDP-1", timeout: float = 5.0) -> None:
        self._display = display
        self._output = output
        self._timeout = timeout

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        env["DISPLAY"] = self._display
        env["DISPLAY_OUTPUT"] = self._output
        return env

    async def _run(self, command: str, message: str) -> None:
        try:
            process = await asyncio.create_subprocess_shell(
                command,
                env=self._env(),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError:
            raise DisplayUnavailable(message) from None
        try:
            _stdout, stderr = await asyncio.wait_for(process.communicate(), self._timeout)
        except (asyncio.TimeoutError, TimeoutError):
            process.kill()
            await process.wait()
            raise DisplayUnavailable(message) from None
        # xset/xrandr sind bei Erfolg still. Fehler landen auf stderr — teils
        # trotz Exit-Code 0 (z. B. `xset dpms force off` ohne DPMS-Extension:
        # "server does not have extension for dpms option"). Beides zaehlt als
        # Fehlschlag, damit kein falscher Erfolg gemeldet wird.
        if process.returncode != 0 or stderr.strip():
            raise DisplayUnavailable(message)

    async def off(self) -> None:
        """`xset dpms force off` (J.2, screen-off.sh)."""
        await self._run("xset dpms force off", XSET_MESSAGE)

    async def on(self) -> None:
        """`xset dpms force on` + volle Helligkeit (J.2, screen-on.sh)."""
        await self._run(
            'xset dpms force on && xrandr --output "$DISPLAY_OUTPUT" --brightness 1.0',
            XSET_MESSAGE,
        )

    async def dim(self, brightness: float) -> None:
        """`xrandr --output … --brightness <wert>` (J.2, screen-dim.sh)."""
        await self._run(
            f'xrandr --output "$DISPLAY_OUTPUT" --brightness {brightness:.4f}',
            XRANDR_MESSAGE,
        )
