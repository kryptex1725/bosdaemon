"""
BosDaemon Plugin: Divera 24/7
Sendet Alarme an Divera (Alarmierungssystem für Feuerwehren)
API-Dokumentation: https://divera247.com/api/

Voraussetzungen:
  - Divera 24/7 Account (Feuerwehr-Organisation)
  - API-Key aus Divera-Einstellungen
"""

import logging
import requests

logger = logging.getLogger("bosdaemon.plugin.divera")

DIVERA_API_BASE = "https://www.divera247.com/api/v2"


class Plugin:
    def __init__(self, config):
        self.api_key       = config.get("api_key", "")
        self.only_channels = config.get("only_channels", [])
        self.min_message_length = config.get("min_message_length", 0)

        # Alarm-Einstellungen
        self.alarm_title   = config.get("alarm_title", "POCSAG Alarm")
        self.alarm_type    = config.get("alarm_type", "")      # Leer = Standard
        self.priority      = config.get("priority", False)     # True = hohe Priorität
        self.address       = config.get("address", "")         # Einsatzort
        self.send_push     = config.get("send_push", True)
        self.send_sms      = config.get("send_sms", False)
        self.send_call     = config.get("send_call", False)
        self.send_mail     = config.get("send_mail", False)

        # RIC → Divera-Einheit Mapping (optional)
        # Damit wird der Alarm nur an die jeweilige Einheit in Divera geschickt
        self.ric_to_group  = config.get("ric_to_group", {})

        if not self.api_key:
            logger.error("Divera: Kein API-Key konfiguriert!")

        logger.info("Divera 24/7 Plugin initialisiert")

    def send(self, alarm):
        alarm_type = alarm.get("type", "alarm")

        # Nur echte Alarme, kein Stille-Alarm
        if alarm_type == "silence":
            return

        channel = alarm.get("channel", "unknown")
        if self.only_channels and channel not in self.only_channels:
            return

        message = alarm.get("message", "")
        if self.min_message_length > 0 and len(message) < self.min_message_length:
            return

        if not self.api_key:
            logger.error("Divera: API-Key fehlt")
            return

        ric   = alarm.get("ric", "?")
        label = alarm.get("label", "")
        ts    = alarm.get("timestamp", "")

        # Titel zusammenbauen
        if label:
            title = label
        else:
            title = f"{self.alarm_title} RIC {ric}"

        # Meldungstext
        text = message if message else f"RIC: {ric}"
        if label and message:
            text = f"{label}\n{message}"

        # Gruppen-IDs aus RIC-Mapping
        group_ids = []
        ric_stripped = ric.lstrip("0") or "0"
        if ric_stripped in self.ric_to_group:
            g = self.ric_to_group[ric_stripped]
            if isinstance(g, list):
                group_ids = g
            else:
                group_ids = [g]

        # Divera Alarm-Payload
        payload = {
            "Alarm": {
                "title":           title,
                "text":            text,
                "address":         self.address,
                "priority":        self.priority,
                "send_push":       self.send_push,
                "send_sms":        self.send_sms,
                "send_call":       self.send_call,
                "send_mail":       self.send_mail,
                "notification_type": self.alarm_type,
            }
        }

        if group_ids:
            payload["Alarm"]["group_id"] = group_ids

        url = f"{DIVERA_API_BASE}/alarms?accesskey={self.api_key}"

        try:
            resp = requests.post(url, json=payload, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            alarm_id = data.get("data", {}).get("id", "?")
            logger.info(f"Divera Alarm ausgelöst: ID {alarm_id} – {title}")
        except requests.RequestException as e:
            logger.error(f"Divera Fehler: {e}")
            if hasattr(e, "response") and e.response is not None:
                logger.error(f"Divera Antwort: {e.response.text[:200]}")
