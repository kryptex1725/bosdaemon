"""
BosDaemon Plugin: Discord
Sendet Alarme via Discord Webhook
"""

import logging
import requests

logger = logging.getLogger("bosdaemon.plugin.discord")


class Plugin:
    def __init__(self, config):
        self.webhook_url = config.get("webhook_url", "")
        self.username = config.get("username", "BosDaemon")
        self.avatar_url = config.get("avatar_url", "")
        self.only_channels = config.get("only_channels", [])
        self.min_message_length = config.get("min_message_length", 0)

        # Kanal-spezifische Webhooks
        self.channels = config.get("channels", {})

        if not self.webhook_url and not self.channels:
            logger.error("Discord: Kein Webhook konfiguriert!")

        logger.info("Discord Plugin initialisiert")

    def send(self, alarm):
        alarm_type = alarm.get("type", "alarm")
        channel = alarm.get("channel", "unknown")

        if self.only_channels and channel not in self.only_channels:
            return

        message = alarm.get("message", "")
        if self.min_message_length > 0 and len(message) < self.min_message_length:
            if alarm_type != "silence":
                return

        # Webhook URL bestimmen
        webhook_url = self.webhook_url
        if channel in self.channels:
            webhook_url = self.channels[channel].get("webhook_url", webhook_url)

        if not webhook_url:
            logger.error("Discord: Keine Webhook-URL")
            return

        ric = alarm.get("ric", "?")
        label = alarm.get("label", "")
        ts = alarm.get("timestamp", "")

        if alarm_type == "silence":
            embed = {
                "title": "⚠️ BOSWatch – Kein Signal!",
                "description": alarm.get("message", ""),
                "color": 0xFF6600,  # Orange
            }
        else:
            if label:
                title = f"🚨 Alarm: {label}"
            else:
                title = f"🚨 Alarm RIC {ric}"

            fields = [
                {"name": "RIC", "value": ric, "inline": True},
                {"name": "Subric", "value": alarm.get("subric", "?"), "inline": True},
                {"name": "Zeit", "value": ts, "inline": False},
            ]
            if message:
                fields.insert(0, {"name": "Meldung", "value": message, "inline": False})
            if label:
                fields.insert(0, {"name": "Einheit", "value": label, "inline": False})

            embed = {
                "title": title,
                "color": 0xFF0000,  # Rot
                "fields": fields,
            }

        payload = {
            "username": self.username,
            "embeds": [embed],
        }
        if self.avatar_url:
            payload["avatar_url"] = self.avatar_url

        try:
            resp = requests.post(webhook_url, json=payload, timeout=10)
            resp.raise_for_status()
            logger.info("Discord gesendet")
        except requests.RequestException as e:
            logger.error(f"Discord Fehler: {e}")
