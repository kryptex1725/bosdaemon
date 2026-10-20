#!/usr/bin/env python3
"""
BosDaemon - POCSAG Monitoring Daemon v2
Stabiler, modularer Ersatz für BOSWatch3

Features:
  - Multi-Kanal (mehrere RTL-SDR Sticks gleichzeitig)
  - Doppelalarm-Filter
  - RegEx-Filter
  - Modular Plugin-System (Pushover, Telegram, Discord, Divera, Webhook, E-Mail)
  - USB-Watchdog mit Auto-Reconnect
  - Stille-Alarm
  - SQLite Alarmhistorie
"""

import os
import sys
import time
import signal
import logging
import subprocess
import threading
import importlib
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from collections import defaultdict

import yaml
import requests

# ─────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────

def setup_logging(log_level="INFO", log_file=None):
    level = getattr(logging, log_level.upper(), logging.INFO)
    handlers = [logging.StreamHandler(sys.stdout)]
    if log_file:
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        handlers.append(logging.FileHandler(log_file))
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers
    )

logger = logging.getLogger("bosdaemon")

# ─────────────────────────────────────────────────────────────
# Konfiguration
# ─────────────────────────────────────────────────────────────

def load_config(path="/etc/bosdaemon/config.yaml"):
    if not os.path.exists(path):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")
    if not os.path.exists(path):
        logger.error(f"Keine Konfigurationsdatei: {path}")
        sys.exit(1)
    with open(path) as f:
        return yaml.safe_load(f)

# ─────────────────────────────────────────────────────────────
# Datenbank
# ─────────────────────────────────────────────────────────────

class AlarmDatabase:
    def __init__(self, db_path):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.lock = threading.Lock()
        self._create_table()

    def _create_table(self):
        with self.lock:
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS alarms (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    channel   TEXT,
                    ric       TEXT NOT NULL,
                    subric    TEXT,
                    message   TEXT,
                    label     TEXT,
                    bitrate   TEXT
                )
            """)
            self.conn.commit()

    def insert(self, alarm):
        ts = alarm.get("timestamp", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        with self.lock:
            self.conn.execute(
                "INSERT INTO alarms (timestamp,channel,ric,subric,message,label,bitrate) "
                "VALUES (?,?,?,?,?,?,?)",
                (ts, alarm.get("channel_name",""),
                 alarm.get("ric",""), alarm.get("subric",""),
                 alarm.get("message",""), alarm.get("label",""),
                 alarm.get("bitrate",""))
            )
            self.conn.commit()

    def close(self):
        self.conn.close()

# ─────────────────────────────────────────────────────────────
# Doppelalarm-Filter
# ─────────────────────────────────────────────────────────────

class DuplicateFilter:
    """
    Verhindert mehrfache Benachrichtigung bei identischen Alarmen.
    POCSAG sendet jeden Alarm typischerweise 2-3x hintereinander.
    """
    def __init__(self, config):
        self.enabled  = config.get("duplicate_filter", {}).get("enabled", True)
        self.window   = config.get("duplicate_filter", {}).get("seconds", 60)
        self._seen    = {}   # key → timestamp
        self._lock    = threading.Lock()

    def is_duplicate(self, alarm):
        if not self.enabled:
            return False
        # Key = RIC + Subric + erste 20 Zeichen der Meldung
        msg_key = alarm.get("message", "")[:20]
        key = f"{alarm.get('ric','')}:{alarm.get('subric','')}:{msg_key}"
        now = time.time()
        with self._lock:
            # Alte Einträge aufräumen
            expired = [k for k, t in self._seen.items() if now - t > self.window]
            for k in expired:
                del self._seen[k]
            # Prüfen
            if key in self._seen:
                age = int(now - self._seen[key])
                logger.debug(f"Doppelalarm erkannt (RIC {alarm.get('ric')}, vor {age}s) – ignoriert")
                return True
            self._seen[key] = now
            return False

# ─────────────────────────────────────────────────────────────
# RegEx-Filter
# ─────────────────────────────────────────────────────────────

class RegexFilter:
    """
    Filtert Alarme anhand von regulären Ausdrücken auf RIC oder Meldungstext.
    Konfigurierbar: Whitelist (nur diese durchlassen) oder Blacklist (diese blockieren).
    """
    def __init__(self, config):
        self.enabled  = config.get("regex_filter", {}).get("enabled", False)
        self.mode     = config.get("regex_filter", {}).get("mode", "blacklist")
        raw_patterns  = config.get("regex_filter", {}).get("patterns", [])
        self.patterns = []
        for p in raw_patterns:
            try:
                self.patterns.append(re.compile(p, re.IGNORECASE))
            except re.error as e:
                logger.warning(f"Ungültiger RegEx '{p}': {e}")

    def is_blocked(self, alarm):
        if not self.enabled or not self.patterns:
            return False
        text = alarm.get("message", "") + " " + alarm.get("ric", "")
        matched = any(p.search(text) for p in self.patterns)
        if self.mode == "whitelist":
            # Nur durchlassen wenn Match
            if not matched:
                logger.debug(f"RegEx-Whitelist: kein Match – ignoriert")
                return True
        else:
            # Blacklist: blockieren wenn Match
            if matched:
                logger.debug(f"RegEx-Blacklist: Match – ignoriert")
                return True
        return False

# ─────────────────────────────────────────────────────────────
# Blacklist
# ─────────────────────────────────────────────────────────────

class Blacklist:
    def __init__(self, config):
        self.rics = set()
        self._last_load = 0
        self._cache_ttl = 300
        for r in config.get("blacklist", {}).get("rics", []):
            self._add(str(r))
        self.remote_url = config.get("blacklist", {}).get("remote_url", "")

    def _add(self, ric):
        self.rics.add(ric.lstrip("0") or "0")
        self.rics.add(ric.zfill(7))

    def is_blocked(self, ric):
        self._maybe_reload()
        return (ric.lstrip("0") or "0") in self.rics or ric.zfill(7) in self.rics

    def _maybe_reload(self):
        if not self.remote_url:
            return
        if time.time() - self._last_load < self._cache_ttl:
            return
        try:
            data = requests.get(self.remote_url, timeout=5).json()
            for r in data:
                self._add(str(r))
            self._last_load = time.time()
            logger.debug(f"Blacklist aktualisiert: {len(self.rics)} Einträge")
        except Exception as e:
            logger.warning(f"Blacklist remote Fehler: {e}")

# ─────────────────────────────────────────────────────────────
# RIC Lookup
# ─────────────────────────────────────────────────────────────

class RicLookup:
    def __init__(self, config):
        self.table = {}
        for entry in config.get("ric_lookup", []):
            ric = str(entry.get("ric", "")).lstrip("0") or "0"
            self.table[ric] = {
                "label":   entry.get("label", ""),
                "channel": entry.get("channel", "default"),
            }

    def lookup(self, ric):
        return self.table.get(ric.lstrip("0") or "0")

# ─────────────────────────────────────────────────────────────
# Plugin-Manager
# ─────────────────────────────────────────────────────────────

class PluginManager:
    def __init__(self, config, db=None):
        self.plugins = []
        plugin_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plugins")
        sys.path.insert(0, plugin_dir)

        for name, pcfg in config.get("notifications", {}).items():
            if not pcfg.get("enabled", False):
                continue
            try:
                mod    = importlib.import_module(name)
                plugin = mod.Plugin(pcfg)
                self.plugins.append(plugin)
                logger.info(f"Plugin geladen: {name}")
            except Exception as e:
                logger.error(f"Plugin {name} Ladefehler: {e}")

    def notify(self, alarm):
        for plugin in self.plugins:
            try:
                plugin.send(alarm)
            except Exception as e:
                logger.error(f"Plugin {type(plugin).__module__} Fehler: {e}")

# ─────────────────────────────────────────────────────────────
# POCSAG Parser
# ─────────────────────────────────────────────────────────────

POCSAG_RE = re.compile(
    r"POCSAG(\d+):\s+Address:\s+(\d+)\s+Function:\s+(\d+)"
    r"(?:\s+Alpha:\s+(.*))?$"
)

def parse_line(line):
    line = line.strip()
    m = POCSAG_RE.match(line)
    if not m:
        return None
    bitrate = m.group(1)
    ric     = m.group(2).lstrip("0") or "0"
    subric  = m.group(3)
    message = (m.group(4) or "").strip()
    # Base64-artige Sequenzen (verschlüsselt) filtern
    if re.match(r'^[A-Za-z0-9+/=]{20,}$', message) and '.' not in message:
        message = ""
    return {
        "bitrate":    bitrate,
        "ric":        ric,
        "ric_padded": ric.zfill(7),
        "subric":     subric,
        "message":    message,
        "timestamp":  datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "type":       "alarm",
    }

# ─────────────────────────────────────────────────────────────
# Stille-Watchdog (pro Kanal)
# ─────────────────────────────────────────────────────────────

class SilenceWatchdog:
    def __init__(self, config, plugin_manager, channel_name=""):
        sc = config.get("silence_watchdog", {})
        self.enabled   = sc.get("enabled", False)
        self.threshold = sc.get("minutes", 15)
        self.pm        = plugin_manager
        self.name      = channel_name
        self._last     = time.time()
        self._alerted  = False
        self._stop     = threading.Event()

    def signal_received(self):
        self._last = time.time()
        if self._alerted:
            logger.info(f"[{self.name}] Signal wieder da – Stille-Alarm zurückgesetzt")
            self._alerted = False

    def start(self):
        if not self.enabled:
            return
        t = threading.Thread(target=self._run, daemon=True)
        t.start()
        logger.info(f"[{self.name}] Stille-Watchdog aktiv ({self.threshold} Min.)")

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.wait(60):
            elapsed = (time.time() - self._last) / 60
            if elapsed >= self.threshold and not self._alerted:
                self._alerted = True
                last_str = datetime.fromtimestamp(self._last).strftime("%d.%m.%Y %H:%M:%S")
                self.pm.notify({
                    "type":    "silence",
                    "ric":     "SYSTEM",
                    "message": (f"Kanal: {self.name}\n"
                                f"Seit {int(elapsed)} Minuten kein POCSAG-Signal!\n"
                                f"Letzter Empfang: {last_str}\n"
                                f"Bitte Antenne und RTL-SDR prüfen."),
                    "label":   f"Kein Signal – {self.name}",
                    "priority":"high",
                    "channel_name": self.name,
                })
                logger.warning(f"[{self.name}] Stille-Alarm! {int(elapsed)} Min. kein Signal")

# ─────────────────────────────────────────────────────────────
# Einzelner Empfänger (ein RTL-SDR Stick / eine Frequenz)
# ─────────────────────────────────────────────────────────────

class Receiver:
    def __init__(self, channel_cfg):
        self.name         = channel_cfg.get("name", "Kanal")
        self.device_index = channel_cfg.get("device_index", 0)
        self.frequency    = channel_cfg.get("frequency", "172.920M")
        self.gain         = channel_cfg.get("gain", 100)
        self.ppm          = channel_cfg.get("ppm", 0)
        self.sample_rate  = channel_cfg.get("sample_rate", 22050)
        self.rtl_fm_path  = channel_cfg.get("rtl_fm_path", "rtl_fm")
        self.mm_path      = channel_cfg.get("multimon_path", "multimon-ng")
        self.rtl_proc     = None
        self.mm_proc      = None

    def start(self):
        logger.info(f"[{self.name}] Starte auf {self.frequency} (Gerät {self.device_index})")
        rtl_cmd = [
            self.rtl_fm_path,
            "-d", str(self.device_index),
            "-f", self.frequency,
            "-p", str(self.ppm),
            "-g", str(self.gain),
            "-M", "fm", "-E", "DC",
            "-s", str(self.sample_rate),
        ]
        mm_cmd = [
            self.mm_path,
            "-a", "POCSAG512", "-a", "POCSAG1200", "-a", "POCSAG2400",
            "-C", "DE", "-f", "alpha", "-t", "raw", "-",
        ]
        self.rtl_proc = subprocess.Popen(rtl_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.mm_proc  = subprocess.Popen(mm_cmd,  stdin=self.rtl_proc.stdout,
                                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        logger.info(f"[{self.name}] Empfänger gestartet")

    def readline(self):
        try:
            line = self.mm_proc.stdout.readline()
            return line if line else None
        except Exception:
            return None

    def is_alive(self):
        return (self.rtl_proc is not None and self.mm_proc is not None
                and self.rtl_proc.poll() is None and self.mm_proc.poll() is None)

    def stop(self):
        for p in [self.mm_proc, self.rtl_proc]:
            if p:
                try:
                    p.terminate(); p.wait(timeout=5)
                except Exception:
                    try: p.kill()
                    except Exception: pass

    def restart(self):
        self.stop()
        time.sleep(2)
        self.start()

# ─────────────────────────────────────────────────────────────
# USB-Watchdog (pro Empfänger)
# ─────────────────────────────────────────────────────────────

class UsbWatchdog:
    def __init__(self, config, receiver):
        uw = config.get("usb_watchdog", {})
        self.enabled        = uw.get("enabled", True)
        self.check_interval = uw.get("check_interval", 30)
        self.device_pattern = uw.get("device_pattern", "0bda:2838")
        self.receiver       = receiver
        self._stop          = threading.Event()

    def start(self):
        if not self.enabled:
            return
        t = threading.Thread(target=self._run, daemon=True)
        t.start()

    def stop(self):
        self._stop.set()

    def _usb_ok(self):
        try:
            out = subprocess.run(["lsusb"], capture_output=True, text=True, timeout=5).stdout
            return self.device_pattern.lower() in out.lower()
        except Exception:
            return True

    def _run(self):
        while not self._stop.wait(self.check_interval):
            if not self._usb_ok():
                logger.warning(f"[{self.receiver.name}] USB nicht gefunden – warte auf Reconnect")
                while not self._stop.is_set():
                    time.sleep(5)
                    if self._usb_ok():
                        logger.info(f"[{self.receiver.name}] USB wieder da – Neustart")
                        time.sleep(2)
                        self.receiver.restart()
                        break
            elif not self.receiver.is_alive():
                logger.warning(f"[{self.receiver.name}] Empfänger tot – Neustart")
                self.receiver.restart()

# ─────────────────────────────────────────────────────────────
# Kanal-Thread (läuft pro RTL-SDR Stick)
# ─────────────────────────────────────────────────────────────

class ChannelWorker(threading.Thread):
    def __init__(self, channel_cfg, global_cfg, plugin_manager, db,
                 blacklist, ric_lookup, dup_filter, regex_filter):
        super().__init__(daemon=True)
        self.name_str    = channel_cfg.get("name", "Kanal")
        self.receiver    = Receiver(channel_cfg)
        self.silence_wd  = SilenceWatchdog(global_cfg, plugin_manager, self.name_str)
        self.usb_wd      = UsbWatchdog(global_cfg, self.receiver)
        self.pm          = plugin_manager
        self.db          = db
        self.bl          = blacklist
        self.lookup      = ric_lookup
        self.dup         = dup_filter
        self.regex       = regex_filter
        self.min_len     = global_cfg.get("filter", {}).get("min_message_length", 0)
        self._stop       = threading.Event()

    def run(self):
        self.receiver.start()
        self.silence_wd.start()
        self.usb_wd.start()

        while not self._stop.is_set():
            if not self.receiver.is_alive():
                logger.warning(f"[{self.name_str}] Empfänger nicht aktiv – Neustart in 5s")
                time.sleep(5)
                self.receiver.restart()
                continue

            line = self.receiver.readline()
            if not line:
                time.sleep(0.05)
                continue

            self.silence_wd.signal_received()

            alarm = parse_line(line)
            if alarm is None:
                continue

            alarm["channel_name"] = self.name_str

            logger.info(f"[{self.name_str}] POCSAG RIC {alarm['ric']} | {alarm['message'][:60]}")

            # ── Filter-Pipeline ──────────────────────────────
            if self.bl.is_blocked(alarm["ric"]):
                logger.debug(f"[{self.name_str}] Blacklist – ignoriert")
                continue

            if self.dup.is_duplicate(alarm):
                continue

            if self.regex.is_blocked(alarm):
                continue

            if self.min_len > 0 and len(alarm["message"]) < self.min_len:
                logger.debug(f"[{self.name_str}] Nachricht zu kurz – ignoriert")
                continue

            # ── RIC Lookup ───────────────────────────────────
            info = self.lookup.lookup(alarm["ric"])
            if info:
                alarm["label"]   = info["label"]
                alarm["channel"] = info["channel"]
            else:
                alarm["label"]   = ""
                alarm["channel"] = "unknown"

            # ── Datenbank ────────────────────────────────────
            if self.db:
                self.db.insert(alarm)

            # ── Plugins ──────────────────────────────────────
            self.pm.notify(alarm)

    def stop(self):
        self._stop.set()
        self.silence_wd.stop()
        self.usb_wd.stop()
        self.receiver.stop()

# ─────────────────────────────────────────────────────────────
# Haupt-Daemon
# ─────────────────────────────────────────────────────────────

class BosDaemon:
    def __init__(self, config_path=None):
        self.config = load_config(config_path or "/etc/bosdaemon/config.yaml")

        log_cfg = self.config.get("logging", {})
        setup_logging(
            log_level=log_cfg.get("level", "INFO"),
            log_file=log_cfg.get("file", "/var/log/bosdaemon/bosdaemon.log"),
        )
        logger.info("BosDaemon v2 startet...")

        # Datenbank
        db_cfg   = self.config.get("database", {})
        self.db  = AlarmDatabase(db_cfg.get("path", "/var/lib/bosdaemon/alarms.db")) \
                   if db_cfg.get("enabled") else None

        # Gemeinsame Komponenten
        self.blacklist    = Blacklist(self.config)
        self.ric_lookup   = RicLookup(self.config)
        self.dup_filter   = DuplicateFilter(self.config)
        self.regex_filter = RegexFilter(self.config)
        self.pm           = PluginManager(self.config, db=self.db)

        # Kanäle aufbauen
        channels_cfg = self.config.get("channels", [])
        if not channels_cfg:
            # Kompatibilität: alte Einzel-sdr-Konfiguration
            sdr = self.config.get("sdr", {})
            channels_cfg = [{
                "name":         sdr.get("name", "Hauptkanal"),
                "frequency":    sdr.get("frequency", "172.920M"),
                "device_index": sdr.get("device_index", 0),
                "gain":         sdr.get("gain", 100),
                "ppm":          sdr.get("ppm", 0),
                "sample_rate":  sdr.get("sample_rate", 22050),
                "rtl_fm_path":  sdr.get("rtl_fm_path", "rtl_fm"),
                "multimon_path":sdr.get("multimon_path", "multimon-ng"),
            }]

        self.workers = [
            ChannelWorker(
                ch, self.config, self.pm, self.db,
                self.blacklist, self.ric_lookup,
                self.dup_filter, self.regex_filter,
            )
            for ch in channels_cfg
        ]

        self._running = True
        signal.signal(signal.SIGTERM, self._shutdown)
        signal.signal(signal.SIGINT,  self._shutdown)

    def _shutdown(self, sig, frame):
        logger.info("Shutdown-Signal empfangen")
        self._running = False

    def run(self):
        logger.info(f"Starte {len(self.workers)} Kanal/Kanäle...")
        for w in self.workers:
            w.start()

        # Hauptthread wartet nur noch auf Stop-Signal
        while self._running:
            time.sleep(1)

        logger.info("BosDaemon wird beendet")
        for w in self.workers:
            w.stop()
        if self.db:
            self.db.close()

# ─────────────────────────────────────────────────────────────
# Einstiegspunkt
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="BosDaemon v2 – Multi-Kanal POCSAG Monitoring")
    parser.add_argument("-c", "--config", default="/etc/bosdaemon/config.yaml")
    args = parser.parse_args()
    BosDaemon(config_path=args.config).run()
