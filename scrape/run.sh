#!/bin/zsh
# usage: scrape/run.sh <channel-url-or-id> <out-dir> [name]
# Scrapes the full history of a Discord channel open in a Chrome tab -> <out-dir>/<name>.jsonl and .csv
set -e
dir=${0:A:h}
cid=${${1%%\?*}##*/}
[[ $cid == <-> ]] || { echo "not a channel URL or ID: $1"; exit 1; }
outdir=$2; mkdir -p $outdir
name=${3:-$($dir/tab.sh $cid $dir/name.js)}
echo "scraping channel $cid -> $outdir/$name.{jsonl,csv}"
$dir/tab.sh $cid $dir/scrape.js
$dir/poll.sh $outdir/$name.jsonl $cid
python3 -I $dir/to_csv.py $outdir/$name.jsonl $outdir/$name.csv
[[ $($dir/tab.sh $cid $dir/hitstart.js) == true ]] \
  || echo "WARNING: did not reach the start of the channel. Run again to continue; duplicates are removed."
