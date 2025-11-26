# Miglioramenti Mail Checker Service

## 🎯 Modifiche Implementate

### 1. ✅ File di Configurazione Esterno

**Creato: `config.json.example`**
- Template di configurazione con tutti i parametri necessari
- Struttura JSON chiara e ben organizzata
- Sezioni separate per email, sicurezza, keywords e impostazioni

**Vantaggi:**
- ✅ Credenziali separate dal codice sorgente
- ✅ Facile da configurare senza modificare il codice Python
- ✅ Sicuro: permessi 600 impostati automaticamente
- ✅ Non viene committato in git (protetto da .gitignore)

### 2. ✅ Script Python Aggiornato (`mail.py`)

**Modifiche principali:**
- Caricamento configurazione da file JSON esterno
- Percorsi dinamici calcolati automaticamente rispetto alla directory dello script
- Gestione errori per file di configurazione mancante o malformato
- Messaggi di errore chiari che guidano l'utente

**Percorsi dinamici:**
```python
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPT_PATH = os.path.join(SCRIPT_DIR, "ngrok", "ret.sh")
STOP_SCRIPT_PATH = os.path.join(SCRIPT_DIR, "ngrok", "stop.sh")
LOG_FILE = os.path.join(SCRIPT_DIR, "mail.log")
```

### 3. ✅ Installer Migliorato (`install.sh`)

**Funzionalità aggiunte:**
- **Rilevamento automatico percorsi**: `INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"`
- **Rilevamento utente corrente**: `CURRENT_USER=$(whoami)`
- **Creazione automatica config.json** dal template se non esiste
- **Generazione dinamica service systemd** con percorsi corretti
- **Protezione file sensibili**: chmod 600 su config.json

**Ora funziona da qualsiasi directory!**
```bash
# Esempio: puoi installare ovunque
/opt/mail-checker/install.sh
/home/user/servizi/mail/install.sh
~/progetti/mail/install.sh
```

### 4. ✅ Service Systemd Dinamico

**Rimosso:** File statico `mail-checker.service`
**Ora:** Generato automaticamente dall'installer con:
- User corrente
- WorkingDirectory corretto
- Percorsi assoluti dinamici

### 5. ✅ Documentazione Aggiornata (`README.md`)

**Sezioni riscritte:**
- Istruzioni di installazione semplificate
- Guida completa alla configurazione di config.json
- Esempi per provider comuni (Gmail, Libero, Yahoo, Outlook)
- Procedura di installazione in directory personalizzata
- Sezione sicurezza aggiornata

### 6. ✅ File .gitignore Aggiunto

**Protegge:**
- config.json (credenziali)
- mail.log e altri log
- Cache Python
- File di sistema

## 📋 Come Usare la Nuova Versione

### Prima Installazione

```bash
# 1. Vai nella directory mail
cd /path/to/mail

# 2. Copia e configura
cp config.json.example config.json
nano config.json  # Inserisci le tue credenziali

# 3. Configura ngrok (se non già fatto)
nano ngrok/ret.sh  # Inserisci il tuo authtoken

# 4. Installa
./install.sh

# 5. Avvia
./start.sh
```

### Migrazione da Versione Precedente

Se hai già una versione con credenziali hardcoded in mail.py:

```bash
# 1. Backup delle credenziali attuali
grep "USERNAME\|PASSWORD\|TRUSTED_SENDER" mail.py > credenziali.txt

# 2. Copia il template
cp config.json.example config.json

# 3. Modifica config.json con le credenziali salvate
nano config.json

# 4. Reinstalla
./install.sh

# 5. Riavvia il servizio
./stop.sh
./start.sh
```

## 🔑 Vantaggi Principali

### Sicurezza
- ✅ Credenziali separate dal codice
- ✅ File protetto con permessi 600
- ✅ .gitignore previene commit accidentali
- ✅ Facile escludere config.json dai backup

### Portabilità
- ✅ Installa ovunque senza modificare il codice
- ✅ Percorsi risolti automaticamente
- ✅ Service systemd generato con i percorsi corretti
- ✅ Nessuna configurazione hardcoded

### Manutenibilità
- ✅ Configurazione centralizzata in un unico file
- ✅ Formato JSON standard e leggibile
- ✅ Facile modificare parametri senza toccare il codice
- ✅ Validazione e messaggi di errore chiari

### Usabilità
- ✅ Processo di installazione semplificato
- ✅ Template con esempi chiari
- ✅ Documentazione completa e aggiornata
- ✅ Supporto multi-utente e multi-directory

## 🗂️ Struttura File Finale

```
mail/
├── .gitignore                 # Protegge file sensibili
├── config.json.example        # Template configurazione
├── config.json                # Configurazione effettiva (da creare)
├── mail.py                    # Script aggiornato (legge config.json)
├── requirements.txt
├── install.sh                 # Installer con rilevamento dinamico
├── start.sh
├── stop.sh
├── README.md                  # Documentazione aggiornata
└── ngrok/
    ├── ret.sh
    └── stop.sh
```

## ✅ Test Consigliati

1. **Test installazione pulita:**
   ```bash
   cd /tmp
   cp -r /home/valyc-pc/lavoro/mail ./test-mail
   cd test-mail
   cp config.json.example config.json
   # Configura config.json
   ./install.sh
   ```

2. **Test percorsi:**
   ```bash
   # Verifica che i percorsi nel service siano corretti
   sudo systemctl cat mail-checker
   ```

3. **Test configurazione:**
   ```bash
   # Prova ad avviare senza config.json
   mv config.json config.json.bak
   python3 mail.py  # Deve mostrare errore chiaro
   mv config.json.bak config.json
   ```

## 🎓 Prossimi Possibili Miglioramenti

1. **Validazione configurazione:**
   - Validare formato email
   - Verificare connessione IMAP/SMTP all'avvio
   - Controllare che il file ngrok/ret.sh abbia l'authtoken configurato

2. **Script di setup interattivo:**
   - Wizard che chiede i parametri e crea config.json
   - Test connessione in tempo reale

3. **Supporto variabili d'ambiente:**
   - Opzione per usare variabili d'ambiente invece di config.json
   - Utile per container Docker

4. **Logging avanzato:**
   - Rotazione automatica dei log
   - Livelli di log configurabili

## 📝 Note Finali

Tutte le modifiche sono retrocompatibili e sicure. Il sistema è ora più:
- **Sicuro** (credenziali separate)
- **Portabile** (percorsi dinamici)
- **Manutenibile** (configurazione esterna)
- **User-friendly** (installazione automatizzata)
