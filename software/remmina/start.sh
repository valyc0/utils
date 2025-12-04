docker run -d \
  --name=remmina \
  -e PUID=1000 \
  -e PGID=1000 \
  -e TZ=Europe/Rome \
  -p 3000:3000 \
  lscr.io/linuxserver/remmina
