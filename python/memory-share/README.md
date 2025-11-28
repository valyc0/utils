# Memory Share

Applicazione web collaborativa per condividere e modificare testo in tempo reale.

## Caratteristiche

- 📝 Condivisione testo in tempo reale
- 🔒 Stanze private tramite nome personalizzato
- 💾 Persistenza automatica su file
- 🎨 Preservazione della formattazione (ideale per codice)
- 🌐 Multi-utente con sincronizzazione istantanea
- ⚡ Interfaccia semplice e intuitiva

## Installazione

1. Installa le dipendenze:
```bash
pip install -r requirements.txt
```

## Utilizzo

Avvia il server specificando la porta (opzionale, default 5000):

```bash
python app.py [porta]
```

Esempi:
```bash
python app.py           # Usa porta 5000
python app.py 8080      # Usa porta 8080
python app.py 3000      # Usa porta 3000
```

Poi apri il browser su `http://localhost:PORTA`

## Come funziona

1. Scegli un nome per la tua stanza
2. Condividi il link con altri utenti
3. Tutti possono vedere e modificare il contenuto in tempo reale
4. Il contenuto viene salvato automaticamente in file nella cartella `rooms/`

## Struttura

- `app.py` - Server Flask con Socket.IO
- `templates/index.html` - Pagina home per scegliere la stanza
- `templates/room.html` - Editor collaborativo
- `rooms/` - Directory con i file delle stanze (creata automaticamente)

## Utilizzo con curl

### Upload di file

```bash
# Upload di un file nella stanza "test"
curl -X POST http://localhost:5000/room/test/upload -F "file=@documento.pdf"

# Upload con percorso assoluto
curl -X POST http://localhost:5000/room/myroom/upload -F "file=@/home/user/immagine.jpg"

# Upload con output formattato JSON
curl -X POST http://localhost:5000/room/progetti/upload -F "file=@file.zip" | jq
```

### Lista dei file

```bash
# Visualizza tutti i file in una stanza
curl http://localhost:5000/room/test/files

# Con output formattato
curl http://localhost:5000/room/test/files | jq
```

### Download di file

```bash
# Download di un file specifico
curl -O http://localhost:5000/room/test/download/documento.pdf

# Download del contenuto della chat
curl -O http://localhost:5000/room/test/download-chat
```

### Eliminazione

```bash
# Elimina un file specifico
curl -X DELETE http://localhost:5000/room/test/delete/documento.pdf

# Elimina l'intera stanza con tutti i file
curl -X DELETE http://localhost:5000/room/test/delete-room
```

### Esempi con server remoto

```bash
# Upload su server remoto
curl -X POST http://80.225.82.255:8085/room/myroom/upload -F "file=@./data.log"

# Lista file da server remoto
curl http://80.225.82.255:8085/room/myroom/files

# Download da server remoto
curl -O http://80.225.82.255:8085/room/myroom/download/data.log
```

**Note:**
- La dimensione massima del file è 20GB
- I file vengono salvati in `rooms/NOME_STANZA/`
- Il nome della stanza viene sanitizzato automaticamente (solo caratteri alfanumerici, `-` e `_`)
