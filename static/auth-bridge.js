'use strict';
(()=>{
  const original=window.fetch.bind(window);
  const channel=typeof BroadcastChannel==='function'?new BroadcastChannel('chief-session-state'):null;
  let displayedPrincipal=null;
  let identity=null, refreshing=null, lastActivity=-Infinity, activityPending=false;
  const loginRoute=document.body.dataset.loginRoute||'/login';
  function lock(){document.body.replaceChildren();location.href=loginRoute+'?expired=1';}
  async function session(){
    if(!refreshing){
      refreshing=original('/api/auth/session',{cache:'no-store'}).then(async response=>{
        if(!response.ok){identity=null;lock();throw new Error('Sign-in required; no pending action was resubmitted.');}
        identity=await response.json();
        const principalKey=JSON.stringify([identity.principal.id,identity.principal.role,identity.principal.domains]);
        if(displayedPrincipal!==null && displayedPrincipal!==principalKey){
          document.body.replaceChildren();
          location.replace(loginRoute==='/work-login'?'/work':'/');
          throw new Error('Account changed; reload the authorized workspace. No action was resubmitted.');
        }
        displayedPrincipal=principalKey;
        if(identity.instance_mode==='preview' && !document.getElementById('instanceNotice')){
          const notice=document.createElement('small');notice.id='instanceNotice';notice.textContent='PREVIEW — isolated data; worker disabled';
          (document.querySelector('.topbar')||document.body).append(notice);
        }
        return identity;
      }).finally(()=>{refreshing=null;});
    }
    return refreshing;
  }
  // Broadcasts convey no credential/authority; the server verifies each tab.
  if(channel)channel.onmessage=()=>{identity=null;session().catch(()=>{});};
  window.fetch=async(input,options={})=>{
    const url=new URL(typeof input==='string'?input:input.url,location.href);
    if(url.origin!==location.origin)return original(input,options);
    const method=(options.method||(input instanceof Request?input.method:'GET')).toUpperCase();
    const mutation=!['GET','HEAD'].includes(method);
    const current=mutation||!identity?await session():identity;
    const headers=new Headers(options.headers||(input instanceof Request?input.headers:{}));if(mutation)headers.set('X-Chief-CSRF',current.csrf);
    const response=await original(input,{...options,headers});
    // Never automatically retry a mutation, including a stale-CSRF response.
    if(response.status===401)await session().catch(()=>{});
    if(response.status===428)location.href=loginRoute+'?reauth=1';
    if(response.ok && url.pathname==='/api/auth/reauthenticate'){identity=null;if(channel)channel.postMessage('changed');}
    return response;
  };
  async function interaction(event){
    if(!event.isTrusted || document.visibilityState!=='visible' || activityPending || performance.now()-lastActivity<15000)return;
    lastActivity=performance.now();activityPending=true;
    try{await window.fetch('/api/auth/activity',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});}
    catch{/* Network failure never queues an activity receipt for later replay. */}
    finally{activityPending=false;}
  }
  for(const event of ['pointerdown','keydown'])document.addEventListener(event,interaction,{capture:true,passive:true});
  document.addEventListener('DOMContentLoaded',()=>{const button=document.createElement('button');button.textContent='Sign out';button.type='button';button.onclick=async()=>{const response=await window.fetch('/api/auth/logout',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});if(response.ok){if(channel)channel.postMessage('signed-out');location.href=loginRoute;}};(document.querySelector('.topbar')||document.body).append(button);});
  session().catch(()=>{});
})();
