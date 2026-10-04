"""Shared current-document checks for preparation and final submission."""
from browser.request_policy import BrowserPolicyBlocked,origin
import json
import uuid

ATTRIBUTES=('type','name','id','placeholder','autocomplete','aria-label','form','formaction')


def install_writer(context):
    """Capture native DOM operations before untrusted scripts, then check+write atomically."""
    name='__chief_write_'+uuid.uuid4().hex
    script=r'''(()=>{
      const doc=document, apply=Reflect.apply, descriptor=Object.getOwnPropertyDescriptor;
      const get=(prototype,name)=>descriptor(prototype,name).get;
      const attr=Element.prototype.getAttribute, tag=get(Element.prototype,'tagName'), matches=Element.prototype.matches;
      const connected=get(Node.prototype,'isConnected'), owner=get(Node.prototype,'ownerDocument');
      const docURL=get(Document.prototype,'URL'), text=get(Node.prototype,'textContent');
      const rects=Element.prototype.getClientRects, style=window.getComputedStyle;
      const css=CSSStyleDeclaration.prototype.getPropertyValue;
      const specs={INPUT:{type:get(HTMLInputElement.prototype,'type'),disabled:get(HTMLInputElement.prototype,'disabled'),readOnly:get(HTMLInputElement.prototype,'readOnly'),labels:get(HTMLInputElement.prototype,'labels'),set:descriptor(HTMLInputElement.prototype,'value').set},
                   TEXTAREA:{disabled:get(HTMLTextAreaElement.prototype,'disabled'),readOnly:get(HTMLTextAreaElement.prototype,'readOnly'),labels:get(HTMLTextAreaElement.prototype,'labels'),set:descriptor(HTMLTextAreaElement.prototype,'value').set}};
      const dispatch=EventTarget.prototype.dispatchEvent, NativeEvent=Event;
      const define=Object.defineProperty, keys=Object.keys;
      define(window,__NAME__,{configurable:false,writable:false,value:(node,request)=>{
        if(!apply(connected,node,[])||apply(owner,node,[])!==doc||apply(docURL,doc,[])!==request.url)return false;
        const kind=apply(tag,node,[]),spec=specs[kind];if(!spec)return false;
        const actual=kind==='TEXTAREA'?'textarea':apply(spec.type,node,[]);
        if(actual!==request.type||apply(spec.disabled,node,[])||apply(matches,node,[':disabled'])||apply(spec.readOnly,node,[]))return false;
        const computed=apply(style,window,[node]);
        if(!apply(rects,node,[]).length||apply(css,computed,['visibility'])==='hidden'||apply(css,computed,['display'])==='none')return false;
        const names=keys(request.attrs);
        for(let i=0;i<names.length;i++)if((apply(attr,node,[names[i]])||'')!==request.attrs[names[i]])return false;
        const labels=apply(spec.labels,node,[]);let label='';
        for(let i=0;labels&&i<labels.length;i++)label+=(i?' ':'')+apply(text,labels[i],[]);
        if(label!==request.labels)return false;
        apply(spec.set,node,[request.value]);
        apply(dispatch,node,[new NativeEvent('input',{bubbles:true})]);
        apply(dispatch,node,[new NativeEvent('change',{bubbles:true})]);
        return true;
      }});
    })();'''
    context.add_init_script(script=script.replace('__NAME__',json.dumps(name)))
    return name


def checked_fields(page,url,fields,safe_field):
    if origin(page.url)!=origin(url):raise BrowserPolicyBlocked('The current page left the approved origin.')
    if page.url.split('#',1)[0]!=url.split('#',1)[0]:raise BrowserPolicyBlocked('The current document URL differs from the approved action.')
    nodes=[];metadata=[]
    for field in fields:
        selector=str(field.get('selector') or '')
        if not selector or page.locator(selector).count()!=1:
            raise BrowserPolicyBlocked('Each reviewed field must uniquely match the current page.')
        node=page.locator(selector).element_handle()
        if node.owner_frame()!=page.main_frame:raise BrowserPolicyBlocked('Only the reviewed main document may receive values.')
        tag=node.evaluate('e=>e.tagName.toLowerCase()')
        attrs={name:node.get_attribute(name) or '' for name in ATTRIBUTES}
        actual_type=attrs['type'].lower() or ('textarea' if tag=='textarea' else 'text')
        labels=node.evaluate('e=>Array.from(e.labels||[]).map(l=>l.textContent).join(" ")')
        described=' '.join((str(field.get('label','')),labels,*attrs.values()))
        if (tag not in {'input','textarea'} or not safe_field(described,actual_type)
                or actual_type!=str(field.get('input_type','text')).lower()
                or not node.is_visible() or not node.is_enabled() or not node.is_editable()):
            raise BrowserPolicyBlocked('The actual field is sensitive, hidden, disabled or differs from its review.')
        form=node.evaluate('e=>e.form?{action:e.form.action,method:e.form.method,target:e.form.target,id:e.form.id}:null')
        if form and (origin(form['action'])!=origin(url) or form['target'] not in {'','_self'}):
            raise BrowserPolicyBlocked('The form targets an unapproved origin or browsing context.')
        nodes.append(node);metadata.append((tag,attrs,form,labels))
    return nodes,metadata


def fill_reviewed(page,context,policy,url,fields,safe_field,*,writer,revalidate=None):
    # Before any private value is exposed to page scripts, deny all page network.
    policy.phase='FILL';context.set_offline(True)
    nodes,metadata=checked_fields(page,url,fields,safe_field)
    initial_url=page.url
    for index,(field,node) in enumerate(zip(fields,nodes)):
        if revalidate:revalidate()
        if page.url!=initial_url:raise BrowserPolicyBlocked('Document navigation invalidated the reviewed form.')
        current,current_metadata=checked_fields(page,url,fields,safe_field)
        if current_metadata!=metadata or not node.evaluate('(e,other)=>e===other',current[index]):
            raise BrowserPolicyBlocked('The inspected form changed before filling.')
        # A stable element handle cannot silently rebind to a different matching node.
        packet={'name':writer,'url':initial_url,'type':str(field.get('input_type','text')).lower(),
                'attrs':metadata[index][1],'labels':metadata[index][3],'value':str(field['value'])}
        if not node.evaluate('(node,p)=>window[p.name](node,p)',packet):
            raise BrowserPolicyBlocked('The actual field changed at the moment of writing.')
        if page.url!=initial_url:raise BrowserPolicyBlocked('Document navigation invalidated form preparation.')
    _,final_metadata=checked_fields(page,url,fields,safe_field)
    if final_metadata!=metadata or any(node.input_value()!=str(field['value']) for node,field in zip(nodes,fields)):
        raise BrowserPolicyBlocked('The form changed while values were being prepared.')
