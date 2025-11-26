#!/bin/bash
# Script per testare il funzionamento di mail.py in locale senza systemd

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="${SCRIPT_DIR}/config.json"

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo "=== Test Locale Mail Checker ==="
echo ""

# Verifica che config.json esista
if [ ! -f "$CONFIG_FILE" ]; then
    echo -e "${RED}Errore: config.json non trovato!${NC}"
    echo ""
    echo "Esegui prima:"
    echo "  ./setup-config.sh    (configurazione guidata)"
    echo "oppure:"
    echo "  cp config.json.example config.json"
    echo "  nano config.json     (modifica manuale)"
    exit 1
fi

echo -e "${GREEN}✓ File config.json trovato${NC}"

# Verifica che Python3 sia installato
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}Errore: python3 non è installato${NC}"
    exit 1
fi

echo -e "${GREEN}✓ Python3 trovato${NC}"

# Verifica che Docker sia in esecuzione (per ngrok)
if ! docker ps &> /dev/null 2>&1; then
    echo -e "${YELLOW}⚠ Docker non disponibile o non in esecuzione${NC}"
    echo -e "${YELLOW}  I comandi ngrok potrebbero non funzionare${NC}"
else
    echo -e "${GREEN}✓ Docker disponibile${NC}"
fi

echo ""
echo -e "${GREEN}Avvio mail.py in modalità locale...${NC}"
echo -e "${YELLOW}Premi Ctrl+C per interrompere${NC}"
echo ""
echo "Log in tempo reale:"
echo "----------------------------------------"

# Esegui lo script Python in foreground
cd "$SCRIPT_DIR"
python3 mail.py
