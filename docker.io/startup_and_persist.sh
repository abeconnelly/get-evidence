#!/bin/bash

service apache2 start
service mysql start

while [ true ]
do
  echo ping
  sleep 3
done
