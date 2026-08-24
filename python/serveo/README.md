# serveo

Reverse tunnel SSH con porta gateway unica: esponi servizi della tua
macchina locale attraverso un server con sole 2 porte aperte.

## Installazione

    python3 -m venv .venv
    source .venv/bin/activate
    pip install .

Dipendenza runtime: `asyncssh`.

Nota: su Debian/Ubuntu recenti `pip` è bloccato a livello di sistema
(PEP 668): serve il virtual environment come sopra, oppure `pipx install .`.

## Avvio sul server

    source .venv/bin/activate   # se non già attivo
    python -m serveo --ssh-port 8090 --gateway-port 8091

Porta **8090** = server SSH (i client si connettono qui), porta
**8091** = gateway pubblico di ingresso traffico. Devono essere aperte
sul firewall.

## Uso dal client

Apri il tunnel:

    ssh -p 8090 -R 1522:localhost:1522 mioserver.com

Nella sessione vedrai la conferma. Chiunque raggiunga la porta
gateway `mioserver.com:8091` viene instradato al tuo `localhost:1522`
(1522 nella `-R` è solo un'etichetta). Funziona con qualsiasi
protocollo TCP (db, RDP, HTTP, WebSocket...).

Con `-N` non apre shell ma mantiene il tunnel:

    ssh -N -p 8090 -R 3000:localhost:3000 mioserver.com

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

    source .venv/bin/activate
    pip install -e '.[dev]'
    python -m pytest tests/ -v

## Avvio senza attivare il venv

    .venv/bin/python -m serveo --ssh-port 8090 --gateway-port 8091
    # oppure
    .venv/bin/serveo

Per fermarlo: `Ctrl+C` se in primo piano, altrimenti trova il PID con
`pgrep -f "python -m serveo"` e fai `kill <PID>`.
