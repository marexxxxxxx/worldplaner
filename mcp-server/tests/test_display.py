"""Display-Fehlerpfad: ohne X11 -> DisplayUnavailable statt Absturz (J.2/F.2)."""

import asyncio

import pytest

from app.display import DisplayController, DisplayUnavailable


def test_screen_off_without_x11_raises_display_unavailable():
    controller = DisplayController(display=":999", output="eDP-1", timeout=5.0)
    with pytest.raises(DisplayUnavailable):
        asyncio.run(controller.off())


def test_screen_dim_without_x11_raises_display_unavailable():
    controller = DisplayController(display=":999", output="eDP-1", timeout=5.0)
    with pytest.raises(DisplayUnavailable):
        asyncio.run(controller.dim(0.3))


def test_silent_zero_exit_is_success():
    # `true` ist still und liefert Exit 0 -> kein Fehler.
    asyncio.run(DisplayController()._run("true", "xset"))


def test_stderr_output_is_failure_even_with_exit_zero():
    # Reproduziert `xset dpms force off` ohne DPMS-Extension: Exit 0, aber stderr.
    controller = DisplayController()
    with pytest.raises(DisplayUnavailable):
        asyncio.run(controller._run("printf 'boom' >&2", "xset"))
