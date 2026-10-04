"""Read-only archive coverage. Presence is never approval or proof of submission."""
import json


def object_json(value):
    try:
        result=json.loads(value or '{}')
        return result if isinstance(result,dict) else {}
    except (ValueError,TypeError):return {}


def summarize(detail,facts):
    snapshots=detail.get('snapshots') or []
    draft=object_json(detail.get('draft_json'))
    claims=draft.get('claims');claims=claims if isinstance(claims,list) else []
    fact_map={str(f['id']):f for f in facts}
    supported=0
    for claim in claims:
        if not isinstance(claim,dict):continue
        fact=fact_map.get(str(claim.get('fact_id')))
        if fact and fact.get('status')=='USER_CONFIRMED' and isinstance(fact.get('text'),str) and fact['text'].strip()==claim.get('text'):supported+=1
    receipt=object_json(detail.get('submission_receipt_json'))
    items=[]
    def item(key,label,present,note):items.append(dict(key=key,label=label,status='RECORDED' if present else 'NOT_RECORDED',note=note))
    item('source','Advert source link',bool(detail.get('job_url') or any(s.get('source_url') for s in snapshots)),'A link does not establish that the advert is still open.')
    item('cv','Saved CV content',any(bool(object_json(s.get('cv_snapshot_json'))) for s in snapshots),'Checks archived content, not merely a file path. Review the intended version before submitting.')
    item('letter','Saved cover letter',any(bool((s.get('cover_letter_text') or '').strip()) for s in snapshots),'Presence does not establish relevance or factual accuracy.')
    items.append(dict(key='claims',label='Referenced factual claims',status='NOT_RECORDED' if not claims else 'REVIEW_REQUIRED' if supported!=len(claims) else 'REFERENCES_MATCH',note=f'{supported} of {len(claims)} claim references match current Job user-confirmed facts. This is not submission approval.'))
    item('receipt','Stored submission receipt',bool(receipt),'A stored receipt still needs outcome review; an attempt or application status alone is not proof of delivery.')
    item('history','Application history',bool(detail.get('events')),'Existing application events and snapshots remain authoritative history.')
    return {'application_id':detail['id'],'items':items,'authority':'NONE','notice':'Record coverage only. Missing is not zero, and recorded does not mean verified. Existing evidence, Policy and human approval checks still apply.'}
