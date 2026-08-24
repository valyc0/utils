# serveo

Reverse tunnel SSH con porta gateway unica: esponi servizi della tua
macchina locale attraverso un server con sole 2 porte aperte.

## Installazione

    pip install .

Dipendenza runtime: `asyncssh`.

## Avvio sul server

    python -m serveo --ssh-port 8086 --gateway-port 8087

Entrambe le porte sono configurabili; devono essere aperte sul firewall.

## Uso dal client

Apri il tunnel:

    ssh -p 8086 -R 1522:localhost:1522 mioserver.com

Nella sessione vedrai la conferma. Chiunque raggiunga
`mioserver.com:8087` viene instradato al tuo `localhost:1522`.
Funziona con qualsiasi protocollo TCP (db, RDP, HTTP, WebSocket...).

Con `-N` non apre shell ma mantiene il tunnel:

    ssh -N -p 8086 -R 3000:localhost:3000 mioserver.com

## Regole

- La porta richiesta dalla `-R` è solo un'etichetta: NON viene aperta
  sul server (il traffico entra sempre dalla porta gateway).
- Un solo tunnel attivo alla volta: una nuova `-R` sostituisce la
  precedente.
- Se la connessione SSH cade, il tunnel si svuota: riconnettiti.
- Nessuna autenticazione: usa solo su macchine tue.

## Host key

Generata automaticamente (Ed25519) in `~/.serveo/host_key` al primo
avvio. I client vedranno questo fingerprint; aggiungi a
`~/.ssh/known_hosts` o usa `-o StrictHostKeyChecking=accept-new`.

## Test

    pip install -e '.[dev]'
    python -m pytest tests/ -v
