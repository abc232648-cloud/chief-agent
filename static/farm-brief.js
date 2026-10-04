'use strict';
let farmBriefLoading=false;
function briefText(parent,tag,text){const node=document.createElement(tag);node.textContent=text;parent.append(node);return node;}
async function renderFarmBrief(){
  if(farmBriefLoading)return;
  farmBriefLoading=true;
  try{
    let card=document.getElementById('farmBriefCard');
    if(!card){
      card=document.createElement('div');card.id='farmBriefCard';card.className='card';
      briefText(card,'h2','Daily brief and alerts');
      const label=briefText(card,'label','Reporting day (Lagos)');
      const date=document.createElement('input');date.type='date';date.id='farmBriefDate';label.append(date);
      const refresh=briefText(card,'button','Refresh brief');refresh.type='button';refresh.onclick=()=>renderFarmBrief();
      date.onchange=()=>{date.dataset.selected='true';renderFarmBrief();};
      const content=document.createElement('div');content.id='farmBriefContent';card.append(content);
      const settings=document.createElement('div');settings.id='farmBriefSettings';card.append(settings);
      document.getElementById('farmRecords').append(card);
    }
    const date=document.getElementById('farmBriefDate');
    const data=await api('/api/farm/brief'+(date.dataset.selected&&date.value?'?day='+encodeURIComponent(date.value):''));
    date.value=data.date;date.max=new Date(Date.now()+3600000).toISOString().slice(0,10);
    const title='Daily brief and alerts ('+data.alerts.length+')';
    card.querySelector('h2').textContent=title;
    const summary=card.querySelector(':scope > summary');if(summary)summary.textContent=title;
    const content=document.getElementById('farmBriefContent');content.replaceChildren();
    briefText(content,'p',data.scope==='FARM'?'Farm-wide recorded activity':'Your own reports and assigned reporting responsibilities');
    briefText(content,'p','Updated '+farmDateText(data.generated_at)+'. Alerts are shown in this app while connected; phone push, SMS and email are not configured.');
    briefText(content,'p',data.notice);
    briefText(content,'h3','Needs attention');
    briefText(content,'p',data.alerts.length+' current alerts for this view.');
    for(const alert of data.alerts)briefText(content,'p',alert.type.replaceAll('_',' ')+' — '+alert.text);
    briefText(content,'h3','Reports for '+data.date);
    if(!data.metrics.length)briefText(content,'p','No observations recorded for this day. Quantities are unknown.');
    for(const m of data.metrics)briefText(content,'p',m.location+' — '+(farmTypes[m.kind]||m.kind)+': '+m.quantity+' '+m.unit+' ('+m.reports+' reports'+(m.contains_estimates?', includes estimates':'')+')');
    briefText(content,'h3','Reporting deadlines');
    if(data.schedule_status==='NOT_CONFIGURED')briefText(content,'p','No reporting deadlines apply to this day. The Owner can configure them below once confirmed.');
    for(const r of data.reporting)briefText(content,'p',r.location+' — '+farmTypes[r.kind]+', '+r.due_time+' Lagos: '+r.status.replaceAll('_',' '));
    briefText(content,'h3','Current work and stock');
    briefText(content,'p',data.current_work.length+' unresolved work items. Review Staff work to report progress or resolve them.');
    for(const b of data.current_balances)briefText(content,'p',b.location+': '+(b.recorded_balance===null?'opening balance missing':b.recorded_balance+' '+b.unit)+(b.needs_reconciliation?' — reconciliation needed':'')+(b.contains_estimates?' — includes estimates':''));
    if(data.finance){
      briefText(content,'h3','Bookkeeping');
      for(const [currency,values] of Object.entries(data.finance.daily)){
        const money=value=>{const n=BigInt(value);return currency+' '+(n/100n)+'.'+String(n%100n).padStart(2,'0');};
        briefText(content,'p','Recorded on '+data.date+': sales '+money(values.sales)+', expense claims '+money(values.expenses)+', confirmed receipts '+money(values.received)+', confirmed payments '+money(values.paid));
      }
      for(const [currency,v] of Object.entries(data.finance.current))briefText(content,'p',currency+': '+v.pending_approvals+' purchase requests awaiting a decision.');
      briefText(content,'p',data.finance.notice);
    }
    renderBriefSettings(data.settings);
    if(data.settings.can_configure)await renderRecipientSettings();
  }catch(error){const target=document.getElementById('farmBriefContent');if(target){target.replaceChildren();briefText(target,'p','Brief unavailable: '+error.message);}}
  finally{farmBriefLoading=false;}
}
function renderBriefSettings(settings){
  const root=document.getElementById('farmBriefSettings');root.hidden=!settings.can_configure;
  if(!settings.can_configure||root.childElementCount)return;
  briefText(root,'h3','Owner: reporting schedule');
  briefText(root,'p','Choose a responsible person and at least one daily report per flock/store. Changes apply tomorrow in Lagos, never retrospectively. An empty schedule disables checks from tomorrow. Report zero explicitly when observed; do not enter invented values.');
  const rows=document.createElement('div');root.append(rows);
  function addRow(value={}){
    const row=document.createElement('fieldset');rows.append(row);
    const options=(title,items,current)=>{const label=briefText(row,'label',title),select=document.createElement('select');label.append(select);farmOptions(select,items,'Choose');select.value=current||'';return select;};
    const entity=options('Flock or feed store',farmCatalog.filter(e=>e.entity_type!=='HOUSE').map(e=>[e.entity_id,e.name+' ('+e.entity_type+')']),value.entity_id);
    const kind=options('Required report',[['mortality','Bird deaths'],['eggs_collected','Egg collection'],['feed_used','Feed used']],value.kind);
    const person=options('Responsible person',settings.assignees.map(u=>[u.id,u.username]),value.assignee);
    const label=briefText(row,'label','Deadline (Lagos)'),time=document.createElement('input');time.type='time';time.value=value.due_time||'';label.append(time);
    row.read=()=>({entity_id:entity.value,kind:kind.value,assignee:person.value,due_time:time.value});
    const remove=briefText(row,'button','Remove requirement');remove.type='button';remove.onclick=()=>row.remove();
  }
  settings.requirements.forEach(addRow);
  const add=briefText(root,'button','Add reporting requirement');add.type='button';add.onclick=()=>addRow();
  const save=briefText(root,'button','Save schedule for tomorrow');save.type='button';
  const message=briefText(root,'p',settings.effective_on?'Latest saved schedule starts '+settings.effective_on: 'No schedule saved.');message.id='farmBriefScheduleResult';message.setAttribute('role','status');
  let pending=null;
  save.onclick=async()=>{
    save.disabled=true;
    try{
      const requirements=[...rows.children].map(r=>r.read()),fingerprint=JSON.stringify(requirements);
      if(pending&&pending.fingerprint!==fingerprint)throw Error('Previous save has an unknown outcome. Retry unchanged or reload to check the saved schedule.');
      if(!pending)pending={fingerprint,payload:{event_id:crypto.randomUUID(),expected_revision:settings.revision,requirements}};
      const result=await farmPost('/api/farm/brief/schedule',pending.payload);settings.revision=pending.payload.event_id;pending=null;
      message.textContent='Saved. Schedule takes effect '+result.effective_on+' (Lagos).';await renderFarmBrief();
    }catch(error){if(error.status>=400&&error.status<500)pending=null;message.textContent=error.message;}
    finally{save.disabled=false;}
  };
}
// Refresh only the brief, leaving all reporting forms and schedule drafts intact.
setInterval(()=>{const card=document.getElementById('farmBriefCard');if(document.visibilityState==='visible'&&card&&!card.closest('.hidden'))renderFarmBrief();},60000);

async function renderRecipientSettings(){
  await renderFinancialPermissions();
  if(document.getElementById('farmRecipientSettings'))return;
  const data=await api('/api/farm/notification-recipients');
  const root=document.createElement('section');root.id='farmRecipientSettings';
  document.getElementById('farmBriefSettings').append(root);
  briefText(root,'h3','Owner: notification recipients');briefText(root,'p',data.notice);
  briefText(root,'p','These preferences prepare future delivery. No external messages will be sent. Old alerts will not be sent when settings change.');
  const choices=[];
  for(const user of data.eligible.filter(u=>u.role==='Manager')){
    const label=briefText(root,'label',user.username+' — operational notices'),box=document.createElement('input');
    box.type='checkbox';box.value=user.id;box.checked=data.manager_ids.includes(user.id);label.prepend(box);choices.push(box);
  }
  if(!choices.length)briefText(root,'p','No enabled Farm General Managers are available.');
  const save=briefText(root,'button','Save notification recipients');save.type='button';
  const status=briefText(root,'p','External delivery: not configured.');status.id='farmRecipientResult';status.setAttribute('role','status');
  let pending=null;
  save.onclick=async()=>{
    save.disabled=true;
    try{
      const manager_ids=choices.filter(c=>c.checked).map(c=>c.value),signature=JSON.stringify(manager_ids);
      if(pending&&pending.signature!==signature)throw Error('Retry unchanged; the previous save result is unknown.');
      if(!pending)pending={signature,payload:{event_id:crypto.randomUUID(),expected_revision:data.revision,manager_ids}};
      await farmPost('/api/farm/notification-recipients',pending.payload);data.revision=pending.payload.event_id;pending=null;
      status.textContent='Recipient preferences saved. External delivery remains unconfigured.';
    }catch(error){if(error.status>=400&&error.status<500)pending=null;status.textContent=error.message;}
    finally{save.disabled=false;}
  };
}

async function renderFinancialPermissions(){
  if(document.getElementById('farmFinancialPermissions'))return;
  const data=await api('/api/farm/financial-permissions');
  const root=document.createElement('section');root.id='farmFinancialPermissions';
  document.getElementById('farmBriefSettings').append(root);
  briefText(root,'h3','Owner: Manager financial permissions');briefText(root,'p',data.notice);
  for(const user of data.users){
    const group=briefText(root,'fieldset','');briefText(group,'legend',user.username);
    const choices={};
    for(const [key,text] of Object.entries(data.labels)){
      const label=briefText(group,'label',text),box=document.createElement('input');
      box.type='checkbox';box.checked=user.permissions[key];label.prepend(box);choices[key]=box;
    }
    const save=briefText(group,'button','Save permissions for '+user.username);save.type='button';
    const status=briefText(group,'p','');status.setAttribute('role','status');let pending=null;
    save.onclick=async()=>{
      save.disabled=true;
      try{
        const permissions=Object.fromEntries(Object.entries(choices).map(([k,b])=>[k,b.checked]));
        const signature=JSON.stringify(permissions);
        if(pending&&pending.signature!==signature)throw Error('Retry unchanged or reload: the previous save outcome is unknown.');
        if(!pending)pending={signature,payload:{event_id:crypto.randomUUID(),human_id:user.id,expected_revision:user.revision,permissions}};
        await farmPost('/api/farm/financial-permissions',pending.payload);
        user.revision=pending.payload.event_id;pending=null;status.textContent='Permissions saved. Server checks apply immediately.';
      }catch(error){if(error.status>=400&&error.status<500)pending=null;status.textContent=error.message;}
      finally{save.disabled=false;}
    };
  }
  if(!data.users.length)briefText(root,'p','No enabled Farm Managers are available.');
}
