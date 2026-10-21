"""
BosDaemon Plugin: Telegram
Sendet Alarme via Telegram Bot
"""

import logging
import requests

logger = logging.getLogger("bosdaemon.plugin.telegram")


class Plugin:
    def __init__(self, config):
        self.bot_token = config.get("bot_token", "")
        self.chat_id = config.get("chat_id", "")
        self.parse_mode = config.get("parse_mode", "HTML")
        self.only_channels = config.get("only_channels", [])
        self.min_message_length = config.get("min_message_length", 0)

        # Kanal-spezifische Chat-IDs optional
        self.channels = config.get("channels", {})

        if not self.bot_token:
            logger.error("Telegram: Kein Bot-Token konfiguriert!")
        if not self.chat_id and not self.channels:
            logger.error("Telegram: Keine Chat-ID konfiguriert!")

        logger.info("Telegram Plugin initialisiert")

    def _api_url(self):
        return f"https://api.telegram.org/bot{self.bot_token}/sendMessage"

    def send(self, alarm):
        alarm_type = alarm.get("type", "alarm")
        channel = alarm.get("channel", "unknown")

        if self.only_channels and channel not in self.only_channels:
            return

        message = alarm.get("message", "")
        if self.min_message_length > 0 and len(message) < self.min_message_length:
            if alarm_type != "silence":
                return

        # Chat-ID bestimmen
        chat_id = self.chat_id
        if channel in self.channels:
            chat_id = self.channels[channel].get("chat_id", chat_id)

        if not self.bot_token or not chat_id:
            logger.error("Telegram: Token oder Chat-ID fehlt")
            return

        ric = alarm.get("ric", "?")
        label = alarm.get("label", "")
        ts = alarm.get("timestamp", "")

        if alarm_type == "silence":
            text = (
                "⚠️ <b>BOSWatch – Kein Signal!</b>\n\n"
                + alarm.get("message", "")
            )
        else:
            if label:
                text = f"🚨 <b>Alarm: {label}</b>\n"
            else:
                text = f"🚨 <b>Alarm RIC {ric}</b>\n"

            if message:
                text += f"\n📟 {message}"
            text += f"\n\n🕐 {ts}"
            if label:
                text += f"\n📍 RIC: {ric}"

        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": self.parse_mode,
        }

        try:
            resp = requests.post(self._api_url(), json=payload, timeout=10)
            resp.raise_for_status()
            logger.info(f"Telegram gesendet an {chat_id}")
        except requests.RequestException as e:
            logger.error(f"Telegram Fehler: {e}")
