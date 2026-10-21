#!/bin/bash
# ─────────────────────────────────────────────────────────────
# BosDaemon Installer v2
# Interaktiver Setup-Assistent mit whiptail GUI
# Unterstützt Multi-Kanal (mehrere RTL-SDR Sticks)
# ─────────────────────────────────────────────────────────────

set -e

DAEMON_USER="bosdaemon"
INSTALL_DIR="/opt/bosdaemon"
CONFIG_DIR="/etc/bosdaemon"
LOG_DIR="/var/log/bosdaemon"
LIB_DIR="/var/lib/bosdaemon"
SERVICE_FILE="/etc/systemd/system/bosdaemon.service"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "  ${GREEN}✓${NC} $1"; }
warn() { echo -e "  ${YELLOW}⚠${NC}  $1"; }
err()  { echo -e "  ${RED}✗${NC} $1"; }

# Root-Check
if [[ $EUID -ne 0 ]]; then
    echo "Bitte als root ausführen: sudo bash install.sh"
    exit 1
fi

# whiptail sicherstellen
command -v whiptail &>/dev/null || apt-get install -y whiptail &>/dev/null

TERM_H=$(tput lines 2>/dev/null || echo 24)
TERM_W=$(tput cols  2>/dev/null || echo 80)
DLG_H=$(( TERM_H > 22 ? 22 : TERM_H - 2 ))
DLG_W=$(( TERM_W > 76 ? 76 : TERM_W - 4 ))

wt_msg()      { whiptail --title "BosDaemon Installer" --msgbox "$1" $DLG_H $DLG_W; }
wt_input()    { whiptail --title "BosDaemon Installer" --inputbox "$2" $DLG_H $DLG_W "$3" 3>&1 1>&2 2>&3; }
wt_pass()     { whiptail --title "BosDaemon Installer" --passwordbox "$2" $DLG_H $DLG_W 3>&1 1>&2 2>&3; }
wt_yesno()    { whiptail --title "BosDaemon Installer" --yesno "$1" $DLG_H $DLG_W; }
wt_menu()     { local t="$1" txt="$2"; shift 2; whiptail --title "BosDaemon Installer" --menu "$txt" $DLG_H $DLG_W 10 "$@" 3>&1 1>&2 2>&3; }
wt_checklist(){ local txt="$1"; shift; whiptail --title "BosDaemon Installer" --checklist "$txt" $DLG_H $DLG_W 10 "$@" 3>&1 1>&2 2>&3; }

# ─────────────────────────────────────────────────────────────
# Begrüßung
# ─────────────────────────────────────────────────────────────
clear
whiptail --title "BosDaemon v2 Installer" --backtitle "POCSAG Monitoring Daemon" \
--msgbox "\
Willkommen beim BosDaemon Installer!

BosDaemon ist ein stabiler POCSAG-Monitoring-Daemon
für RTL-SDR Sticks – der bessere BOSWatch.

Neu in v2:
  • Multi-Kanal (bis zu 5 Frequenzen gleichzeitig)
  • Doppelalarm-Filter
  • RegEx-Filter
  • Divera 24/7 Integration

Benachrichtigungen:
  • Pushover  • Telegram  • E-Mail
  • Webhook   • Discord   • Divera 24/7

Drücke OK um zu starten (~5-10 Minuten)." $DLG_H $DLG_W

# ─────────────────────────────────────────────────────────────
# [1/8] Abhängigkeiten
# ─────────────────────────────────────────────────────────────
{
    echo 5;  apt-get update -qq 2>/dev/null
    echo 30; apt-get install -y -qq rtl-sdr multimon-ng python3 python3-pip python3-yaml 2>/dev/null
    echo 70; pip3 install requests pyyaml --quiet 2>/dev/null || true
    echo 95; sleep 0.3; echo 100
} | whiptail --title "BosDaemon Installer" --gauge "[1/8] Abhängigkeiten installieren..." 8 $DLG_W 0

# ─────────────────────────────────────────────────────────────
# [2/8] RTL-SDR Sticks erkennen
# ─────────────────────────────────────────────────────────────
STICKS_FOUND=0
LSUSB_OUT=$(lsusb 2>/dev/null | grep -i "0bda:2838\|0bda:2832\|RTL2838\|RTL283" || true)
if [[ -n "$LSUSB_OUT" ]]; then
    STICKS_FOUND=$(echo "$LSUSB_OUT" | wc -l)
fi

if [[ $STICKS_FOUND -gt 0 ]]; then
    wt_msg "[2/8] $STICKS_FOUND RTL-SDR Stick(s) erkannt!\n\n$LSUSB_OUT\n\nDu kannst alle erkannten Sticks als separate\nEmpfangskanäle einrichten."
else
    wt_msg "[2/8] Kein RTL-SDR Stick erkannt.\n\nStick später anschließen ist OK.\nDu kannst die Kanäle trotzdem konfigurieren."
fi

# ─────────────────────────────────────────────────────────────
# [3/8] Multi-Kanal konfigurieren
# ─────────────────────────────────────────────────────────────
NUM_CHANNELS=$(wt_menu "[3/8] Wie viele Empfangskanäle?" \
    "Jeder Kanal = ein RTL-SDR Stick + eine Frequenz.\n\nBeispiele:\n  1 Kanal  → nur FW Rottweil 172.920 MHz\n  2 Kanäle → FW + RD auf verschiedenen Frequenzen\n  3 Kanäle → FW + RD + Nachbarlandkreis" \
    "1" "1 Kanal  (ein RTL-SDR Stick)" \
    "2" "2 Kanäle (zwei Sticks)" \
    "3" "3 Kanäle (drei Sticks)" \
    "4" "4 Kanäle (vier Sticks)" \
    "5" "5 Kanäle (fünf Sticks)")

# Arrays für Kanal-Daten
declare -a CH_NAME CH_FREQ CH_GAIN CH_PPM CH_DEV

for (( i=0; i<NUM_CHANNELS; i++ )); do
    CH_NUM=$(( i + 1 ))

    wt_msg "Kanal $CH_NUM von $NUM_CHANNELS konfigurieren\n\nDu gibst jetzt Frequenz, Name und\nEinstellungen für diesen Empfänger ein."

    CH_NAME[$i]=$(wt_input "Kanal $CH_NUM" \
        "Name für Kanal $CH_NUM:\n\n(Wird in Logs und Benachrichtigungen angezeigt)\nBeispiele: 'FW Rottweil', 'RD Schwarzwald-Baar'" \
        "Kanal $CH_NUM")

    CH_FREQ[$i]=$(wt_input "Kanal $CH_NUM – Frequenz" \
        "POCSAG-Frequenz für '${CH_NAME[$i]}' (MHz):\n\nBeispiele:\n  172.920  → BW Süd\n  172.975  → Bayern\n  172.450  → NRW" \
        "172.920")

    CH_GAIN[$i]=$(wt_menu "Kanal $CH_NUM – Verstärkung" \
        "Gain für '${CH_NAME[$i]}':" \
        "100" "Hoch (Standard)" \
        "49"  "Mittel" \
        "0"   "Automatisch (AGC)" \
        "custom" "Eigener Wert")

    if [[ "${CH_GAIN[$i]}" == "custom" ]]; then
        CH_GAIN[$i]=$(wt_input "Kanal $CH_NUM" "Eigener Gain-Wert (0-100):" "100")
    fi

    CH_PPM[$i]=$(wt_input "Kanal $CH_NUM – PPM" \
        "PPM-Korrektur für '${CH_NAME[$i]}' (Standard: 0):" "0")

    CH_DEV[$i]=$i   # Geräteindex = Kanal-Index (0, 1, 2, ...)
done

# ─────────────────────────────────────────────────────────────
# [4/8] Filter konfigurieren
# ─────────────────────────────────────────────────────────────
wt_msg "[4/8] Filter konfigurieren\n\nBosDaemon hat drei Filter:\n\n  Doppelalarm-Filter:\n  Verhindert mehrfache Benachrichtigung\n  (POCSAG sendet jeden Alarm 2-3x)\n\n  RegEx-Filter:\n  Alarme nach Textmuster filtern\n\n  Mindestlänge:\n  Leeralarm-Unterdrückung"

DUP_SECONDS=$(wt_input "[4/8] Doppelalarm-Filter" \
    "Zeitfenster für Doppelalarm-Erkennung in Sekunden.\n\nIm selben Zeitfenster: identische Alarme werden\nnur einmal gesendet.\n\nEmpfehlung: 60 Sekunden" \
    "60")

MIN_LEN=$(wt_input "[4/8] Mindest-Nachrichtenlänge" \
    "Alarme ignorieren wenn Meldung kürzer als X Zeichen.\n0 = alle Alarme senden\n5 = nur mit Text (empfohlen)" \
    "0")

REGEX_ENABLED="false"
REGEX_MODE="blacklist"
REGEX_PATTERNS=""

if wt_yesno "[4/8] RegEx-Filter aktivieren?\n\nMit RegEx-Filter kannst du Alarme nach\nTextmustern filtern.\n\nBeispiel Blacklist: Bestimmte Wörter blockieren\nBeispiel Whitelist: Nur bestimmte Wörter durchlassen\n\nKann auch später in der Config eingetragen werden."; then
    REGEX_ENABLED="true"
    REGEX_MODE=$(wt_menu "[4/8] RegEx Modus" \
        "Wie soll der RegEx-Filter arbeiten?" \
        "blacklist" "Blacklist: Passende Alarme BLOCKIEREN" \
        "whitelist" "Whitelist: Nur passende Alarme DURCHLASSEN")

    REGEX_PATTERNS=$(wt_input "[4/8] RegEx Muster" \
        "RegEx-Muster eingeben (kommagetrennt):\n\nBeispiele:\n  TEST,ÜBUNG        → Testalarme blockieren\n  Brand,Unfall      → Whitelist: nur diese\n\nKann später in der Config erweitert werden." \
        "TEST,ÜBUNG,test")
fi

# ─────────────────────────────────────────────────────────────
# [5/8] RIC-Import & Benachrichtigungs-Routing
# ─────────────────────────────────────────────────────────────

# --- CSV-Import ---
RIC_IMPORT_FILE=""
RIC_ENTRIES=""   # wird später in YAML umgewandelt

if wt_yesno "[5/8] RIC-Liste aus CSV importieren?\n\nWenn du eine CSV-Datei mit Fahrzeug-/Einheiten-RICs\nhast, kannst du sie jetzt importieren.\n\nFormat: ric,name (z.B. 0032425,FF Stuttgart)\noder 4-spaltig: ric,,status,name\n\nDu kannst RICs auch später manuell in der Config\neintragen."; then

    RIC_IMPORT_FILE=$(wt_input "[5/8] CSV-Import" \
        "Vollständiger Pfad zur CSV-Datei:\n\nBeispiel: /home/alarm/rics.csv\n\nDie Datei muss auf diesem Server liegen." \
        "/home/alarm/rics.csv")

    if [[ -f "$RIC_IMPORT_FILE" ]]; then
        # CSV einlesen: unterstützt 1-spaltig (ric), 2-spaltig (ric,name),
        # 4-spaltig (ric,,status,name) – wie die mitgelieferte Datei
        RIC_COUNT=0
        RIC_ENTRIES=""
        while IFS=',' read -r col1 col2 col3 col4 rest; do
            # Anführungszeichen und Sternchen entfernen
            ric=$(echo "$col1" | tr -d '"* ')
            # 4-spaltig: Name ist in Spalte 4
            if [[ -n "$col4" ]]; then
                name=$(echo "$col4" | tr -d '"')
            elif [[ -n "$col2" ]]; then
                name=$(echo "$col2" | tr -d '"')
            else
                name="$ric"
            fi
            # Subric (a/b/d) abtrennen für saubereren Key
            subric=""
            if [[ "$ric" =~ ^([0-9]+)([a-z])$ ]]; then
                subric="${BASH_REMATCH[2]}"
                ric="${BASH_REMATCH[1]}"
            fi
            # Leerzeilen/Header überspringen
            [[ -z "$ric" || ! "$ric" =~ ^[0-9] ]] && continue
            # Routing-Kanal anhand des Namens auto-erkennen
            route="sonstige"
            if echo "$name" | grep -qiE "BF |FF |FW |Feuerwehr|WF "; then
                route="fw"
            elif echo "$name" | grep -qiE "^S [0-9]+/8[0-9]|RTW|KTW|Rettung"; then
                route="rd"
            elif echo "$name" | grep -qiE "SEG|PSNV|OLS|Notarzt|NAW|NEF"; then
                route="seg"
            fi
            label_full="$name"
            [[ -n "$subric" ]] && label_full="$name ($subric)"
            RIC_ENTRIES+="  - ric: \"$ric\"\n    label: \"$label_full\"\n    channel: \"$route\"\n"
            (( RIC_COUNT++ )) || true
        done < "$RIC_IMPORT_FILE"

        if [[ $RIC_COUNT -gt 0 ]]; then
            wt_msg "[5/8] CSV-Import erfolgreich!\n\n$RIC_COUNT RICs importiert.\n\nAuto-Routing:\n  • BF/FF/FW → 'fw'\n  • S xx/8x, RTW → 'rd'\n  • SEG/PSNV/OLS → 'seg'\n  • Rest → 'sonstige'\n\nDu kannst das Routing nach der Installation\nin der Config anpassen:\n  nano /etc/bosdaemon/config.yaml"
        else
            wt_msg "[5/8] CSV konnte nicht eingelesen werden.\n\nDatei gefunden: $RIC_IMPORT_FILE\nAber keine gültigen RICs darin.\n\nRICs können später manuell eingetragen werden."
            RIC_IMPORT_FILE=""
        fi
    else
        wt_msg "[5/8] Datei nicht gefunden:\n$RIC_IMPORT_FILE\n\nRICs können nach der Installation manuell\nin der Config eingetragen werden."
        RIC_IMPORT_FILE=""
    fi
fi

# --- Routing-Kanäle definieren ---
ROUTING_CHANNELS=""   # z.B. "fw rd seg sonstige"
ROUTING_NAMES=""      # sprechende Namen für Anzeige

if wt_yesno "[5/8] Benachrichtigungs-Routing aktivieren?\n\nRouting = verschiedene RIC-Gruppen gehen an\nverschiedene Pushover/Telegram-Kanäle.\n\nBeispiel:\n  Feuerwehr → Pushover Gruppe A\n  Rettungsdienst → Pushover Gruppe B\n  SEG/OLS → Telegram Kanal C\n\nOhne Routing: alle Alarme an alle Kanäle."; then

    ROUTING_CHANNELS=$(wt_checklist \
        "[5/8] Welche Routing-Kanäle brauchst du?\n(Leertaste = auswählen)" \
        "fw"       "Feuerwehr (BF/FF/FW)"              "ON" \
        "rd"       "Rettungsdienst (RTW/KTW)"           "ON" \
        "seg"      "SEG / PSNV / OLS"                   "ON" \
        "sonstige" "Sonstige / Unbekannte RICs"          "OFF" \
        3>&1 1>&2 2>&3) || true
    ROUTING_CHANNELS=$(echo "$ROUTING_CHANNELS" | tr -d '"')

    # Sprechende Namen einlesen
    for rch in $ROUTING_CHANNELS; do
        default_name=""
        case "$rch" in
            fw)       default_name="Feuerwehr" ;;
            rd)       default_name="Rettungsdienst" ;;
            seg)      default_name="SEG / OLS" ;;
            sonstige) default_name="Sonstige" ;;
        esac
        rname=$(wt_input "Routing-Kanal '$rch'" \
            "Anzeigename für Kanal '$rch':" "$default_name")
        ROUTING_NAMES+="$rch:$rname,"
    done

    wt_msg "[5/8] Routing konfiguriert!\n\nKanäle: $ROUTING_CHANNELS\n\nIm nächsten Schritt gibst du pro Plugin\ndie Kanal-spezifischen Keys ein\n(z.B. verschiedene Pushover-Group-Keys)."
fi

# ─────────────────────────────────────────────────────────────
# [6/8] Benachrichtigungskanäle
# ─────────────────────────────────────────────────────────────
NOTIF_CHOICES=$(wt_checklist \
    "[5/8] Welche Benachrichtigungen aktivieren?\n(Leertaste = auswählen)" \
    "pushover"     "Pushover (iOS/Android App)"        "OFF" \
    "telegram"     "Telegram Bot"                       "OFF" \
    "email_notify" "E-Mail (SMTP)"                      "OFF" \
    "webhook"      "HTTP Webhook (Website/n8n/etc.)"    "OFF" \
    "discord"      "Discord Webhook"                    "OFF" \
    "divera"       "Divera 24/7 (Feuerwehr-Alarmierung)" "OFF" \
    3>&1 1>&2 2>&3) || true
NOTIF_CHOICES=$(echo "$NOTIF_CHOICES" | tr -d '"')

# ── Pushover ─────────────────────────────────────────────────
PUSHOVER_TOKEN=""; PUSHOVER_KEY=""; PUSHOVER_PRIORITY="normal"; PUSHOVER_MIN_LEN="0"
if echo "$NOTIF_CHOICES" | grep -qw "pushover"; then
    wt_msg "Pushover\n\nDu brauchst:\n  1. App-Token → https://pushover.net/apps\n  2. User-Key oder Group-Key\n     (Group Key für mehrere Empfänger)"
    PUSHOVER_TOKEN=$(wt_input "Pushover" "App-Token:" "")
    PUSHOVER_KEY=$(wt_input "Pushover" "User-Key oder Group-Key:" "")
    PUSHOVER_PRIORITY=$(wt_menu "Pushover Priorität" "Standard-Priorität:" \
        "normal" "Normal" "high" "Hoch (durchdringend)" "low" "Niedrig (lautlos)")
    PUSHOVER_MIN_LEN=$(wt_input "Pushover" \
        "Mindestlänge Meldung für Push (0 = alle):" "5")
fi

# ── Telegram ─────────────────────────────────────────────────
TELEGRAM_TOKEN=""; TELEGRAM_CHATID=""
if echo "$NOTIF_CHOICES" | grep -qw "telegram"; then
    wt_msg "Telegram\n\n1. Bot erstellen: schreibe @BotFather\n   Befehl: /newbot\n2. Bot-Token kopieren\n3. Deinem Bot eine Nachricht schreiben\n4. Chat-ID ermitteln:\n   https://api.telegram.org/bot<TOKEN>/getUpdates"
    TELEGRAM_TOKEN=$(wt_input "Telegram" "Bot-Token:" "")
    TELEGRAM_CHATID=$(wt_input "Telegram" "Chat-ID:" "")
fi

# ── E-Mail ───────────────────────────────────────────────────
EMAIL_HOST=""; EMAIL_PORT="587"; EMAIL_USER=""; EMAIL_PASS=""; EMAIL_TO=""
if echo "$NOTIF_CHOICES" | grep -qw "email_notify"; then
    EMAIL_HOST=$(wt_input "E-Mail" "SMTP-Server:" "smtp.gmail.com")
    EMAIL_PORT=$(wt_input "E-Mail" "SMTP-Port:" "587")
    EMAIL_USER=$(wt_input "E-Mail" "SMTP-Benutzername:" "")
    EMAIL_PASS=$(wt_pass  "E-Mail" "SMTP-Passwort:")
    EMAIL_TO=$(wt_input   "E-Mail" "Empfänger-Adresse:" "")
fi

# ── Webhook ──────────────────────────────────────────────────
WEBHOOK_URL=""; WEBHOOK_SECRET=""
if echo "$NOTIF_CHOICES" | grep -qw "webhook"; then
    WEBHOOK_URL=$(wt_input "Webhook" \
        "Webhook URL (bekommt JSON POST):\nz.B. https://deine-seite.de/alarm" "")
    WEBHOOK_SECRET=$(wt_input "Webhook" \
        "Webhook Secret (optional, leer = keins):" "")
fi

# ── Discord ──────────────────────────────────────────────────
DISCORD_WEBHOOK=""
if echo "$NOTIF_CHOICES" | grep -qw "discord"; then
    wt_msg "Discord\n\nWebhook erstellen:\n  Discord → Kanal → Einstellungen\n  → Integrationen → Webhooks → Neu"
    DISCORD_WEBHOOK=$(wt_input "Discord" "Webhook URL:" "")
fi

# ── Divera ───────────────────────────────────────────────────
DIVERA_KEY=""
if echo "$NOTIF_CHOICES" | grep -qw "divera"; then
    wt_msg "Divera 24/7\n\nAPI-Key ermitteln:\n  1. Divera → Einstellungen → API\n  2. API-Key kopieren\n\nWichtiger Hinweis:\n  Divera löst echte Alarmierungen aus!\n  Erst testen, dann aktivieren."
    DIVERA_KEY=$(wt_input "Divera 24/7" "API-Key:" "")
fi

# ── Routing-Keys pro Plugin ──────────────────────────────────
# Falls Routing aktiviert: Kanal-spezifische Keys abfragen
declare -A PUSHOVER_ROUTING_KEYS  # rch → user_key
declare -A TELEGRAM_ROUTING_IDS   # rch → chat_id

if [[ -n "$ROUTING_CHANNELS" ]]; then

    if echo "$NOTIF_CHOICES" | grep -qw "pushover"; then
        wt_msg "Pushover Routing-Keys\n\nDu hast Routing aktiviert. Jetzt kannst du\npro Kanal einen eigenen Pushover Group-Key\neintragen.\n\nWenn ein Kanal denselben Key wie Standard\nnehmen soll → einfach leer lassen."

        for rch in $ROUTING_CHANNELS; do
            # Anzeigename aus ROUTING_NAMES lesen
            rname=$(echo "$ROUTING_NAMES" | grep -oP "(?<=^|,)${rch}:[^,]+" | cut -d: -f2-)
            [[ -z "$rname" ]] && rname="$rch"
            rkey=$(wt_input "Pushover: Kanal '$rname'" \
                "Pushover Group-Key für Kanal '$rname':\n(leer = Standard-Key wird verwendet)" "")
            [[ -n "$rkey" ]] && PUSHOVER_ROUTING_KEYS[$rch]="$rkey"
        done
    fi

    if echo "$NOTIF_CHOICES" | grep -qw "telegram"; then
        wt_msg "Telegram Routing-Chats\n\nDu hast Routing aktiviert. Jetzt kannst du\npro Kanal eine eigene Telegram Chat-ID\neintragen.\n\nWenn ein Kanal dieselbe Chat-ID wie Standard\nnehmen soll → einfach leer lassen."

        for rch in $ROUTING_CHANNELS; do
            rname=$(echo "$ROUTING_NAMES" | grep -oP "(?<=^|,)${rch}:[^,]+" | cut -d: -f2-)
            [[ -z "$rname" ]] && rname="$rch"
            rcid=$(wt_input "Telegram: Kanal '$rname'" \
                "Telegram Chat-ID für Kanal '$rname':\n(leer = Standard-Chat-ID wird verwendet)" "")
            [[ -n "$rcid" ]] && TELEGRAM_ROUTING_IDS[$rch]="$rcid"
        done
    fi
fi

# ─────────────────────────────────────────────────────────────
# [6/8] Stille-Alarm
# ─────────────────────────────────────────────────────────────
SILENCE_ENABLED="false"; SILENCE_MINUTES="15"
if wt_yesno "[6/8] Stille-Alarm aktivieren?\n\nAlarm wenn für X Minuten kein Signal –\nnützlich um USB-Probleme früh zu erkennen."; then
    SILENCE_ENABLED="true"
    SILENCE_MINUTES=$(wt_input "[6/8] Stille-Alarm" \
        "Alarm nach wie vielen Minuten ohne Signal?" "15")
fi

# ─────────────────────────────────────────────────────────────
# [7/8] Alarmhistorie
# ─────────────────────────────────────────────────────────────
DB_ENABLED="false"
if wt_yesno "[7/8] Alarmhistorie in SQLite speichern?\n\nSpeichert: Zeitstempel, Kanal, RIC, Einheit, Meldung\nDatei: $LIB_DIR/alarms.db"; then
    DB_ENABLED="true"
fi

# ─────────────────────────────────────────────────────────────
# [8/8] Zusammenfassung
# ─────────────────────────────────────────────────────────────
SUMMARY="Zusammenfassung:\n\n"
SUMMARY+="Kanäle:\n"
for (( i=0; i<NUM_CHANNELS; i++ )); do
    SUMMARY+="  Kanal $((i+1)): ${CH_NAME[$i]} | ${CH_FREQ[$i]} MHz | Gain ${CH_GAIN[$i]}\n"
done
SUMMARY+="\nFilter:\n"
SUMMARY+="  Doppelalarm: ${DUP_SECONDS}s Fenster\n"
SUMMARY+="  Mindestlänge: ${MIN_LEN} Zeichen\n"
[[ "$REGEX_ENABLED" == "true" ]] && SUMMARY+="  RegEx: ${REGEX_MODE}\n"
SUMMARY+="\nBenachrichtigungen:\n"
for ch in pushover telegram email_notify webhook discord divera; do
    echo "$NOTIF_CHOICES" | grep -qw "$ch" && SUMMARY+="  ✓ $ch\n"
done
SUMMARY+="\nStille-Alarm: $([ "$SILENCE_ENABLED" = "true" ] && echo "${SILENCE_MINUTES} Min." || echo 'Nein')\n"
SUMMARY+="Datenbank: $([ "$DB_ENABLED" = "true" ] && echo 'Ja' || echo 'Nein')\n"
[[ -n "$RIC_IMPORT_FILE" ]] && SUMMARY+="\nRIC-Import: $RIC_COUNT RICs aus CSV\n"
[[ -n "$ROUTING_CHANNELS" ]] && SUMMARY+="\nRouting-Kanäle: $ROUTING_CHANNELS\n"

if ! wt_yesno "$SUMMARY\nJetzt installieren?"; then
    wt_msg "Installation abgebrochen."
    exit 0
fi

# ─────────────────────────────────────────────────────────────
# Installation
# ─────────────────────────────────────────────────────────────
{
echo 5
mkdir -p "$INSTALL_DIR" "$CONFIG_DIR" "$LOG_DIR" "$LIB_DIR"
echo 10
id "$DAEMON_USER" &>/dev/null || useradd -r -s /bin/false -d "$INSTALL_DIR" "$DAEMON_USER" 2>/dev/null || true
usermod -aG plugdev "$DAEMON_USER" 2>/dev/null || true
echo 15
cp "$SCRIPT_DIR/bosdaemon.py" "$INSTALL_DIR/"
cp -r "$SCRIPT_DIR/plugins/"   "$INSTALL_DIR/"
echo 30

# ── Config schreiben ─────────────────────────────────────────
{
cat << HEADER
# ─────────────────────────────────────────────────────────────
# BosDaemon v2 Konfiguration
# Änderungen übernehmen: sudo systemctl restart bosdaemon
# ─────────────────────────────────────────────────────────────

# Empfangskanäle (ein Eintrag pro RTL-SDR Stick)
channels:
HEADER

for (( i=0; i<NUM_CHANNELS; i++ )); do
cat << CHENTRY
  - name: "${CH_NAME[$i]}"
    frequency: "${CH_FREQ[$i]}M"
    device_index: ${CH_DEV[$i]}
    gain: ${CH_GAIN[$i]}
    ppm: ${CH_PPM[$i]}
    sample_rate: 22050
    rtl_fm_path: rtl_fm
    multimon_path: multimon-ng

CHENTRY
done

cat << FILTER

# Filter
filter:
  min_message_length: ${MIN_LEN}

# Doppelalarm-Filter
duplicate_filter:
  enabled: true
  seconds: ${DUP_SECONDS}     # Identische Alarme in diesem Fenster ignorieren

# RegEx-Filter
regex_filter:
  enabled: ${REGEX_ENABLED}
  mode: ${REGEX_MODE}         # blacklist oder whitelist
  patterns:
FILTER

# RegEx Patterns
if [[ "$REGEX_ENABLED" == "true" && -n "$REGEX_PATTERNS" ]]; then
    IFS=',' read -ra PATS <<< "$REGEX_PATTERNS"
    for p in "${PATS[@]}"; do
        p=$(echo "$p" | xargs)
        echo "    - \"$p\""
    done
else
    echo "    - \"TEST\""
    echo "    - \"ÜBUNG\""
    echo "    # Weitere Muster hier eintragen"
fi

cat << BLACKLIST

# Blacklist: Diese RICs komplett ignorieren
blacklist:
  rics:
    - 201225    # Beispiel: DAU-Schleife
    # - 123456
  # remote_url: ""  # Optional: URL zu JSON-Array

# RIC Lookup: Einheitennamen zuweisen
# 'channel' bestimmt welche Benachrichtigungen genutzt werden
ric_lookup:
BLACKLIST

# RICs aus CSV oder Beispiele
if [[ -n "$RIC_ENTRIES" ]]; then
    printf "%b" "$RIC_ENTRIES"
else
cat << RICEXAMPLE
  - ric: "0204872"
    label: "Beispiel: DRK RTW"
    channel: "rd"
  - ric: "0201337"
    label: "Beispiel: FW Rottweil"
    channel: "fw"
  # Weitere RICs hier eintragen...
RICEXAMPLE
fi

cat << NOTIF_HEADER

# Benachrichtigungen
notifications:

NOTIF_HEADER

# Pushover
if echo "$NOTIF_CHOICES" | grep -qw "pushover"; then
cat << PUSHOVER
  pushover:
    enabled: true
    token: "${PUSHOVER_TOKEN}"
    user_key: "${PUSHOVER_KEY}"
    priority: "${PUSHOVER_PRIORITY}"
    sound: siren
    title_prefix: "🚨 Alarm"
    min_message_length: ${PUSHOVER_MIN_LEN}
PUSHOVER
    # Routing-Section für Pushover ausgeben (nur befüllte Keys)
    has_pushover_routing=false
    for rch in $ROUTING_CHANNELS; do
        [[ -n "${PUSHOVER_ROUTING_KEYS[$rch]:-}" ]] && has_pushover_routing=true && break
    done
    if $has_pushover_routing; then
        echo "    routing:"
        for rch in $ROUTING_CHANNELS; do
            rkey="${PUSHOVER_ROUTING_KEYS[$rch]:-}"
            if [[ -n "$rkey" ]]; then
                echo "      ${rch}:"
                echo "        user_key: \"${rkey}\""
            fi
        done
    fi
    echo ""
else
echo "  pushover:"$'\n'"    enabled: false"$'\n'
fi

# Telegram
if echo "$NOTIF_CHOICES" | grep -qw "telegram"; then
cat << TELEGRAM
  telegram:
    enabled: true
    bot_token: "${TELEGRAM_TOKEN}"
    chat_id: "${TELEGRAM_CHATID}"
    min_message_length: 0
TELEGRAM
    # Routing-Section für Telegram ausgeben (nur befüllte IDs)
    has_telegram_routing=false
    for rch in $ROUTING_CHANNELS; do
        [[ -n "${TELEGRAM_ROUTING_IDS[$rch]:-}" ]] && has_telegram_routing=true && break
    done
    if $has_telegram_routing; then
        echo "    routing:"
        for rch in $ROUTING_CHANNELS; do
            rcid="${TELEGRAM_ROUTING_IDS[$rch]:-}"
            if [[ -n "$rcid" ]]; then
                echo "      ${rch}:"
                echo "        chat_id: \"${rcid}\""
            fi
        done
    fi
    echo ""
else
echo "  telegram:"$'\n'"    enabled: false"$'\n'
fi

# E-Mail
if echo "$NOTIF_CHOICES" | grep -qw "email_notify"; then
cat << EMAILCFG
  email_notify:
    enabled: true
    smtp_host: "${EMAIL_HOST}"
    smtp_port: ${EMAIL_PORT}
    smtp_user: "${EMAIL_USER}"
    smtp_pass: "${EMAIL_PASS}"
    use_tls: true
    from: "${EMAIL_USER}"
    to: "${EMAIL_TO}"

EMAILCFG
else
echo "  email_notify:"$'\n'"    enabled: false"$'\n'
fi

# Webhook
if echo "$NOTIF_CHOICES" | grep -qw "webhook"; then
cat << WEBHOOKCFG
  webhook:
    enabled: true
    url: "${WEBHOOK_URL}"
    method: POST
    secret: "${WEBHOOK_SECRET}"

WEBHOOKCFG
else
echo "  webhook:"$'\n'"    enabled: false"$'\n'
fi

# Discord
if echo "$NOTIF_CHOICES" | grep -qw "discord"; then
cat << DISCORDCFG
  discord:
    enabled: true
    webhook_url: "${DISCORD_WEBHOOK}"
    username: BosDaemon

DISCORDCFG
else
echo "  discord:"$'\n'"    enabled: false"$'\n'
fi

# Divera
if echo "$NOTIF_CHOICES" | grep -qw "divera"; then
cat << DIVERACFG
  divera:
    enabled: true
    api_key: "${DIVERA_KEY}"
    alarm_title: "POCSAG Alarm"
    priority: false       # true = hohe Priorität
    send_push: true
    send_sms: false
    send_call: false
    send_mail: false
    # RIC → Divera-Gruppen-ID Mapping (optional):
    # ric_to_group:
    #   "204872": [123, 456]   # Gruppen-IDs aus Divera

DIVERACFG
else
echo "  divera:"$'\n'"    enabled: false"$'\n'
fi

cat << FOOTER
# Stille-Alarm
silence_watchdog:
  enabled: ${SILENCE_ENABLED}
  minutes: ${SILENCE_MINUTES}

# USB-Watchdog
usb_watchdog:
  enabled: true
  check_interval: 30
  device_pattern: "0bda:2838"

# Datenbank
database:
  enabled: ${DB_ENABLED}
  path: "${LIB_DIR}/alarms.db"

# Logging
logging:
  level: INFO
  file: "${LOG_DIR}/bosdaemon.log"
FOOTER

} > "$CONFIG_DIR/config.yaml"

echo 55
chown -R "$DAEMON_USER:$DAEMON_USER" "$INSTALL_DIR" "$LOG_DIR" "$LIB_DIR"
chmod 600 "$CONFIG_DIR/config.yaml"

# udev für RTL-SDR
cat > /etc/udev/rules.d/20-rtlsdr.rules << 'UDEV'
SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="2832", GROUP="plugdev", MODE="0666"
SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="2838", GROUP="plugdev", MODE="0666"
UDEV
udevadm control --reload-rules 2>/dev/null || true
echo 70

# systemd Service
cat > "$SERVICE_FILE" << SVCEOF
[Unit]
Description=BosDaemon v2 POCSAG Monitoring
After=network.target

[Service]
Type=simple
User=${DAEMON_USER}
ExecStart=/usr/bin/python3 ${INSTALL_DIR}/bosdaemon.py -c ${CONFIG_DIR}/config.yaml
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=bosdaemon
MemoryMax=512M
TasksMax=64

[Install]
WantedBy=multi-user.target
SVCEOF

echo 85
systemctl daemon-reload
systemctl enable bosdaemon
systemctl start bosdaemon
echo 95; sleep 1; echo 100

} | whiptail --title "BosDaemon Installer" --gauge "Installation läuft..." 8 $DLG_W 0

# Test-Tool installieren
cat > /usr/local/bin/bosdaemon-test << 'TESTEOF'
#!/usr/bin/env python3
"""BosDaemon Test-Alarm – sendet durch alle aktiven Plugins"""
import sys
sys.path.insert(0, '/opt/bosdaemon')
sys.path.insert(0, '/opt/bosdaemon/plugins')
import yaml, importlib
from datetime import datetime

with open('/etc/bosdaemon/config.yaml') as f:
    config = yaml.safe_load(f)

alarm = {
    'type': 'alarm', 'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    'ric': '0000000', 'ric_padded': '0000000', 'subric': '1',
    'message': 'BosDaemon Test-Alarm – Installation erfolgreich!',
    'label': 'TEST', 'channel': 'test', 'channel_name': 'Test', 'bitrate': '1200',
}

print("BosDaemon Test-Alarm")
print("=" * 40)
for name, cfg in config.get('notifications', {}).items():
    if not cfg.get('enabled', False):
        print(f"  - {name}: deaktiviert")
        continue
    try:
        mod = importlib.import_module(name)
        plugin = mod.Plugin(cfg)
        plugin.send(alarm)
        print(f"  ✓ {name}: gesendet")
    except Exception as e:
        print(f"  ✗ {name}: Fehler – {e}")
TESTEOF
chmod +x /usr/local/bin/bosdaemon-test

# Kanal-Status-Tool
cat > /usr/local/bin/bosdaemon-status << 'STATEOF'
#!/bin/bash
echo "BosDaemon Status"
echo "================"
systemctl is-active bosdaemon &>/dev/null && echo "● Läuft" || echo "● Gestoppt"
echo ""
echo "Letzte Logs:"
journalctl -u bosdaemon -n 15 --no-pager 2>/dev/null
STATEOF
chmod +x /usr/local/bin/bosdaemon-status

# TUI Dashboard installieren
pip3 install textual --quiet --break-system-packages 2>/dev/null || true
cp "$SCRIPT_DIR/bosdaemon-tui.py" "$INSTALL_DIR/"
chmod +x "$INSTALL_DIR/bosdaemon-tui.py"

cat > /usr/local/bin/bosdaemon-tui << TUIEOF
#!/bin/bash
exec python3 ${INSTALL_DIR}/bosdaemon-tui.py "\$@"
TUIEOF
chmod +x /usr/local/bin/bosdaemon-tui

# Ergebnis
sleep 2
STATUS=$(systemctl is-active bosdaemon 2>/dev/null || echo "unknown")

if [[ "$STATUS" == "active" ]]; then
    wt_msg "✅ BosDaemon läuft!\n\n$NUM_CHANNELS Kanal/Kanäle aktiv.\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n🖥  MANAGEMENT DASHBOARD:\n  sudo bosdaemon-tui\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\nWeitere Befehle:\n\n  Test-Alarm:    bosdaemon-test\n  Logs live:     journalctl -u bosdaemon -f\n  Config:        nano $CONFIG_DIR/config.yaml\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
else
    wt_msg "⚠️  Service nicht gestartet.\n\nFehleranalyse:\n  journalctl -u bosdaemon -n 30\n\nHäufige Ursachen:\n  • RTL-SDR Stick nicht angeschlossen\n  • Frequenz falsch\n  • Anderes Programm nutzt den Stick\n\n  sudo systemctl start bosdaemon"
fi

clear
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  BosDaemon v2 installiert! ($NUM_CHANNELS Kanal/Kanäle)${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "  Dashboard: sudo bosdaemon-tui"
echo "  Test:      bosdaemon-test"
echo "  Logs:      journalctl -u bosdaemon -f"
echo "  Config:    $CONFIG_DIR/config.yaml"
echo ""
