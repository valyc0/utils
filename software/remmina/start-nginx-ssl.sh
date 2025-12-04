#!/bin/bash

docker run -d \
  --net host \
  --name nginx-remmina-ssl \
  -v $PWD/conf/nginx-ssl.conf:/etc/nginx/conf.d/default.conf \
  -v $PWD/ssl:/etc/nginx/ssl \
  -v $PWD/conf/.htpasswd:/etc/nginx/.htpasswd \
  nginx
