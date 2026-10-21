#!/usr/bin/env python3
"""
BosDaemon TUI – Terminal Management Dashboard v3
Aufruf: sudo bosdaemon-tui

Tabs:
  📋 Logs          – Live journalctl, farbig
  🚨 Alarme        – SQLite-Verlauf, Suche, CSV-Export, RIC-Statistik
  🔧 Werkzeuge     – Test-Alarm, USB-Scanner, Plugin-Check, Systeminfo
  ⚙ Einstellungen – Alle Config-Werte als Formular, Backup/Restore
"""

import subprocess, threading, sqlite3, yaml, csv, os, sys, copy, shutil, platform
from datetime import datetime
from pathlib import Path

try:
    from textual.app import App, ComposeResult
    from textual.widgets import (
        Header, Footer, TabbedContent, TabPane,
        DataTable, RichLog, Label, Button, Input,
        Static, Select, Switch, Rule, ProgressBar
    )
    from textual.containers import Horizontal, Vertical, ScrollableContainer
    from textual.binding import Binding
    from textual.screen import ModalScreen
except ImportError:
    print("Bitte ausführen:  pip3 install textual --break-system-packages")
    sys.exit(1)

CONFIG_PATH   = os.environ.get("BOSDAEMON_CONFIG", "/etc/bosdaemon/config.yaml")
SERVICE_NAME  = "bosdaemon"
BACKUP_SUFFIX = ".bak"

# ─────────────────────────────────────────────────────────────
# Hilfsfunktionen
# ─────────────────────────────────────────────────────────────
def load_config() -> dict:
    try:
        with open(CONFIG_PATH) as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}

def save_config(cfg: dict) -> str | None:
    try:
        # Backup vor dem Speichern
        if Path(CONFIG_PATH).exists():
            shutil.copy2(CONFIG_PATH, CONFIG_PATH + BACKUP_SUFFIX)
        with open(CONFIG_PATH, "w") as f:
            yaml.dump(cfg, f, allow_unicode=True,
                      default_flow_style=False, sort_keys=False)
        return None
    except Exception as e:
        return str(e)

def restore_backup() -> str | None:
    bak = CONFIG_PATH + BACKUP_SUFFIX
    if not Path(bak).exists():
        return "Kein Backup vorhanden"
    try:
        shutil.copy2(bak, CONFIG_PATH)
        return None
    except Exception as e:
        return str(e)

def get_db_path(config: dict) -> str:
    return config.get("database", {}).get("path", "/var/lib/bosdaemon/alarms.db")

def run_cmd(*args, timeout=10) -> tuple[int, str]:
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except Exception as e:
        return 1, str(e)

def service_status() -> str:
    code, _ = run_cmd("systemctl", "is-active", SERVICE_NAME)
    return "active" if code == 0 else "inactive"

def s(v) -> str:
    return str(v) if v is not None else ""

def b(v) -> bool:
    if isinstance(v, bool): return v
    if isinstance(v, str):  return v.lower() in ("true","yes","1","ja")
    return bool(v)

def scan_rtlsdr_sticks() -> list[dict]:
    """Gibt Liste der angeschlossenen RTL-SDR Sticks zurück."""
    sticks = []
    _, out = run_cmd("lsusb")
    for line in out.splitlines():
        if any(vid in line for vid in ["0bda:2838","0bda:2832","RTL2838","RTL283"]):
            sticks.append({"line": line, "index": len(sticks)})
    # Versuche auch rtl_test
    _, rt = run_cmd("rtl_test", "-t", timeout=3)
    for line in rt.splitlines():
        if "Found" in line and "device" in line.lower():
            pass  # Info bereits via lsusb
    return sticks

def get_system_info() -> dict:
    info = {}
    # CPU-Temperatur (Raspberry Pi)
    try:
        temp_file = Path("/sys/class/thermal/thermal_zone0/temp")
        if temp_file.exists():
            info["cpu_temp"] = f"{int(temp_file.read_text().strip()) / 1000:.1f} °C"
        else:
            _, out = run_cmd("vcgencmd", "measure_temp")
            info["cpu_temp"] = out.replace("temp=","").strip()
    except Exception:
        info["cpu_temp"] = "–"

    # CPU-Auslastung
    try:
        _, out = run_cmd("top","-bn1")
        for line in out.splitlines():
            if "Cpu" in line or "cpu" in line:
                info["cpu_usage"] = line.strip()
                break
        else:
            info["cpu_usage"] = "–"
    except Exception:
        info["cpu_usage"] = "–"

    # RAM
    try:
        _, out = run_cmd("free","-h")
        for line in out.splitlines():
            if line.startswith("Mem"):
                parts = line.split()
                info["ram_total"]  = parts[1] if len(parts)>1 else "–"
                info["ram_used"]   = parts[2] if len(parts)>2 else "–"
                info["ram_free"]   = parts[3] if len(parts)>3 else "–"
                break
    except Exception:
        info["ram_total"] = info["ram_used"] = info["ram_free"] = "–"

    # Disk
    try:
        usage = shutil.disk_usage("/")
        info["disk_total"] = f"{usage.total/1024**3:.1f} GB"
        info["disk_used"]  = f"{usage.used /1024**3:.1f} GB"
        info["disk_free"]  = f"{usage.free /1024**3:.1f} GB"
        info["disk_pct"]   = f"{usage.used/usage.total*100:.0f}%"
    except Exception:
        info["disk_total"] = info["disk_used"] = info["disk_free"] = info["disk_pct"] = "–"

    # Uptime
    try:
        _, out = run_cmd("uptime","-p")
        info["uptime"] = out.strip()
    except Exception:
        info["uptime"] = "–"

    # Hostname & OS
    info["hostname"] = platform.node()
    info["os"]       = platform.version()[:60]

    return info

def check_plugin(name: str, cfg: dict) -> tuple[bool, str]:
    """Schnell-Check ob Plugin erreichbar/konfiguriert ist."""
    import socket
    if not cfg.get("enabled", False):
        return None, "deaktiviert"

    if name == "pushover":
        token = cfg.get("token","")
        key   = cfg.get("user_key","")
        if not token or not key:
            return False, "Token oder Key fehlt"
        try:
            import urllib.request, json
            req = urllib.request.Request(
                "https://api.pushover.net/1/users/validate.json",
                data=f"token={token}&user={key}".encode(),
                method="POST")
            with urllib.request.urlopen(req, timeout=5) as r:
                data = json.loads(r.read())
                return data.get("status")==1, "OK" if data.get("status")==1 else data.get("errors","?")
        except Exception as e:
            return False, str(e)[:50]

    elif name == "telegram":
        token = cfg.get("bot_token","")
        if not token:
            return False, "Bot-Token fehlt"
        try:
            import urllib.request, json
            with urllib.request.urlopen(
                    f"https://api.telegram.org/bot{token}/getMe", timeout=5) as r:
                data = json.loads(r.read())
                return data.get("ok", False), data.get("result",{}).get("username","?")
        except Exception as e:
            return False, str(e)[:50]

    elif name == "discord":
        url = cfg.get("webhook_url","")
        if not url:
            return False, "Webhook-URL fehlt"
        try:
            import urllib.request
            with urllib.request.urlopen(url, timeout=5) as r:
                return r.status == 200, "OK"
        except Exception as e:
            return False, str(e)[:50]

    elif name == "email_notify":
        host = cfg.get("smtp_host","")
        port = cfg.get("smtp_port", 587)
        if not host:
            return False, "SMTP-Host fehlt"
        try:
            sock = socket.create_connection((host, int(port)), timeout=5)
            sock.close()
            return True, f"{host}:{port} erreichbar"
        except Exception as e:
            return False, str(e)[:50]

    elif name == "divera":
        key = cfg.get("api_key","")
        if not key:
            return False, "API-Key fehlt"
        try:
            import urllib.request, json
            with urllib.request.urlopen(
                    f"https://www.divera247.com/api/v2/pull/vehicle-status?accesskey={key}",
                    timeout=5) as r:
                return r.status == 200, "API erreichbar"
        except Exception as e:
            return False, str(e)[:50]

    elif name == "webhook":
        url = cfg.get("url","")
        if not url:
            return False, "URL fehlt"
        return True, "URL konfiguriert (kein Ping)"

    return True, "konfiguriert"


# ─────────────────────────────────────────────────────────────
# Modals
# ─────────────────────────────────────────────────────────────
class ConfirmScreen(ModalScreen):
    def __init__(self, msg: str):
        super().__init__()
        self._msg = msg
    def compose(self) -> ComposeResult:
        with Vertical(id="modal-box"):
            yield Label(self._msg, id="modal-msg")
            with Horizontal(id="modal-btns"):
                yield Button("✓ Ja",        id="yes", variant="error")
                yield Button("✗ Abbrechen", id="no",  variant="default")
    def on_button_pressed(self, e: Button.Pressed):
        self.dismiss(e.button.id == "yes")


# ─────────────────────────────────────────────────────────────
# Haupt-App
# ─────────────────────────────────────────────────────────────
class BosDaemonTUI(App):

    CSS = """
    Screen { background: $surface; }
    Header { background: $primary; height: 1; }
    Footer { background: $primary-darken-1; height: 1; }

    /* Status-Leiste */
    #statusbar {
        height: 3; background: $panel;
        border-bottom: solid $primary;
        padding: 0 2; align: left middle;
    }
    #status-label { width: 1fr; }
    .status-btn { min-width: 14; margin: 0 1; height: 1; }

    TabbedContent { height: 1fr; }

    /* Logs */
    RichLog {
        height: 1fr; border: solid $primary-darken-2;
        background: $surface-darken-1;
    }

    /* Tabellen */
    DataTable { height: 1fr; border: solid $primary-darken-2; }

    /* Action-Bar */
    .action-bar {
        height: 3; background: $panel; align: left middle;
        padding: 0 2; border-top: solid $primary-darken-2;
    }
    .action-btn { min-width: 16; margin: 0 1; height: 1; }

    /* Suche */
    #search-bar {
        height: 3; background: $panel-darken-1;
        padding: 0 2; align: left middle;
        border-bottom: solid $primary-darken-2;
    }
    #search-input { width: 40; margin-right: 2; }
    #search-info  { width: 1fr; color: $text-muted; }

    /* ── Einstellungen ── */
    #settings-outer { height: 1fr; layout: horizontal; }
    #settings-nav {
        width: 22; background: $panel;
        border-right: solid $primary-darken-2;
        padding: 1 0;
    }
    .nav-section { color: $text-muted; padding: 0 2; height: 1; margin-top: 1; }
    .nav-btn {
        width: 1fr; height: 2; margin: 0;
        border: none; background: transparent;
        color: $text-muted; text-align: left; padding: 0 2;
    }
    .nav-btn:hover { background: $primary-darken-2; color: $text; }
    .nav-btn.-active { background: $primary; color: $text; text-style: bold; }
    #settings-content { width: 1fr; height: 1fr; }
    .settings-panel {
        height: 1fr; display: none;
        padding: 1 2; overflow-y: auto;
    }
    .settings-panel.-visible { display: block; }
    .settings-title   { text-style: bold; color: $accent; margin-bottom: 1; }
    .settings-subtitle { color: $text-muted; margin-bottom: 1; }

    /* Formular */
    .field-row { height: 3; margin-bottom: 1; align: left middle; }
    .field-label  { width: 28; color: $text; padding-right: 2; }
    .field-input  { width: 1fr; }
    .field-select { width: 1fr; }
    .field-switch { width: 8; }

    /* Kanal-Karte */
    .channel-card {
        background: $panel; border: solid $primary-darken-2;
        padding: 1 2; margin-bottom: 1;
    }
    .channel-card-title { text-style: bold; color: $accent; margin-bottom: 1; }

    /* Speichern */
    #save-bar {
        height: 3; background: $panel-darken-1;
        border-top: solid $success;
        align: left middle; padding: 0 2;
    }
    .save-btn { min-width: 22; margin: 0 1; height: 1; }
    #save-status { color: $success; }

    /* ── Werkzeuge ── */
    #tools-outer { height: 1fr; layout: horizontal; }
    #tools-nav {
        width: 22; background: $panel;
        border-right: solid $primary-darken-2;
        padding: 1 0;
    }
    .tools-panel {
        width: 1fr; height: 1fr; display: none;
        padding: 1 2; overflow-y: auto;
    }
    .tools-panel.-visible { display: block; }
    .tool-title { text-style: bold; color: $accent; margin-bottom: 1; }
    .tool-subtitle { color: $text-muted; margin-bottom: 1; }

    /* Systeminfo */
    .sysinfo-row {
        height: 2; align: left middle;
        border-bottom: solid $primary-darken-2; padding: 0 1;
    }
    .sysinfo-key   { width: 24; color: $text-muted; }
    .sysinfo-value { width: 1fr; color: $text; text-style: bold; }

    /* Plugin-Check */
    .plugin-row {
        height: 2; align: left middle;
        border-bottom: solid $primary-darken-2; padding: 0 1;
    }
    .plugin-name   { width: 16; }
    .plugin-status { width: 1fr; }

    /* USB */
    .usb-row {
        height: 2; align: left middle;
        border-bottom: solid $primary-darken-2; padding: 0 1;
    }
    .usb-index { width: 10; color: $accent; }
    .usb-line  { width: 1fr; }

    /* Test-Alarm Ergebnis */
    .test-result {
        background: $panel; border: solid $primary-darken-2;
        padding: 1 2; margin-top: 1; height: 1fr;
        overflow-y: auto;
    }

    /* RIC-Stats */
    .stats-bar {
        height: 2; align: left middle;
        border-bottom: solid $surface-lighten-1; padding: 0 1;
    }
    .stats-ric   { width: 14; color: $accent; }
    .stats-label { width: 22; }
    .stats-count { width: 8; color: $text-muted; }
    .stats-bar-fill { color: $primary; }

    /* Modal */
    ModalScreen { align: center middle; }
    #modal-box {
        background: $panel; border: solid $primary;
        padding: 2 4; width: 55; min-height: 10;
    }
    #modal-msg  { text-align: center; margin-bottom: 2; }
    #modal-btns { align: center middle; height: 3; }
    """

    BINDINGS = [
        Binding("q",  "quit",    "Beenden", priority=True),
        Binding("r",  "refresh", "Refresh"),
        Binding("f5", "refresh", "Refresh", show=False),
    ]
    TITLE     = "BosDaemon TUI"
    SUB_TITLE = "POCSAG Management Dashboard"

    # Nav-Mapping: nav-id → panel-id
    NAV_SETTINGS = {
        "nav-channels":   "panel-channels",
        "nav-filter":     "panel-filter",
        "nav-pushover":   "panel-pushover",
        "nav-telegram":   "panel-telegram",
        "nav-email":      "panel-email",
        "nav-webhook":    "panel-webhook",
        "nav-discord":    "panel-discord",
        "nav-divera":     "panel-divera",
        "nav-ric-lookup": "panel-ric-lookup",
        "nav-blacklist":  "panel-blacklist",
        "nav-system":     "panel-system",
    }
    NAV_TOOLS = {
        "tool-test":    "panel-test",
        "tool-usb":     "panel-usb",
        "tool-plugins": "panel-plugins",
        "tool-sysinfo": "panel-sysinfo",
        "tool-stats":   "panel-stats",
        "tool-backup":  "panel-backup",
    }

    def __init__(self):
        super().__init__()
        self.config          = load_config()
        self._log_stop       = threading.Event()
        self._active_setting = "nav-channels"
        self._active_tool    = "tool-test"
        self._all_alarms     = []   # Cache für Suche

    # ────────────────────────────────────────────────────────
    # Layout
    # ────────────────────────────────────────────────────────
    def compose(self) -> ComposeResult:
        yield Header()

        with Horizontal(id="statusbar"):
            yield Label("", id="status-label")
            yield Button("▶ Start",   id="btn-start",   classes="status-btn", variant="success")
            yield Button("■ Stop",    id="btn-stop",     classes="status-btn", variant="error")
            yield Button("↺ Restart", id="btn-restart",  classes="status-btn", variant="warning")

        with TabbedContent():

            # ── Logs ──────────────────────────────────────
            with TabPane("📋 Logs", id="tab-logs"):
                yield RichLog(id="log-view", highlight=True, markup=True, wrap=True)

            # ── Alarme ────────────────────────────────────
            with TabPane("🚨 Alarme", id="tab-alarms"):
                with Horizontal(id="search-bar"):
                    yield Label("Suche: ", classes="field-label")
                    yield Input(placeholder="RIC, Einheit oder Meldungstext...",
                                id="search-input", classes="")
                    yield Button("🔍", id="btn-search", classes="action-btn")
                    yield Button("✕ Zurücksetzen", id="btn-search-reset", classes="action-btn")
                    yield Label("", id="search-info")
                yield DataTable(id="alarm-table", zebra_stripes=True, cursor_type="row")
                with Horizontal(classes="action-bar"):
                    yield Button("📥 CSV Export",      id="btn-csv",          classes="action-btn", variant="primary")
                    yield Button("🗑 Verlauf löschen", id="btn-clear-db",      classes="action-btn", variant="error")
                    yield Button("🔄 Neu laden",        id="btn-reload-alarms", classes="action-btn")
                    yield Label("", id="alarm-count")

            # ── Werkzeuge ─────────────────────────────────
            with TabPane("🔧 Werkzeuge", id="tab-tools"):
                with Horizontal(id="tools-outer"):
                    with Vertical(id="tools-nav"):
                        yield Label(" Diagnose", classes="nav-section")
                        yield Button("🚨 Test-Alarm",    id="tool-test",    classes="nav-btn -active")
                        yield Button("📡 USB-Scanner",   id="tool-usb",     classes="nav-btn")
                        yield Button("🔌 Plugin-Check",  id="tool-plugins", classes="nav-btn")
                        yield Rule()
                        yield Label(" System", classes="nav-section")
                        yield Button("🖥 Systeminfo",    id="tool-sysinfo", classes="nav-btn")
                        yield Button("📊 RIC-Statistik", id="tool-stats",   classes="nav-btn")
                        yield Button("💾 Backup/Restore",id="tool-backup",  classes="nav-btn")

                    # Tool-Panels
                    with Vertical(id="settings-content"):
                        yield self._make_panel_test()
                        yield self._make_panel_usb()
                        yield self._make_panel_plugins()
                        yield self._make_panel_sysinfo()
                        yield self._make_panel_stats()
                        yield self._make_panel_backup()

            # ── Einstellungen ─────────────────────────────
            with TabPane("⚙ Einstellungen", id="tab-settings"):
                with Horizontal(id="settings-outer"):
                    with Vertical(id="settings-nav"):
                        yield Label(" Empfang",  classes="nav-section")
                        yield Button("📡 Kanäle",     id="nav-channels",   classes="nav-btn -active")
                        yield Button("🔍 Filter",      id="nav-filter",     classes="nav-btn")
                        yield Rule()
                        yield Label(" Benachrichtigungen", classes="nav-section")
                        yield Button("📲 Pushover",   id="nav-pushover",   classes="nav-btn")
                        yield Button("✈ Telegram",    id="nav-telegram",   classes="nav-btn")
                        yield Button("📧 E-Mail",      id="nav-email",      classes="nav-btn")
                        yield Button("🌐 Webhook",     id="nav-webhook",    classes="nav-btn")
                        yield Button("💬 Discord",     id="nav-discord",    classes="nav-btn")
                        yield Button("🚒 Divera",      id="nav-divera",     classes="nav-btn")
                        yield Rule()
                        yield Label(" Verwaltung", classes="nav-section")
                        yield Button("📖 RIC-Lookup",  id="nav-ric-lookup", classes="nav-btn")
                        yield Button("🚫 Blacklist",   id="nav-blacklist",  classes="nav-btn")
                        yield Button("🖥 System",       id="nav-system",     classes="nav-btn")

                    with Vertical(id="settings-content"):
                        yield self._panel_channels()
                        yield self._panel_filter()
                        yield self._panel_pushover()
                        yield self._panel_telegram()
                        yield self._panel_email()
                        yield self._panel_webhook()
                        yield self._panel_discord()
                        yield self._panel_divera()
                        yield self._panel_ric_lookup()
                        yield self._panel_blacklist()
                        yield self._panel_system()

                        with Horizontal(id="save-bar"):
                            yield Button("💾 Speichern & Daemon neu starten",
                                         id="btn-save-restart", classes="save-btn", variant="success")
                            yield Button("💾 Nur speichern",
                                         id="btn-save-only",    classes="save-btn", variant="primary")
                            yield Button("↩ Backup wiederherstellen",
                                         id="btn-restore",      classes="save-btn", variant="warning")
                            yield Label("", id="save-status")

        yield Footer()

    # ────────────────────────────────────────────────────────
    # Werkzeug-Panels (Compose-Zeit, leer – werden on_mount gefüllt)
    # ────────────────────────────────────────────────────────
    def _make_panel_test(self) -> ScrollableContainer:
        return ScrollableContainer(id="panel-test", classes="tools-panel -visible")

    def _make_panel_usb(self) -> ScrollableContainer:
        return ScrollableContainer(id="panel-usb", classes="tools-panel")

    def _make_panel_plugins(self) -> ScrollableContainer:
        return ScrollableContainer(id="panel-plugins", classes="tools-panel")

    def _make_panel_sysinfo(self) -> ScrollableContainer:
        return ScrollableContainer(id="panel-sysinfo", classes="tools-panel")

    def _make_panel_stats(self) -> ScrollableContainer:
        return ScrollableContainer(id="panel-stats", classes="tools-panel")

    def _make_panel_backup(self) -> ScrollableContainer:
        return ScrollableContainer(id="panel-backup", classes="tools-panel")

    # ────────────────────────────────────────────────────────
    # on_mount
    # ────────────────────────────────────────────────────────
    def on_mount(self) -> None:
        self._update_status()
        self._load_alarm_table()
        self._start_log_stream()
        self._fill_all_settings()
        self._fill_tool_test()
        self._fill_tool_backup()

    # ────────────────────────────────────────────────────────
    # Werkzeuge befüllen
    # ────────────────────────────────────────────────────────
    def _fill_tool_test(self) -> None:
        panel = self.query_one("#panel-test", ScrollableContainer)
        panel.remove_children()
        panel.mount(Label("🚨 Test-Alarm senden", classes="tool-title"))
        panel.mount(Label("Sendet einen Test durch alle aktivierten Plugins.",
                          classes="tool-subtitle"))
        with Horizontal(classes="field-row") as h:
            h.mount(Button("▶ Test-Alarm jetzt senden", id="btn-run-test",
                           variant="error", classes="action-btn"))
        panel.mount(h)
        panel.mount(Static("", id="test-result", classes="test-result"))

    def _fill_tool_usb(self) -> None:
        panel = self.query_one("#panel-usb", ScrollableContainer)
        panel.remove_children()
        panel.mount(Label("📡 USB-Scanner – RTL-SDR Sticks", classes="tool-title"))
        panel.mount(Label("Zeigt angeschlossene RTL-SDR Empfänger mit Device-Index.",
                          classes="tool-subtitle"))
        sticks = scan_rtlsdr_sticks()
        if not sticks:
            panel.mount(Label("[yellow]Kein RTL-SDR Stick erkannt.[/yellow]\n"
                              "Stick anschließen, dann 🔄 drücken.", markup=True))
        else:
            panel.mount(Label(f"[green]{len(sticks)} Stick(s) gefunden:[/green]",
                              markup=True))
            for st in sticks:
                row = Horizontal(classes="usb-row")
                row.mount(Label(f"  Index {st['index']}", classes="usb-index"))
                row.mount(Label(st["line"],               classes="usb-line"))
                panel.mount(row)
            panel.mount(Label(
                "\n[dim]Hinweis: Device-Index 0 = erster Stick in lsusb-Reihenfolge.\n"
                "Bei mehreren Sticks: Sticks einzeln ein-/ausstecken um Reihenfolge zu prüfen.[/dim]",
                markup=True))

        with Horizontal(classes="field-row") as h:
            h.mount(Button("🔄 Neu scannen", id="btn-scan-usb", classes="action-btn"))
        panel.mount(h)

    def _fill_tool_plugins(self) -> None:
        panel = self.query_one("#panel-plugins", ScrollableContainer)
        panel.remove_children()
        panel.mount(Label("🔌 Plugin-Check", classes="tool-title"))
        panel.mount(Label("Prüft ob konfigurierte Benachrichtigungen erreichbar sind.",
                          classes="tool-subtitle"))

        notif = self.config.get("notifications", {})
        plugin_names = ["pushover","telegram","email_notify","webhook","discord","divera"]
        icons = {"pushover":"📲","telegram":"✈","email_notify":"📧",
                 "webhook":"🌐","discord":"💬","divera":"🚒"}

        for name in plugin_names:
            cfg = notif.get(name, {})
            enabled = b(cfg.get("enabled", False))
            if not enabled:
                row = Horizontal(classes="plugin-row")
                row.mount(Label(f"  {icons.get(name,'')} {name}", classes="plugin-name"))
                row.mount(Label("[dim]deaktiviert[/dim]", markup=True, classes="plugin-status"))
                panel.mount(row)
            else:
                row = Horizontal(classes="plugin-row")
                row.mount(Label(f"  {icons.get(name,'')} {name}", classes="plugin-name"))
                row.mount(Label("[yellow]⏳ wird geprüft...[/yellow]",
                                id=f"ps-{name}", markup=True, classes="plugin-status"))
                panel.mount(row)

        with Horizontal(classes="field-row") as h:
            h.mount(Button("▶ Alle prüfen", id="btn-check-plugins",
                           variant="primary", classes="action-btn"))
        panel.mount(h)

    def _fill_tool_sysinfo(self) -> None:
        panel = self.query_one("#panel-sysinfo", ScrollableContainer)
        panel.remove_children()
        panel.mount(Label("🖥 Systeminfo", classes="tool-title"))
        info = get_system_info()
        rows = [
            ("Hostname",       info.get("hostname","–")),
            ("Uptime",         info.get("uptime","–")),
            ("CPU Temperatur", info.get("cpu_temp","–")),
            ("RAM gesamt",     info.get("ram_total","–")),
            ("RAM genutzt",    info.get("ram_used","–")),
            ("RAM frei",       info.get("ram_free","–")),
            ("Disk gesamt",    info.get("disk_total","–")),
            ("Disk genutzt",   info.get("disk_used","–") + f" ({info.get('disk_pct','–')})"),
            ("Disk frei",      info.get("disk_free","–")),
        ]
        for key, val in rows:
            row = Horizontal(classes="sysinfo-row")
            row.mount(Label(f"  {key}", classes="sysinfo-key"))
            row.mount(Label(val,        classes="sysinfo-value"))
            panel.mount(row)

        # Dienst-Info
        panel.mount(Rule())
        panel.mount(Label("Dienst-Status", classes="tool-subtitle"))
        _, journal = run_cmd("journalctl","-u",SERVICE_NAME,"-n","5","--no-pager")
        panel.mount(Label(journal or "–"))

        with Horizontal(classes="field-row") as h:
            h.mount(Button("🔄 Aktualisieren", id="btn-refresh-sysinfo", classes="action-btn"))
        panel.mount(h)

    def _fill_tool_stats(self) -> None:
        panel = self.query_one("#panel-stats", ScrollableContainer)
        panel.remove_children()
        panel.mount(Label("📊 RIC-Statistik – Top 20", classes="tool-title"))
        panel.mount(Label("Häufigste Alarme aus der Datenbank.", classes="tool-subtitle"))

        db_path = get_db_path(self.config)
        if not Path(db_path).exists():
            panel.mount(Label("[yellow]Keine Datenbank gefunden.[/yellow]", markup=True))
            return

        try:
            conn = sqlite3.connect(db_path)
            rows = conn.execute(
                "SELECT ric, label, COUNT(*) as cnt FROM alarms "
                "GROUP BY ric ORDER BY cnt DESC LIMIT 20"
            ).fetchall()
            total = conn.execute("SELECT COUNT(*) FROM alarms").fetchone()[0]
            conn.close()

            panel.mount(Label(f"  Gesamt: {total} Alarme in DB", classes="tool-subtitle"))
            panel.mount(Rule())

            max_cnt = rows[0][2] if rows else 1
            for ric, label, cnt in rows:
                bar_len = int(cnt / max_cnt * 20)
                bar = "█" * bar_len + "░" * (20 - bar_len)
                row = Horizontal(classes="stats-bar")
                row.mount(Label(f"  {ric}",         classes="stats-ric"))
                row.mount(Label(label or "–",        classes="stats-label"))
                row.mount(Label(f"{cnt:4d}",         classes="stats-count"))
                row.mount(Label(f"[cyan]{bar}[/cyan]",
                                markup=True, classes="stats-bar-fill"))
                panel.mount(row)
        except Exception as e:
            panel.mount(Label(f"[red]Fehler: {e}[/red]", markup=True))

        with Horizontal(classes="field-row") as h:
            h.mount(Button("🔄 Aktualisieren", id="btn-refresh-stats", classes="action-btn"))
        panel.mount(h)

    def _fill_tool_backup(self) -> None:
        panel = self.query_one("#panel-backup", ScrollableContainer)
        panel.remove_children()
        panel.mount(Label("💾 Config Backup & Restore", classes="tool-title"))

        bak = CONFIG_PATH + BACKUP_SUFFIX
        bak_exists = Path(bak).exists()
        bak_time   = ""
        if bak_exists:
            t = datetime.fromtimestamp(Path(bak).stat().st_mtime)
            bak_time = t.strftime("%d.%m.%Y %H:%M:%S")

        info_rows = [
            ("Aktuelle Config:",  CONFIG_PATH),
            ("Backup-Datei:",     bak),
            ("Backup vorhanden:", f"[green]Ja ({bak_time})[/green]" if bak_exists
                                  else "[yellow]Nein[/yellow]"),
        ]
        for k, v in info_rows:
            row = Horizontal(classes="sysinfo-row")
            row.mount(Label(f"  {k}", classes="sysinfo-key"))
            row.mount(Label(v, markup=True, classes="sysinfo-value"))
            panel.mount(row)

        panel.mount(Rule())
        panel.mount(Label(
            "Beim Speichern wird automatisch ein Backup angelegt.\n"
            "Restore lädt das letzte Backup zurück.", classes="tool-subtitle"))

        with Horizontal(classes="field-row") as h:
            h.mount(Button("📥 Backup jetzt erstellen",    id="btn-manual-backup",
                           variant="primary", classes="action-btn"))
            h.mount(Button("↩ Backup wiederherstellen",    id="btn-restore2",
                           variant="warning", classes="action-btn",
                           disabled=not bak_exists))
        panel.mount(h)
        panel.mount(Label("", id="backup-status"))

    # ────────────────────────────────────────────────────────
    # Settings-Panels (Formular)
    # ────────────────────────────────────────────────────────
    def _panel_channels(self) -> ScrollableContainer:
        return ScrollableContainer(id="panel-channels", classes="settings-panel -visible")

    def _panel_filter(self) -> ScrollableContainer:
        return ScrollableContainer(id="panel-filter",   classes="settings-panel")

    def _panel_pushover(self) -> ScrollableContainer:
        return ScrollableContainer(id="panel-pushover", classes="settings-panel")

    def _panel_telegram(self) -> ScrollableContainer:
        return ScrollableContainer(id="panel-telegram", classes="settings-panel")

    def _panel_email(self) -> ScrollableContainer:
        return ScrollableContainer(id="panel-email",    classes="settings-panel")

    def _panel_webhook(self) -> ScrollableContainer:
        return ScrollableContainer(id="panel-webhook",  classes="settings-panel")

    def _panel_discord(self) -> ScrollableContainer:
        return ScrollableContainer(id="panel-discord",  classes="settings-panel")

    def _panel_divera(self) -> ScrollableContainer:
        return ScrollableContainer(id="panel-divera",   classes="settings-panel")

    def _panel_ric_lookup(self) -> ScrollableContainer:
        return ScrollableContainer(id="panel-ric-lookup", classes="settings-panel")

    def _panel_blacklist(self) -> ScrollableContainer:
        return ScrollableContainer(id="panel-blacklist",  classes="settings-panel")

    def _panel_system(self) -> ScrollableContainer:
        return ScrollableContainer(id="panel-system",     classes="settings-panel")

    def _fill_all_settings(self) -> None:
        self._fill_channels()
        self._fill_filter()
        self._fill_pushover()
        self._fill_telegram()
        self._fill_email()
        self._fill_webhook()
        self._fill_discord()
        self._fill_divera()
        self._fill_ric_lookup()
        self._fill_blacklist()
        self._fill_system()

    # ── Kanäle ──────────────────────────────────────────────
    def _fill_channels(self) -> None:
        panel = self.query_one("#panel-channels", ScrollableContainer)
        panel.remove_children()
        channels = self.config.get("channels", [])
        if not channels:
            sdr = self.config.get("sdr", {})
            channels = [{"name": "Standard", **sdr}] if sdr else [{}]

        panel.mount(Label("📡 Empfangskanäle", classes="settings-title"))
        panel.mount(Label("Ein Eintrag = ein RTL-SDR Stick.", classes="settings-subtitle"))

        for i, ch in enumerate(channels):
            card = Vertical(classes="channel-card")
            card.mount(Label(f"Kanal {i+1}: {ch.get('name','')}", classes="channel-card-title"))
            for lbl, fid, val, ph in [
                ("Name",         f"ch{i}_name", s(ch.get("name",  f"Kanal {i+1}")), "z.B. FW Rottweil"),
                ("Frequenz",     f"ch{i}_freq", s(ch.get("frequency","172.920M")),  "z.B. 172.920M"),
                ("Gain",         f"ch{i}_gain", s(ch.get("gain",  100)),            "0–100"),
                ("PPM-Korrektur",f"ch{i}_ppm",  s(ch.get("ppm",   0)),              "0"),
                ("Device-Index", f"ch{i}_dev",  s(ch.get("device_index", i)),       "0,1,2..."),
                ("Sample-Rate",  f"ch{i}_sr",   s(ch.get("sample_rate","22050")),   "22050"),
            ]:
                row = Horizontal(classes="field-row")
                row.mount(Label(lbl, classes="field-label"))
                row.mount(Input(value=val, id=fid, placeholder=ph, classes="field-input"))
                card.mount(row)
            panel.mount(card)

        with Horizontal(classes="field-row") as h:
            h.mount(Button("+ Kanal hinzufügen",  id="btn-add-channel", variant="success", classes="action-btn"))
            h.mount(Button("− Letzten entfernen", id="btn-del-channel", variant="error",   classes="action-btn"))
        panel.mount(h)

    # ── Filter ──────────────────────────────────────────────
    def _fill_filter(self) -> None:
        panel = self.query_one("#panel-filter", ScrollableContainer)
        panel.remove_children()
        dup = self.config.get("duplicate_filter", {})
        rex = self.config.get("regex_filter",     {})
        flt = self.config.get("filter",           {})

        panel.mount(Label("🔍 Filter", classes="settings-title"))
        panel.mount(Label("Doppelalarm-Filter", classes="settings-subtitle"))
        for lbl, wid, widget in [
            ("Aktiviert",            "dup_enabled",
             Switch(value=b(dup.get("enabled",True)), id="dup_enabled", classes="field-switch")),
            ("Zeitfenster (Sek.)",   None, None),
        ]:
            row = Horizontal(classes="field-row")
            row.mount(Label(lbl, classes="field-label"))
            row.mount(widget if widget else Input(
                value=s(dup.get("seconds",60)), id="dup_seconds",
                placeholder="60", classes="field-input"))
            panel.mount(row)
        row = Horizontal(classes="field-row")
        row.mount(Label("Zeitfenster (Sek.)", classes="field-label"))
        row.mount(Input(value=s(dup.get("seconds",60)), id="dup_seconds",
                        placeholder="60", classes="field-input"))
        panel.mount(row)

        panel.mount(Rule())
        panel.mount(Label("Leeralarm-Filter", classes="settings-subtitle"))
        row = Horizontal(classes="field-row")
        row.mount(Label("Mindestlänge (Zeichen)", classes="field-label"))
        row.mount(Input(value=s(flt.get("min_message_length",0)), id="flt_minlen",
                        placeholder="0 = alle", classes="field-input"))
        panel.mount(row)

        panel.mount(Rule())
        panel.mount(Label("RegEx-Filter", classes="settings-subtitle"))
        r_en = Horizontal(classes="field-row")
        r_en.mount(Label("Aktiviert", classes="field-label"))
        r_en.mount(Switch(value=b(rex.get("enabled",False)), id="rex_enabled", classes="field-switch"))
        panel.mount(r_en)
        r_mo = Horizontal(classes="field-row")
        r_mo.mount(Label("Modus", classes="field-label"))
        r_mo.mount(Select(
            [("Blacklist – passende Alarme BLOCKIEREN","blacklist"),
             ("Whitelist – nur passende Alarme SENDEN","whitelist")],
            value=rex.get("mode","blacklist"), id="rex_mode", classes="field-select"))
        panel.mount(r_mo)
        patterns = ", ".join(rex.get("patterns", []))
        r_pa = Horizontal(classes="field-row")
        r_pa.mount(Label("Muster (kommagetrennt)", classes="field-label"))
        r_pa.mount(Input(value=patterns, id="rex_patterns",
                         placeholder="TEST, ÜBUNG, Probe", classes="field-input"))
        panel.mount(r_pa)

    # ── Pushover ─────────────────────────────────────────────
    def _fill_pushover(self) -> None:
        panel = self.query_one("#panel-pushover", ScrollableContainer)
        panel.remove_children()
        cfg = self.config.get("notifications", {}).get("pushover", {})
        panel.mount(Label("📲 Pushover", classes="settings-title"))
        r = Horizontal(classes="field-row")
        r.mount(Label("Aktiviert", classes="field-label"))
        r.mount(Switch(value=b(cfg.get("enabled",False)), id="push_enabled", classes="field-switch"))
        panel.mount(r)
        for lbl, val, fid, ph in [
            ("App-Token",     cfg.get("token",""),                "push_token",   "aToken..."),
            ("User/Group-Key",cfg.get("user_key",""),             "push_key",     "uXXX..."),
            ("Sound",         cfg.get("sound","siren"),           "push_sound",   "siren"),
            ("Titel-Prefix",  cfg.get("title_prefix","🚨 Alarm"), "push_title",   "🚨 Alarm"),
            ("Mindestlänge",  s(cfg.get("min_message_length",0)), "push_minlen",  "0"),
        ]:
            row = Horizontal(classes="field-row")
            row.mount(Label(lbl, classes="field-label"))
            row.mount(Input(value=val, id=fid, placeholder=ph, classes="field-input"))
            panel.mount(row)
        r2 = Horizontal(classes="field-row")
        r2.mount(Label("Priorität", classes="field-label"))
        r2.mount(Select(
            [("Normal","normal"),("Hoch","high"),("Niedrig","low"),("Notfall","emergency")],
            value=cfg.get("priority","normal"), id="push_priority", classes="field-select"))
        panel.mount(r2)

    # ── Telegram ─────────────────────────────────────────────
    def _fill_telegram(self) -> None:
        panel = self.query_one("#panel-telegram", ScrollableContainer)
        panel.remove_children()
        cfg = self.config.get("notifications", {}).get("telegram", {})
        panel.mount(Label("✈ Telegram", classes="settings-title"))
        panel.mount(Label("Bot erstellen: @BotFather → /newbot", classes="settings-subtitle"))
        r = Horizontal(classes="field-row")
        r.mount(Label("Aktiviert", classes="field-label"))
        r.mount(Switch(value=b(cfg.get("enabled",False)), id="tg_enabled", classes="field-switch"))
        panel.mount(r)
        for lbl, val, fid, ph in [
            ("Bot-Token",    cfg.get("bot_token",""), "tg_token",  "123456:ABC-..."),
            ("Chat-ID",      cfg.get("chat_id",""),   "tg_chatid", "-100123456789"),
            ("Mindestlänge", s(cfg.get("min_message_length",0)), "tg_minlen", "0"),
        ]:
            row = Horizontal(classes="field-row")
            row.mount(Label(lbl, classes="field-label"))
            row.mount(Input(value=val, id=fid, placeholder=ph, classes="field-input"))
            panel.mount(row)

    # ── E-Mail ───────────────────────────────────────────────
    def _fill_email(self) -> None:
        panel = self.query_one("#panel-email", ScrollableContainer)
        panel.remove_children()
        cfg = self.config.get("notifications", {}).get("email_notify", {})
        panel.mount(Label("📧 E-Mail (SMTP)", classes="settings-title"))
        r = Horizontal(classes="field-row")
        r.mount(Label("Aktiviert", classes="field-label"))
        r.mount(Switch(value=b(cfg.get("enabled",False)), id="mail_enabled", classes="field-switch"))
        panel.mount(r)
        for lbl, val, fid, ph, pw in [
            ("SMTP-Server",  cfg.get("smtp_host",""),         "mail_host",  "smtp.gmail.com", False),
            ("SMTP-Port",    s(cfg.get("smtp_port",587)),     "mail_port",  "587",            False),
            ("Benutzername", cfg.get("smtp_user",""),         "mail_user",  "user@...",       False),
            ("Passwort",     cfg.get("smtp_pass",""),         "mail_pass",  "",               True),
            ("Absender",     cfg.get("from",""),              "mail_from",  "alarm@...",      False),
            ("Empfänger",    cfg.get("to",""),                "mail_to",    "chef@...",       False),
        ]:
            row = Horizontal(classes="field-row")
            row.mount(Label(lbl, classes="field-label"))
            row.mount(Input(value=val, id=fid, placeholder=ph,
                            password=pw, classes="field-input"))
            panel.mount(row)
        r2 = Horizontal(classes="field-row")
        r2.mount(Label("TLS/STARTTLS", classes="field-label"))
        r2.mount(Switch(value=b(cfg.get("use_tls",True)), id="mail_tls", classes="field-switch"))
        panel.mount(r2)

    # ── Webhook ──────────────────────────────────────────────
    def _fill_webhook(self) -> None:
        panel = self.query_one("#panel-webhook", ScrollableContainer)
        panel.remove_children()
        cfg = self.config.get("notifications", {}).get("webhook", {})
        panel.mount(Label("🌐 Webhook", classes="settings-title"))
        r = Horizontal(classes="field-row")
        r.mount(Label("Aktiviert", classes="field-label"))
        r.mount(Switch(value=b(cfg.get("enabled",False)), id="wh_enabled", classes="field-switch"))
        panel.mount(r)
        for lbl, val, fid, ph in [
            ("URL",    cfg.get("url",""),    "wh_url",    "https://..."),
            ("Secret", cfg.get("secret",""), "wh_secret", "optionaler Key"),
        ]:
            row = Horizontal(classes="field-row")
            row.mount(Label(lbl, classes="field-label"))
            row.mount(Input(value=val, id=fid, placeholder=ph, classes="field-input"))
            panel.mount(row)
        r2 = Horizontal(classes="field-row")
        r2.mount(Label("Methode", classes="field-label"))
        r2.mount(Select([("POST","POST"),("GET","GET")],
                        value=cfg.get("method","POST"), id="wh_method", classes="field-select"))
        panel.mount(r2)

    # ── Discord ──────────────────────────────────────────────
    def _fill_discord(self) -> None:
        panel = self.query_one("#panel-discord", ScrollableContainer)
        panel.remove_children()
        cfg = self.config.get("notifications", {}).get("discord", {})
        panel.mount(Label("💬 Discord", classes="settings-title"))
        r = Horizontal(classes="field-row")
        r.mount(Label("Aktiviert", classes="field-label"))
        r.mount(Switch(value=b(cfg.get("enabled",False)), id="dc_enabled", classes="field-switch"))
        panel.mount(r)
        for lbl, val, fid, ph in [
            ("Webhook-URL", cfg.get("webhook_url",""),     "dc_url",  "https://discord.com/api/webhooks/..."),
            ("Bot-Name",    cfg.get("username","BosDaemon"),"dc_name","BosDaemon"),
        ]:
            row = Horizontal(classes="field-row")
            row.mount(Label(lbl, classes="field-label"))
            row.mount(Input(value=val, id=fid, placeholder=ph, classes="field-input"))
            panel.mount(row)

    # ── Divera ───────────────────────────────────────────────
    def _fill_divera(self) -> None:
        panel = self.query_one("#panel-divera", ScrollableContainer)
        panel.remove_children()
        cfg = self.config.get("notifications", {}).get("divera", {})
        panel.mount(Label("🚒 Divera 24/7", classes="settings-title"))
        panel.mount(Label("⚠ Löst echte Alarmierungen aus!", classes="settings-subtitle"))
        r = Horizontal(classes="field-row")
        r.mount(Label("Aktiviert", classes="field-label"))
        r.mount(Switch(value=b(cfg.get("enabled",False)), id="div_enabled", classes="field-switch"))
        panel.mount(r)
        for lbl, val, fid, ph in [
            ("API-Key",     cfg.get("api_key",""),           "div_key",   "API-Key aus Divera"),
            ("Alarm-Titel", cfg.get("alarm_title","POCSAG Alarm"), "div_title","POCSAG Alarm"),
        ]:
            row = Horizontal(classes="field-row")
            row.mount(Label(lbl, classes="field-label"))
            row.mount(Input(value=val, id=fid, placeholder=ph, classes="field-input"))
            panel.mount(row)
        for lbl, sw_id, val in [
            ("Push senden",    "div_push",  b(cfg.get("send_push",  True))),
            ("SMS senden",     "div_sms",   b(cfg.get("send_sms",  False))),
            ("Anruf auslösen", "div_call",  b(cfg.get("send_call", False))),
            ("E-Mail senden",  "div_mail",  b(cfg.get("send_mail", False))),
            ("Hohe Priorität", "div_prio",  b(cfg.get("priority",  False))),
        ]:
            row = Horizontal(classes="field-row")
            row.mount(Label(lbl, classes="field-label"))
            row.mount(Switch(value=val, id=sw_id, classes="field-switch"))
            panel.mount(row)

    # ── RIC-Lookup ───────────────────────────────────────────
    def _fill_ric_lookup(self) -> None:
        panel = self.query_one("#panel-ric-lookup", ScrollableContainer)
        panel.remove_children()
        entries = self.config.get("ric_lookup", [])
        panel.mount(Label("📖 RIC-Lookup", classes="settings-title"))
        panel.mount(Label("RIC → Einheitenname + Gruppe", classes="settings-subtitle"))
        for i, e in enumerate(entries):
            row = Horizontal(classes="field-row")
            row.mount(Input(value=e.get("ric",""),     id=f"rl{i}_ric",   placeholder="RIC",   classes="field-input"))
            row.mount(Input(value=e.get("label",""),   id=f"rl{i}_label", placeholder="Name",  classes="field-input"))
            row.mount(Input(value=e.get("channel",""), id=f"rl{i}_ch",    placeholder="Gruppe",classes="field-input"))
            row.mount(Button("✕", id=f"btn-del-rl-{i}", classes="list-del", variant="error"))
            panel.mount(row)
        with Horizontal(classes="field-row") as h:
            h.mount(Button("+ Eintrag", id="btn-add-rl", variant="success", classes="action-btn"))
        panel.mount(h)

    # ── Blacklist ────────────────────────────────────────────
    def _fill_blacklist(self) -> None:
        panel = self.query_one("#panel-blacklist", ScrollableContainer)
        panel.remove_children()
        rics = self.config.get("blacklist", {}).get("rics", [])
        panel.mount(Label("🚫 Blacklist", classes="settings-title"))
        panel.mount(Label("Alarme dieser RICs werden ignoriert.", classes="settings-subtitle"))
        for i, ric in enumerate(rics):
            row = Horizontal(classes="field-row")
            row.mount(Input(value=s(ric), id=f"bl{i}_ric",
                            placeholder="RIC-Nummer", classes="field-input"))
            row.mount(Button("✕", id=f"btn-del-bl-{i}", classes="list-del", variant="error"))
            panel.mount(row)
        with Horizontal(classes="field-row") as h:
            h.mount(Button("+ RIC", id="btn-add-bl", variant="success", classes="action-btn"))
        panel.mount(h)

    # ── System ───────────────────────────────────────────────
    def _fill_system(self) -> None:
        panel = self.query_one("#panel-system", ScrollableContainer)
        panel.remove_children()
        sw  = self.config.get("silence_watchdog", {})
        db  = self.config.get("database",         {})
        log = self.config.get("logging",          {})
        usb = self.config.get("usb_watchdog",     {})

        panel.mount(Label("🖥 System", classes="settings-title"))

        panel.mount(Label("Stille-Alarm", classes="settings-subtitle"))
        for widget, wid in [
            (Switch(value=b(sw.get("enabled",False)), id="sw_enabled", classes="field-switch"), "sw_enabled"),
        ]:
            row = Horizontal(classes="field-row")
            row.mount(Label("Aktiviert", classes="field-label"))
            row.mount(widget)
            panel.mount(row)
        row = Horizontal(classes="field-row")
        row.mount(Label("Minuten ohne Signal", classes="field-label"))
        row.mount(Input(value=s(sw.get("minutes",15)), id="sw_minutes",
                        placeholder="15", classes="field-input"))
        panel.mount(row)
        panel.mount(Rule())

        panel.mount(Label("USB-Watchdog", classes="settings-subtitle"))
        r2 = Horizontal(classes="field-row")
        r2.mount(Label("Aktiviert", classes="field-label"))
        r2.mount(Switch(value=b(usb.get("enabled",True)), id="usb_enabled", classes="field-switch"))
        panel.mount(r2)
        r3 = Horizontal(classes="field-row")
        r3.mount(Label("Prüf-Intervall (Sek.)", classes="field-label"))
        r3.mount(Input(value=s(usb.get("check_interval",30)), id="usb_interval",
                       placeholder="30", classes="field-input"))
        panel.mount(r3)
        panel.mount(Rule())

        panel.mount(Label("Datenbank", classes="settings-subtitle"))
        r4 = Horizontal(classes="field-row")
        r4.mount(Label("Aktiviert", classes="field-label"))
        r4.mount(Switch(value=b(db.get("enabled",False)), id="db_enabled", classes="field-switch"))
        panel.mount(r4)
        r5 = Horizontal(classes="field-row")
        r5.mount(Label("Pfad", classes="field-label"))
        r5.mount(Input(value=db.get("path","/var/lib/bosdaemon/alarms.db"),
                       id="db_path", classes="field-input"))
        panel.mount(r5)
        panel.mount(Rule())

        panel.mount(Label("Logging", classes="settings-subtitle"))
        r6 = Horizontal(classes="field-row")
        r6.mount(Label("Log-Level", classes="field-label"))
        r6.mount(Select(
            [("DEBUG","DEBUG"),("INFO","INFO"),("WARNING","WARNING"),("ERROR","ERROR")],
            value=log.get("level","INFO"), id="log_level", classes="field-select"))
        panel.mount(r6)
        r7 = Horizontal(classes="field-row")
        r7.mount(Label("Log-Datei", classes="field-label"))
        r7.mount(Input(value=log.get("file","/var/log/bosdaemon/bosdaemon.log"),
                       id="log_file", classes="field-input"))
        panel.mount(r7)

    # ────────────────────────────────────────────────────────
    # Config einlesen & speichern
    # ────────────────────────────────────────────────────────
    def _rv(self, wid: str, default="") -> str:
        try: return self.query_one(f"#{wid}", Input).value
        except: return default

    def _rs(self, wid: str, default=False) -> bool:
        try: return self.query_one(f"#{wid}", Switch).value
        except: return default

    def _rsel(self, wid: str, default="") -> str:
        try:
            v = self.query_one(f"#{wid}", Select).value
            return str(v) if v is not None else default
        except: return default

    def _collect_config(self) -> dict:
        cfg = copy.deepcopy(self.config)
        channels = cfg.get("channels", [{}])
        new_ch = []
        for i in range(len(channels)):
            try: self.query_one(f"#ch{i}_name", Input)
            except: break
            new_ch.append({
                "name":          self._rv(f"ch{i}_name", f"Kanal {i+1}"),
                "frequency":     self._rv(f"ch{i}_freq","172.920M"),
                "gain":          int(self._rv(f"ch{i}_gain","100") or 100),
                "ppm":           int(self._rv(f"ch{i}_ppm","0") or 0),
                "device_index":  int(self._rv(f"ch{i}_dev",str(i)) or i),
                "sample_rate":   int(self._rv(f"ch{i}_sr","22050") or 22050),
                "rtl_fm_path":   channels[i].get("rtl_fm_path","rtl_fm"),
                "multimon_path": channels[i].get("multimon_path","multimon-ng"),
            })
        cfg["channels"] = new_ch
        cfg["duplicate_filter"] = {"enabled":self._rs("dup_enabled",True),
                                    "seconds":int(self._rv("dup_seconds","60") or 60)}
        cfg["filter"] = {"min_message_length":int(self._rv("flt_minlen","0") or 0)}
        cfg["regex_filter"] = {
            "enabled": self._rs("rex_enabled",False),
            "mode":    self._rsel("rex_mode","blacklist"),
            "patterns":[p.strip() for p in self._rv("rex_patterns","").split(",") if p.strip()],
        }
        n = cfg.setdefault("notifications",{})
        n["pushover"] = {"enabled":self._rs("push_enabled",False),"token":self._rv("push_token"),
                          "user_key":self._rv("push_key"),"priority":self._rsel("push_priority","normal"),
                          "sound":self._rv("push_sound","siren"),"title_prefix":self._rv("push_title","🚨 Alarm"),
                          "min_message_length":int(self._rv("push_minlen","0") or 0)}
        n["telegram"] = {"enabled":self._rs("tg_enabled",False),"bot_token":self._rv("tg_token"),
                          "chat_id":self._rv("tg_chatid"),"min_message_length":int(self._rv("tg_minlen","0") or 0)}
        n["email_notify"] = {"enabled":self._rs("mail_enabled",False),"smtp_host":self._rv("mail_host"),
                              "smtp_port":int(self._rv("mail_port","587") or 587),"smtp_user":self._rv("mail_user"),
                              "smtp_pass":self._rv("mail_pass"),"use_tls":self._rs("mail_tls",True),
                              "from":self._rv("mail_from"),"to":self._rv("mail_to")}
        n["webhook"] = {"enabled":self._rs("wh_enabled",False),"url":self._rv("wh_url"),
                         "method":self._rsel("wh_method","POST"),"secret":self._rv("wh_secret")}
        n["discord"] = {"enabled":self._rs("dc_enabled",False),"webhook_url":self._rv("dc_url"),
                         "username":self._rv("dc_name","BosDaemon")}
        n["divera"]  = {"enabled":self._rs("div_enabled",False),"api_key":self._rv("div_key"),
                         "alarm_title":self._rv("div_title","POCSAG Alarm"),
                         "send_push":self._rs("div_push",True),"send_sms":self._rs("div_sms",False),
                         "send_call":self._rs("div_call",False),"send_mail":self._rs("div_mail",False),
                         "priority":self._rs("div_prio",False),
                         "ric_to_group":cfg.get("notifications",{}).get("divera",{}).get("ric_to_group",{})}
        rl_entries = cfg.get("ric_lookup",[])
        cfg["ric_lookup"] = [
            {"ric":self._rv(f"rl{i}_ric"),"label":self._rv(f"rl{i}_label"),"channel":self._rv(f"rl{i}_ch")}
            for i in range(len(rl_entries)) if self._rv(f"rl{i}_ric")
        ]
        bl = cfg.get("blacklist",{}).get("rics",[])
        cfg.setdefault("blacklist",{})["rics"] = [
            self._rv(f"bl{i}_ric") for i in range(len(bl)) if self._rv(f"bl{i}_ric")
        ]
        cfg["silence_watchdog"] = {"enabled":self._rs("sw_enabled",False),
                                    "minutes":int(self._rv("sw_minutes","15") or 15)}
        cfg["usb_watchdog"] = {"enabled":self._rs("usb_enabled",True),
                                "check_interval":int(self._rv("usb_interval","30") or 30),
                                "device_pattern":cfg.get("usb_watchdog",{}).get("device_pattern","0bda:2838")}
        cfg["database"] = {"enabled":self._rs("db_enabled",False),
                            "path":self._rv("db_path","/var/lib/bosdaemon/alarms.db")}
        cfg["logging"]  = {"level":self._rsel("log_level","INFO"),
                            "file":self._rv("log_file","/var/log/bosdaemon/bosdaemon.log")}
        return cfg

    def _do_save(self, restart: bool = False) -> None:
        lbl = self.query_one("#save-status", Label)
        err = save_config(self._collect_config())
        if err:
            lbl.update(f"[red]✗ {err}[/red]")
            return
        self.config = load_config()
        if restart:
            code, _ = run_cmd("systemctl","restart",SERVICE_NAME)
            msg = "✓ Gespeichert & Daemon neugestartet" if code==0 else "✓ Gespeichert (Neustart fehlgeschlagen)"
        else:
            msg = "✓ Gespeichert (config.yaml.bak angelegt)"
        lbl.update(f"[green]{msg}[/green]")
        self.notify(msg, severity="information")
        self._update_status()

    # ────────────────────────────────────────────────────────
    # Navigation
    # ────────────────────────────────────────────────────────
    def _switch_panel(self, nav_map: dict, active_attr: str, nav_id: str) -> None:
        old_nav   = getattr(self, active_attr)
        old_panel = nav_map.get(old_nav)
        if old_panel:
            try: self.query_one(f"#{old_panel}").remove_class("-visible")
            except: pass
        try: self.query_one(f"#{old_nav}").remove_class("-active")
        except: pass
        setattr(self, active_attr, nav_id)
        new_panel = nav_map.get(nav_id)
        if new_panel:
            try: self.query_one(f"#{new_panel}").add_class("-visible")
            except: pass
        try: self.query_one(f"#{nav_id}").add_class("-active")
        except: pass

    # ────────────────────────────────────────────────────────
    # Button-Handler
    # ────────────────────────────────────────────────────────
    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""

        if bid in self.NAV_SETTINGS:
            self._switch_panel(self.NAV_SETTINGS, "_active_setting", bid)
            return
        if bid in self.NAV_TOOLS:
            self._switch_panel(self.NAV_TOOLS, "_active_tool", bid)
            # Panels bei Aufruf befüllen (lazy)
            if bid == "tool-usb":     self._fill_tool_usb()
            elif bid == "tool-plugins": self._fill_tool_plugins()
            elif bid == "tool-sysinfo": self._fill_tool_sysinfo()
            elif bid == "tool-stats":   self._fill_tool_stats()
            elif bid == "tool-backup":  self._fill_tool_backup()
            return

        # ── Service ──
        if   bid == "btn-start":   self._service_action("start")
        elif bid == "btn-stop":
            self.push_screen(ConfirmScreen("BosDaemon wirklich stoppen?"),
                             lambda ok: ok and self._service_action("stop"))
        elif bid == "btn-restart": self._service_action("restart")

        # ── Alarme ──
        elif bid == "btn-csv":           self._export_csv()
        elif bid == "btn-reload-alarms": self._load_alarm_table()
        elif bid == "btn-search":        self._do_search()
        elif bid == "btn-search-reset":  self._reset_search()
        elif bid == "btn-clear-db":
            self.push_screen(
                ConfirmScreen("Alarm-Verlauf wirklich löschen?"),
                self._on_confirm_clear_db)

        # ── Speichern ──
        elif bid == "btn-save-restart": self._do_save(restart=True)
        elif bid == "btn-save-only":    self._do_save(restart=False)
        elif bid in ("btn-restore","btn-restore2"):
            self.push_screen(
                ConfirmScreen("Backup wiederherstellen?\n(aktuelle Config wird überschrieben)"),
                self._on_confirm_restore)

        # ── Kanäle +/- ──
        elif bid == "btn-add-channel":
            chs = self.config.get("channels",[{}])
            chs.append({"name":f"Kanal {len(chs)+1}","frequency":"172.920M","gain":100,"ppm":0})
            self.config["channels"] = chs
            self._fill_channels()
        elif bid == "btn-del-channel":
            chs = self.config.get("channels",[{}])
            if len(chs) > 1:
                chs.pop()
                self.config["channels"] = chs
                self._fill_channels()

        # ── RIC-Lookup +/- ──
        elif bid == "btn-add-rl":
            rl = self.config.get("ric_lookup",[])
            rl.append({"ric":"","label":"","channel":""})
            self.config["ric_lookup"] = rl
            self._fill_ric_lookup()
        elif bid.startswith("btn-del-rl-"):
            idx = int(bid.split("-")[-1])
            rl = self.config.get("ric_lookup",[])
            if 0 <= idx < len(rl): rl.pop(idx)
            self.config["ric_lookup"] = rl
            self._fill_ric_lookup()

        # ── Blacklist +/- ──
        elif bid == "btn-add-bl":
            bl = self.config.get("blacklist",{}).get("rics",[])
            bl.append("")
            self.config.setdefault("blacklist",{})["rics"] = bl
            self._fill_blacklist()
        elif bid.startswith("btn-del-bl-"):
            idx = int(bid.split("-")[-1])
            bl = self.config.get("blacklist",{}).get("rics",[])
            if 0 <= idx < len(bl): bl.pop(idx)
            self.config.setdefault("blacklist",{})["rics"] = bl
            self._fill_blacklist()

        # ── Werkzeuge ──
        elif bid == "btn-run-test":       self._run_test_alarm()
        elif bid == "btn-scan-usb":       self._fill_tool_usb()
        elif bid == "btn-check-plugins":  self._run_plugin_check()
        elif bid == "btn-refresh-sysinfo":self._fill_tool_sysinfo()
        elif bid == "btn-refresh-stats":  self._fill_tool_stats()
        elif bid == "btn-manual-backup":
            if Path(CONFIG_PATH).exists():
                shutil.copy2(CONFIG_PATH, CONFIG_PATH + BACKUP_SUFFIX)
                self._fill_tool_backup()
                self.notify("✓ Backup erstellt", severity="information")

    # ────────────────────────────────────────────────────────
    # Werkzeug-Aktionen
    # ────────────────────────────────────────────────────────
    def _run_test_alarm(self) -> None:
        result_widget = self.query_one("#test-result", Static)
        result_widget.update("⏳ Sende Test-Alarm...")

        def do_test():
            lines = ["Test-Alarm – " + datetime.now().strftime("%H:%M:%S"), ""]
            alarm = {
                "type":"alarm","timestamp":datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "ric":"0000000","ric_padded":"0000000","subric":"1",
                "message":"BosDaemon Test – Installation erfolgreich!",
                "label":"TEST","channel":"test","channel_name":"Test","bitrate":"1200",
            }
            import importlib, sys as _sys
            _sys.path.insert(0, "/opt/bosdaemon/plugins")
            notif = self.config.get("notifications", {})
            for name, cfg in notif.items():
                if not cfg.get("enabled", False):
                    lines.append(f"  —  {name}: deaktiviert")
                    continue
                try:
                    mod = importlib.import_module(name)
                    plugin = mod.Plugin(cfg)
                    plugin.send(alarm)
                    lines.append(f"  [green]✓[/green]  {name}: gesendet")
                except Exception as e:
                    lines.append(f"  [red]✗[/red]  {name}: {e}")
            self.call_from_thread(result_widget.update, "\n".join(lines))

        threading.Thread(target=do_test, daemon=True).start()

    def _run_plugin_check(self) -> None:
        notif = self.config.get("notifications", {})
        plugin_names = ["pushover","telegram","email_notify","webhook","discord","divera"]

        def do_check():
            for name in plugin_names:
                cfg = notif.get(name, {})
                ok, msg = check_plugin(name, cfg)
                try:
                    lbl = self.query_one(f"#ps-{name}", Label)
                    if ok is None:
                        self.call_from_thread(lbl.update, "[dim]deaktiviert[/dim]")
                    elif ok:
                        self.call_from_thread(lbl.update, f"[green]✓ {msg}[/green]")
                    else:
                        self.call_from_thread(lbl.update, f"[red]✗ {msg}[/red]")
                except Exception:
                    pass

        threading.Thread(target=do_check, daemon=True).start()

    def _on_confirm_restore(self, ok: bool) -> None:
        if not ok: return
        err = restore_backup()
        if err:
            self.notify(f"Restore fehlgeschlagen: {err}", severity="error")
        else:
            self.config = load_config()
            self._fill_all_settings()
            self._fill_tool_backup()
            self.notify("✓ Backup wiederhergestellt – Einstellungen neu geladen",
                        severity="information")

    # ────────────────────────────────────────────────────────
    # Alarm-Suche
    # ────────────────────────────────────────────────────────
    def _do_search(self) -> None:
        query = self.query_one("#search-input", Input).value.strip().lower()
        if not query:
            self._reset_search()
            return
        table = self.query_one("#alarm-table", DataTable)
        table.clear()
        matches = [r for r in self._all_alarms
                   if any(query in s(c).lower() for c in r)]
        for row in matches:
            ts,ch,ric,lbl,msg,brate = row
            table.add_row(ts,ch or "–",ric,lbl or "–",(msg or "")[:80],brate or "–")
        self.query_one("#search-info", Label).update(
            f"[yellow]{len(matches)} Treffer[/yellow]")

    def _reset_search(self) -> None:
        self.query_one("#search-input", Input).value = ""
        self.query_one("#search-info",  Label).update("")
        self._load_alarm_table()

    # ────────────────────────────────────────────────────────
    # Alarm-Tabelle, Export, DB löschen
    # ────────────────────────────────────────────────────────
    def _load_alarm_table(self) -> None:
        table = self.query_one("#alarm-table", DataTable)
        table.clear(columns=True)
        table.add_columns("Zeit","Kanal","RIC","Einheit","Meldung","Bit")
        db_path = get_db_path(self.config)
        self._all_alarms = []
        if not Path(db_path).exists():
            table.add_row("–","–","–","Keine Datenbank","–","–"); return
        try:
            conn = sqlite3.connect(db_path)
            rows = conn.execute(
                "SELECT timestamp,channel_name,ric,label,message,bitrate "
                "FROM alarms ORDER BY id DESC LIMIT 500").fetchall()
            conn.close()
            self._all_alarms = rows
            self.query_one("#alarm-count", Label).update(f"  {len(rows)} Alarme")
            for row in rows:
                ts,ch,ric,lbl,msg,brate = row
                table.add_row(ts,ch or "–",ric,lbl or "–",(msg or "")[:80],brate or "–")
        except Exception as e:
            table.add_row("–","–","–",f"DB-Fehler: {e}","–","–")

    def _on_confirm_clear_db(self, ok: bool) -> None:
        if not ok: return
        try:
            conn = sqlite3.connect(get_db_path(self.config))
            conn.execute("DELETE FROM alarms"); conn.commit(); conn.close()
            self._load_alarm_table()
            self.notify("✓ Alarm-Verlauf gelöscht", severity="information")
        except Exception as e:
            self.notify(f"Fehler: {e}", severity="error")

    def _export_csv(self) -> None:
        db_path = get_db_path(self.config)
        if not Path(db_path).exists():
            self.notify("Keine Datenbank!", severity="error"); return
        out = f"/tmp/bosdaemon_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        try:
            conn = sqlite3.connect(db_path)
            rows = conn.execute(
                "SELECT timestamp,channel_name,ric,ric_padded,subric,"
                "label,message,bitrate FROM alarms ORDER BY id DESC").fetchall()
            conn.close()
            with open(out,"w",newline="",encoding="utf-8") as f:
                w = csv.writer(f, delimiter=";")
                w.writerow(["Zeit","Kanal","RIC","RIC padded","Subric",
                             "Einheit","Meldung","Bitrate"])
                w.writerows(rows)
            self.notify(f"✓ CSV: {out}", severity="information", timeout=8)
        except Exception as e:
            self.notify(f"CSV-Fehler: {e}", severity="error")

    # ────────────────────────────────────────────────────────
    # Status, Service, Logs
    # ────────────────────────────────────────────────────────
    def _update_status(self) -> None:
        st = service_status()
        ch = len(self.config.get("channels",
                  [self.config.get("sdr",{})] if self.config.get("sdr") else [{}]))
        lbl = self.query_one("#status-label", Label)
        if st == "active":
            lbl.update(f"  [bold green]● LÄUFT[/bold green]   Kanäle: {ch}   Config: {CONFIG_PATH}")
        else:
            lbl.update(f"  [bold red]● GESTOPPT[/bold red]   Config: {CONFIG_PATH}")

    def _service_action(self, action: str) -> None:
        code, out = run_cmd("systemctl", action, SERVICE_NAME)
        if code == 0:
            self.notify(f"✓ Service {action}", severity="information")
        else:
            self.notify(f"Fehler: {out[:100]}", severity="error")
        self._update_status()

    def _start_log_stream(self) -> None:
        self._log_stop.clear()
        threading.Thread(target=self._stream_logs, daemon=True).start()

    def _stream_logs(self) -> None:
        log_view = self.query_one("#log-view", RichLog)
        try:
            proc = subprocess.Popen(
                ["journalctl","-u",SERVICE_NAME,"-f","-n","80","--no-pager","--output=short-iso"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
            for line in proc.stdout:
                if self._log_stop.is_set(): proc.terminate(); break
                line = line.rstrip()
                if not line: continue
                if   "ERROR"  in line: self.call_from_thread(log_view.write, f"[red]{line}[/red]")
                elif "WARNING"in line: self.call_from_thread(log_view.write, f"[yellow]{line}[/yellow]")
                elif "Alarm"  in line: self.call_from_thread(log_view.write, f"[bold cyan]{line}[/bold cyan]")
                elif "Plugin" in line: self.call_from_thread(log_view.write, f"[green]{line}[/green]")
                else:                  self.call_from_thread(log_view.write, line)
        except Exception as e:
            self.call_from_thread(log_view.write, f"[red]Log-Fehler: {e}[/red]")

    # ────────────────────────────────────────────────────────
    # Keyboard-Shortcuts
    # ────────────────────────────────────────────────────────
    def action_refresh(self) -> None:
        self.config = load_config()
        self._update_status()
        self._load_alarm_table()
        self._fill_all_settings()
        self.notify("Aktualisiert", severity="information", timeout=2)

    def action_quit(self) -> None:
        self._log_stop.set()
        self.exit()


if __name__ == "__main__":
    if os.geteuid() != 0:
        print("Für Service-Steuerung bitte als root starten:  sudo bosdaemon-tui")
        print()
    BosDaemonTUI().run()
