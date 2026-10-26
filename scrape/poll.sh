#!/bin/zsh
# usage: poll.sh <out.jsonl> <channel_id> — saves scraped messages from the tab to disk every 10 s until the scrape ends
# prints: total messages, new this poll, oldest new timestamp, done/START flags, channel path
dir=${0:A:h}; out=$1
while true; do
  r=$($dir/tab.sh $2 $dir/drain.js) || { echo "chrome error; retrying"; sleep 10; continue; }
  print -r -- "$r" | python3 -I -c "
import json,sys; d=json.load(sys.stdin)
with open(sys.argv[1],'a') as f:
    for m in d['q']: f.write(json.dumps(m)+'\n')
print(d['n'], len(d['q']), min([m['ts'] for m in d['q']] or ['-']), 'done' if d['done'] else '', 'START' if d['hitStart'] else '', d['url'])
" $out
  [[ $r == *'"done":true'* ]] && break
  sleep 10
done
