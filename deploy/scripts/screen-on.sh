#!/usr/bin/env bash
# worldplaner — Display einschalten und auf volle Helligkeit setzen
# (MCP-Tool `screen_on`, Spez. J.2).
# DISPLAY und DISPLAY_OUTPUT kommen aus der Umgebung (Defaults :0 bzw. eDP-1).
set -euo pipefail

DISPLAY="${DISPLAY:-:0}" xset dpms force on
DISPLAY="${DISPLAY:-:0}" xrandr --output "${DISPLAY_OUTPUT:-eDP-1}" --brightness 1.0
