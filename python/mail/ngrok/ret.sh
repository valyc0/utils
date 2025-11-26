#!/bin/bash

# Controlla che sia stato passato un argomento (numero di porta)
if [ -z "$1" ]; then
  echo "Errore: devi specificare il numero di porta come argomento."
  echo "Uso: $0 <numero_di_porta>"
  exit 1
fi

# Rimuovi il container se esiste già
docker ps -a | grep -q "myngrok" && docker rm -f myngrok

# Avvia il container Docker con ngrok, usando la porta dinamica
docker run -d \
  --name myngrok \
  --net=host \
  -e NGROK_AUTHTOKEN=1p3PqY7SXGlC9v0W2rKIYcGNX5I_p2GQRP3PwcoC9BFY5g3f \
  ngrok/ngrok:latest tcp $1

# Attendi un po' per permettere a ngrok di avviarsi (10 secondi)
sleep 5

# Recupera l'IP pubblico di Ngrok dal tunnel
ngrok_ip=$(curl -s http://localhost:4040/api/tunnels | jq -r '.tunnels[0].public_url')

# Mostra l'IP pubblico di Ngrok
echo "Ngrok IP pubblico: $ngrok_ip"

# Il container continua a rimanere in esecuzione
