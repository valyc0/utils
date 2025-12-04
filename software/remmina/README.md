# Configurazione Remmina con Nginx e SSL

## Problema Identificato

Il browser stava tentando di connettersi con HTTPS alla porta 3001, ma nginx era configurato solo per HTTP. 
Questo causava errori 400 perché nginx riceveva handshake TLS/SSL ma non era configurato per gestirli.

## Soluzione Implementata

Ho configurato nginx con supporto SSL/TLS usando certificati self-signed.

## File Creati

1. **conf/nginx-ssl.conf** - Configurazione nginx con SSL
2. **generate-ssl.sh** - Script per generare certificati SSL self-signed
3. **start-nginx-ssl.sh** - Script per avviare nginx con SSL
4. **ssl/cert.pem** e **ssl/key.pem** - Certificati SSL generati

## Come Usare

### Configurazione Autenticazione (Prima Volta)

Prima di avviare nginx, configura username e password per l'autenticazione Basic:

```bash
# Genera il file .htpasswd con le tue credenziali
./generate-htpasswd.sh <username> <password>

# Esempio:
./generate-htpasswd.sh admin miapassword123
```

**Credenziali di default**: Se hai già eseguito lo script, le credenziali sono:
- Username: `admin`
- Password: `remmina2024`

### Avvio Completo

```bash
# 1. Avvia Remmina (se non già avviato)
./start.sh

# 2. Avvia nginx con SSL
./start-nginx-ssl.sh
```

### Accesso

- **Con tunnel SSH sulla porta 3000**: Continua a funzionare come prima
- **Con IP diretto sulla porta 3001**: Ora funziona con HTTPS e richiede autenticazione
  - URL: `https://192.168.188.106:3001`
  - **Autenticazione**: Il browser chiederà username e password (default: admin/remmina2024)
  - **Nota**: Il browser mostrerà un avviso di sicurezza perché il certificato è self-signed. 
    Dovrai accettare l'eccezione di sicurezza.

### Rigenerare i Certificati (opzionale)

Se vuoi rigenerare i certificati SSL:

```bash
./generate-ssl.sh
docker restart nginx-remmina-ssl
```

### Cambiare Username e Password

Per cambiare le credenziali di autenticazione:

```bash
# 1. Genera nuovo file .htpasswd
./generate-htpasswd.sh nuovo_username nuova_password

# 2. Riavvia nginx per applicare le modifiche
docker restart nginx-remmina-ssl
```

### Stop e Cleanup

```bash
# Ferma nginx
docker stop nginx-remmina-ssl
docker rm nginx-remmina-ssl

# Ferma Remmina
docker stop remmina
docker rm remmina
```

## Configurazione Nginx

La configurazione include:

- **Autenticazione Basic HTTP**: Richiede username e password per accedere
- **SSL/TLS**: Supporto per TLS 1.2 e 1.3
- **HTTP/2**: Abilitato per migliori performance
- **WebSocket**: Supporto per upgrade di connessione (necessario per Remmina)
- **Timeout**: Configurati a 1800s per sessioni lunghe
- **Proxy Headers**: Tutti gli header necessari per il corretto funzionamento

## Troubleshooting

### Verificare i log di nginx

```bash
docker logs nginx-remmina-ssl
```

### Verificare che le porte siano in ascolto

```bash
netstat -tlnp | grep 3001
```

### Testare la connessione SSL

```bash
curl -k https://localhost:3001
```

## Note

- Il certificato SSL è self-signed e valido per 365 giorni
- Per un ambiente di produzione, considera l'uso di Let's Encrypt o certificati firmati da CA
- La configurazione usa `--net host` per nginx, quindi condivide la rete con l'host
