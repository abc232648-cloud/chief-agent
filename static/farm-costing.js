'use strict';
function farmCostMoney(value,currency,places=2){
  if(value===null)return 'Unavailable';
  const match=/^(-?)(\d+)(?:\.(\d+))?$/.exec(String(value));
  if(!match)return 'Unavailable';
  const fraction=match[3]||'',scale=10n**BigInt(fraction.length+2),display=10n**BigInt(places);
  const raw=BigInt(match[2]+fraction),rounded=(raw*display+scale/2n)/scale;
  return currency+' '+(match[1]&&rounded?'-':'')+(rounded/display)+'.'+(rounded%display).toString().padStart(places,'0');
}
async function mountCosting(){
  if(document.getElementById('farmCosting'))return;
  const data=await api('/api/farm/costing/policy');
  const root=document.createElement('section');root.id='farmCosting';document.getElementById('farmFinanceCard').append(root);
  const add=(tag,text,parent=root)=>{const e=document.createElement(tag);if(text)e.textContent=text;parent.append(e);return e;};
  add('h3','Whole-farm costing');
  add('p','Compare recorded feed consumed and other operating expenses with eggs collected. One-off expenses are not assumed to repeat. This is a provisional estimate, not profit or permission to spend. Missing records remain incomplete.');
  const form=add('form'),input=(parent,label,id,type='text')=>{const l=add('label',label,parent),i=add('input',null,l);i.id=id;i.type=type;return i;};
  const start=input(form,'From (Farm reporting timezone)','farmCostStart','date'),end=input(form,'Through (Farm reporting timezone)','farmCostEnd','date');start.required=end.required=true;
  const today=chiefReportingDay(farmReportingTimezone || Intl.DateTimeFormat().resolvedOptions().timeZone);start.value=end.value=today;
  const label=add('label','Saved costing settings',form),select=add('select',null,label);select.id='farmCostPolicy';
  function options(){select.replaceChildren();for(const row of [...data.policies].reverse()){const o=add('option',farmDateText(row.received_at)+' — '+row.payload.currency+' — '+row.payload.reason,select);o.value=row.payload.event_id;}}
  options();const calculate=add('button','Calculate recorded costs',form);calculate.type='submit';
  const status=add('p');status.id='farmCostStatus';status.setAttribute('role','status');const output=add('div');output.id='farmCostOutput';
  form.onsubmit=async e=>{e.preventDefault();calculate.disabled=true;try{
    if(!select.value)throw Error('The Owner needs to save costing settings first. Unknown values can stay blank.');
    const q=new URLSearchParams({start:start.value,end:end.value,revision:select.value});const r=await api('/api/farm/costing?'+q);
    output.replaceChildren();
    add('p','Feed price: '+({UNKNOWN:'Unknown',RECORDED_PRICE:'Owner-recorded price',PLANNING_ESTIMATE:'Optional planning estimate',UNCLASSIFIED:'Older setting — basis not recorded'}[r.price_basis]||'Unknown'),output);
    if(r.price_source)add('p','Price source: '+r.price_source,output);
    for(const [label,key] of [['Feed consumed (kg)','feed_consumed_kg'],['Eggs collected','collected_eggs'],['Losses linked to these collections','linked_losses_from_collections'],['Losses with unresolved source','unallocated_relevant_losses'],['Eggs remaining after linked losses (estimate)','saleable_eggs']])add('p',label+': '+(r[key]===null?'Unavailable':r[key]),output);
    for(const [label,key] of [['Feed purchases','feed_purchases_minor'],['Bird acquisition','bird_acquisition_minor'],['Other recorded operating expenses','operating_expenses_minor'],['Consumed feed valuation','consumed_feed_cost_minor'],['Cost per collected egg','cost_per_collected_egg_minor'],['Estimated cost per egg after linked losses','saleable_cost_per_egg_minor']])add('p',label+': '+farmCostMoney(r[key],r.currency,key.includes('per_egg')||key.includes('per_collected_egg')?4:2),output);
    add('h4','Expenses allocated to flocks',output);
    for(const flock of r.flock_expenses)add('p',flock.name+': '+farmCostMoney(flock.allocated_minor,r.currency),output);
    add('p','Unallocated recorded expenses: '+farmCostMoney(r.unallocated_expenses_minor,r.currency),output);
    add('p','Money is shown in '+r.currency+', rounded for display. Cost estimates remain provisional.',output);
    const list=add('ul',null,output);for(const text of r.limitations)add('li',text,list);
    const refs=add('details',null,output);add('summary','Source references and settings version',refs);add('p','Settings version: '+r.policy_revision,refs);for(const ref of r.source_references)add('p',ref,refs);
    status.textContent='Provisional calculation updated from recorded evidence.';
  }catch(error){output.replaceChildren();status.textContent=error.message;}finally{calculate.disabled=false;}};
  await mountEggLossLinks(root);
  await mountExpenseAllocations(root);
  if(!data.can_configure)return;
  const settings=add('form');add('h4','Owner: costing settings and units',settings);add('p','Blank means unknown. Feed valuation applies to the selected report period. Saving keeps earlier versions; these settings do not change stock records.',settings);
  const currency=input(settings,'Currency (NGN, USD, GBP or EUR)','farmCostCurrency');currency.value='NGN';currency.required=true;
  const price=input(settings,'Feed price per kg in the selected currency (optional)','farmCostFeed','number');price.min='0';price.step='0.01';
  const basisLabel=add('label','Price basis',settings),basis=add('select',null,basisLabel);basis.id='farmCostBasis';
  for(const [value,text] of [['UNKNOWN','Unknown — no price supplied'],['RECORDED_PRICE','Recorded price — source required'],['PLANNING_ESTIMATE','Optional planning estimate']]){const option=add('option',text,basis);option.value=value;}
  const source=input(settings,'Price source (required for a recorded price)','farmCostSource');source.maxLength=400;
  const crate=input(settings,'Eggs per crate (optional)','farmCostCrate','number');crate.min='1';crate.step='1';
  const bag=input(settings,'Kilograms per bag (optional)','farmCostBag','number');bag.min='0.001';bag.step='0.001';
  const reason=input(settings,'Reason for these settings','farmCostReason');reason.required=true;reason.maxLength=400;
  const save=add('button','Save costing settings',settings);save.type='submit';let pending=null;
  settings.onsubmit=async e=>{e.preventDefault();save.disabled=true;try{
    const values={price_basis:basis.value,price_source:source.value,currency:currency.value,feed_minor_per_kg:price.value===''?null:farmMinorAmount(price.value),eggs_per_crate:crate.value===''?null:Number(crate.value),kg_per_bag:bag.value||null,reason:reason.value};const signature=JSON.stringify(values);
    if(pending&&pending.signature!==signature)throw Error('Retry unchanged or reload: the previous save outcome is unknown.');
    if(!pending)pending={signature,payload:{...values,event_id:crypto.randomUUID(),expected_revision:data.revision}};
    await farmPost('/api/farm/costing/policy',pending.payload);data.revision=pending.payload.event_id;data.policies.push({payload:pending.payload,received_at:new Date().toISOString()});pending=null;options();status.textContent='Costing settings saved as a new version.';
  }catch(error){if(error.status>=400&&error.status<500)pending=null;status.textContent=error.message;}finally{save.disabled=false;}};
}
async function mountEggLossLinks(root){
  const host=document.createElement('section');host.id='farmEggLossLinks';root.append(host);
  const add=(tag,text,parent=host)=>{const e=document.createElement(tag);if(text)e.textContent=text;parent.append(e);return e;};
  add('h4','Link recorded egg losses');
  add('p','Choose where lost eggs came from only when you know. Links explain existing losses; they do not deduct stock again. Leave unknown sources unallocated.');
  const message=add('p');message.setAttribute('role','status');const rows=add('div');
  async function load(){
    const data=await api('/api/farm/egg-loss-links');rows.replaceChildren();
    if(!data.items.length)add('p','No recorded egg losses to link.',rows);
    for(const item of data.items){
      const loss=item.loss,card=add('article',null,rows);
      add('p',loss.location+' — '+farmDateText(loss.observed_at)+' — '+loss.quantity+' eggs lost',card);
      add('p',item.unallocated+' eggs unallocated'+(item.needs_review?' — source changed; review these links.':''),card);
      if(!data.can_edit){add('p','The Owner can change loss links.',card);continue;}
      const fields=add('div',null,card),inputs=[];
      function line(existing){
        const group=add('div',null,fields),label=add('label','Collection record',group),select=add('select',null,label);select.setAttribute('aria-label','Collection record');
        const empty=add('option','Choose a collection',select);empty.value='';
        for(const c of data.collections){const opt=add('option',c.location+' — '+farmDateText(c.observed_at)+' — '+c.quantity+' eggs',select);opt.value=c.event_id;}
        if(existing&&!data.collections.some(c=>c.event_id===existing.collection_id)){const opt=add('option','Earlier collection was corrected — choose its current record',select);opt.value=existing.collection_id;}
        if(existing)select.value=existing.collection_id;
        const countLabel=add('label','Eggs linked to this collection',group),count=add('input',null,countLabel);count.type='number';count.min='1';count.step='1';if(existing)count.value=existing.eggs;
        const remove=add('button','Remove this link',group);remove.type='button';const entry={select,count,group};inputs.push(entry);remove.onclick=()=>{inputs.splice(inputs.indexOf(entry),1);group.remove();};
      }
      item.allocations.forEach(line);
      const more=add('button','Add collection link',card);more.type='button';more.onclick=()=>line();
      const whyLabel=add('label','Reason for loss links',card),why=add('input',null,whyLabel);why.maxLength=400;
      const save=add('button','Save loss links',card);save.type='button';let pending=null;
      save.onclick=async()=>{save.disabled=true;try{
        const values={loss_id:loss.event_id,expected_revision:item.revision,allocations:inputs.map(x=>({collection_id:x.select.value,eggs:Number(x.count.value)})),reason:why.value};
        const signature=JSON.stringify(values);
        if(pending&&pending.signature!==signature)throw Error('Retry unchanged or reload; the previous save outcome is unknown.');
        if(!pending)pending={signature,payload:{...values,event_id:crypto.randomUUID()}};
        await farmPost('/api/farm/egg-loss-links',pending.payload);pending=null;await load();
        document.getElementById('farmCostOutput').replaceChildren();
        message.textContent='Loss links saved. Stock unchanged. Recalculate costs to include the links.';
      }catch(error){if(error.status>=400&&error.status<500)pending=null;message.textContent=error.message;}finally{save.disabled=false;}};
    }
  }
  await load();
}
async function mountExpenseAllocations(root){
  const host=document.createElement('section');host.id='farmExpenseAllocations';root.append(host);
  const add=(tag,text,parent=host)=>{const e=document.createElement(tag);if(text)e.textContent=text;parent.append(e);return e;};
  add('h4','Split recorded expenses between flocks');
  add('p','Enter each flock’s exact share in the bill’s currency. Leave unknown shares unallocated. This does not create another expense or change stock. The Owner can revise shares; earlier versions are kept.');
  const message=add('p');message.setAttribute('role','status');const rows=add('div');
  async function load(){
    const data=await api('/api/farm/expense-allocations');rows.replaceChildren();
    if(!data.items.length)add('p','No recorded expenses to allocate.',rows);
    for(const item of data.items){
      const expense=item.expense,card=add('article',null,rows);
      add('p',expense.counterparty+' — '+farmDateText(expense.observed_at)+' — '+farmCostMoney(expense.amount_minor,expense.currency),card);
      add('p','Unallocated: '+farmCostMoney(item.unallocated_minor,expense.currency),card);
      if(!data.can_edit){
        for(const share of item.allocations){const flock=data.flocks.find(f=>f.entity_id===share.flock_id);add('p',(flock?.name||'Unavailable flock')+': '+farmCostMoney(share.amount_minor,expense.currency),card);}
        continue;
      }
      const fields=add('div',null,card),inputs=[];
      function line(existing){
        const group=add('div',null,fields),label=add('label','Flock receiving this expense share',group),select=add('select',null,label);select.setAttribute('aria-label','Flock receiving this expense share');
        const empty=add('option','Choose a flock',select);empty.value='';
        for(const f of data.flocks){const opt=add('option',f.name,select);opt.value=f.entity_id;}
        if(existing)select.value=existing.flock_id;
        const amountLabel=add('label','Expense share ('+expense.currency+')',group),amount=add('input',null,amountLabel);amount.type='number';amount.min='0.01';amount.step='0.01';
        if(existing)amount.value=(BigInt(existing.amount_minor)/100n)+'.'+(BigInt(existing.amount_minor)%100n).toString().padStart(2,'0');
        const remove=add('button','Remove flock share',group);remove.type='button';const entry={select,amount};inputs.push(entry);remove.onclick=()=>{inputs.splice(inputs.indexOf(entry),1);group.remove();};
      }
      item.allocations.forEach(line);
      const more=add('button','Add flock share',card);more.type='button';more.onclick=()=>line();
      const whyLabel=add('label','Reason for expense allocation',card),why=add('input',null,whyLabel);why.maxLength=400;
      const save=add('button','Save expense allocation',card);save.type='button';let pending=null;
      save.onclick=async()=>{save.disabled=true;try{
        const values={expense_id:expense.event_id,expected_revision:item.revision,allocations:inputs.map(x=>({flock_id:x.select.value,amount_minor:farmMinorAmount(x.amount.value)})),reason:why.value};
        const signature=JSON.stringify(values);
        if(pending&&pending.signature!==signature)throw Error('Retry unchanged or reload; the previous save outcome is unknown.');
        if(!pending)pending={signature,payload:{...values,event_id:crypto.randomUUID()}};
        await farmPost('/api/farm/expense-allocations',pending.payload);pending=null;await load();
        document.getElementById('farmCostOutput').replaceChildren();message.textContent='Expense allocation saved. No additional expense or stock change. Recalculate to update flock figures.';
      }catch(error){if(error.status>=400&&error.status<500)pending=null;message.textContent=error.message;}finally{save.disabled=false;}};
    }
  }
  await load();
}
