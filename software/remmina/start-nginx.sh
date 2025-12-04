docker run -d \
--net host \
 --name nginx-remmina  \
 -v  $PWD/conf:/etc/nginx/conf.d \
 nginx