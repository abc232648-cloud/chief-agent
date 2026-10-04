function farmDateText(value) {return value ? new Intl.DateTimeFormat('en-GB',{dateStyle:'medium',timeStyle:'short',timeZone:'Africa/Lagos'}).format(new Date(value))+' (Lagos)' : 'Not recorded';}
'use strict';
const farmTypes = {
  birds_opening: 'Opening bird count', birds_arrived: 'Birds arrived', birds_departed: 'Birds departed',
  mortality: 'Bird deaths', eggs_opening: 'Opening egg stock', eggs_collected: 'Eggs collected',
  eggs_dispatched: 'Eggs dispatched', eggs_lost: 'Eggs broken / lost',
  feed_opening: 'Opening feed stock', feed_received: 'Feed received', feed_used: 'Feed used'
};
for(const [prefix,label] of [['birds','Bird'],['eggs','Egg'],['feed','Feed']]){farmTypes[prefix+'_adjustment_in']=label+' count adjustment (increase)';farmTypes[prefix+'_adjustment_out']=label+' count adjustment (decrease)';}
let farmUnitSettings={revision:null,eggs_per_crate:null,kg_per_bag:null};
let farmPending = null;
let farmCatalog = [];
let farmHistoryOffset=0;
let farmCorrectionEntity = null;
let farmCorrectionTime = null;
function farmUnit() {
  const kg = document.getElementById('farmKind').value.startsWith('feed_');
  document.getElementById('farmUnit').textContent = kg ? '(kg)' : '(count)';
  const select=document.getElementById('farmEntryUnit'),previous=select.value;select.replaceChildren();
  for(const [value,label] of [['base',kg?'Kilograms':'Individual birds or eggs'],...(kg&&farmUnitSettings.kg_per_bag!==null?[['bags','Bags ('+farmUnitSettings.kg_per_bag+' kg each)']]:!kg&&document.getElementById('farmKind').value.startsWith('eggs_')&&farmUnitSettings.eggs_per_crate!==null?[['crates','Crates ('+farmUnitSettings.eggs_per_crate+' eggs each)']]:[])]){const o=document.createElement('option');o.value=value;o.textContent=label;select.append(o);}
  if([...select.options].some(o=>o.value===previous))select.value=previous;
  document.getElementById('farmQuantity').step = kg||select.value!=='base' ? '0.001' : '1';
  farmConversionPreview();
}
function resetFarmForm() {
  document.getElementById('farmRecordForm').reset();
  document.getElementById('farmHistorical').disabled=false;
  for (const id of ['farmKind', 'farmLocation', 'farmObserved']) document.getElementById(id).disabled = false;
  document.getElementById('farmCorrects').value = '';
  document.getElementById('farmReason').required = false;
  document.getElementById('farmReasonLabel').hidden = true;
  document.getElementById('farmCancelCorrection').hidden = true;
  farmCorrectionTime = null; farmCorrectionEntity = null;
  document.getElementById('farmEntity').disabled = false;
  const now = new Date();
  document.getElementById('farmObserved').value = new Date(now.getTime() + 3600000).toISOString().slice(0,16);
  farmUnit();
}
async function loadFarmRecords() {
  const data = await api('/api/farm/journal?offset='+farmHistoryOffset);
  const setup = await api('/api/farm/setup');
  farmCatalog = setup.entities;
  farmUnitSettings=await api('/api/farm/units');
  await renderFarmSetup(setup);
  await renderFarmFinance(setup);
  await renderFarmStaff();
  await renderFarmAI();
  await renderFarmBrief();
  await renderPhysicalCounts();
  await mountHealthRecords();
  const manager = setup.can_manage;
  document.getElementById('farmHistoricalLabel').hidden=!manager;
  const form = document.getElementById('farmRecordForm');
  form.hidden = !canDomain('work.request', 'farming');
  const select = document.getElementById('farmKind');
  const prior = select.value;
  select.replaceChildren();
  for (const [value, label] of Object.entries(farmTypes)) {
    if (value.includes('_adjustment_') || (!manager && value.endsWith('_opening'))) continue;
    const option = document.createElement('option'); option.value = value; option.textContent = label; select.append(option);
  }
  if ([...select.options].some(o => o.value === prior)) select.value = prior;
  if (!document.getElementById('farmObserved').value) resetFarmForm();
  farmUnit();
  const balances = document.getElementById('farmBalances'); balances.replaceChildren();
  if (!data.balances.length) balances.textContent = 'No reports yet. No opening bird or stock counts have been assumed.';
  for (const b of data.balances) {
    const item = document.createElement('div'); item.className = 'card';
    const title = document.createElement('h2'); title.textContent = b.location + ' — ' + b.unit + (b.entity_id ? ' ('+b.entity_id.slice(0,8)+')' : ' (legacy location)');
    const value = document.createElement('p'); value.textContent = b.recorded_balance === null ? 'Opening balance needed' : 'Recorded balance: ' + b.recorded_balance + ' ' + b.unit;
    const detail = document.createElement('p'); detail.textContent = (b.contains_estimates ? 'Includes estimates. ' : 'Based on reported measurements. ') + (b.needs_reconciliation ? 'Reconciliation needed. ' : '') + 'Last observation: ' + farmDateText(b.last_observed_at);
    item.append(title, value, detail); balances.append(item);
  }
  const history = document.getElementById('farmHistory'); history.replaceChildren();
  document.getElementById('farmOlder').disabled=data.next_offset===null;
  document.getElementById('farmNewer').disabled=farmHistoryOffset===0;
  for (const record of data.records) {
    const p = record.payload; const item = document.createElement('div'); item.className = 'item';
    const text = document.createElement('p'); text.textContent = [farmTypes[p.kind], p.location, p.quantity, p.basis, farmDateText(p.observed_at), 'Reporter: ' + record.actor_name, p.notes, p.conversion?'Entered as '+p.conversion.quantity+' '+p.conversion.unit:'', p.historical_before_opening ? 'Historical activity before opening count; excluded from current stock' : '', p.corrects ? 'Correction of ' + p.corrects + ': ' + p.reason : ''].filter(Boolean).join(' · ');
    item.append(text);
    if (manager && record.is_current && !p.kind.includes('_adjustment_')) {
      const button = document.createElement('button'); button.type = 'button'; button.textContent = 'Correct record';
      button.addEventListener('click', () => {
        if (farmPending && !confirm('Discard the pending submission and start a correction?')) return;
        farmPending = null; resetFarmForm();
        for (const [id, value] of [['farmKind',p.kind],['farmLocation',p.location],['farmQuantity',p.quantity],['farmBasis',p.basis],['farmNotes',p.notes],['farmCorrects',p.event_id]]) document.getElementById(id).value = value;
        const at = new Date(p.observed_at);
        document.getElementById('farmObserved').value = new Date(at.getTime()+3600000).toISOString().slice(0,16);
        document.getElementById('farmHistorical').checked=!!p.historical_before_opening;
        document.getElementById('farmHistorical').disabled=true;
        farmCorrectionTime = p.observed_at; farmCorrectionEntity = p.entity_id || null;
        document.getElementById('farmEntity').value = farmCorrectionEntity || '';
        document.getElementById('farmEntity').disabled = true;
        for (const id of ['farmKind','farmLocation','farmObserved']) document.getElementById(id).disabled = true;
        document.getElementById('farmReason').required = true;
        document.getElementById('farmReasonLabel').hidden = false;
        document.getElementById('farmCancelCorrection').hidden = false;
        farmUnit(); openFarmSection('farmEntrySection'); document.getElementById('farmQuantity').focus();
      });
      item.append(button);
    }
    history.append(item);
  }
  prepareFarmSections();
}
document.addEventListener('DOMContentLoaded', () => {
  const form = document.getElementById('farmRecordForm');
  document.getElementById('farmKind').addEventListener('change', farmUnit);
  document.getElementById('farmEntryUnit').addEventListener('change',farmUnit);
  document.getElementById('farmQuantity').addEventListener('input',farmConversionPreview);
  document.getElementById('farmCancelCorrection').addEventListener('click', () => {farmPending = null; resetFarmForm();});
  form.addEventListener('submit', async event => {
    event.preventDefault(); if (!form.reportValidity()) return;
    const message = document.getElementById('farmResult'); const button = document.getElementById('farmSave');
    button.disabled = true;
    try {
      const value = id => document.getElementById(id).value;
      const payload = {kind:value('farmKind'),location:value('farmLocation'),quantity:value('farmQuantity'),basis:value('farmBasis'),observed_at:farmCorrectionTime || new Date(value('farmObserved')+'+01:00').toISOString(),notes:value('farmNotes'),corrects:value('farmCorrects') || null,reason:value('farmReason')};
      const conversion=farmConvertedQuantity();
      if(conversion){payload.quantity=conversion.base;payload.conversion={unit:value('farmEntryUnit'),quantity:value('farmQuantity'),policy_revision:farmUnitSettings.revision};}
      if(document.getElementById('farmHistorical').checked)payload.historical_before_opening=true;
      const entityId = farmCorrectionTime ? farmCorrectionEntity : value('farmEntity');
      if (entityId) payload.entity_id = entityId;
      const fingerprint = JSON.stringify(payload);
      if (farmPending && farmPending.fingerprint !== fingerprint) throw new Error('A previous submission has an unknown outcome. Restore its values and retry, or reload and check the history before entering a replacement.');
      if (!farmPending) farmPending = {fingerprint, payload:{...payload,event_id:crypto.randomUUID()}};
      const response = await fetch('/api/farm/journal', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(farmPending.payload)});
      const result = await response.json();
      if (!response.ok) {
        // An explicit rejection is editable; a lost response retains its event ID.
        if (response.status >= 400 && response.status < 500) farmPending = null;
        throw new Error(result.reason || result.message || 'Record could not be saved.');
      }
      farmPending = null; resetFarmForm(); message.textContent = 'Record saved.'; await loadFarmRecords();
    } catch (error) { message.textContent = error.message; }
    finally { button.disabled = false; }
  });
});

async function farmPost(path, payload) {
  const response = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
  const data = await response.json();
  if (!response.ok) {const error=new Error(data.reason || data.message || 'Request rejected. Reauthenticate in Users & access if required.');error.status=response.status;throw error;}
  return data;
}
function farmOptions(select, items, empty) {
  const previous = select.value; select.replaceChildren();
  if (empty) {const o=document.createElement('option');o.value='';o.textContent=empty;select.append(o);}
  for (const [id, label] of items) {const o=document.createElement('option');o.value=id;o.textContent=label;select.append(o);}
  if ([...select.options].some(o=>o.value===previous)) select.value=previous;
}
async function renderFarmSetup(data) {
  document.getElementById('farmSetupCard').hidden=!data.can_setup;
  document.getElementById('farmRoleCard').hidden=!data.can_assign;
  farmOptions(document.getElementById('farmEntity'),farmCatalog.filter(e=>e.entity_type!=='HOUSE').map(e=>[e.entity_id,e.name+' ('+e.entity_type.toLowerCase()+', '+e.entity_id.slice(0,8)+')']),'Legacy text location');
  farmOptions(document.getElementById('farmEntityHouse'),farmCatalog.filter(e=>e.entity_type==='HOUSE').map(e=>[e.entity_id,e.name]),'Choose house');
  farmOptions(document.getElementById('farmEntityUpdateId'),farmCatalog.map(e=>[e.entity_id,e.name+' ('+e.entity_id.slice(0,8)+')']),'Choose location');
  const history=document.getElementById('farmEntityHistory');history.replaceChildren();
  document.getElementById('farmEntityHistoryNotice').textContent=data.can_setup?'Showing the latest '+(data.entity_history||[]).length+' of '+data.entity_history_count+' setup records. Original stock observations remain unchanged.':'';
  for(const row of data.entity_history||[]){const line=document.createElement('p');line.textContent=[row.payload.operation==='create_entity'?'Created':'Updated',row.payload.name,'Opening date: '+(row.payload.opened_on||'unknown'),row.payload.reason||'Initial setup','Recorded '+farmDateText(row.received_at),'Actor '+row.actor_id].join(' · ');history.append(line);}
  if(data.can_assign) {
    farmOptions(document.getElementById('farmRoleHuman'),data.assignable_users.map(u=>[u.id,u.username+' ('+u.role+')']),'Choose person');
  }
}
document.addEventListener('DOMContentLoaded',()=>{
  document.getElementById('farmEntity').addEventListener('change',()=>{
    const e=farmCatalog.find(e=>e.entity_id===document.getElementById('farmEntity').value);
    const location=document.getElementById('farmLocation');location.disabled=!!e;if(e)location.value=e.name;
  });
  const bind=(formId,resultId,build)=>{
    let pending=null;
    document.getElementById(formId).addEventListener('submit',async event=>{
      event.preventDefault(); const button=event.target.querySelector('button[type=submit]');button.disabled=true;
      try {
        const fields=build(), signature=JSON.stringify(fields);
        if(pending&&pending.signature!==signature)throw new Error('Unknown submission outcome: retry the same values before making changes.');
        if(!pending)pending={signature,payload:{...fields,event_id:crypto.randomUUID()}};
        await farmPost('/api/farm/setup',pending.payload);pending=null;
        if(formId==='farmEntityForm')entityId=crypto.randomUUID();
        if(formId==='farmEntityUpdateForm'){event.target.reset();editRevision=null;}
        document.getElementById(resultId).textContent='Saved.';await loadFarmRecords();
      }catch(error){if(error.status>=400&&error.status<500)pending=null;document.getElementById(resultId).textContent=error.message;}
      finally{button.disabled=false;}
    });
  };
  const v=id=>document.getElementById(id).value;
  let entityId=crypto.randomUUID();
  bind('farmEntityForm','farmSetupResult',()=>({operation:'create_entity',entity_id:entityId,entity_type:v('farmEntityType'),name:v('farmEntityName'),opened_on:v('farmEntityDate')||null,house_id:v('farmEntityType')==='FLOCK'?v('farmEntityHouse'):null}));
  let editRevision=null;
  document.getElementById('farmEntityUpdateId').addEventListener('change',()=>{
    const selected=farmCatalog.find(e=>e.entity_id===v('farmEntityUpdateId'));
    editRevision=selected?.revision||null;
    document.getElementById('farmEntityUpdateName').value=selected?.name||'';
    document.getElementById('farmEntityUpdateDate').value=selected?.opened_on||'';
  });
  bind('farmEntityUpdateForm','farmEntityUpdateResult',()=>({operation:'update_entity',entity_id:v('farmEntityUpdateId'),expected_revision:editRevision,name:v('farmEntityUpdateName'),opened_on:v('farmEntityUpdateDate')||null,reason:v('farmEntityUpdateReason')}));
  bind('farmRoleForm','farmRoleResult',()=>({operation:'assign_role',human_id:v('farmRoleHuman'),role:v('farmRoleValue'),reason:v('farmRoleReason')}));
});

async function renderFarmFinance(setup) {
  const card=document.getElementById('farmFinanceCard');card.hidden=!setup.can_finance;
  if(card.hidden){document.getElementById('farmMoneyHistory').replaceChildren();document.getElementById('farmMoneyBalances').replaceChildren();return;}
  await mountFinance();
  const data=await api('/api/farm/bookkeeping');
  let types=[['PURCHASE_REQUEST','Purchase request'],['EXPENSE_CLAIM','Reported expense'],['SALE','Sale'],['PAYMENT_CLAIM','Payment claim'],['OPENING_RECEIVABLE','Opening debt owed to the farm'],['OPENING_PAYABLE','Opening debt the farm owes'],['DISPUTE_DEBT','Report a disputed balance']];
  if(!data.can_approve){const p=setup.financial_permissions;types=types.filter(([kind])=>kind==='DISPUTE_DEBT'?p.flag_debts:kind==='PAYMENT_CLAIM'?p.report_payments:['SALE','EXPENSE_CLAIM','OPENING_RECEIVABLE','OPENING_PAYABLE'].includes(kind)?p.record_debts:true);}
  if(data.can_approve)types.push(['APPROVE_REQUEST','Approve request'],['REJECT_REQUEST','Reject request'],['CONFIRM_PAYMENT','Confirm payment'],['VOID','Void incorrect record'],['DEBT_DOCUMENT','Attach later invoice to opening debt'],['RESOLVE_DEBT_DISPUTE','Resolve a balance dispute']);
  farmOptions(document.getElementById('farmMoneyKind'),types);
  document.getElementById('farmMoneyKind').dispatchEvent(new Event('change'));
  farmOptions(document.getElementById('farmMoneyReference'),data.records.map(r=>[r.payload.event_id,(financeLabels[r.payload.kind]||r.payload.kind)+' · '+r.payload.counterparty+' · '+farmMoneyText(r.payload.amount_minor,r.payload.currency)+' · '+r.payload.event_id.slice(0,8)]),'No related record');
  const target=document.getElementById('farmMoneyBalances');target.replaceChildren();
  for(const b of data.balances){const p=document.createElement('p');p.textContent=(financeLabels[b.kind]||b.kind)+' '+b.reference.slice(0,8)+': outstanding '+farmMoneyText(b.outstanding_minor,b.currency)+'; unconfirmed payments '+farmMoneyText(b.unconfirmed_payment_minor,b.currency);target.append(p);}
  await refreshFinance();
  await mountCosting();
  await mountPlanning();
  await mountLabour();
  const history=document.getElementById('farmMoneyHistory');history.replaceChildren();
  for(const r of data.records){
    const p=document.createElement('p');p.textContent=[financeLabels[r.payload.kind]||r.payload.kind,farmMoneyText(r.payload.amount_minor,r.payload.currency),r.payload.counterparty,r.payload.reason,'Reported '+farmDateText(r.received_at),'Reference '+r.payload.event_id].join(' · ');
    const use=document.createElement('button');use.type='button';use.textContent='Use as reference';
    use.onclick=()=>{document.getElementById('farmMoneyReference').value=r.payload.event_id;document.getElementById('farmCurrency').value=r.payload.currency;document.getElementById('farmMoneyResult').textContent='Reference selected. Choose the intended record type and review the amount before saving.';};
    p.append(document.createTextNode(' '),use);history.append(p);
  }
}
document.addEventListener('DOMContentLoaded',()=>{
  let pending=null;
  document.getElementById('farmFinanceForm').addEventListener('submit',async event=>{
    event.preventDefault();const button=event.target.querySelector('button[type=submit]');button.disabled=true;
    try{
      const v=id=>document.getElementById(id).value;
      const fields={kind:v('farmMoneyKind'),amount_minor:farmMinorAmount(v('farmMoneyAmount')),currency:v('farmCurrency'),reference:v('farmMoneyReference')||null,counterparty:v('farmCounterparty'),receipt_ref:v('farmReceipt'),observed_at:new Date(v('farmMoneyObserved')+'+01:00').toISOString(),reason:v('farmMoneyReason')};
      const details=financeDetails();if(details)fields.details=details;
      const signature=JSON.stringify(fields);
      if(pending&&pending.signature!==signature)throw new Error('Retry the previous values first; their submission outcome is unknown.');
      if(!pending)pending={signature,payload:{...fields,event_id:crypto.randomUUID()}};
      await farmPost('/api/farm/bookkeeping',pending.payload);pending=null;
      document.getElementById('farmMoneyResult').textContent='Saved.';await loadFarmRecords();
    }catch(error){if(error.status>=400&&error.status<500)pending=null;document.getElementById('farmMoneyResult').textContent=error.message;}
    finally{button.disabled=false;}
  });
});

async function renderFarmStaff(){
  const data=await api('/api/farm/staff');
  farmOptions(document.getElementById('farmStaffKind'),data.can_manage?[['INCIDENT','Report a problem'],['TASK','Assign a task']]:[['INCIDENT','Report a problem']]);
  farmOptions(document.getElementById('farmAssignee'),data.assignees.map(a=>[a.id,a.username]),'Choose person');
  const target=document.getElementById('farmStaffItems');target.replaceChildren();
  for(const item of data.items){
    const row=document.createElement('article');row.className='item';
    const text=document.createElement('p');text.textContent=[item.kind,item.text,item.state,item.severity,item.due_at?'Due '+farmDateText(item.due_at):'',item.overdue?'Overdue':''].filter(Boolean).join(' · ');row.append(text);
    const details=document.createElement('details'),summary=document.createElement('summary');summary.textContent='History';details.append(summary);
    for(const r of item.history){const line=document.createElement('p');line.textContent=r.payload.kind+' · '+r.payload.text+' · '+farmDateText(r.received_at);details.append(line);}row.append(details);
    const actions=item.state==='RESOLVED'?(data.can_manage?[['REOPEN','Reopen']]:[]):[['ACKNOWLEDGE','Acknowledge'],['ESCALATE','Escalate'],['REPORT_COMPLETION','Report completed'],...(data.can_manage?[['RESOLVE','Resolve']]:[])];
    for(const [kind,label] of actions){
      const button=document.createElement('button');button.type='button';button.textContent=label;
      let pending=null;
      button.onclick=async()=>{
        const text=prompt('Describe this update (required):');if(!text)return;
        if(pending&&pending.text!==text){document.getElementById('farmStaffResult').textContent='Retry the same update; its outcome is unknown.';return;}
        if(!pending)pending={event_id:crypto.randomUUID(),kind,reference:item.id,expected_event:item.latest_event,assignee:null,due_at:null,text,severity:item.severity};
        button.disabled=true;
        try{await farmPost('/api/farm/staff',pending);pending=null;await renderFarmStaff();}
        catch(error){if(error.status>=400&&error.status<500)pending=null;document.getElementById('farmStaffResult').textContent=error.message;}
        finally{button.disabled=false;}
      };row.append(button);
    }
    addFarmPhotoControls(row,item.id);
    target.append(row);
  }
}
document.addEventListener('DOMContentLoaded',()=>{
  let pending=null;
  document.getElementById('farmStaffForm').addEventListener('submit',async event=>{
    event.preventDefault();const button=event.target.querySelector('button[type=submit]');button.disabled=true;
    try{
      const v=id=>document.getElementById(id).value,kind=v('farmStaffKind');
      const fields={kind,reference:null,expected_event:null,assignee:kind==='TASK'?v('farmAssignee'):null,due_at:kind==='TASK'&&v('farmDue')?new Date(v('farmDue')+'+01:00').toISOString():null,text:v('farmStaffText'),severity:v('farmSeverity')};
      const signature=JSON.stringify(fields);
      if(pending&&pending.signature!==signature)throw new Error('Retry the same report first; its outcome is unknown.');
      if(!pending)pending={signature,payload:{...fields,event_id:crypto.randomUUID()}};
      await farmPost('/api/farm/staff',pending.payload);pending=null;document.getElementById('farmStaffResult').textContent='Saved.';await renderFarmStaff();
    }catch(error){if(error.status>=400&&error.status<500)pending=null;document.getElementById('farmStaffResult').textContent=error.message;}
    finally{button.disabled=false;}
  });
});

document.addEventListener('DOMContentLoaded',()=>{
  document.getElementById('farmAskForm').addEventListener('submit',async event=>{
    event.preventDefault();const button=event.target.querySelector('button[type=submit]');button.disabled=true;
    try{
      const result=await farmPost('/api/farm/assistant',{question:document.getElementById('farmQuestion').value});
      document.getElementById('farmAnswer').textContent=(result.live_ai?'Qwen guidance — check before acting: ':'Built-in guidance (not live AI): ')+result.answer;
    }catch(error){document.getElementById('farmAnswer').textContent=error.message;}
    finally{button.disabled=false;}
  });
});

function addFarmPhotoControls(row,workId){
  const label=document.createElement('label');label.textContent='Attach a photo to this work item';
  const input=document.createElement('input');input.type='file';input.accept='image/jpeg,image/png';label.append(input);
  const message=document.createElement('p');message.setAttribute('role','status');
  const gallery=document.createElement('div'),button=document.createElement('button');button.type='button';button.textContent='View photos';
  button.onclick=async()=>{
    try{const items=await api('/api/farm/photos?work_id='+encodeURIComponent(workId));gallery.replaceChildren();
      for(const item of items){const image=document.createElement('img');image.src='/api/farm/photos/'+encodeURIComponent(item.id);image.alt='Reported work photo, uploaded '+farmDateText(item.received_at);image.width=240;image.loading='lazy';gallery.append(image);}
      if(!items.length)gallery.textContent='No photos attached.';
    }catch(error){message.textContent=error.message;}
  };
  let pending=null;
  input.onchange=async()=>{
    if(!input.files.length)return;input.disabled=true;
    try{
      const file=input.files[0];if(file.size>8*1024*1024||!['image/jpeg','image/png'].includes(file.type))throw new Error('Choose a JPEG or PNG up to 8 MiB.');
      const fingerprint=file.name+':'+file.size+':'+file.lastModified;
      if(pending&&pending.fingerprint!==fingerprint)throw new Error('Retry the same photo first; its upload outcome is unknown.');
      if(!pending){
        const bitmap=await createImageBitmap(file);
        try{
          if(bitmap.width*bitmap.height>24000000)throw new Error('Photo dimensions are too large.');
          const ratio=Math.min(1,1280/Math.max(bitmap.width,bitmap.height)),canvas=document.createElement('canvas');
          canvas.width=Math.max(1,Math.round(bitmap.width*ratio));canvas.height=Math.max(1,Math.round(bitmap.height*ratio));canvas.getContext('2d').drawImage(bitmap,0,0,canvas.width,canvas.height);
          const image_base64=canvas.toDataURL('image/png').split(',')[1];
          pending={fingerprint,payload:{event_id:crypto.randomUUID(),work_id:workId,image_base64}};
        }finally{bitmap.close();}
      }
      await farmPost('/api/farm/photos',pending.payload);pending=null;message.textContent='Photo attached. Metadata was not retained.';input.value='';
    }catch(error){if(error.status>=400&&error.status<500)pending=null;message.textContent=error.message;}
    finally{input.disabled=false;}
  };
  row.append(label,message,button,gallery);
}

document.addEventListener('DOMContentLoaded',()=>{
  document.getElementById('farmOlder').onclick=async()=>{farmHistoryOffset+=100;try{await loadFarmRecords();}catch(error){document.getElementById('farmResult').textContent=error.message;}};
  document.getElementById('farmNewer').onclick=async()=>{farmHistoryOffset=Math.max(0,farmHistoryOffset-100);try{await loadFarmRecords();}catch(error){document.getElementById('farmResult').textContent=error.message;}};
});

function farmMinorAmount(value){
  if(!/^\d{1,10}(?:\.\d{1,2})?$/.test(value))throw new Error('Use a non-negative money amount with at most two decimal places.');
  const [whole,fraction='']=value.split('.');const amount=BigInt(whole)*100n+BigInt(fraction.padEnd(2,'0'));
  if(amount>999999999999n)throw new Error('Amount exceeds the supported limit.');return Number(amount);
}
function farmMoneyText(minor,currency){return currency+' '+String(Math.floor(minor/100))+'.'+String(minor%100).padStart(2,'0');}

async function renderFarmAI(){
  const status=await api('/api/farm/assistant');
  const card=document.getElementById('farmAssistantCard');
  card.querySelector('p').textContent=status.notice;
  let panel=document.getElementById('farmAISetup');
  if(!panel){panel=document.createElement('div');panel.id='farmAISetup';card.append(panel);}
  panel.hidden=!status.can_configure;
  if(!status.can_configure)return;
  // Do not reset the owner's selection/confirmation on dashboard refreshes.
  if(panel.childElementCount)return;
  const help=document.createElement('p');help.textContent='Owner setup: save the Groq key privately in Models first, then choose its Qwen registration here. Testing sends a synthetic prompt; activation sends users’ questions and permitted Farm context to Groq. Job settings remain separate.';
  const label=document.createElement('label');label.textContent='Saved Qwen connection';
  const select=document.createElement('select');select.id='farmAIRegistration';label.append(select);
  const list=await api('/api/model-setup');
  farmOptions(select,list.registrations.filter(r=>r.provider==='groq'&&r.provider_model.startsWith('qwen/')&&r.cost==='FREE'&&r.key_configured).map(r=>[r.id,r.provider_model+' — '+r.id.slice(-8)]),'Choose saved connection');
  const confirmation=document.createElement('label'),check=document.createElement('input');check.type='checkbox';check.id='farmAIFree';confirmation.append(check,document.createTextNode('I checked that this Groq account uses the free plan. No paid fallback is allowed.'));
  const test=document.createElement('button');test.type='button';test.textContent='Test and activate Qwen';
  const disable=document.createElement('button');disable.type='button';disable.textContent='Disable Farm AI';
  const message=document.createElement('p');message.id='farmAIResult';message.setAttribute('role','status');
  async function change(payload){test.disabled=disable.disabled=true;try{const result=await farmPost('/api/farm/assistant/configure',payload);message.textContent=result.status==='ACTIVE'?'Qwen connection tested and active. Answers cannot authorize actions.':'Farm live AI disabled.';const current=await api('/api/farm/assistant');document.getElementById('farmAssistantCard').querySelector('p').textContent=current.notice;}catch(error){message.textContent=error.message;}finally{test.disabled=disable.disabled=false;}}
  test.onclick=()=>{if(!select.value||!check.checked){message.textContent='Choose a saved Qwen connection and confirm the free account first.';return;}change({operation:'qualify',registration:select.value,free_account_confirmed:true});};
  disable.onclick=()=>change({operation:'disable'});
  panel.append(help,label,confirmation,test,disable,message);
}


// Native disclosure controls preserve form nodes, drafts and permission attributes.
function openFarmSection(id) {
  const section = document.getElementById(id);
  if (section && section.matches('details.farmSection') && !section.hidden) section.open = true;
}
function prepareFarmSections() {
  const root = document.getElementById('farmRecords');
  if (!root || root.dataset.sectionsReady) return;
  root.dataset.sectionsReady = 'true';
  const balances = document.getElementById('farmBalances');
  const balanceCard = document.createElement('div'); balanceCard.className = 'card'; balanceCard.id = 'farmBalancesSection';
  const heading = document.createElement('h2'); heading.textContent = 'Birds, eggs and feed balances';
  balances.before(balanceCard); balanceCard.append(heading, balances);
  for (const card of [...root.children].filter(node => node.classList.contains('card'))) {
    const title = card.querySelector('h2'); if (!title) continue;
    const section = document.createElement('details');
    for (const attribute of [...card.attributes]) section.setAttribute(attribute.name, attribute.value);
    section.classList.add('farmSection');
    if (card.querySelector('#farmRecordForm')) section.id = 'farmEntrySection';
    if (card.querySelector('#farmHistory')) section.id = 'farmHistorySection';
    if (card.querySelector('#farmStaffForm')) section.id = 'farmStaffSection';
    const summary = document.createElement('summary'); summary.textContent = title.textContent;
    section.append(summary, ...card.childNodes); card.replaceWith(section);
  }
  const toolbar = document.createElement('div'); toolbar.className = 'farmSectionTools';
  const help = document.createElement('p'); help.textContent = 'Open only what you need. Closing a section keeps your unfinished entries on this page.';
  toolbar.append(help);
  for (const [label, open] of [['Expand all sections', true], ['Collapse all sections', false]]) {
    const button = document.createElement('button'); button.type = 'button'; button.textContent = label;
    button.addEventListener('click', () => {
      for (const section of root.querySelectorAll(':scope > details.farmSection')) if (!section.hidden) section.open = open;
    }); toolbar.append(button);
  }
  const intro = root.querySelector(':scope > p'); intro.after(toolbar);
  let previous = toolbar;
  for (const id of ['farmEntrySection', 'farmBriefCard', 'farmBalancesSection', 'farmStaffSection', 'farmFinanceCard', 'farmAssistantCard', 'farmHistorySection', 'farmSetupCard', 'farmRoleCard']) {
    const section = document.getElementById(id); previous.after(section); previous = section;
  }
}

function farmConvertedQuantity(unit=document.getElementById('farmEntryUnit').value,quantity=document.getElementById('farmQuantity').value){
  if(unit==='base')return null;
  const factor=String(unit==='crates'?farmUnitSettings.eggs_per_crate:farmUnitSettings.kg_per_bag);
  const scaled=value=>{if(!/^\d{1,9}(?:\.\d{1,3})?$/.test(value))throw Error('Use a quantity with at most three decimal places.');const [whole,part='']=value.split('.');return BigInt(whole)*1000n+BigInt(part.padEnd(3,'0'));};
  const product=scaled(quantity)*scaled(factor);
  if(product%1000n!==0n||(unit==='crates'&&product%1000000n!==0n))throw Error('Conversion must produce whole eggs or kilograms with at most three decimal places.');
  const value=product/1000n,base=(value/1000n).toString()+'.'+(value%1000n).toString().padStart(3,'0');
  return {base,unit:unit==='crates'?'eggs':'kg'};
}
function farmConversionPreview(){
  const target=document.getElementById('farmConversionPreview');
  try{const result=farmConvertedQuantity();target.textContent=result?'Will record '+result.base+' '+result.unit+' using the saved conversion size.':'Quantity is recorded directly; no crate or bag size is assumed.';}catch(error){target.textContent=error.message;}
}
