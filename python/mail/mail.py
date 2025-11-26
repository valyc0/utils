#!/usr/bin/env python3
import imaplib
import email
from email.header import decode_header
from email.utils import parseaddr
import os
import subprocess
import time
import logging
import sys
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import re
import json

# === CARICA CONFIGURAZIONE ===
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, "config.json")

# Carica la configurazione dal file JSON
try:
    with open(CONFIG_FILE, 'r') as f:
        config = json.load(f)
except FileNotFoundError:
    print(f"ERRORE: File di configurazione '{CONFIG_FILE}' non trovato!")
    print(f"Copia config.json.example in config.json e configura i tuoi parametri.")
    sys.exit(1)
except json.JSONDecodeError as e:
    print(f"ERRORE: Il file di configurazione '{CONFIG_FILE}' non è un JSON valido: {e}")
    sys.exit(1)

# === CONFIGURAZIONE ===
IMAP_SERVER = config['email']['imap_server']
IMAP_PORT = config['email']['imap_port']
SMTP_SERVER = config['email']['smtp_server']
SMTP_PORT = config['email']['smtp_port']
USERNAME = config['email']['username']
PASSWORD = config['email']['password']
TRUSTED_SENDER = config['security']['trusted_sender']
KEYWORD_START = config['keywords']['start']
KEYWORD_STOP = config['keywords']['stop']
CHECK_INTERVAL = config['settings']['check_interval']

# Percorsi dinamici relativi alla directory dello script
SCRIPT_PATH = os.path.join(SCRIPT_DIR, "ngrok", "ret.sh")
STOP_SCRIPT_PATH = os.path.join(SCRIPT_DIR, "ngrok", "stop.sh")
LOG_FILE = os.path.join(SCRIPT_DIR, "mail.log")

# === crea cartella log se non esiste ===
log_dir = os.path.dirname(LOG_FILE)
os.makedirs(log_dir, exist_ok=True)

# === logging ===
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout)
    ]
)

def decode_header_value(value):
    """Decodifica il valore dell'header (come Subject o From)"""
    if not value:
        return ""
    parts = decode_header(value)
    decoded = ""
    for part, enc in parts:
        if isinstance(part, bytes):
            decoded += part.decode(enc or "utf-8", errors="replace")
        else:
            decoded += part
    return decoded

def is_trusted_sender(from_header):
    """Controlla se la mail proviene dal mittente autorizzato"""
    name, addr = parseaddr(from_header or "")
    return addr.lower() == TRUSTED_SENDER.lower()

def extract_number_from_subject(subject):
    """Estrae il numero che segue la parola 'parti' nel subject"""
    match = re.search(r"\bparti\s*(\d+)\b", subject, re.IGNORECASE)
    if match:
        return match.group(1)
    return None

def send_email_reply(to_address, subject, body):
    """Invia una risposta via email con l'output dello script"""
    msg = MIMEMultipart()
    msg["From"] = USERNAME
    msg["To"] = to_address
    msg["Subject"] = subject

    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()  # Abilita la crittografia TLS
            server.login(USERNAME, PASSWORD)  # Usa la password corretta
            server.sendmail(USERNAME, to_address, msg.as_string())
            logging.info("Email di risposta inviata a: %s", to_address)
    except Exception as e:
        logging.error("Errore nell'invio dell'email: %s", e)

def run_script(script_path, number=None):
    """Esegue uno script, passando un numero come argomento se necessario"""
    script_command = ["/bin/bash", script_path]
    if number:
        script_command.append(number)
    
    try:
        result = subprocess.run(script_command, capture_output=True, text=True)
        output = result.stdout.strip()  # Output dello script
        error = result.stderr.strip()  # Eventuali errori

        if output:
            logging.info("Output dello script: %s", output)
        if error:
            logging.error("Errore nello script: %s", error)

        return output or error
    except subprocess.CalledProcessError as e:
        logging.error("Errore esecuzione script: %s", e.stderr.decode(errors='replace'))
        return f"Errore nell'esecuzione dello script: {e.stderr.decode(errors='replace')}"

def check_emails():
    """Controlla la casella e avvia lo script se arriva la mail corretta"""
    if not PASSWORD:
        logging.error("Variabile d'ambiente LIBERO_PASS non impostata. Esco.")
        return

    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
        mail.login(USERNAME, PASSWORD)
        mail.select("INBOX")
        status, data = mail.search(None, '(UNSEEN)')
        if status != "OK":
            logging.error("Errore nella ricerca email: %s", status)
            mail.logout()
            return

        ids = data[0].split()
        if not ids:
            logging.debug("Nessuna mail non letta trovata.")
            mail.logout()
            return

        logging.info("Trovate %d mail non lette.", len(ids))

        for num in ids:
            try:
                status, msg_data = mail.fetch(num, "(RFC822)")
                if status != "OK" or not msg_data or not msg_data[0]:
                    logging.warning("Errore fetch per id %s", num)
                    continue

                msg = email.message_from_bytes(msg_data[0][1])
                subject = decode_header_value(msg.get("Subject", ""))
                from_header = msg.get("From", "")

                logging.info("Mail id %s - From: %s - Subject: %s",
                             num.decode() if isinstance(num, bytes) else num,
                             from_header, subject)

                # Controlla condizioni: mittente e parola chiave
                if is_trusted_sender(from_header):
                    if KEYWORD_START.lower() in subject.lower():
                        logging.info("Condizione per 'parti' soddisfatta.")

                        # Estrai il numero dopo "parti" nel subject
                        number = extract_number_from_subject(subject)
                        if number:
                            logging.info(f"Numero trovato nel subject: {number}")
                            output = run_script(SCRIPT_PATH, number)
                        else:
                            logging.warning("Numero non trovato nel subject.")
                            continue  # Ignora questa mail se non c'è numero

                    elif KEYWORD_STOP.lower() in subject.lower():
                        logging.info("Condizione per 'stop' soddisfatta.")
                        output = run_script(STOP_SCRIPT_PATH)

                    else:
                        logging.debug("Mail ignorata: non contiene parole chiave.")
                        continue  # Ignora questa mail se non c'è la keyword

                    # Invia la risposta via email con l'output dello script
                    send_email_reply(from_header, f"Risultato: {subject}", output)

                    # Segna la mail come letta
                    mail.store(num, '+FLAGS', '\\Seen')
                else:
                    logging.debug("Mail ignorata: mittente non autorizzato.")
            except Exception as e:
                logging.exception("Errore durante l'elaborazione della mail %s: %s", num, e)

        mail.logout()

    except imaplib.IMAP4.error as e:
        logging.error("Errore IMAP: %s", e)
    except Exception as e:
        logging.exception("Errore generale in check_emails: %s", e)

if __name__ == "__main__":
    logging.info("Avvio polling email Libero (intervallo: %s s)", CHECK_INTERVAL)
    while True:
        try:
            check_emails()
        except Exception:
            logging.exception("Errore non gestito nel ciclo principale.")
        time.sleep(CHECK_INTERVAL)
