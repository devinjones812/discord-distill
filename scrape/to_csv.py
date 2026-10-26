import csv, json, sys
# usage: to_csv.py <in.jsonl> <out.csv>
msgs = {}
for line in open(sys.argv[1]):
    m = json.loads(line); msgs[m['id']] = m
msgs = sorted(msgs.values(), key=lambda m: int(m['id']))
last = ''
for m in msgs:  # grouped follow-up messages have no author header
    m['author'] = m['author'] or last
    last = m['author']
cols = ['id', 'ts', 'author', 'content', 'reply_to', 'reply_to_id', 'edited', 'attachments', 'embed_text']
with open(sys.argv[2], 'w', newline='', encoding='utf-8') as f:
    w = csv.DictWriter(f, fieldnames=cols, extrasaction='ignore')
    w.writeheader(); w.writerows(msgs)
print(f'{len(msgs)} messages -> {sys.argv[2]}' + (f'  ({msgs[0]["ts"]} .. {msgs[-1]["ts"]})' if msgs else ''))
