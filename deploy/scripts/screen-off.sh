#!/usr/bin/env bash
# worldplaner — Display vollstaendig ausschalten (MCP-Tool `screen_off`, Spez. J.2).
# DISPLAY kommt aus der Umgebung, Default :0. Kein xrandr noetig.
set -euo pipefail

DISPLAY="${DISPLAY:-:0}" xset dpms force off
