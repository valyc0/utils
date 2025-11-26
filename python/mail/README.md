# Mail Checker Service

Sistema automatico per monitorare una casella email Libero e controllare tunnel ngrok tramite comandi via email.

## 📋 Descrizione

Il servizio monitora una casella email Libero e risponde ai comandi ricevuti da un mittente autorizzato:
- **Comando "parti"**: Avvia un tunnel ngrok su una porta specificata nel subject (es: "parti 8080")
- **Comando "stop"**: Ferma il tunnel ngrok attivo

Il sistema invia automaticamente una email di risposta con l'output del comando eseguito.

## 🚀 Caratteristiche

- ✅ Monitoraggio continuo della casella email (polling configurabile, default 60 secondi)
- ✅ Autenticazione mittente autorizzato (configurabile)
- ✅ Gestione tunnel ngrok tramite Docker
- ✅ Servizio systemd per avvio automatico
- ✅ Logging completo delle operazioni
- ✅ Risposta automatica via email con risultati
- ✅ Supporto per qualsiasi provider email (Gmail, Libero, Yahoo, ecc.)
- ✅ Configurazione flessibile di porte e comandi

## 📦 Requisiti

- Python 3.6+
- Docker
- jq (per parsing JSON)
- Connessione Internet

## 🔧 Installazione

### Prerequisiti

Prima di procedere con l'installazione, assicurati di avere:
- ✅ Un account email con accesso IMAP/SMTP
- ✅ Un account ngrok con authtoken (registrati su https://ngrok.com)
- ✅ Docker installato e funzionante
- ✅ Python 3.6+ installato

### 1. Configura i parametri

**IMPORTANTE**: Devi configurare i parametri prima dell'avvio del servizio.

#### Copia e modifica il file di configurazione

```bash
cd /path/to/your/mail/directory
cp config.json.example config.json
chmod 600 config.json  # Proteggi il file con le credenziali
```

#### Edita `config.json` con i tuoi parametri:

```json
{
  "email": {
    "imap_server": "imapmail.libero.it",
    "imap_port": 993,
    "smtp_server": "smtp.libero.it",
    "smtp_port": 587,
    "username": "tua-email@libero.it",
    "password": "tua_password"
  },
  "security": {
    "trusted_sender": "email-autorizzata@example.com"
  },
  "keywords": {
    "start": "parti",
    "stop": "stop"
  },
  "settings": {
    "check_interval": 60
  }
}
```

**Parametri da configurare:**
- `username`: La casella email da monitorare
- `password`: Password della casella email (usa password per app se disponibile)
- `trusted_sender`: Email autorizzata a inviare comandi (solo questa potrà controllare il sistema)
- `imap_server/smtp_server`: Server email (cambia se usi Gmail, Yahoo, ecc.)
- `check_interval`: Secondi tra un controllo e l'altro (default: 60)

**Provider comuni:**
- **Gmail**: `imap.gmail.com` (993), `smtp.gmail.com` (587) - usa password per app
- **Libero**: `imapmail.libero.it` (993), `smtp.libero.it` (587)
- **Yahoo**: `imap.mail.yahoo.com` (993), `smtp.mail.yahoo.com` (587)
- **Outlook**: `outlook.office365.com` (993/587)

#### Configura ngrok authtoken

Edita `ngrok/ret.sh` e sostituisci il token alla riga 15:

```bash
-e NGROK_AUTHTOKEN=tuo_token_ngrok_personale
```

**Come ottenere il token:**
1. Registrati su https://ngrok.com
2. Vai su https://dashboard.ngrok.com/get-started/your-authtoken
3. Copia il tuo authtoken
4. Sostituiscilo nel file `ngrok/ret.sh`

### 2. Esegui lo script di installazione

```bash
cd /path/to/your/mail/directory
chmod +x install.sh
./install.sh
```

Lo script:
- Rileva automaticamente la directory di installazione
- Installa le dipendenze necessarie (jq)
- Scarica l'immagine Docker di ngrok
- Crea il file config.json dal template (se non esiste)
- Configura il service systemd con i percorsi corretti
- Imposta i permessi appropriati

### 3. Verifica e configura

Se l'installer ha creato `config.json` dal template, **devi configurarlo prima di avviare**:

```bash
nano config.json  # oppure usa il tuo editor preferito
```

### 4. Verifica installazione

```bash
sudo systemctl status mail-checker
```

## 🧪 Test del Servizio

Prima di installare il servizio systemd, puoi testare il funzionamento in locale:

```bash
./start-local.sh
```

Questo script:
- ✅ Verifica che `config.json` sia presente
- ✅ Controlla che Python3 sia installato
- ✅ Avvia `mail.py` in modalità interattiva (foreground)
- ✅ Mostra i log in tempo reale nella console
- ✅ Si interrompe con Ctrl+C

**Ideale per:**
- Verificare la configurazione prima dell'installazione
- Testare le credenziali email
- Debug e troubleshooting
- Vedere i log in tempo reale durante lo sviluppo

## 🎮 Utilizzo

### Avvio del servizio

```bash
./start.sh
```

oppure

```bash
sudo systemctl start mail-checker
```

### Arresto del servizio

```bash
./stop.sh
```

oppure

```bash
sudo systemctl stop mail-checker
```

### Visualizza log in tempo reale

```bash
tail -f mail.log
```

### Verifica stato servizio

```bash
sudo systemctl status mail-checker
```

## 📧 Comandi via Email

Invia una email all'indirizzo configurato in `USERNAME` (la casella monitorata) dall'indirizzo configurato in `TRUSTED_SENDER` con:

### Avviare tunnel ngrok
- **Subject**: `parti 8080` (o qualsiasi numero di porta)
- **Risultato**: Avvia ngrok sulla porta specificata e risponde con l'URL pubblico
- **Esempio**: Subject "parti 3000" avvierà ngrok sulla porta 3000

### Fermare tunnel ngrok
- **Subject**: `stop`
- **Risultato**: Ferma il container ngrok attivo

**Note:**
- Solo le email provenienti da `TRUSTED_SENDER` verranno elaborate
- Il numero di porta nel subject è obbligatorio per il comando "parti"
- Riceverai una email di risposta con l'output del comando eseguito

## 📁 Struttura File

```
mail/  (può essere installata ovunque)
├── config.json.example        # Template di configurazione
├── config.json                # Configurazione (da creare, gitignored)
├── mail.py                    # Script principale Python
├── requirements.txt           # Dipendenze Python (vuoto, usa stdlib)
├── setup-config.sh            # Script configurazione guidata (NEW!)
├── install.sh                 # Script installazione (rileva percorsi automaticamente)
├── start-local.sh             # Test locale senza systemd (NEW!)
├── start.sh                   # Avvia il servizio
├── stop.sh                    # Ferma il servizio
├── mail.log                   # Log delle operazioni
├── README.md                  # Questa documentazione
└── ngrok/
    ├── ret.sh                 # Script avvio ngrok
    └── stop.sh                # Script stop ngrok
```

**Note:**
- Il servizio systemd viene generato dinamicamente con i percorsi corretti
- Tutti i percorsi sono risolti automaticamente rispetto alla directory di installazione
- `config.json` è protetto con permessi 600 e non deve essere committato

## ⚙️ Configurazione

### File di Configurazione (config.json)

Il servizio utilizza un file `config.json` per tutte le configurazioni sensibili. Questo file:
- È creato automaticamente dall'installer dal template `config.json.example`
- Ha permessi 600 (leggibile solo dal proprietario)
- Non deve mai essere committato in repository pubblici

**Struttura completa:**

```json
{
  "email": {
    "imap_server": "imapmail.libero.it",
    "imap_port": 993,
    "smtp_server": "smtp.libero.it",
    "smtp_port": 587,
    "username": "tua-email@provider.it",
    "password": "tua_password"
  },
  "security": {
    "trusted_sender": "email-autorizzata@example.com"
  },
  "keywords": {
    "start": "parti",
    "stop": "stop"
  },
  "settings": {
    "check_interval": 60
  }
}
```

### ⚠️ CONFIGURAZIONE OBBLIGATORIA

Prima di avviare il servizio, modifica `config.json`:

#### 1. Credenziali Email

```json
"email": {
  "username": "tua-email@provider.it",  // Casella da monitorare
  "password": "tua_password"             // Password (usa password per app)
}
```

**Per Gmail:**
- Abilita l'accesso IMAP nelle impostazioni
- Genera una "password per app" dalla sezione sicurezza
- Usa `imap.gmail.com` e `smtp.gmail.com`

#### 2. Mittente Autorizzato

```json
"security": {
  "trusted_sender": "tuo-indirizzo@example.com"
}
```

Solo le email da questo indirizzo verranno elaborate.

#### 3. Server Email (se necessario)

Per provider diversi da Libero, modifica:

```json
"email": {
  "imap_server": "imap.gmail.com",  // Es. Gmail
  "smtp_server": "smtp.gmail.com"
}
```

#### 4. Personalizzazioni Opzionali

```json
"keywords": {
  "start": "avvia",  // Cambia la parola chiave per avviare
  "stop": "ferma"    // Cambia la parola chiave per fermare
},
"settings": {
  "check_interval": 30  // Controlla ogni 30 secondi invece di 60
}
```

### Installazione in Directory Personalizzata

L'installer rileva automaticamente la directory da cui viene lanciato. Puoi installare ovunque:

```bash
# Esempio: installazione in /opt/mail-checker
sudo mkdir -p /opt/mail-checker
sudo chown $USER:$USER /opt/mail-checker
cp -r /path/to/mail/* /opt/mail-checker/
cd /opt/mail-checker
./install.sh
```

Tutti i percorsi nel service systemd saranno configurati automaticamente.

## 🔍 Troubleshooting

### Il servizio non si avvia

```bash
# Controlla i log
sudo journalctl -u mail-checker -n 50 --no-pager

# Controlla il file di log
cat mail.log
```

### Docker non funziona

```bash
# Aggiungi l'utente al gruppo docker
sudo usermod -aG docker $USER

# Ricarica la sessione
newgrp docker
```

### Ngrok non risponde

```bash
# Verifica che il container sia in esecuzione
docker ps | grep myngrok

# Controlla i log del container
docker logs myngrok
```

### Email non vengono lette

1. Verifica le credenziali in `mail.py`
2. Controlla che il mittente sia autorizzato (`TRUSTED_SENDER`)
3. Verifica la connessione Internet
4. Controlla i log: `tail -f mail.log`

## 🛑 Disinstallazione

```bash
# Ferma e disabilita il servizio
sudo systemctl stop mail-checker
sudo systemctl disable mail-checker

# Rimuovi il service file
sudo rm /etc/systemd/system/mail-checker.service

# Ricarica systemd
sudo systemctl daemon-reload

# Rimuovi la directory (opzionale)
cd /home/valyc-pc/lavoro
rm -rf mail
```

## 🔒 Sicurezza

✅ **Le credenziali sono ora gestite in modo sicuro tramite config.json**

### Implementato automaticamente:

1. **File di configurazione separato:**
   - Credenziali in `config.json` separato dal codice
   - Permessi 600 (leggibile solo dal proprietario)
   - File escluso dal version control (aggiungi a .gitignore)

2. **Best practices applicate:**
   ```bash
   # I permessi sono impostati automaticamente dall'installer
   chmod 600 config.json
   ```

### Raccomandazioni aggiuntive:

1. **Usa password per applicazioni** (se disponibile):
   - Gmail: Genera una "password per app" dalle impostazioni di sicurezza
   - Outlook/Hotmail: Usa una "password per app"
   - Evita di usare la password principale dell'account

2. **Protezione del repository:**
   ```bash
   # Aggiungi al .gitignore se usi git
   echo "config.json" >> .gitignore
   echo "mail.log" >> .gitignore
   ```

3. **Backup sicuri:**
   - Se fai backup della directory, cripta i file contenenti credenziali
   - Non condividere `config.json` configurato

4. **Monitoraggio:**
   - Controlla regolarmente i log per accessi sospetti
   - Verifica che solo il `trusted_sender` possa inviare comandi
   - Monitora gli accessi alla casella email

5. **Limitazione mittente:**
   - Configura `trusted_sender` con l'email più sicura possibile
   - Considera di usare un indirizzo dedicato solo per questo scopo

## 📝 Log

I log vengono salvati in:
- `mail.log` - Log applicativo del servizio
- `journalctl -u mail-checker` - Log systemd

## 🤝 Supporto

Per problemi o domande, controlla:
1. Il file di log: `tail -f mail.log`
2. Lo stato del servizio: `sudo systemctl status mail-checker`
3. I log di sistema: `sudo journalctl -u mail-checker -f`

## 📄 Licenza

Uso personale.
