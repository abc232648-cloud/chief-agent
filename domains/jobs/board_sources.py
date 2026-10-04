"""Pure adapters for captured official board responses. No network or submission."""
import hashlib
import json
from html.parser import HTMLParser
from urllib.parse import urlsplit
from operations.time_integrity import aware_utc, utc_now, utc_text
from skills.job_ranking import canonicalize_url, dedupe_key


class Text(HTMLParser):
    def __init__(self):super().__init__(convert_charrefs=True);self.parts=[];self.hidden=0
    def handle_starttag(self,tag,attrs):
        if tag in {'script','style'}:self.hidden+=1
        elif tag in {'p','br','li','div'}:self.parts.append('\n')
    def handle_endtag(self,tag):
        if tag in {'script','style'}:self.hidden=max(0,self.hidden-1)
    def handle_data(self,data):
        if not self.hidden:self.parts.append(data)


def plain(value):
    if not isinstance(value,str) or len(value)>200000:raise ValueError('Invalid or oversized listing text.')
    parser=Text();parser.feed(value);return ''.join(parser.parts).strip()


def normalize(provider,board,payload,*,fetched_at):
    if provider not in {'greenhouse','lever'}:raise ValueError('Unsupported job provider.')
    if not isinstance(board,str) or not board or len(board)>100 or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in board):raise ValueError('Invalid employer board identifier.')
    fetched=aware_utc(fetched_at)
    if fetched>utc_now():raise ValueError('Fetch timestamp cannot be in the future.')
    entries=payload.get('jobs') if provider=='greenhouse' and isinstance(payload,dict) else payload if provider=='lever' else None
    if not isinstance(entries,list) or len(entries)>10000:raise ValueError('Expected a bounded listing response.')
    output=[];seen=set()
    for item in entries:
        if not isinstance(item,dict):raise ValueError('Malformed listing.')
        identity=item.get('id')
        if type(identity) not in {str,int} or not str(identity) or len(str(identity))>200:raise ValueError('Listing needs a provider ID.')
        key=provider+':'+board+':'+str(identity)
        if key in seen:raise ValueError('Duplicate provider identity in one response.')
        seen.add(key)
        title=item.get('title' if provider=='greenhouse' else 'text');url=item.get('absolute_url' if provider=='greenhouse' else 'hostedUrl')
        if not isinstance(title,str) or not title.strip() or len(title)>1000 or not isinstance(url,str) or len(url)>4096:raise ValueError('Listing title and URL required.')
        parsed=urlsplit(url)
        if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password:raise ValueError('Listing URL must use HTTPS without embedded credentials.')
        location=item.get('location',{}).get('name') if provider=='greenhouse' and isinstance(item.get('location'),dict) else item.get('categories',{}).get('location') if provider=='lever' and isinstance(item.get('categories'),dict) else None
        description=plain(item.get('content','') if provider=='greenhouse' else item.get('descriptionPlain',item.get('description','')))
        if provider=='lever':
            sections=item.get('lists',[])
            if not isinstance(sections,list) or len(sections)>100:raise ValueError('Malformed listing sections.')
            for section in sections:
                if not isinstance(section,dict):raise ValueError('Malformed listing section.')
                description+='\n'+plain(section.get('text',''))+'\n'+plain(section.get('content',''))
        if len(description)>250000:raise ValueError('Listing description exceeds capacity.')
        if location is not None and (not isinstance(location,str) or len(location)>1000):raise ValueError('Malformed location.')
        job={'title':plain(title),'company':board,'location':location,'description':description,'url':canonicalize_url(url),'source_key':key,'provider':provider,'board':board,'provider_id':str(identity)}
        job['content_sha256']=hashlib.sha256(json.dumps(job,sort_keys=True).encode()).hexdigest()
        job.update(fetched_at=utc_text(fetched),dedupe_key=dedupe_key(job),source_status='LISTED',authority='NONE')
        output.append(job)
    return output


def compare(previous,current,*,complete=False):
    """Absence from a complete snapshot is NOT_LISTED, never proof of closure."""
    if type(complete)is not bool:raise ValueError('Snapshot completeness must be explicit.')
    all_rows=previous+current
    scopes={(r['provider'],r['board']) for r in all_rows}
    if len(scopes)>1:raise ValueError('Compare one provider/employer board at a time.')
    old={r['source_key']:r for r in previous};new={r['source_key']:r for r in current}
    if len(old)!=len(previous) or len(new)!=len(current):raise ValueError('Duplicate snapshot identities.')
    result=[]
    for key,row in new.items():
        prior=old.get(key)
        if prior and aware_utc(row['fetched_at'])<aware_utc(prior['fetched_at']):raise ValueError('Older fetch cannot replace a newer observation.')
        result.append({'source_key':key,'change':'NEW' if prior is None else 'UNCHANGED' if prior['content_sha256']==row['content_sha256'] else 'CHANGED'})
    for key in old.keys()-new.keys():result.append({'source_key':key,'change':'NOT_LISTED' if complete else 'NOT_OBSERVED_IN_PARTIAL_RESPONSE'})
    return result
