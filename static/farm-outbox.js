'use strict';
// Private report and media payloads are encrypted before persistence. Session
// tokens, passwords and encryption keys are never cached. Opaque profile/media
// identifiers remain only so IndexedDB can isolate each signed-in person's data.
window.FarmOutbox=(()=>{
  const DB='farm-private-outbox-v1',DB_VERSION=2,ITERATIONS=600000,MAX_REPORTS=100,MAX_MEDIA=20,MAX_MEDIA_PER_WORK=10,MAX_MEDIA_BASE64=(2*1024*1024*4/3+8);
  const encoder=new TextEncoder(),decoder=new TextDecoder();
  let key=null,profileId=null,timer=null,generation=0;
  const bytes=text=>Uint8Array.from(atob(text),c=>c.charCodeAt(0));
  const base64=data=>{let text='';for(const byte of new Uint8Array(data))text+=String.fromCharCode(byte);return btoa(text);};
  async function database(){return new Promise((resolve,reject)=>{
    const request=indexedDB.open(DB,DB_VERSION);
    request.onupgradeneeded=()=>{
      const db=request.result;
      if(!db.objectStoreNames.contains('profiles'))db.createObjectStore('profiles',{keyPath:'id'});
      if(!db.objectStoreNames.contains('media')){const media=db.createObjectStore('media',{keyPath:'key'});media.createIndex('owner','owner',{unique:false});}
    };
    request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(new Error('Private browser storage is unavailable.'));
  });}
  async function operation(storeName,mode,callback){
    const db=await database();
    try{return await new Promise((resolve,reject)=>{
      const tx=db.transaction(storeName,mode),request=callback(tx.objectStore(storeName));
      let value;request.onsuccess=()=>{value=request.result;};
      tx.oncomplete=()=>resolve(value);tx.onerror=tx.onabort=()=>reject(new Error('Private browser storage failed; pending work was not confirmed saved.'));
    });}finally{db.close();}
  }
  const get=id=>operation('profiles','readonly',store=>store.get(id));
  const put=value=>operation('profiles','readwrite',store=>store.put(value));
  const mediaKey=(owner,eventId)=>owner+':'+eventId;
  const getMedia=(owner,eventId)=>operation('media','readonly',store=>store.get(mediaKey(owner,eventId)));
  const putMedia=value=>operation('media','readwrite',store=>store.put(value));
  const deleteMedia=(owner,eventId)=>operation('media','readwrite',store=>store.delete(mediaKey(owner,eventId)));
  const mediaFor=owner=>operation('media','readonly',store=>store.index('owner').getAll(owner));
  async function exclusive(id,callback){
    if(!navigator.locks)throw new Error('This browser cannot safely coordinate offline reporting. Use online entry.');
    return navigator.locks.request('farm-outbox-'+id,callback);
  }
  function checkPhrase(phrase){if(typeof phrase!=='string'||phrase.length<16||phrase.length>200)throw new Error('Use an offline passphrase of 16–200 characters. It is separate from your sign-in password.');}
  async function derive(phrase,salt){
    checkPhrase(phrase);
    const material=await crypto.subtle.importKey('raw',encoder.encode(phrase),'PBKDF2',false,['deriveKey']);
    return crypto.subtle.deriveKey({name:'PBKDF2',hash:'SHA-256',salt:bytes(salt),iterations:ITERATIONS},material,{name:'AES-GCM',length:256},false,['encrypt','decrypt']);
  }
  async function encrypt(data,record,useKey){
    const iv=crypto.getRandomValues(new Uint8Array(12));
    const raw=await crypto.subtle.encrypt({name:'AES-GCM',iv,additionalData:encoder.encode(location.origin+'|'+record.id+'|1')},useKey,encoder.encode(JSON.stringify(data)));
    return {id:record.id,version:1,iterations:ITERATIONS,salt:record.salt,iv:base64(iv),ciphertext:base64(raw)};
  }
  async function decrypt(record,useKey){
    if(!record||record.version!==1||record.iterations!==ITERATIONS)throw new Error('Offline profile is unavailable or has an unsupported version.');
    try{
      const raw=await crypto.subtle.decrypt({name:'AES-GCM',iv:bytes(record.iv),additionalData:encoder.encode(location.origin+'|'+record.id+'|1')},useKey,bytes(record.ciphertext));
      const data=JSON.parse(decoder.decode(raw));
      if(data.identity!==record.id||!Array.isArray(data.pending)||!Array.isArray(data.work_items||[]))throw new Error();
      return data;
    }catch{throw new Error('The offline passphrase is incorrect or the encrypted profile is damaged.');}
  }
  async function encryptMedia(data,owner,eventId,useKey){
    const iv=crypto.getRandomValues(new Uint8Array(12));
    const raw=await crypto.subtle.encrypt({name:'AES-GCM',iv,additionalData:encoder.encode(location.origin+'|'+owner+'|'+eventId+'|media|1')},useKey,encoder.encode(JSON.stringify(data)));
    return {key:mediaKey(owner,eventId),owner,event_id:eventId,version:1,iv:base64(iv),ciphertext:base64(raw)};
  }
  async function decryptMedia(record,owner,useKey){
    if(!record||record.owner!==owner||record.version!==1)throw new Error('Offline media record is unavailable or unsupported.');
    try{
      const raw=await crypto.subtle.decrypt({name:'AES-GCM',iv:bytes(record.iv),additionalData:encoder.encode(location.origin+'|'+owner+'|'+record.event_id+'|media|1')},useKey,bytes(record.ciphertext));
      const data=JSON.parse(decoder.decode(raw));
      if(data.identity!==owner||data.payload?.event_id!==record.event_id)throw new Error();
      return data;
    }catch{throw new Error('Encrypted offline media is damaged or belongs to another profile.');}
  }
  function lock(){generation++;key=null;profileId=null;clearTimeout(timer);window.dispatchEvent(new Event('farm-outbox-locked'));}
  function touch(){clearTimeout(timer);timer=setTimeout(lock,10*60*1000);}
  function unlocked(){if(!key||!profileId)throw new Error('Unlock your offline profile first.');return {id:profileId,useKey:key};}
  async function session(){
    const response=await fetch('/api/auth/session',{cache:'no-store'});
    if(!response.ok)throw new Error('Sign in online before synchronizing. Pending work remains encrypted.');
    return response.json();
  }
  async function snapshot(){
    const [setupResponse,staffResponse,taskResponse]=await Promise.all([
      fetch('/api/farm/setup',{cache:'no-store'}),fetch('/api/farm/staff',{cache:'no-store'}),fetch('/api/farm/tasks',{cache:'no-store'})
    ]);
    if(!setupResponse.ok||!staffResponse.ok||!taskResponse.ok)throw new Error('Farm access is required before refreshing offline work.');
    const setup=await setupResponse.json(),staff=await staffResponse.json(),taskData=await taskResponse.json();
    const seen=new Set(),work_items=[];
    for(const item of staff.items||[]){if(!seen.has(item.id)){seen.add(item.id);work_items.push({id:item.id,label:item.text||item.kind||'Farm work',state:item.state||''});}}
    for(const task of taskData.tasks||[]){if(!seen.has(task.task_id)){seen.add(task.task_id);work_items.push({id:task.task_id,label:task.title||'Farm task',state:task.status||''});}}
    return {entities:setup.entities||[],work_items};
  }
  async function provision(phrase){
    checkPhrase(phrase);lock();const start=generation;const current=await session();const farm=await snapshot(),id=current.principal.id;
    return exclusive(id,async()=>{
      if(await get(id))throw new Error('An offline profile already exists for this account. Unlock it instead.');
      const record={id,salt:base64(crypto.getRandomValues(new Uint8Array(16)))};
      const useKey=await derive(phrase,record.salt);
      const data={identity:id,cached_at:new Date().toISOString(),entities:farm.entities,work_items:farm.work_items,pending:[],last_sync:null};
      await put(await encrypt(data,record,useKey));
      localStorage.setItem('farm-outbox-present-'+id,'1');if(generation!==start||document.hidden)throw new Error('Offline profile created but locked. Unlock it when ready.');key=useKey;profileId=id;touch();
      const persistent=navigator.storage?.persist?await navigator.storage.persist():false;
      return {id,persistent};
    });
  }
  async function unlock(id,phrase){
    lock();const start=generation;const record=await get(id);if(!record)throw new Error('Offline data is missing. Browser storage may have been cleared.');
    const useKey=await derive(phrase,record.salt);const data=await decrypt(record,useKey);
    if(!Array.isArray(data.work_items)){data.work_items=[];await put(await encrypt(data,record,useKey));}
    if(generation!==start||document.hidden)throw new Error('Unlock was interrupted. Try again.');
    key=useKey;profileId=id;touch();return view();
  }
  async function view(){const {id,useKey}=unlocked();const data=await decrypt(await get(id),useKey);if(key!==useKey||profileId!==id)throw new Error('Offline workspace is locked.');return data;}
  async function change(callback){
    const {id,useKey}=unlocked();
    return exclusive(id,async()=>{
      const record=await get(id),data=await decrypt(record,useKey);
      const result=await callback(data);await put(await encrypt(data,record,useKey));return result;
    });
  }
  async function queue(payload){
    const allowed=['birds_arrived','birds_departed','mortality','eggs_collected','eggs_dispatched','eggs_lost','feed_received','feed_used'];
    if(!payload||!allowed.includes(payload.kind)||payload.corrects!==null||payload.reason!==''||!payload.entity_id)throw new Error('Offline reporting supports observations only, using registered locations. No approvals, opening balances or corrections.');
    return change(data=>{
      const age=Date.now()-Date.parse(data.cached_at);
      if(age<0||age>7*86400000)throw new Error('Refresh this profile online before adding reports; its location list is older than seven days or the clock changed. Existing reports are retained.');
      if(data.pending.length>=MAX_REPORTS)throw new Error('Sync or review the 100 pending reports before adding more.');
      if(!data.entities.some(e=>e.entity_id===payload.entity_id))throw new Error('Unknown offline location.');
      if(data.pending.some(r=>r.payload.event_id===payload.event_id))throw new Error('Report identifier already queued.');
      data.pending.push({payload,status:'PENDING',queued_at:new Date().toISOString()});return payload.event_id;
    });
  }
  async function mediaView(){
    const {id,useKey}=unlocked(),records=await mediaFor(id),items=[];
    for(const record of records)items.push(await decryptMedia(record,id,useKey));
    items.sort((a,b)=>a.queued_at.localeCompare(b.queued_at));return items;
  }
  async function queueMedia(payload){
    const {id,useKey}=unlocked();
    if(!payload||Object.keys(payload).sort().join(',')!=='event_id,image_base64,work_id')throw new Error('Supply exactly one pending photo and its work item.');
    if(!/^[A-Za-z0-9_-]{8,80}$/.test(payload.event_id)||!/^[A-Za-z0-9_-]{8,80}$/.test(payload.work_id))throw new Error('Invalid pending photo identifier.');
    if(typeof payload.image_base64!=='string'||payload.image_base64.length<12||payload.image_base64.length>MAX_MEDIA_BASE64)throw new Error('Pending photo exceeds the encrypted 2 MiB limit.');
    return exclusive(id,async()=>{
      const profile=await decrypt(await get(id),useKey),age=Date.now()-Date.parse(profile.cached_at);
      if(age<0||age>7*86400000)throw new Error('Refresh this profile online before attaching offline photos; its work list is older than seven days.');
      if(!profile.work_items.some(item=>item.id===payload.work_id))throw new Error('This work item is not in the current offline assignment list.');
      const current=await mediaFor(id);
      if(current.length>=MAX_MEDIA)throw new Error('Sync or review the 20 pending photos before adding more.');
      let forWork=0;for(const record of current){const item=await decryptMedia(record,id,useKey);if(item.payload.work_id===payload.work_id)forWork++;}
      if(forWork>=MAX_MEDIA_PER_WORK)throw new Error('This work item already has 10 pending photos.');
      if(await getMedia(id,payload.event_id))throw new Error('Photo identifier already queued.');
      const item={identity:id,payload,status:'PENDING',queued_at:new Date().toISOString(),receipt:null,reason:null};
      await putMedia(await encryptMedia(item,id,payload.event_id,useKey));return payload.event_id;
    });
  }
  async function sync(){
    const {id,useKey}=unlocked();
    return exclusive(id,async()=>{
      const current=await session();if(current.principal.id!==id)throw new Error('A different person is signed in. No reports were sent.');
      let record=await get(id),data=await decrypt(record,useKey);let sent=0;
      for(const item of [...data.pending]){
        if(item.status==='CONFLICT')continue;
        if(key!==useKey||profileId!==id)break;
        item.status='SENDING';await put(await encrypt(data,record,useKey));
        let response;
        try{response=await fetch('/api/farm/journal',{method:'POST',cache:'no-store',headers:{'Content-Type':'application/json','X-Chief-CSRF':current.csrf},body:JSON.stringify(item.payload)});}
        catch{throw new Error('Connection interrupted. The same report IDs will be reused on your next explicit sync.');}
        if(response.status===401||response.status===403||response.status===428)throw new Error('Current access or reauthentication is required. Unsent reports remain encrypted.');
        if(response.ok){
          const result=await response.json();
          if(!['RECORDED','ALREADY_RECORDED'].includes(result.status))throw new Error('Unrecognized response; report retained for review.');
          data.pending=data.pending.filter(r=>r.payload.event_id!==item.payload.event_id);sent++;
        }else if(response.status>=400&&response.status<500){item.status='CONFLICT';item.reason='Server rejected this report. Check its values online before entering a replacement.';}
        else throw new Error('Server outcome is unknown. Report retained with its original ID.');
        await put(await encrypt(data,record,useKey));
      }
      data.last_sync=new Date().toISOString();await put(await encrypt(data,record,useKey));return {sent,pending:data.pending.length};
    });
  }
  function receiptValid(record,payload){return !!record&&record.id===payload.event_id&&record.work_id===payload.work_id&&/^[a-f0-9]{64}$/.test(record.sha256||'')&&Number.isInteger(record.bytes)&&record.bytes>0&&record.bytes<=2*1024*1024;}
  async function authoritativePhoto(item){
    const response=await fetch('/api/farm/photos?work_id='+encodeURIComponent(item.payload.work_id),{cache:'no-store'});
    if(response.status===401||response.status===403||response.status===428)throw new Error('Current access or reauthentication is required. Pending photos remain encrypted.');
    if(!response.ok)throw new Error('Authoritative photo state is unavailable. Pending photo retained for reconciliation.');
    const listing=await response.json();if(!Array.isArray(listing))throw new Error('Unrecognized authoritative photo response. Pending photo retained.');
    const match=listing.find(record=>record.id===item.payload.event_id);
    if(!match)return {matched:false,conflict:false};
    if(!item.receipt||!receiptValid(match,item.payload)||match.sha256!==item.receipt.sha256||match.bytes!==item.receipt.bytes)return {matched:false,conflict:true};
    return {matched:true,conflict:false};
  }
  async function syncMedia(){
    const {id,useKey}=unlocked();
    return exclusive(id,async()=>{
      const current=await session();if(current.principal.id!==id)throw new Error('A different person is signed in. No photos were sent.');
      let reconciled=0;
      for(const encrypted of await mediaFor(id)){
        if(key!==useKey||profileId!==id)break;
        let item=await decryptMedia(encrypted,id,useKey);if(item.status==='CONFLICT')continue;
        if(item.receipt){
          const check=await authoritativePhoto(item);
          if(check.matched){await deleteMedia(id,item.payload.event_id);reconciled++;continue;}
          if(check.conflict){item.status='CONFLICT';item.reason='Chief photo metadata no longer matches the upload receipt. Review online; nothing was discarded.';await putMedia(await encryptMedia(item,id,item.payload.event_id,useKey));continue;}
          item.status='RECONCILE';item.reason='Chief has not yet returned this accepted photo in the authoritative listing. It remains encrypted here.';await putMedia(await encryptMedia(item,id,item.payload.event_id,useKey));continue;
        }
        item.status='SENDING';item.reason=null;await putMedia(await encryptMedia(item,id,item.payload.event_id,useKey));
        let response;
        try{response=await fetch('/api/farm/photos',{method:'POST',cache:'no-store',headers:{'Content-Type':'application/json','X-Chief-CSRF':current.csrf},body:JSON.stringify(item.payload)});}
        catch{item.status='PENDING';item.reason='Connection interrupted; the same photo ID will be reused.';await putMedia(await encryptMedia(item,id,item.payload.event_id,useKey));throw new Error('Photo upload connection interrupted. The encrypted photo was retained with its original ID.');}
        if(response.status===401||response.status===403||response.status===428){item.status='PENDING';await putMedia(await encryptMedia(item,id,item.payload.event_id,useKey));throw new Error('Current access or reauthentication is required. Pending photos remain encrypted.');}
        if(response.ok){
          const result=await response.json();
          if(!['RECORDED','ALREADY_RECORDED'].includes(result.status)||!receiptValid(result.record,item.payload)){item.status='RECONCILE';item.reason='Upload response was not a valid Chief photo receipt. The encrypted photo was retained.';await putMedia(await encryptMedia(item,id,item.payload.event_id,useKey));continue;}
          item.receipt=result.record;item.status='RECONCILE';item.reason='Uploaded; waiting for an independent authoritative listing check.';await putMedia(await encryptMedia(item,id,item.payload.event_id,useKey));
          const check=await authoritativePhoto(item);
          if(check.matched){await deleteMedia(id,item.payload.event_id);reconciled++;}
          else{item.status=check.conflict?'CONFLICT':'RECONCILE';item.reason=check.conflict?'Chief listing conflicts with the upload receipt. Nothing was discarded.':'Uploaded but not yet visible in Chief authoritative state. Photo retained.';await putMedia(await encryptMedia(item,id,item.payload.event_id,useKey));}
        }else if(response.status>=400&&response.status<500){item.status='CONFLICT';item.reason='Chief rejected this photo. Review the work assignment or image online before replacing it.';await putMedia(await encryptMedia(item,id,item.payload.event_id,useKey));}
        else{item.status='PENDING';item.reason='Chief outcome is unknown. The photo was retained with its original ID.';await putMedia(await encryptMedia(item,id,item.payload.event_id,useKey));throw new Error('Chief photo outcome is unknown. Pending photo retained.');}
      }
      return {reconciled,pending:(await mediaFor(id)).length};
    });
  }
  async function refresh(){
    const current=await session(),{id}=unlocked();if(current.principal.id!==id)throw new Error('Sign in as the owner of this offline profile.');
    const farm=await snapshot();return change(profile=>{profile.entities=farm.entities;profile.work_items=farm.work_items;profile.cached_at=new Date().toISOString();});
  }
  async function discard(eventId){return change(data=>{data.pending=data.pending.filter(r=>r.payload.event_id!==eventId);});}
  async function discardMedia(eventId){const {id}=unlocked();await deleteMedia(id,eventId);}
  async function erase(){
    const {id}=unlocked();await exclusive(id,async()=>{for(const record of await mediaFor(id))await deleteMedia(id,record.event_id);await operation('profiles','readwrite',store=>store.delete(id));});
    localStorage.removeItem('farm-outbox-present-'+id);lock();
  }
  async function profiles(){return (await operation('profiles','readonly',store=>store.getAllKeys())).map(id=>({id,label:'Offline workspace '+id.slice(0,8)}));}
  for(const type of ['pointerdown','keydown'])document.addEventListener(type,event=>{if(event.isTrusted&&key)touch();},{capture:true,passive:true});
  document.addEventListener('visibilitychange',()=>{if(document.hidden)lock();});
  if(typeof BroadcastChannel==='function'){const channel=new BroadcastChannel('chief-session-state');channel.onmessage=lock;}
  return {provision,unlock,view,queue,mediaView,queueMedia,sync,syncMedia,refresh,discard,discardMedia,erase,profiles,lock};
})();
