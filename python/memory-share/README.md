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
