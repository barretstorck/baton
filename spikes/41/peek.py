import json, sys, collections
c = collections.Counter()
for i, l in enumerate(open(sys.argv[1])):
    d = json.loads(l)
    c[d.get('type')] += 1
    if i < 5 or (d.get('type') == 'assistant' and c['assistant'] == 3):
        print(i, json.dumps(d)[:1000]); print()
print(c)
