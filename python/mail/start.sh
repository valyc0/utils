#!/bin/bash

SERVICE_NAME="mail-checker"
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${YELLOW}Avvio del servizio ${SERVICE_NAME}...${NC}"

# Controlla se il servizio esiste
if ! systemctl list-unit-files | grep -q "^${SERVICE_NAME}.service"; then
    echo -e "${RED}Errore: il servizio ${SERVICE_NAME} non è installato${NC}"
    echo "Esegui prima: ./install.sh"
    exit 1
fi

# Avvia il servizio
sudo systemctl start ${SERVICE_NAME}.service

# Controlla lo stato
if sudo systemctl is-active --quiet ${SERVICE_NAME}.service; then
    echo -e "${GREEN}✓ Servizio avviato con successo!${NC}"
    echo ""
    sudo systemctl status ${SERVICE_NAME}.service --no-pager
    echo ""
    echo "Per visualizzare i log: tail -f mail.log"
else
    echo -e "${RED}✗ Errore nell'avvio del servizio${NC}"
    sudo systemctl status ${SERVICE_NAME}.service --no-pager
    exit 1
fi
