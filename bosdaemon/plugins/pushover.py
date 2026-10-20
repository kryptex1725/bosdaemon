"""
BosDaemon Plugin: Pushover
Sendet Alarme via Pushover App Notification
"""

import logging
import requests

logger = logging.getLogger("bosdaemon.plugin.pushover")

PUSHOVER_API = "https://api.pushover.net/1/messages.json"

# Prioritäten
PRIORITY_MAP = {
    "low":    -1,
    "normal":  0,
    "high":    1,
    "emergency": 2,
}


class Plugin:
    def __init__(self, config):
        self.token = config.get("token", "")
        self.user_key = config.get("user_key", "")
        self.priority = config.get("priority", "normal")
        self.sound = config.get("sound", "siren")
        self.title_prefix = config.get("title_prefix", "🚨 Alarm")

        # Kanal-spezifische Targets (optional)
        # z.B. channels: { fw: { user_key: "..." }, rd: { user_key: "..." } }
        self.channels = config.get("channels", {})

        # Filter: nur bestimmte RICs oder Channels
        self.only_channels = config.get("only_channels", [])  # leer = alle
        self.min_message_length = config.get("min_message_length", 0)

        if not self.token:
            logger.error("Pushover: Kein App-Token konfiguriert!")
        if not self.user_key and not self.channels:
            logger.error("Pushover: Kein User/Group-Key konfiguriert!")

        logger.info("Pushover Plugin initialisiert")

    def send(self, alarm):
        # Nur echte Alarme oder Stille-Alarm
        alarm_type = alarm.get("type", "alarm")
        channel = alarm.get("channel", "unknown")

        # Kanal-Filter
        if self.only_channels and channel not in self.only_channels:
            logger.debug(f"Pushover: Kanal '{channel}' nicht in only_channels – übersprungen")
            return

        # Nachrichtenlänge prüfen
        message = alarm.get("message", "")
        if self.min_message_length > 0 and len(message) < self.min_message_length:
            if alarm_type != "silence":
                logger.debug(f"Pushover: Nachricht zu kurz – übersprungen")
                return

        # Target bestimmen (channel-spezifisch oder default)
        user_key = self.user_key
        token = self.token
        if channel in self.channels:
            ch = self.channels[channel]
            user_key = ch.get("user_key", user_key)
            token = ch.get("token", token)

        if not token or not user_key:
            logger.error("Pushover: Token oder User-Key fehlt")
            return

        # Titel und Nachricht zusammenbauen
        label = alarm.get("label", "")
        ric = alarm.get("ric", "?")

        if alarm_type == "silence":
            title = "⚠️ BOSWatch – Kein Signal!"
            body = alarm.get("message", "Kein POCSAG-Signal!")
            priority = PRIORITY_MAP.get("high", 1)
            sound = "persistent"
        else:
            if label:
                title = f"{self.title_prefix}: {label}"
            else:
                title = f"{self.title_prefix} RIC {ric}"

            if message:
                body = message
            else:
                body = f"RIC: {ric} (Subric: {alarm.get('subric','?')})"

            priority_str = alarm.get("priority", self.priority)
            priority = PRIORITY_MAP.get(priority_str, 0)
            sound = self.sound

        payload = {
            "token": token,
            "user": user_key,
            "title": title,
            "message": body,
            "priority": priority,
            "sound": sound,
        }

        # Emergency priority braucht retry + expire
        if priority == 2:
            payload["retry"] = 30
            payload["expire"] = 3600

        try:
            resp = requests.post(PUSHOVER_API, data=payload, timeout=10)
            resp.raise_for_status()
            logger.info(f"Pushover gesendet: {title}")
        except requests.RequestException as e:
            logger.error(f"Pushover Fehler: {e}")
