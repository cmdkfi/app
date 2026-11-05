"""Founder-specified agreement policy, 28 Oct 2026; offline evaluation only.

Supporting items must be visible and cited, using the frozen Step 1 support
labels and source fields. Retractions only affect earlier claims of that source.
Screen freshness runs from sample time, including the exact 30-minute boundary.
"""
from datetime import datetime

def gate(task, verdict, items, fires):
    now=datetime.fromisoformat(task['t'].replace('Z','+00:00'))
    imap={i['id']:i for i in items}
    available=[i for i in items if i['t']<=now]
    retracted=set()
    for retraction in available:
        for rid in retraction.get('retracts',[]):
            prior=imap.get(rid)
            if prior and prior['source']==retraction['source'] and prior['t']<=retraction['t']:
                retracted.add(rid)
    citations={task['oid_map'].get(x) for x in verdict.get('source_ids',[])}
    support=[imap[i] for i in task['shown'] if imap[i]['src']!='onchain'
             and imap[i]['support'] and i in citations and i not in retracted]
    sources=sorted({i['source'] for i in support})
    fresh=[f for f in fires if imap[f['item']]['t']<=now
           and 0<=(now-f['t']).total_seconds()<=1800]
    stale=[f for f in fires if imap[f['item']]['t']<=now
           and (now-f['t']).total_seconds()>1800]
    return dict(passes=bool(fresh) or len(sources)>=2,onchain_fired=bool(fresh),
        supporting_sources=sources,supporting_items=[i['id'] for i in support],
        screen_items=sorted({f['item'] for f in fresh}),retracted_items=sorted(retracted),
        fresh_fires=[dict(item=f['item'],sample_time=f['t'].isoformat(),age_s=(now-f['t']).total_seconds()) for f in fresh],
        stale_screen_items=sorted({f['item'] for f in stale}),freshness_s=1800)
