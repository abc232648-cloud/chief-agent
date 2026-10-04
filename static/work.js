"use strict";
let chiefContext=null;
async function api(path){const r=await fetch(path,{cache:'no-store'});const d=await r.json();if(!r.ok)throw new Error(d.reason||'Access unavailable.');return d;}
async function postJson(path,body){return farmPost(path,body);}
function canDomain(permission,domain){return !!chiefContext?.domains.find(d=>d.id===domain)?.permissions[permission];}
async function showWork(id){
  const allowed=new Set(['workHome']);
  if(canDomain('work.read','farming'))allowed.add('farmRecords');
  if(canDomain('work.read','jobs'))allowed.add('jobWork');
  if(chiefContext?.user_management.roles.length)allowed.add('users');
  if(!allowed.has(id))throw new Error('This page is outside your work responsibilities.');
  for(const s of document.querySelectorAll('#workContent>section'))s.classList.toggle('hidden',s.id!==id);
  if(id==='farmRecords'){
    await loadFarmRecords();
    const setup=await api('/api/farm/setup');
    document.getElementById('workRole').textContent=setup.farm_role.replaceAll('_',' ');
    document.getElementById('farmBalances').hidden=!setup.can_manage;
    document.getElementById('farmBalancesSection').hidden=!setup.can_manage;
    document.getElementById('farmHistory').parentElement.hidden=!setup.can_manage;
  }
  if(id==='users')await loadUsers();
  if(id==='jobWork'){
    const jobs=await api('/api/jobs'),target=document.getElementById('jobWorkRecords');target.replaceChildren();
    for(const job of jobs){const p=document.createElement('p');p.textContent=(job.title||'Job')+' · '+(job.company||'');target.append(p);}
    if(!target.children.length)target.textContent='No Job records are currently available.';
  }
}
document.addEventListener('DOMContentLoaded',async()=>{
  try{
    chiefContext=await api('/api/ui/context');
    document.getElementById('workTitle').onclick=()=>showWork('workHome').catch(e=>document.getElementById('workError').textContent=e.message);
    if(canDomain('work.read','farming')){document.title='Farm Agent';document.getElementById('workTitle').textContent='Farm Agent';document.querySelector('link[rel=manifest]').href='/static/farm.webmanifest';}
    const nav=document.getElementById('workNavigation');
    const choices=[];
    if(canDomain('work.read','farming'))choices.push(['farmRecords','Farm work']);
    if(canDomain('work.read','jobs'))choices.push(['jobWork','Job work']);
    if(chiefContext.user_management.roles.length)choices.push(['users','Manage Workers']);
    for(const [id,label] of choices){const b=document.createElement('button');b.type='button';b.textContent=label;b.onclick=()=>showWork(id).catch(e=>document.getElementById('workError').textContent=e.message);nav.append(b);}
    if(canDomain('work.read','farming')){const offline=document.createElement('button');offline.type='button';offline.textContent='Offline reports';offline.onclick=async()=>{try{if(!('serviceWorker' in navigator))throw new Error('This browser does not support offline reporting.');await navigator.serviceWorker.register('/farm-sw.js',{scope:'/work'});await navigator.serviceWorker.ready;location.href='/work?offline=1';}catch(error){document.getElementById('workError').textContent=error.message;}};nav.append(offline);}
    document.getElementById('workWelcome').textContent=choices.length?'Choose your work area.':'No work area is assigned. Ask your Owner to review your access.';
    await showWork(choices[0]?.[0]||'workHome');
  }catch(error){document.getElementById('workError').textContent=error.message;}
});
document.addEventListener('click',async event=>{
  const b=event.target.closest('button[data-action]');if(!b)return;
  const action=userActions(b)[b.dataset.action];if(!action)return;
  b.disabled=true;try{await action();}catch(error){document.getElementById('usersResult').textContent=error.message;}finally{b.disabled=false;}
});
