"""
BosDaemon Plugin: HTTP Webhook
Sendet Alarme als HTTP POST JSON an eine beliebige URL
Geeignet für eigene Websites, n8n, Make, Home Assistant etc.
"""

import logging
import json
import requests

logger = logging.getLogger("bosdaemon.plugin.webhook")


class Plugin:
    def __init__(self, config):
        self.url = config.get("url", "")
        self.method = config.get("method", "POST").upper()
        self.headers = config.get("headers", {"Content-Type": "application/json"})
        self.secret = config.get("secret", "")
        self.only_channels = config.get("only_channels", [])
        self.min_message_length = config.get("min_message_length", 0)
        self.include_fields = config.get("include_fields", [])  # leer = alle

        if not self.url:
            logger.error("Webhook: Keine URL konfiguriert!")

        # Secret als Header hinzufügen wenn gesetzt
        if self.secret:
            self.headers["X-BOSWatch-Secret"] = self.secret

        logger.info(f"Webhook Plugin initialisiert: {self.url}")

    def send(self, alarm):
        alarm_type = alarm.get("type", "alarm")
        channel = alarm.get("channel", "unknown")

        if self.only_channels and channel not in self.only_channels:
            return

        message = alarm.get("message", "")
        if self.min_message_length > 0 and len(message) < self.min_message_length:
            if alarm_type != "silence":
                return

        # Payload zusammenbauen
        payload = {
            "type": alarm_type,
            "timestamp": alarm.get("timestamp", ""),
            "ric": alarm.get("ric", ""),
            "ric_padded": alarm.get("ric_padded", ""),
            "subric": alarm.get("subric", ""),
            "message": message,
            "label": alarm.get("label", ""),
            "channel": channel,
            "bitrate": alarm.get("bitrate", ""),
        }

        # Nur bestimmte Felder senden wenn konfiguriert
        if self.include_fields:
            payload = {k: v for k, v in payload.items() if k in self.include_fields}

        try:
            if self.method == "POST":
                resp = requests.post(
                    self.url,
                    json=payload,
                    headers=self.headers,
                    timeout=10
                )
            elif self.method == "GET":
                resp = requests.get(
                    self.url,
                    params=payload,
                    headers=self.headers,
                    timeout=10
                )
            else:
                logger.error(f"Webhook: Unbekannte Methode {self.method}")
                return

            resp.raise_for_status()
            logger.info(f"Webhook gesendet an {self.url} (Status {resp.status_code})")
        except requests.RequestException as e:
            logger.error(f"Webhook Fehler: {e}")
