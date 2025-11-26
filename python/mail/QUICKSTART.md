# Guida Rapida - Installazione e Configurazione

## 🚀 Installazione Rapida (3 Passi)

### Metodo 1: Configurazione Guidata (Consigliato)

```bash
cd /path/to/mail
./setup-config.sh    # Script interattivo per creare config.json
./install.sh         # Installa il servizio
./start.sh           # Avvia il servizio
```

### Metodo 2: Configurazione Manuale

```bash
cd /path/to/mail
cp config.json.example config.json
nano config.json     # Modifica con i tuoi dati
./install.sh
./start.sh
```

## 📝 Cosa Configurare

### 1. Configurazione Email (config.json)

```json
{
  "email": {
    "username": "tua-email@provider.it",
    "password": "tua_password"
  },
  "security": {
    "trusted_sender": "email-autorizzata@example.com"
  }
}
```

### 2. Token Ngrok (ngrok/ret.sh)

Edita `ngrok/ret.sh` alla riga 15:
```bash
-e NGROK_AUTHTOKEN=tuo_token_ngrok
```

Ottieni il token su: https://dashboard.ngrok.com/get-started/your-authtoken

## 🎯 Provider Email Comuni

### Gmail
```json
"imap_server": "imap.gmail.com",
"smtp_server": "smtp.gmail.com"
```
⚠️ Usa una "password per app" (non la password Gmail normale)
Genera su: https://myaccount.google.com/apppasswords

### Libero (default)
```json
"imap_server": "imapmail.libero.it",
"smtp_server": "smtp.libero.it"
```

### Yahoo
```json
"imap_server": "imap.mail.yahoo.com",
"smtp_server": "smtp.mail.yahoo.com"
```

### Outlook
```json
"imap_server": "outlook.office365.com",
"smtp_server": "outlook.office365.com"
```

## ✅ Verifica Installazione

```bash
# Stato servizio
sudo systemctl status mail-checker

# Log in tempo reale
tail -f mail.log

# Verifica configurazione
python3 -c "import json; print(json.load(open('config.json'))['email']['username'])"
```

## 🔧 Comandi Utili

```bash
./start.sh           # Avvia servizio
./stop.sh            # Ferma servizio
tail -f mail.log     # Visualizza log
sudo systemctl status mail-checker  # Stato
```

## 📧 Test Funzionamento

Invia una email a `username` (configurato in config.json) da `trusted_sender`:
- **Subject**: `parti 8080`
- **Risultato atteso**: Ricevi email con URL ngrok

## ❗ Risoluzione Problemi

### "File di configurazione non trovato"
```bash
cp config.json.example config.json
nano config.json
```

### "Errore IMAP login"
- Verifica username e password in config.json
- Per Gmail: usa password per app
- Controlla che IMAP sia abilitato nell'account

### "Mail ignorata: mittente non autorizzato"
- Verifica `trusted_sender` in config.json
- Deve corrispondere esattamente all'email del mittente

### Service non si avvia
```bash
sudo journalctl -u mail-checker -n 50
cat mail.log
```

## 🔒 Sicurezza

✅ File config.json protetto automaticamente (permessi 600)
✅ Escluso da git tramite .gitignore
✅ Credenziali separate dal codice

⚠️ NON committare config.json in repository pubblici
⚠️ Usa password per app quando disponibile

## 📍 Installazione in Directory Personalizzata

Il sistema rileva automaticamente la directory:

```bash
# Esempio: installazione in /opt
sudo mkdir -p /opt/mail-checker
sudo chown $USER:$USER /opt/mail-checker
cp -r /home/valyc-pc/lavoro/mail/* /opt/mail-checker/
cd /opt/mail-checker
./setup-config.sh
./install.sh
./start.sh
```

## 📚 Documentazione Completa

Vedi `README.md` per documentazione dettagliata e `CHANGELOG.md` per le modifiche implementate.
