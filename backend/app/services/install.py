"""Zip-/Git-Widget-Installation (spec-phase2 D/WP-P2-4).

Ablauf: Archiv/Clone in ein temporaeres Verzeichnis entpacken bzw. klonen,
`manifest.json` gegen den Plugin-Vertrag (spec E.2) pruefen, erst danach nach
`WIDGETS_INSTALL_DIR/<id>/` verschieben und die `widgets`-Zeile mit
`is_builtin=0` anlegen. Jeder Validierungsfehler laesst `WIDGETS_INSTALL_DIR`
unberuehrt (kein Teilrest); eine bestehende Registrierung wird vor dem
Schreiben als `409 CONFLICT` abgelehnt.

Die Herkunft (Zip/Git) wird nicht persistiert (spec-phase2 C.3/D19); sie ergibt
sich aus dem Ablageort und `is_builtin=0`.
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from .. import db
from ..config import get_settings
from ..models import conflict, validation_error

DEFAULT_INSTALL_DIR = "/data/widgets"
DEFAULT_MAX_ZIP_BYTES = 10485760
DEFAULT_MAX_UNCOMPRESSED_BYTES = 52428800
GIT_TIMEOUT_SECONDS = 60

# Manifest-Pflichtfelder und Wertebereiche (spec E.2).
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
SEMVER_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")
CATEGORIES = ("weather", "health", "productivity", "general")
PERMISSIONS = (
    "garmin_cache",
    "weather",
    "tasks",
    "state",
    "api_cache",
    "baikal",
    "grocy",
    "mealie",
)
REQUIRED_MANIFEST_FIELDS = ("id", "name", "version", "category", "default_size")

# Nur explizit unterstuetzte Git-URL-Formen; lokale Pfade fuer Tests/Offline.
GIT_URL_PREFIXES = ("http://", "https://", "ssh://", "git@", "file://")
GIT_REF_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")


def _config() -> dict[str, Any]:
    """Install-Einstellungen; Settings-Wert, sonst Env, sonst Default.

    `config.py` wird im Integrationsschritt (Plan 10.2) additiv ergaenzt; bis
    dahin greift der dokumentierte Env-Wert.
    """
    settings = get_settings()

    def text(attribute: str, env_name: str, default: str) -> str:
        value = getattr(settings, attribute, None)
        if value is None:
            value = os.environ.get(env_name, default)
        return str(value)

    def number(attribute: str, env_name: str, default: int) -> int:
        try:
            return int(text(attribute, env_name, str(default)))
        except ValueError:
            return default

    return {
        "install_dir": text("widgets_install_dir", "WIDGETS_INSTALL_DIR", DEFAULT_INSTALL_DIR),
        "max_zip_bytes": number(
            "widgets_max_zip_bytes", "WIDGETS_MAX_ZIP_BYTES", DEFAULT_MAX_ZIP_BYTES
        ),
        "max_uncompressed_bytes": number(
            "widgets_max_uncompressed_bytes",
            "WIDGETS_MAX_UNCOMPRESSED_BYTES",
            DEFAULT_MAX_UNCOMPRESSED_BYTES,
        ),
    }


def install_dir() -> Path:
    """Zielordner installierter Widgets (`WIDGETS_INSTALL_DIR`)."""
    return Path(_config()["install_dir"])


# --- Manifest ---------------------------------------------------------------


def _read_manifest(root: Path) -> dict:
    path = root / "manifest.json"
    if not path.is_file():
        raise validation_error("manifest.json fehlt im Widget-Ordner")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise validation_error("manifest.json ist kein gueltiges JSON") from exc
    return validate_manifest(data)


def validate_manifest(data: Any) -> dict:
    """Prueft ein Manifest gegen den Plugin-Vertrag (spec E.2)."""
    if not isinstance(data, dict):
        raise validation_error("manifest.json ist kein JSON-Objekt")
    missing = [field for field in REQUIRED_MANIFEST_FIELDS if field not in data]
    if missing:
        raise validation_error(f"manifest.json fehlen Pflichtfelder: {', '.join(missing)}")
    if not isinstance(data["id"], str) or not ID_PATTERN.fullmatch(data["id"]):
        raise validation_error(f"Ungueltige Widget-id: {data['id']!r}")
    if not isinstance(data["name"], str) or not 1 <= len(data["name"]) <= 80:
        raise validation_error("manifest.json: 'name' muss 1-80 Zeichen lang sein")
    if not isinstance(data["version"], str) or not SEMVER_PATTERN.fullmatch(data["version"]):
        raise validation_error("manifest.json: 'version' muss SemVer (x.y.z) sein")
    if data["category"] not in CATEGORIES:
        raise validation_error(f"manifest.json: unbekannte Kategorie {data['category']!r}")
    size = data["default_size"]
    if (
        not isinstance(size, dict)
        or not isinstance(size.get("w"), int)
        or not isinstance(size.get("h"), int)
        or not 1 <= size["w"] <= 12
        or size["h"] < 1
    ):
        raise validation_error("manifest.json: 'default_size' muss {w:1-12, h:>=1} sein")
    if "max_instances" in data and (
        not isinstance(data["max_instances"], int) or data["max_instances"] < 1
    ):
        raise validation_error("manifest.json: 'max_instances' muss >= 1 sein")
    permissions = data.get("permissions", [])
    if not isinstance(permissions, list) or any(p not in PERMISSIONS for p in permissions):
        raise validation_error("manifest.json: 'permissions' enthaelt unbekannte Werte")
    return data


# --- gemeinsame Helfer ------------------------------------------------------


def _widget_root(base: Path) -> Path:
    """Widget-Wurzel: `manifest.json` direkt oder in genau einem Unterordner."""
    if (base / "manifest.json").is_file():
        return base
    children = [entry for entry in base.iterdir() if entry.name != "__MACOSX"]
    dirs = [entry for entry in children if entry.is_dir()]
    if len(dirs) == 1 and (dirs[0] / "manifest.json").is_file():
        return dirs[0]
    raise validation_error(
        "Zip/Clone muss genau einen Widget-Ordner mit manifest.json enthalten"
    )


def _check_folder_id(root: Path, base: Path, manifest: dict) -> None:
    if root != base and root.name != manifest["id"]:
        raise validation_error(
            f"Ordner {root.name!r} passt nicht zur Widget-id {manifest['id']!r}"
        )


def _register(manifest: dict, root: Path) -> dict:
    """Verschiebt die Widget-Wurzel in den Install-Ordner und registriert sie."""
    widget_id = manifest["id"]
    dest = install_dir() / widget_id
    with db.get_conn() as conn:
        if conn.execute("SELECT 1 FROM widgets WHERE id = ?", (widget_id,)).fetchone() is not None:
            raise conflict(f"Widget {widget_id} ist bereits registriert")
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            shutil.rmtree(dest)
        shutil.move(str(root), str(dest))
        now = db.utc_now()
        try:
            conn.execute(
                "INSERT INTO widgets "
                "(id, name, version, author, description, category, icon, is_builtin, "
                " is_enabled, max_instances, manifest_json, config_json, installed_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 0, 1, ?, ?, '{}', ?, ?)",
                (
                    widget_id,
                    manifest["name"],
                    manifest["version"],
                    manifest.get("author", ""),
                    manifest.get("description", ""),
                    manifest["category"],
                    manifest.get("icon", ""),
                    manifest.get("max_instances", 1),
                    json.dumps(manifest),
                    now,
                    now,
                ),
            )
        except Exception:
            shutil.rmtree(dest, ignore_errors=True)
            raise
        row = conn.execute("SELECT * FROM widgets WHERE id = ?", (widget_id,)).fetchone()
    return dict(row)


# --- Zip --------------------------------------------------------------------


def _check_zip_members(archive: zipfile.ZipFile, max_uncompressed: int) -> None:
    """Weist `../`-/absolute Pfade, Symlinks und zu grosse Archive ab (Härtung)."""
    total = 0
    for info in archive.infolist():
        name = info.filename
        if name.startswith("/") or PurePosixPath(name).is_absolute():
            raise validation_error(f"Absoluter Pfad im Zip: {name}")
        if ".." in PurePosixPath(name).parts:
            raise validation_error(f"Pfad-Traversal im Zip: {name}")
        if "\\" in name:
            raise validation_error(f"Ungueltiger Pfad im Zip: {name}")
        mode = info.external_attr >> 16
        if mode & 0o170000 == 0o120000:
            raise validation_error(f"Symlink im Zip nicht erlaubt: {name}")
        if not info.is_dir():
            total += info.file_size
            if total > max_uncompressed:
                raise validation_error(
                    "Entpackte Groesse ueberschreitet WIDGETS_MAX_UNCOMPRESSED_BYTES"
                )


def install_zip(data: bytes) -> dict:
    """Installiert ein Widget aus einem Zip-Archiv (multipart-Feld `file`)."""
    cfg = _config()
    if len(data) > cfg["max_zip_bytes"]:
        raise validation_error("Archivgroesse ueberschreitet WIDGETS_MAX_ZIP_BYTES")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise validation_error("Ungueltiges Zip-Archiv") from exc

    tmp = Path(tempfile.mkdtemp(prefix="widget-zip-"))
    try:
        with archive:
            _check_zip_members(archive, cfg["max_uncompressed_bytes"])
            archive.extractall(tmp)
        root = _widget_root(tmp)
        manifest = _read_manifest(root)
        _check_folder_id(root, tmp, manifest)
        return _register(manifest, root)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --- Git --------------------------------------------------------------------


def _git_clone(url: str, ref: str, dest: Path) -> None:
    try:
        completed = subprocess.run(
            [
                "git",
                "clone",
                "--depth",
                "1",
                "--branch",
                ref,
                "--single-branch",
                url,
                str(dest),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise validation_error(f"Git-Clone fehlgeschlagen: {exc}") from exc
    if completed.returncode != 0:
        lines = [line for line in (completed.stderr or "").strip().splitlines() if line]
        detail = lines[-1] if lines else f"git exit {completed.returncode}"
        raise validation_error(f"Git-Clone fehlgeschlagen: {detail}")


def install_git(url: str, ref: str) -> dict:
    """Installiert ein Widget aus einem Git-Repo (shallow clone, dann wie Zip)."""
    if not isinstance(url, str) or not (url.startswith(GIT_URL_PREFIXES) or url.startswith("/")):
        raise validation_error(
            "Git-URL muss mit http(s)://, ssh://, git@, file:// oder / beginnen"
        )
    if not isinstance(ref, str) or not GIT_REF_PATTERN.fullmatch(ref):
        raise validation_error("Git-ref ist ungueltig")

    tmp = Path(tempfile.mkdtemp(prefix="widget-git-"))
    clone_dir = tmp / "repo"
    try:
        _git_clone(url, ref, clone_dir)
        root = _widget_root(clone_dir)
        manifest = _read_manifest(root)
        _check_folder_id(root, clone_dir, manifest)
        shutil.rmtree(root / ".git", ignore_errors=True)
        return _register(manifest, root)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --- Deinstallation ---------------------------------------------------------


def remove(widget_id: str) -> None:
    """Entfernt den Ordner `WIDGETS_INSTALL_DIR/<id>/` (best effort)."""
    shutil.rmtree(install_dir() / widget_id, ignore_errors=True)
