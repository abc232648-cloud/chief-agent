'use strict';
if(new URLSearchParams(location.search).has('expired'))document.getElementById('result').textContent='Your session ended. Unsaved changes were not submitted. Sign in, review them and submit explicitly.';
document.getElementById('sign-in').addEventListener('submit',async event=>{
  event.preventDefault();const reauth=new URLSearchParams(location.search).has('reauth');
  const headers={'Content-Type':'application/json'};
  const password=document.getElementById('password');
  try{
    if(reauth){const session=await fetch('/api/auth/session');if(!session.ok){location.href=document.body.dataset.loginRoute||'/login';return;}headers['X-Chief-CSRF']=(await session.json()).csrf;}
    const response=await fetch(reauth?'/api/auth/reauthenticate':'/api/auth/login',{method:'POST',headers,body:JSON.stringify({username:document.getElementById('username').value,password:password.value})});
    const result=await response.json();
    if(response.ok){
      if(typeof BroadcastChannel==='function'){const channel=new BroadcastChannel('chief-session-state');channel.postMessage('changed');channel.close();}
      location.href=document.body.dataset.nextRoute||'/';
    }else document.getElementById('result').textContent=result.reason||'Sign-in failed.';
  }catch{document.getElementById('result').textContent='Connection failed. No action was retried.';}
  finally{password.value='';}
});
