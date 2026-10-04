'use strict';
async function renderPhysicalCounts(){
  let card=document.getElementById('farmPhysicalCounts');
  if(!card){
    card=document.createElement('section');card.id='farmPhysicalCounts';card.className='card';
    card.innerHTML='<h2>Current stock: count and adjustment</h2><p>Use this when the stock you physically count differs from the record, such as finding an uncounted tray of eggs. Enter the total counted, not just the extra amount. For an old collection or loss record, use Record activity or update history instead. Record what was counted and when. A count does not change stock until the Owner reviews and approves it. Use individual birds or eggs, kilograms of feed, or a saved crate/bag size.</p><form id="farmPhysicalCountForm"><label>Flock or store<select id="farmPhysicalEntity" required></select></label><label>Unit<select id="farmPhysicalUnit"><option value="birds">Birds</option><option value="eggs">Eggs</option><option value="kg">Feed (kg)</option></select></label><label>Counted quantity<input id="farmPhysicalQuantity" type="number" min="0" max="999999999" step="0.001" required></label><p id="farmPhysicalPreview" role="status"></p><label>Counted at (Lagos)<input id="farmPhysicalObserved" type="datetime-local" required></label><label>Count source and reason<input id="farmPhysicalReason" maxlength="400" required></label><button type="submit">Save physical count for review</button></form><p id="farmPhysicalCountResult" role="status"></p><div id="farmPhysicalItems"></div>';
    document.getElementById('farmRecords').append(card);
    document.getElementById('farmPhysicalObserved').value=new Date(Date.now()+3600000).toISOString().slice(0,16);
    const preview=()=>{const unit=document.getElementById('farmPhysicalUnit').value,target=document.getElementById('farmPhysicalPreview');try{const c=['crates','bags'].includes(unit)?farmConvertedQuantity(unit,document.getElementById('farmPhysicalQuantity').value):null;target.textContent=c?'Will submit a total count of '+c.base+' '+c.unit+'. Owner review is still required.':'Enter the total physically counted, not an increase or decrease.';}catch(error){target.textContent=error.message;}};
    document.getElementById('farmPhysicalUnit').addEventListener('change',preview);document.getElementById('farmPhysicalQuantity').addEventListener('input',preview);
    let pending=null;
    document.getElementById('farmPhysicalCountForm').onsubmit=async event=>{
      event.preventDefault();const button=event.target.querySelector('button');button.disabled=true;
      try{
        const value=id=>document.getElementById(id).value;
        const fields={operation:'COUNT',entity_id:value('farmPhysicalEntity'),unit:value('farmPhysicalUnit'),quantity:value('farmPhysicalQuantity'),observed_at:new Date(value('farmPhysicalObserved')+'+01:00').toISOString(),reason:value('farmPhysicalReason')};
        if(['crates','bags'].includes(fields.unit)){const c=farmConvertedQuantity(fields.unit,fields.quantity);fields.conversion={unit:fields.unit,quantity:fields.quantity,policy_revision:farmUnitSettings.revision};fields.unit=c.unit==='eggs'?'eggs':'kg';fields.quantity=c.base;}
        const signature=JSON.stringify(fields);
        if(pending&&pending.signature!==signature)throw Error('Retry unchanged; the previous count save has an unknown result.');
        if(!pending)pending={signature,payload:{...fields,event_id:crypto.randomUUID()}};
        await farmPost('/api/farm/physical-counts',pending.payload);pending=null;
        document.getElementById('farmPhysicalCountResult').textContent='Physical count saved for Owner review. Stock is unchanged.';
        await renderPhysicalCounts();
      }catch(error){if(error.status>=400&&error.status<500)pending=null;document.getElementById('farmPhysicalCountResult').textContent=error.message;}
      finally{button.disabled=false;}
    };
  }
  const unitSelect=document.getElementById('farmPhysicalUnit'),oldUnit=unitSelect.value;unitSelect.replaceChildren();
  for(const [value,text] of [['birds','Birds'],['eggs','Eggs'],['kg','Feed (kg)'],...(farmUnitSettings.eggs_per_crate!==null?[['crates','Crates ('+farmUnitSettings.eggs_per_crate+' eggs each)']]:[]),...(farmUnitSettings.kg_per_bag!==null?[['bags','Bags ('+farmUnitSettings.kg_per_bag+' kg each)']]:[])]){const option=document.createElement('option');option.value=value;option.textContent=text;unitSelect.append(option);}
  if([...unitSelect.options].some(o=>o.value===oldUnit))unitSelect.value=oldUnit;unitSelect.dispatchEvent(new Event('change'));
  document.getElementById('farmPhysicalCountForm').hidden=!canDomain('work.request','farming');
  farmOptions(document.getElementById('farmPhysicalEntity'),farmCatalog.filter(e=>e.entity_type!=='HOUSE').map(e=>[e.entity_id,e.name]),'Choose flock or store');
  const data=await api('/api/farm/physical-counts'),items=document.getElementById('farmPhysicalItems');items.replaceChildren();
  for(const item of data.items){
    const p=item.payload,row=document.createElement('article');row.className='physicalCountItem';
    const name=farmCatalog.find(e=>e.entity_id===p.entity_id)?.name||'Farm stock';
    briefText(row,'h3',name+' — '+p.quantity+' '+p.unit);
    briefText(row,'p','Counted '+farmDateText(p.observed_at)+'; entered '+farmDateText(item.received_at));
    briefText(row,'p',p.reason);
    if(p.conversion)briefText(row,'p','Entered as '+p.conversion.quantity+' '+p.conversion.unit+' using the saved size at submission.');
    briefText(row,'p',item.recorded_balance===null?'Opening stock unknown; no adjustment can be approved.':'Recorded at count time: '+item.recorded_balance+'; difference: '+item.difference+' '+p.unit);
    briefText(row,'p',{PENDING:'Awaiting Owner review',APPROVE:'Approved',REJECT:'Rejected'}[item.state]);
    for(const decision of item.decisions)briefText(row,'p','Owner decision: '+decision.payload.reason+' — '+farmDateText(decision.received_at));
    if(item.state==='PENDING'&&item.stale)briefText(row,'p','Stock history changed. Review and submit a fresh count before approval.');
    if(data.can_approve&&item.state==='PENDING'){
      const label=briefText(row,'label','Owner decision reason'),reason=document.createElement('input');reason.maxLength=400;label.append(reason);
      const actions=item.stale||item.difference===null?[['REJECT','Reject count']]:[['APPROVE','Approve stock adjustment'],['REJECT','Reject count']];
      let pending=null;
      for(const [operation,text] of actions){const button=briefText(row,'button',text);button.type='button';button.onclick=async()=>{
        button.disabled=true;
        try{
          const fields={operation,reference:p.event_id,reason:reason.value},signature=JSON.stringify(fields);
          if(pending&&pending.signature!==signature)throw Error('Retry the previous decision unchanged; its result is unknown.');
          if(!pending)pending={signature,payload:{...fields,event_id:crypto.randomUUID()}};
          await farmPost('/api/farm/physical-counts',pending.payload);pending=null;await loadFarmRecords();
        }catch(error){if(error.status>=400&&error.status<500)pending=null;document.getElementById('farmPhysicalCountResult').textContent=error.message;}
        finally{button.disabled=false;}
      };}
    }
    items.append(row);
  }
}
