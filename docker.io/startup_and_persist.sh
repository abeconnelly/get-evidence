#!/bin/bash

service apache2 start
service mysql start
/etc/init.d/genome-analyzer start

#echo "12 3 * * * $USER cd $HOME/get-evidence && make daily" | sudo tee /etc/cron.d/get-evidence

while [ true ]
do
  echo ping
  sleep 3
done
