'use strict';
// Only encrypted records are persisted. No session token, password, key or API
// response is cached. Offline possession never grants current server authority.
window.FarmOutbox=(()=>{
  const DB='farm-private-outbox-v1',ITERATIONS=600000,MAX_REPORTS=100;
  const encoder=new TextEncoder(),decoder=new TextDecoder();
  let key=null,profileId=null,timer=null,generation=0;
  const bytes=text=>Uint8Array.from(atob(text),c=>c.charCodeAt(0));
  const base64=data=>btoa(String.fromCharCode(...new Uint8Array(data)));
  async function database(){return new Promise((resolve,reject)=>{
    const request=indexedDB.open(DB,1);
    request.onupgradeneeded=()=>request.result.createObjectStore('profiles',{keyPath:'id'});
    request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(new Error('Private browser storage is unavailable.'));
  });}
  async function operation(mode,callback){
    const db=await database();
    try{return await new Promise((resolve,reject)=>{
      const tx=db.transaction('profiles',mode),request=callback(tx.objectStore('profiles'));
      let value;request.onsuccess=()=>{value=request.result;};
      tx.oncomplete=()=>resolve(value);tx.onerror=tx.onabort=()=>reject(new Error('Private browser storage failed; the report was not confirmed saved.'));
    });}finally{db.close();}
  }
  const get=id=>operation('readonly',store=>store.get(id));
  const put=value=>operation('readwrite',store=>store.put(value));
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
    // Avoid large argument lists for encrypted photo-free report buffers.
    let text='';for(const byte of new Uint8Array(raw))text+=String.fromCharCode(byte);
    return {id:record.id,version:1,iterations:ITERATIONS,salt:record.salt,iv:base64(iv),ciphertext:btoa(text)};
  }
  async function decrypt(record,useKey){
    if(!record||record.version!==1||record.iterations!==ITERATIONS)throw new Error('Offline profile is unavailable or has an unsupported version.');
    try{
      const raw=await crypto.subtle.decrypt({name:'AES-GCM',iv:bytes(record.iv),additionalData:encoder.encode(location.origin+'|'+record.id+'|1')},useKey,bytes(record.ciphertext));
      const data=JSON.parse(decoder.decode(raw));
      if(data.identity!==record.id||!Array.isArray(data.pending))throw new Error();
      return data;
    }catch{throw new Error('The offline passphrase is incorrect or the encrypted profile is damaged.');}
  }
  function lock(){generation++;key=null;profileId=null;clearTimeout(timer);window.dispatchEvent(new Event('farm-outbox-locked'));}
  function touch(){clearTimeout(timer);timer=setTimeout(lock,10*60*1000);}
  function unlocked(){if(!key||!profileId)throw new Error('Unlock your offline profile first.');return {id:profileId,useKey:key};}
  async function session(){
    const response=await fetch('/api/auth/session',{cache:'no-store'});
    if(!response.ok)throw new Error('Sign in online before synchronizing. Pending reports remain encrypted.');
    return response.json();
  }
  async function provision(phrase){
    checkPhrase(phrase);lock();const start=generation;const current=await session();
    const response=await fetch('/api/farm/setup',{cache:'no-store'});if(!response.ok)throw new Error('Farm access is required.');
    const setup=await response.json(),id=current.principal.id;
    return exclusive(id,async()=>{
      if(await get(id))throw new Error('An offline profile already exists for this account. Unlock it instead.');
      const record={id,salt:base64(crypto.getRandomValues(new Uint8Array(16)))};
      const useKey=await derive(phrase,record.salt);
      const data={identity:id,cached_at:new Date().toISOString(),entities:setup.entities,pending:[],last_sync:null};
      await put(await encrypt(data,record,useKey));
      localStorage.setItem('farm-outbox-present-'+id,'1');if(generation!==start||document.hidden)throw new Error('Offline profile created but locked. Unlock it when ready.');key=useKey;profileId=id;touch();
      const persistent=navigator.storage?.persist?await navigator.storage.persist():false;
      return {id,persistent};
    });
  }
  async function unlock(id,phrase){
    lock();const start=generation;const record=await get(id);if(!record)throw new Error('Offline data is missing. Browser storage may have been cleared.');
    const useKey=await derive(phrase,record.salt);await decrypt(record,useKey);
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
  async function refresh(){
    const current=await session(),{id}=unlocked();if(current.principal.id!==id)throw new Error('Sign in as the owner of this offline profile.');
    const response=await fetch('/api/farm/setup',{cache:'no-store'});if(!response.ok)throw new Error('Farm access unavailable.');
    const data=await response.json();return change(profile=>{profile.entities=data.entities;profile.cached_at=new Date().toISOString();});
  }
  async function discard(eventId){return change(data=>{data.pending=data.pending.filter(r=>r.payload.event_id!==eventId);});}
  async function erase(){const {id}=unlocked();await exclusive(id,()=>operation('readwrite',store=>store.delete(id)));localStorage.removeItem('farm-outbox-present-'+id);lock();}
  async function profiles(){return (await operation('readonly',store=>store.getAllKeys())).map(id=>({id,label:'Offline workspace '+id.slice(0,8)}));}
  for(const type of ['pointerdown','keydown'])document.addEventListener(type,event=>{if(event.isTrusted&&key)touch();},{capture:true,passive:true});
  document.addEventListener('visibilitychange',()=>{if(document.hidden)lock();});
  if(typeof BroadcastChannel==='function'){const channel=new BroadcastChannel('chief-session-state');channel.onmessage=lock;}
  return {provision,unlock,view,queue,sync,refresh,discard,erase,profiles,lock};
})();
