# tcp_over_http

Trasporta una connessione TCP (database, SSH, qualsiasi protocollo) dentro richieste HTTP/HTTPS.
Un solo file, solo libreria standard di Python 3.8+.

```
app --tcp--> HttpTcpClient --HTTP(S)--> HttpTcpServer --tcp--> servizio (es. DB :3128)
 (tua macchina)                              (macchina vicino al servizio)
```

## Avvio rapido

**1. Sul server** (la macchina che raggiunge il servizio):

```bash
python tcp_over_http.py server \
  --listen 0.0.0.0:8080 \
  --target 127.0.0.1:3128 \
  --token SEGRETO
```

**2. Sul client** (la tua macchina):

```bash
python tcp_over_http.py client \
  --listen 127.0.0.1:3128 \
  --url http://IP_SERVER:8080 \
  --token SEGRETO
```

**3.** Connetti la tua applicazione a `127.0.0.1:3128`: il traffico arriva al servizio sul server.

### Opzioni

| Modo   | Opzione    | Significato                                          |
|--------|------------|------------------------------------------------------|
| server | `--listen` | `host:porta` su cui ascolta l'HTTP                   |
| server | `--target` | `host:porta` TCP a cui inoltrare                     |
| client | `--listen` | `host:porta` TCP locale da esporre                   |
| client | `--url`    | URL del server (`http://` o `https://`)              |
| entrambi | `--token` | segreto condiviso (header `X-Token`), consigliato   |

## Uso da codice Python

```python
from tcp_over_http import HttpTcpServer, HttpTcpClient

# server
HttpTcpServer("0.0.0.0", 8080, "127.0.0.1", 3128, token="SEGRETO").serve_forever()

# client
HttpTcpClient("127.0.0.1", 3128, "https://tuodominio.it", token="SEGRETO").serve_forever()
```

`serve_forever()` è bloccante: usa un thread se devi fare altro. `shutdown()` ferma il server/client.

## Uso su internet (con HTTPS)

Il tunnel da solo non cifra nulla. Metti il server dietro un reverse proxy con TLS
e usa `https://` nell'URL del client.

Esempio Caddy:

```
tuodominio.it {
    reverse_proxy 127.0.0.1:8080 {
        flush_interval -1
        transport http {
            response_header_timeout 60s
        }
    }
}
```

Esempio nginx:

```nginx
location / {
    proxy_pass http://127.0.0.1:8080;
    proxy_buffering off;
    proxy_request_buffering off;
    proxy_read_timeout 60s;
}
```

Se il server è su un sotto-percorso (`https://dominio/tunnel`), mettilo nell'URL del client:
`--url https://dominio/tunnel` (il proxy deve togliere il prefisso prima di inoltrare).

## Esempi

**Database PostgreSQL remoto:**
```bash
# server: --target 127.0.0.1:5432      client: --listen 127.0.0.1:5432
psql -h 127.0.0.1 -p 5432 -U utente db
```

**SSH:**
```bash
# server: --target 127.0.0.1:22        client: --listen 127.0.0.1:2222
ssh -p 2222 utente@127.0.0.1
```

## Protocollo

Ogni connessione TCP è una sessione:

| Richiesta            | Effetto                                                        |
|----------------------|----------------------------------------------------------------|
| `POST /open`         | il server si connette al target, risponde con l'id di sessione |
| `POST /send/<id>`    | il body viene scritto sul socket target                        |
| `GET /recv/<id>`     | long-poll (25s): `200` dati, `204` nulla da leggere, `410` sessione chiusa |
| `POST /close/<id>`   | chiude la sessione                                             |

Senza token valido il server risponde `403`.

## Limiti

- Latenza maggiore di una connessione TCP diretta (una richiesta HTTP per chunk).
- Il server chiude le sessioni **abbandonate** dopo 5 minuti (`IDLE_TIMEOUT`): cioè quando non arriva
  nessuna richiesta HTTP, ad esempio se il client è morto o ha perso la rete. Una connessione viva ma
  ferma (es. un `psql` aperto) non scade, perché il long-poll si rinnova ogni 25 secondi (`POLL_TIMEOUT`).
- Se un proxy/CDN tronca le risposte prima di 25 secondi, abbassa `POLL_TIMEOUT` (es. 15).
- Non è pensato per throughput elevato; per quello è meglio un trasporto WebSocket.
- Il token è un segreto condiviso semplice: senza HTTPS viaggia in chiaro.
- Usalo solo per servizi e reti su cui hai autorizzazione.
