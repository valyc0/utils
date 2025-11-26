#!/bin/bash
set -e

echo "=== Installazione Mail Checker Service ==="
echo ""

# Colori per output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Rileva directory di installazione dinamicamente
INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="mail-checker"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
CURRENT_USER=$(whoami)

echo -e "${GREEN}Directory di installazione: ${INSTALL_DIR}${NC}"
echo -e "${GREEN}Utente corrente: ${CURRENT_USER}${NC}"
echo ""

# Controlla dipendenze
echo -e "${YELLOW}[1/7] Controllo dipendenze...${NC}"

if ! command -v python3 &> /dev/null; then
    echo -e "${RED}Errore: python3 non è installato${NC}"
    echo "Installalo con: sudo apt install python3"
    exit 1
fi
echo -e "${GREEN}✓ Python3 trovato${NC}"

if ! command -v docker &> /dev/null; then
    echo -e "${RED}Errore: Docker non è installato${NC}"
    echo "Installalo con: sudo apt install docker.io"
    exit 1
fi
echo -e "${GREEN}✓ Docker trovato${NC}"

if ! command -v jq &> /dev/null; then
    echo -e "${YELLOW}jq non trovato. Installazione...${NC}"
    sudo apt update && sudo apt install -y jq
fi
echo -e "${GREEN}✓ jq trovato${NC}"

# Verifica che l'utente possa eseguire Docker
if ! docker ps &> /dev/null; then
    echo -e "${YELLOW}Aggiunta utente al gruppo docker...${NC}"
    sudo usermod -aG docker $USER
    echo -e "${YELLOW}⚠ Riavvia la sessione o esegui: newgrp docker${NC}"
fi

# Scarica immagine ngrok
echo -e "${YELLOW}[2/7] Download immagine Docker ngrok...${NC}"
sudo docker pull ngrok/ngrok:latest
echo -e "${GREEN}✓ Immagine ngrok scaricata${NC}"

# Rendi eseguibili gli script
echo -e "${YELLOW}[3/7] Configurazione permessi file...${NC}"
chmod +x mail.py
chmod +x ngrok/ret.sh
chmod +x ngrok/stop.sh
chmod +x start.sh
chmod +x stop.sh
echo -e "${GREEN}✓ Permessi configurati${NC}"

# Crea file di configurazione se non esiste
echo -e "${YELLOW}[4/7] Creazione file di configurazione...${NC}"
if [ ! -f "${INSTALL_DIR}/config.json" ]; then
    if [ -f "${INSTALL_DIR}/config.json.example" ]; then
        cp "${INSTALL_DIR}/config.json.example" "${INSTALL_DIR}/config.json"
        echo -e "${YELLOW}⚠ File config.json creato da template${NC}"
        echo -e "${YELLOW}⚠ IMPORTANTE: Edita ${INSTALL_DIR}/config.json e configura:${NC}"
        echo -e "${YELLOW}   - username e password email${NC}"
        echo -e "${YELLOW}   - trusted_sender (email autorizzata)${NC}"
        echo -e "${YELLOW}   - server IMAP/SMTP se diverso da Libero${NC}"
        chmod 600 "${INSTALL_DIR}/config.json"
        echo -e "${RED}ATTENZIONE: Configurazione richiesta prima dell'avvio!${NC}"
    else
        echo -e "${RED}Errore: config.json.example non trovato!${NC}"
        exit 1
    fi
else
    echo -e "${GREEN}✓ File config.json già presente${NC}"
fi

# Crea il service file systemd
echo -e "${YELLOW}[5/7] Creazione service systemd...${NC}"
sudo tee "$SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=Mail Checker Service - Monitora email e controlla ngrok
After=network.target docker.service
Requires=docker.service

[Service]
Type=simple
User=${CURRENT_USER}
WorkingDirectory=${INSTALL_DIR}
ExecStart=/usr/bin/python3 ${INSTALL_DIR}/mail.py
Restart=always
RestartSec=10
StandardOutput=append:${INSTALL_DIR}/mail.log
StandardError=append:${INSTALL_DIR}/mail.log

[Install]
WantedBy=multi-user.target
EOF

echo -e "${GREEN}✓ Service file creato: ${SERVICE_FILE}${NC}"

# Ricarica systemd
echo -e "${YELLOW}[6/7] Ricaricamento systemd...${NC}"
sudo systemctl daemon-reload
echo -e "${GREEN}✓ Systemd ricaricato${NC}"

# Abilita il servizio all'avvio
echo -e "${YELLOW}[7/7] Abilitazione servizio all'avvio...${NC}"
sudo systemctl enable ${SERVICE_NAME}.service
echo -e "${GREEN}✓ Servizio abilitato${NC}"

echo ""
echo -e "${GREEN}=== Installazione completata con successo! ===${NC}"
echo ""
echo "Comandi disponibili:"
echo "  ./start.sh         - Avvia il servizio"
echo "  ./stop.sh          - Ferma il servizio"
echo "  sudo systemctl status ${SERVICE_NAME}  - Controlla stato servizio"
echo "  tail -f mail.log   - Visualizza log in tempo reale"
echo ""
echo -e "${YELLOW}Per avviare il servizio ora: ./start.sh${NC}"
