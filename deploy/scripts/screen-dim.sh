#!/usr/bin/env bash
# worldplaner — Display dimmen (MCP-Tool `screen_dim`, Spez. J.2).
# Argument 1 = Helligkeit 0.0-1.0 (Default 0.3).
# DISPLAY und DISPLAY_OUTPUT kommen aus der Umgebung (Defaults :0 bzw. eDP-1).
set -euo pipefail

BRIGHTNESS="${1:-0.3}"

DISPLAY="${DISPLAY:-:0}" xrandr --output "${DISPLAY_OUTPUT:-eDP-1}" --brightness "$BRIGHTNESS"
