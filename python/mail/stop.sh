#!/bin/bash

SERVICE_NAME="mail-checker"
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${YELLOW}Arresto del servizio ${SERVICE_NAME}...${NC}"

# Controlla se il servizio esiste
if ! systemctl list-unit-files | grep -q "^${SERVICE_NAME}.service"; then
    echo -e "${RED}Errore: il servizio ${SERVICE_NAME} non è installato${NC}"
    exit 1
fi

# Ferma il servizio
sudo systemctl stop ${SERVICE_NAME}.service

# Controlla lo stato
if ! sudo systemctl is-active --quiet ${SERVICE_NAME}.service; then
    echo -e "${GREEN}✓ Servizio fermato con successo!${NC}"
    
    # Ferma anche il container ngrok se è in esecuzione
    if docker ps | grep -q "myngrok"; then
        echo -e "${YELLOW}Fermo anche il container ngrok...${NC}"
        docker rm -f myngrok
        echo -e "${GREEN}✓ Container ngrok fermato${NC}"
    fi
else
    echo -e "${RED}✗ Errore nell'arresto del servizio${NC}"
    sudo systemctl status ${SERVICE_NAME}.service --no-pager
    exit 1
fi
