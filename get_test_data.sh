#!/usr/bin/env bash
set -e

mkdir -p data

FILES=$(cat <<END
Dixon2012-J1-NcoI-R1-filtered.100kb.multires.cool
ctcf_known1_100.bed
END
)

for FILE in $FILES; do
  [ -e data/$FILE ] || wget -P data/ https://s3.amazonaws.com/pkerp/public/$FILE
done

