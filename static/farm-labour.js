'use strict';
async function mountLabour(){
  if(document.getElementById('farmLabour'))return;
  const root=document.createElement('section');root.id='farmLabour';document.getElementById('farmFinanceCard').append(root);
  const add=(tag,text,parent=root)=>{const e=document.createElement(tag);if(text)e.textContent=text;parent.append(e);return e;};
  add('h3','Temporary labour');add('p','Record completed work and agreed pay. Saving does not create a bill or mark anybody paid.');
  const form=add('form');
  const input=(label,id,type='text')=>{const l=add('label',label,form),e=add('input',null,l);e.id=id;e.type=type;return e;};
  const person=input('Person or team','farmLabourPerson');person.required=true;person.maxLength=120;
  const task=input('Work done','farmLabourTask');task.required=true;task.maxLength=400;
  const date=input('Work date (Lagos)','farmLabourDate','date');date.required=true;
  const l=add('label','Time recorded as',form),basis=add('select',null,l);basis.id='farmLabourBasis';
  for(const [value,text] of [['DAYS','Stated days worked'],['TIMED','Actual start and end times']]){const o=add('option',text,basis);o.value=value;}
  const days=input('Days worked (day-based entry)','farmLabourDays','number');days.min='0.001';days.max='365';days.step='0.001';
  const start=input('Started at (Lagos, timed entry)','farmLabourStart','datetime-local'),end=input('Finished at (Lagos, timed entry)','farmLabourEnd','datetime-local');
  const currency=input('Currency','farmLabourCurrency');currency.value='NGN';currency.required=true;
  const amount=input('Agreed total pay (leave blank if unknown)','farmLabourPay','number');amount.min='0';amount.step='0.01';
  const reason=input('Source or explanation','farmLabourReason');reason.required=true;reason.maxLength=400;
  const save=add('button','Save labour record',form);save.type='submit';const status=add('p');status.id='farmLabourStatus';status.setAttribute('role','status');
  const list=add('div');list.id='farmLabourHistory';let pending=null,offset=0,corrects=null;
  const cancel=add('button','Cancel labour correction',form);cancel.type='button';cancel.hidden=true;
  cancel.onclick=()=>{if(pending){status.textContent='A save has an unknown outcome. Retry unchanged or reload before cancelling.';return;}corrects=null;form.reset();currency.value='NGN';save.textContent='Save labour record';cancel.hidden=true;};
  const newer=add('button','Newer labour records'),older=add('button','Older labour records');newer.type=older.type='button';
  async function refresh(){
    const data=await api('/api/farm/labour?offset='+offset);list.replaceChildren();
    for(const r of data.items){
      const p=r.payload,row=add('article',null,list);row.className='labourItem';
      add('p',p.person+' — '+p.task+' — '+p.work_date+' — '+(p.time_basis==='DAYS'?p.days+' stated days':farmDateText(p.started_at)+' to '+farmDateText(p.ended_at))+' — Agreed pay: '+(p.agreed_total_minor===null?'Unknown':financeMoney(String(p.agreed_total_minor),p.currency))+' — '+(r.is_current?'Current':'Corrected record')+' — '+({LINKED:'Expense linked',NOT_LINKED:'No expense linked',NEEDS_REVIEW:'Linked expense needs review'}[r.expense_status]),row);
      add('p','Entered '+farmDateText(r.received_at)+' — '+p.reason,row);
      if(r.is_current&&!r.expense_id){
        const edit=add('button','Correct labour record',row);edit.type='button';
        edit.onclick=()=>{
          if(pending){status.textContent='Retry the pending save unchanged or reload before correcting.';return;}
          corrects=p.event_id;person.value=p.person;task.value=p.task;date.value=p.work_date;basis.value=p.time_basis;days.value=p.days||'';
          const local=v=>v?new Date(new Date(v).getTime()+3600000).toISOString().slice(0,16):'';
          start.value=local(p.started_at);end.value=local(p.ended_at);currency.value=p.currency;amount.value=p.agreed_total_minor===null?'':(p.agreed_total_minor/100).toFixed(2);reason.value='';
          save.textContent='Save labour correction';cancel.hidden=false;reason.focus();status.textContent='Correcting an existing record. Explain the change; the original will remain in history.';
        };
      }
      for(const change of r.link_history||[])add('p',(change.payload.operation==='LINK_EXPENSE'?'Expense linked':'Expense link removed')+' — '+farmDateText(change.received_at)+' — '+change.payload.reason,row);
      if(r.expense_id)add('p','Review the bookkeeping expense before correcting this work record. Removing a link does not cancel an expense, a debt or a payment.',row);
      if(data.can_link&&r.is_current&&r.expense_link_id){
        const whyLabel=add('label','Reason for removing expense link',row),why=add('input',null,whyLabel);why.maxLength=400;
        const unlink=add('button','Remove expense link',row);unlink.type='button';let unlinkPending=null;
        unlink.onclick=async()=>{unlink.disabled=true;try{
          const fields={operation:'UNLINK_EXPENSE',link_id:r.expense_link_id,reason:why.value},signature=JSON.stringify(fields);
          if(unlinkPending&&unlinkPending.signature!==signature)throw Error('Retry unchanged or reload: previous removal outcome is unknown.');
          if(!unlinkPending)unlinkPending={signature,payload:{...fields,event_id:crypto.randomUUID()}};
          await farmPost('/api/farm/labour',unlinkPending.payload);unlinkPending=null;status.textContent='Link removed. Expense and payment history unchanged. You can now correct the work record.';await refresh();
        }catch(error){if(error.status>=400&&error.status<500)unlinkPending=null;status.textContent=error.message;}finally{unlink.disabled=false;}};
      }
      if(data.can_link&&r.is_current&&!r.expense_id&&p.agreed_total_minor!==null){
        const open=add('button','Link existing wages expense',row);open.type='button';
        open.onclick=async()=>{open.disabled=true;try{
          const books=await api('/api/farm/bookkeeping'),voided=new Set(books.records.filter(x=>x.payload.kind==='VOID').map(x=>x.payload.reference));
          const choices=books.records.map(x=>x.payload).filter(x=>x.kind==='EXPENSE_CLAIM'&&!voided.has(x.event_id)&&x.details?.category==='WAGES'&&x.counterparty===p.person&&x.currency===p.currency&&x.amount_minor===p.agreed_total_minor);
          if(!choices.length){add('p','No matching wages expense. Record the expense in bookkeeping with the same person, currency and agreed total, then refresh. No expense was created here.',row);return;}
          const label=add('label','Matching wages expense',row),select=add('select',null,label);
          for(const expense of choices){const option=add('option',farmDateText(expense.observed_at)+' — '+financeMoney(String(expense.amount_minor),expense.currency)+' — '+expense.reason,select);option.value=expense.event_id;}
          const whyLabel=add('label','Reason for linking',row),why=add('input',null,whyLabel);why.maxLength=400;
          const link=add('button','Confirm expense link',row);link.type='button';let linkPending=null;
          link.onclick=async()=>{link.disabled=true;try{
            const fields={operation:'LINK_EXPENSE',work_id:p.event_id,expense_id:select.value,reason:why.value},signature=JSON.stringify(fields);
            if(linkPending&&linkPending.signature!==signature)throw Error('Retry the previous link unchanged or reload; its outcome is unknown.');
            if(!linkPending)linkPending={signature,payload:{...fields,event_id:crypto.randomUUID()}};
            await farmPost('/api/farm/labour',linkPending.payload);linkPending=null;status.textContent='Existing expense linked. No additional cost or payment created.';await refresh();
          }catch(error){if(error.status>=400&&error.status<500)linkPending=null;status.textContent=error.message;}finally{link.disabled=false;}};
        }catch(error){status.textContent=error.message;open.disabled=false;}};
      }
    }
    newer.disabled=offset===0;older.disabled=data.next_offset===null;
  }
  newer.onclick=()=>{offset=Math.max(0,offset-100);refresh().catch(e=>status.textContent=e.message);};older.onclick=()=>{offset+=100;refresh().catch(e=>status.textContent=e.message);};
  form.onsubmit=async e=>{e.preventDefault();save.disabled=true;try{
    const fields={operation:'WORK',person:person.value,task:task.value,time_basis:basis.value,work_date:date.value,started_at:basis.value==='TIMED'?new Date(start.value+'+01:00').toISOString():null,ended_at:basis.value==='TIMED'?new Date(end.value+'+01:00').toISOString():null,days:basis.value==='DAYS'?days.value:null,currency:currency.value,agreed_total_minor:amount.value===''?null:farmMinorAmount(amount.value),corrects,reason:reason.value};const signature=JSON.stringify(fields);
    if(pending&&pending.signature!==signature)throw Error('Retry unchanged or reload: previous save outcome unknown.');
    if(!pending)pending={signature,payload:{...fields,event_id:crypto.randomUUID()}};
    await farmPost('/api/farm/labour',pending.payload);pending=null;const corrected=!!corrects;corrects=null;save.textContent='Save labour record';cancel.hidden=true;status.textContent=corrected?'Correction recorded. Original retained; no expense or payment created.':'Labour recorded. No expense or payment created.';await refresh();
  }catch(error){if(error.status>=400&&error.status<500)pending=null;status.textContent=error.message;}finally{save.disabled=false;}};
  await refresh();
}
