#!/bin/bash
# Script di configurazione rapida per Mail Checker Service

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="${SCRIPT_DIR}/config.json"
CONFIG_EXAMPLE="${SCRIPT_DIR}/config.json.example"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo "=== Configurazione Mail Checker Service ==="
echo ""

# Verifica se config.json esiste già
if [ -f "$CONFIG_FILE" ]; then
    echo -e "${YELLOW}Il file config.json esiste già.${NC}"
    read -p "Vuoi sovrascriverlo? (s/N): " OVERWRITE
    if [[ ! "$OVERWRITE" =~ ^[Ss]$ ]]; then
        echo "Configurazione annullata."
        exit 0
    fi
fi

# Copia il template
if [ ! -f "$CONFIG_EXAMPLE" ]; then
    echo -e "${RED}Errore: config.json.example non trovato!${NC}"
    exit 1
fi

cp "$CONFIG_EXAMPLE" "$CONFIG_FILE"
echo -e "${GREEN}✓ Template copiato${NC}"
echo ""

# Richiedi informazioni
echo "Inserisci i dati di configurazione:"
echo ""

echo "=== Configurazione Email ==="
read -p "Server IMAP (default: imapmail.libero.it): " IMAP_SERVER
IMAP_SERVER=${IMAP_SERVER:-imapmail.libero.it}

read -p "Porta IMAP (default: 993): " IMAP_PORT
IMAP_PORT=${IMAP_PORT:-993}

read -p "Server SMTP (default: smtp.libero.it): " SMTP_SERVER
SMTP_SERVER=${SMTP_SERVER:-smtp.libero.it}

read -p "Porta SMTP (default: 587): " SMTP_PORT
SMTP_PORT=${SMTP_PORT:-587}

read -p "Username (email da monitorare): " USERNAME
while [ -z "$USERNAME" ]; do
    echo -e "${RED}Username obbligatorio!${NC}"
    read -p "Username (email da monitorare): " USERNAME
done

read -sp "Password: " PASSWORD
echo ""
while [ -z "$PASSWORD" ]; do
    echo -e "${RED}Password obbligatoria!${NC}"
    read -sp "Password: " PASSWORD
    echo ""
done

echo ""
echo "=== Sicurezza ==="
read -p "Email mittente autorizzato (trusted sender): " TRUSTED_SENDER
while [ -z "$TRUSTED_SENDER" ]; do
    echo -e "${RED}Trusted sender obbligatorio!${NC}"
    read -p "Email mittente autorizzato: " TRUSTED_SENDER
done

echo ""
echo "=== Parole Chiave Comandi (opzionale, premi INVIO per default) ==="
read -p "Parola chiave START (default: parti): " KEYWORD_START
KEYWORD_START=${KEYWORD_START:-parti}

read -p "Parola chiave STOP (default: stop): " KEYWORD_STOP
KEYWORD_STOP=${KEYWORD_STOP:-stop}

echo ""
echo "=== Impostazioni ==="
read -p "Intervallo controllo in secondi (default: 60): " CHECK_INTERVAL
CHECK_INTERVAL=${CHECK_INTERVAL:-60}

# Escape delle password per JSON
PASSWORD_ESCAPED=$(echo "$PASSWORD" | sed 's/\\/\\\\/g; s/"/\\"/g')

# Crea il file config.json
cat > "$CONFIG_FILE" <<EOF
{
  "email": {
    "imap_server": "$IMAP_SERVER",
    "imap_port": $IMAP_PORT,
    "smtp_server": "$SMTP_SERVER",
    "smtp_port": $SMTP_PORT,
    "username": "$USERNAME",
    "password": "$PASSWORD_ESCAPED"
  },
  "security": {
    "trusted_sender": "$TRUSTED_SENDER"
  },
  "keywords": {
    "start": "$KEYWORD_START",
    "stop": "$KEYWORD_STOP"
  },
  "settings": {
    "check_interval": $CHECK_INTERVAL
  }
}
EOF

# Imposta permessi sicuri
chmod 600 "$CONFIG_FILE"

echo ""
echo -e "${GREEN}=== Configurazione completata! ===${NC}"
echo ""
echo "File creato: $CONFIG_FILE"
echo "Permessi impostati: 600 (solo lettura per il proprietario)"
echo ""
echo "Prossimi passi:"
echo "1. Configura ngrok authtoken in ngrok/ret.sh"
echo "2. Esegui ./install.sh per installare il servizio"
echo "3. Avvia con ./start.sh"
echo ""
echo -e "${YELLOW}IMPORTANTE: Non condividere il file config.json!${NC}"
