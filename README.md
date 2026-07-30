# Utils

Collezione di utility e tool per vari scopi.

## 📂 Struttura

### Python Tools (`python/`)

#### 🌐 File Server (`file-server.py`)
Server HTTP semplice per upload e download di file con autenticazione basic.

**Caratteristiche:**
- Upload di file tramite interfaccia web
- Download e visualizzazione file
- Autenticazione HTTP Basic
- Storage locale in directory `storage/`

**Uso:**
```bash
python file-server.py [porta]
```

#### 🔀 TCP Proxy (`tcp-proxy.py`)
Proxy TCP per inoltrare connessioni da una porta locale verso un server remoto.

**Uso:**
```bash
python tcp-proxy.py <local_port> <remote_host> <remote_port>
```

**Esempio:**
```bash
python tcp-proxy.py 8080 192.168.1.100 80
```

#### 📧 Mail Checker Service (`mail/`)
Sistema automatico per monitorare caselle email e controllare tunnel ngrok tramite comandi via email.

**Caratteristiche:**
- Monitoraggio continuo casella email (polling configurabile)
- Gestione tunnel ngrok tramite Docker
- Comandi via email: "parti" e "stop"
- Servizio systemd per avvio automatico
- Risposta automatica via email

**Setup:**
```bash
cd python/mail
cp config.json.example config.json
# Modifica config.json con le tue credenziali
./install.sh
```

Vedi [python/mail/README.md](python/mail/README.md) per dettagli completi.

#### 💾 Memory Share (`memory-share/`)
Applicazione web collaborativa per condividere e modificare testo in tempo reale.

**Caratteristiche:**
- Condivisione testo in tempo reale
- Stanze private tramite nome personalizzato
- Persistenza automatica su file
- Multi-utente con sincronizzazione istantanea
- Ideale per condividere codice o note

**Uso:**
```bash
cd python/memory-share
pip install -r requirements.txt
python app.py [porta]
```

Vedi [python/memory-share/README.md](python/memory-share/README.md) per dettagli completi.

### Software (`software/`)

#### 🖥️ Remmina (`software/remmina/`)
Configurazione per desktop remoto con Remmina tramite Docker.

## 📦 proxy tar gz
codice java e python per avere un proxy http/https locale

## 📦 Requisiti Generali

- Python 3.6+
- Docker (per alcuni tool)
- Dipendenze specifiche indicate nei singoli README

## 🚀 Quick Start

Ogni tool ha la propria documentazione nella rispettiva directory. Consulta i README specifici per istruzioni dettagliate di installazione e utilizzo.
