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
  const start=input(form,'From (Lagos)','farmCostStart','date'),end=input(form,'Through (Lagos)','farmCostEnd','date');start.required=end.required=true;
  const today=new Date(Date.now()+3600000).toISOString().slice(0,10);start.value=end.value=today;
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
    for(const [label,key] of [['Feed consumed (kg)','feed_consumed_kg'],['Eggs collected','collected_eggs']])add('p',label+': '+(r[key]===null?'Unavailable':r[key]),output);
    for(const [label,key] of [['Feed purchases','feed_purchases_minor'],['Bird acquisition','bird_acquisition_minor'],['Other recorded operating expenses','operating_expenses_minor'],['Consumed feed valuation','consumed_feed_cost_minor'],['Cost per collected egg','cost_per_collected_egg_minor'],['Cost per saleable egg','saleable_cost_per_egg_minor']])add('p',label+': '+farmCostMoney(r[key],r.currency,key.includes('per_egg')||key.includes('per_collected_egg')?4:2),output);
    add('p','Money is shown in '+r.currency+', rounded for display. Cost estimates remain provisional.',output);
    const list=add('ul',null,output);for(const text of r.limitations)add('li',text,list);
    const refs=add('details',null,output);add('summary','Source references and settings version',refs);add('p','Settings version: '+r.policy_revision,refs);for(const ref of r.source_references)add('p',ref,refs);
    status.textContent='Provisional calculation updated from recorded evidence.';
  }catch(error){output.replaceChildren();status.textContent=error.message;}finally{calculate.disabled=false;}};
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
