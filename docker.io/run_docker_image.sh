#!/bin/bash

if [ "$1" == "" ]
then
  echo "provide docker image to run"
  exit 1
fi

docker run -d -p 80:80 $1
