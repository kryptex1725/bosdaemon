"""
BosDaemon Plugin: E-Mail
Sendet Alarme per E-Mail via SMTP
"""

import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

logger = logging.getLogger("bosdaemon.plugin.email_notify")


class Plugin:
    def __init__(self, config):
        self.smtp_host = config.get("smtp_host", "")
        self.smtp_port = config.get("smtp_port", 587)
        self.smtp_user = config.get("smtp_user", "")
        self.smtp_pass = config.get("smtp_pass", "")
        self.use_tls = config.get("use_tls", True)
        self.from_addr = config.get("from", self.smtp_user)
        self.to_addr = config.get("to", "")
        self.only_channels = config.get("only_channels", [])
        self.min_message_length = config.get("min_message_length", 0)

        if not self.smtp_host:
            logger.error("E-Mail: Kein SMTP-Server konfiguriert!")
        if not self.to_addr:
            logger.error("E-Mail: Keine Empfänger-Adresse konfiguriert!")

        logger.info("E-Mail Plugin initialisiert")

    def send(self, alarm):
        alarm_type = alarm.get("type", "alarm")
        channel = alarm.get("channel", "unknown")

        if self.only_channels and channel not in self.only_channels:
            return

        message = alarm.get("message", "")
        if self.min_message_length > 0 and len(message) < self.min_message_length:
            if alarm_type != "silence":
                return

        ric = alarm.get("ric", "?")
        label = alarm.get("label", "")
        ts = alarm.get("timestamp", "")

        if alarm_type == "silence":
            subject = "⚠️ BOSWatch – Kein Signal!"
            body = alarm.get("message", "Kein POCSAG-Signal!")
        else:
            if label:
                subject = f"🚨 Alarm: {label}"
            else:
                subject = f"🚨 Alarm RIC {ric}"

            body = f"Zeitpunkt: {ts}\n"
            body += f"RIC: {ric} (Subric: {alarm.get('subric','?')})\n"
            if label:
                body += f"Einheit: {label}\n"
            if message:
                body += f"\nMeldung:\n{message}\n"

        msg = MIMEMultipart()
        msg["From"] = self.from_addr
        msg["To"] = self.to_addr
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain", "utf-8"))

        try:
            if self.use_tls:
                server = smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=15)
                server.starttls()
            else:
                server = smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, timeout=15)

            server.login(self.smtp_user, self.smtp_pass)
            server.sendmail(self.from_addr, self.to_addr, msg.as_string())
            server.quit()
            logger.info(f"E-Mail gesendet an {self.to_addr}")
        except Exception as e:
            logger.error(f"E-Mail Fehler: {e}")
