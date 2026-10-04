'use strict';
let financeData=null,financeOffset=0,financeContacts=[];
const financeLabels={DISPUTE_DEBT:'Balance disputed',RESOLVE_DEBT_DISPUTE:'Dispute resolved by Owner',OPENING_RECEIVABLE:'Opening debt owed to the farm',OPENING_PAYABLE:'Opening debt the farm owes',DEBT_DOCUMENT:'Invoice attached to opening debt',SALE:'Sale',EXPENSE_CLAIM:'Reported expense',PURCHASE_REQUEST:'Purchase request',PAYMENT_CLAIM:'Payment claim',CONFIRM_PAYMENT:'Payment confirmed',APPROVE_REQUEST:'Request approved',REJECT_REQUEST:'Request rejected',VOID:'Voided record'};
function financeMoney(value,currency){const n=BigInt(value);return currency+' '+(n/100n).toString()+'.'+(n%100n).toString().padStart(2,'0');}
function financeElement(tag,text){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;return e;}
function financeDetails(){
  const kind=document.getElementById('farmMoneyKind').value;
  if(!['SALE','EXPENSE_CLAIM','PURCHASE_REQUEST','OPENING_RECEIVABLE','OPENING_PAYABLE'].includes(kind))return null;
  const items=[...document.querySelectorAll('#financeLines .financeLine')].map(row=>({description:row.querySelector('input[type=text]').value.trim(),amount_minor:farmMinorAmount(row.querySelector('input[type=number]').value)}));
  if(!document.getElementById('financeCategory').value){if(items.length||document.getElementById('financeContact').value||document.getElementById('financeDue').value)throw new Error('Choose a category for these transaction details.');return null;}
  return {category:document.getElementById('financeCategory').value,contact_id:document.getElementById('financeContact').value||null,due_on:document.getElementById('financeDue').value||null,items};
}
function financeQuery(offset=0){const q=new URLSearchParams({offset:String(offset)});for(const [id,key] of [['financeFrom','start'],['financeTo','end'],['financeSearch','q'],['financeFilterContact','contact']]){const value=document.getElementById(id).value;if(value)q.set(key,value);}return q;}
async function mountFinance(){
  const parent=document.getElementById('farmFinanceCard');if(document.getElementById('financeDashboard'))return;
  const dashboard=financeElement('section');dashboard.id='financeDashboard';
  dashboard.innerHTML='<h3>Financial overview</h3><p>Reported sales and expenses are separate from confirmed payments. Amounts in different currencies are never combined.</p><form id="financeFilters"><label>From (Lagos)<input id="financeFrom" type="date"></label><label>Through (Lagos)<input id="financeTo" type="date"></label><label>Search description, name or reference<input id="financeSearch" maxlength="120"></label><label>Customer or supplier<select id="financeFilterContact"></select></label><button type="submit">Apply financial filters</button></form><div id="financeSummary"></div><p id="financeNotice"></p><button type="button" id="financeExport">Export filtered records (CSV)</button> <button type="button" id="financeStatement">Prepare statement</button><p id="financeStatus" role="status"></p><div id="financeLedger"></div><button type="button" id="financePrevious">Previous financial page</button> <button type="button" id="financeNext">Next financial page</button><div id="financePrint" hidden></div>';
  parent.querySelector('h2').after(dashboard);
  const extra=financeElement('fieldset');extra.id='financeExtra';extra.innerHTML='<legend>Sale, expense, purchase or opening debt details</legend><label>Category<select id="financeCategory"></select></label><label>Saved customer or supplier<select id="financeContact"></select></label><label>Due date (optional)<input id="financeDue" type="date"></label><div id="financeLines"></div><button type="button" id="financeAddLine">Add line item</button><p>Line amounts, when supplied, must add up to the transaction amount. Payment claims and approval decisions do not use these details.</p>';
  document.getElementById('farmFinanceForm').querySelector('button[type=submit]').before(extra);
  const directory=financeElement('details');directory.innerHTML='<summary>Add a customer or supplier</summary><form id="financeContactForm"><label>Name<input id="financeContactName" maxlength="120" required></label><label>Role<select id="financeContactType"><option value="CUSTOMER">Customer</option><option value="SUPPLIER">Supplier</option><option value="BOTH">Customer and supplier</option></select></label><label>Contact details (optional)<input id="financeContactInfo" maxlength="160"></label><label>Phone (optional)<input id="financeContactPhone" type="tel" maxlength="40"></label><label>Email (optional)<input id="financeContactEmail" type="email" maxlength="254"></label><button type="submit">Save contact</button></form><p id="financeContactStatus" role="status"></p>';parent.append(directory);
  const receipt=financeElement('details');receipt.innerHTML='<summary>Attach a receipt</summary><p>Select its financial record using “Use as reference” above, then choose a receipt photo. Images are private; attachments do not confirm payment.</p><label>Receipt photo<input type="file" id="financeReceiptFile" accept="image/jpeg,image/png"></label><button type="button" id="financeReceiptSave">Save receipt photo</button><p id="financeReceiptStatus" role="status"></p>';parent.append(receipt);
  const syncDetails=()=>{extra.disabled=!['SALE','EXPENSE_CLAIM','PURCHASE_REQUEST','OPENING_RECEIVABLE','OPENING_PAYABLE'].includes(document.getElementById('farmMoneyKind').value);};
  document.getElementById('farmMoneyKind').addEventListener('change',syncDetails);syncDetails();
  document.getElementById('financeContact').onchange=()=>{const c=financeContacts.find(c=>c.event_id===document.getElementById('financeContact').value);if(c)document.getElementById('farmCounterparty').value=c.name;};
  document.getElementById('financeAddLine').onclick=()=>{if(document.querySelectorAll('.financeLine').length>=30)return;const row=financeElement('div');row.className='financeLine';row.innerHTML='<label>Description<input type="text" maxlength="120" required></label><label>Line amount<input type="number" min="0.01" max="9999999999.99" step="0.01" required></label>';const remove=financeElement('button','Remove line');remove.type='button';remove.onclick=()=>row.remove();row.append(remove);document.getElementById('financeLines').append(row);};
  document.getElementById('financeFilters').onsubmit=async e=>{e.preventDefault();financeOffset=0;await financeRefreshSafe();};
  document.getElementById('financePrevious').onclick=async()=>{financeOffset=Math.max(0,financeOffset-100);await financeRefreshSafe();};
  document.getElementById('financeNext').onclick=async()=>{financeOffset+=100;await financeRefreshSafe();};
  let pendingContact=null;
  document.getElementById('financeContactForm').onsubmit=async e=>{e.preventDefault();const button=e.target.querySelector('button');button.disabled=true;try{const fields={operation:'contact',name:document.getElementById('financeContactName').value.trim(),type:document.getElementById('financeContactType').value,contact:document.getElementById('financeContactInfo').value.trim(),phone:document.getElementById('financeContactPhone').value.trim(),email:document.getElementById('financeContactEmail').value.trim()};const signature=JSON.stringify(fields);if(pendingContact&&pendingContact.signature!==signature)throw new Error('Retry the previous contact first; its save result is unknown.');if(!pendingContact)pendingContact={signature,body:{...fields,event_id:crypto.randomUUID()}};await farmPost('/api/farm/finance',pendingContact.body);pendingContact=null;document.getElementById('financeContactStatus').textContent='Contact saved.';await refreshFinance();}catch(error){if(error.status>=400&&error.status<500)pendingContact=null;document.getElementById('financeContactStatus').textContent=error.message;}finally{button.disabled=false;}};
  document.getElementById('financeExport').onclick=exportFinance;
  document.getElementById('financeStatement').onclick=()=>printFinance(null);
  document.getElementById('financeReceiptSave').onclick=saveFinanceReceipt;
}
async function financeRefreshSafe(){try{await refreshFinance();}catch(error){document.getElementById('financeStatus').textContent=error.message;}}
async function refreshFinance(){
  financeData=await api('/api/farm/finance?'+financeQuery(financeOffset));financeContacts=financeData.contacts;
  farmOptions(document.getElementById('financeCategory'),Object.entries(financeData.categories),'Not specified');
  farmOptions(document.getElementById('financeContact'),financeContacts.map(c=>[c.event_id,c.name+' ('+c.type.toLowerCase()+')']),'No saved contact');
  farmOptions(document.getElementById('financeFilterContact'),financeContacts.map(c=>[c.event_id,c.name]),'All customers and suppliers');
  const summary=document.getElementById('financeSummary');summary.replaceChildren();
  const labels={sales:'Reported sales',expenses:'Reported expenses',received:'Confirmed money received',paid:'Confirmed money paid',receivable:'Customers still owe',payable:'Expenses still unpaid',unconfirmed:'Payment claims awaiting confirmation',pending_approvals:'Purchase requests awaiting decision'};
  for(const [currency,values] of Object.entries(financeData.summaries)){const card=financeElement('article');card.className='card';card.append(financeElement('h4',currency));for(const [key,label] of Object.entries(labels))card.append(financeElement('p',label+': '+(key==='pending_approvals'?values[key]:financeMoney(values[key],currency))));summary.append(card);}
  if(!summary.children.length)summary.textContent='No financial records match these filters.';
  document.getElementById('financeNotice').textContent=financeData.notice;
  const ledger=document.getElementById('financeLedger');ledger.replaceChildren();
  for(const r of financeData.records){const p=r.payload,card=financeElement('article');card.className='item';card.append(financeElement('h4',(financeLabels[p.kind]||p.kind)+' - '+financeMoney(p.amount_minor,p.currency)),financeElement('p',p.counterparty||'No customer or supplier recorded'),financeElement('p',p.reason),financeElement('p',farmDateText(p.observed_at)),financeElement('p','Category: '+(financeData.categories[p.details?.category]||'Not recorded')));
    const balance=financeData.balances.find(b=>b.id===p.event_id);if(balance)card.append(financeElement('p','Outstanding: '+financeMoney(balance.outstanding_minor,p.currency)+'; due '+(balance.due_on||'not recorded')+(balance.overdue?' - overdue':'')+(balance.disputed?' - disputed; Owner review required':'')));
    const use=financeElement('button','Use as reference');use.type='button';use.onclick=()=>{document.getElementById('farmMoneyReference').value=p.event_id;document.getElementById('farmCurrency').value=p.currency;};card.append(use);
    const history=financeElement('button','View linked history');history.type='button';history.onclick=async()=>{
      history.disabled=true;
      try{
        const data=await api('/api/farm/finance/history/'+encodeURIComponent(p.event_id));
        const box=financeElement('section');box.className='financeLinkedHistory';box.append(financeElement('h5','Linked financial history'),financeElement('p',data.notice));
        if(data.truncated)box.append(financeElement('p','This view reached its 1,000-record limit. The displayed history is incomplete.'));
        for(const row of data.records){const value=row.payload;box.append(financeElement('p',[
          financeLabels[value.kind]||'Financial record',financeMoney(value.amount_minor,value.currency),value.counterparty,
          row.is_current?'Current record':'Voided original',value.reason,'Recorded by '+row.actor,
          'Observed '+farmDateText(value.observed_at),'Entered '+farmDateText(row.received_at),
          'Reference '+value.event_id,value.reference?'Linked to '+value.reference:''].filter(Boolean).join(' · ')));}
        card.append(box);
      }catch(error){document.getElementById('financeStatus').textContent=error.message;history.disabled=false;}
    };card.append(history);
    if(p.kind==='SALE'){const invoice=financeElement('button','Prepare invoice');invoice.type='button';invoice.onclick=()=>printFinance(p);card.append(invoice);}
    for(const receipt of financeData.receipts.filter(a=>a.reference===p.event_id)){const view=financeElement('button','View receipt');view.type='button';view.onclick=()=>{view.disabled=true;const image=financeElement('img');image.alt='Private receipt';image.style.maxWidth='100%';image.src='/api/farm/receipts/'+encodeURIComponent(receipt.event_id);card.append(image);};card.append(view);}ledger.append(card);
  }
  document.getElementById('financePrevious').disabled=financeOffset===0;document.getElementById('financeNext').disabled=financeOffset+100>=financeData.record_count;
}
function financeCsvCell(value){let s=String(value??'');if(/^[\s]*[=+@-]/.test(s)||/^[\t\r\n]/.test(s))s="'"+s;return '"'+s.replaceAll('"','""')+'"';}
async function financePages(){
  const filters=financeQuery(0);let records=[],balances=[],offset=0,revision=null;
  while(true){
    const query=new URLSearchParams(filters);query.set('offset',String(offset));
    const data=await api('/api/farm/finance?'+query);
    if(revision!==null&&revision!==data.revision)throw new Error('Financial records changed. Retry with the latest records.');
    revision=data.revision;
    if(data.record_count>10000)throw new Error('Narrow the filters to at most 10,000 records before preparing this document.');
    records.push(...data.records);balances.push(...data.balances);offset+=data.records.length;
    if(offset>=data.record_count)break;
    if(!data.records.length)throw new Error('Records changed; retry.');
  }
  return {records,balances};
}
async function exportFinance(){const button=document.getElementById('financeExport');button.disabled=true;try{const all=(await financePages()).records;
  const columns=['Reference','Record type','Currency','Amount','Customer/supplier','Category','Due date','Observed UTC','Description'];const rows=all.map(r=>{const p=r.payload;return [p.event_id,financeLabels[p.kind]||p.kind,p.currency,financeMoney(p.amount_minor,p.currency).split(' ')[1],p.counterparty,p.details?.category||'Unclassified legacy',p.details?.due_on||'',p.observed_at,p.reason];});const blob=new Blob(['\uFEFF'+[columns,...rows].map(r=>r.map(financeCsvCell).join(',')).join('\r\n')],{type:'text/csv;charset=utf-8'});const url=URL.createObjectURL(blob),a=financeElement('a');a.href=url;a.download='farm-financial-records.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);document.getElementById('financeStatus').textContent=all.length+' records exported. Store the file privately.';}catch(error){document.getElementById('financeStatus').textContent=error.message;}finally{button.disabled=false;}}
async function printFinance(invoice){let balances=financeData.balances;if(!invoice){try{balances=(await financePages()).balances;}catch(error){document.getElementById('financeStatus').textContent=error.message;return;}}const box=document.getElementById('financePrint');box.replaceChildren();box.hidden=false;box.append(financeElement('h2',invoice?'Sales invoice':'Customer / supplier statement'),financeElement('p','Farm operational record - not evidence of payment.'));
  if(invoice){box.append(financeElement('p','Reference: '+invoice.event_id),financeElement('p','Customer: '+invoice.counterparty),financeElement('p','Total: '+financeMoney(invoice.amount_minor,invoice.currency)),financeElement('p','Due: '+(invoice.details?.due_on||'Not specified')));for(const item of invoice.details?.items||[])box.append(financeElement('p',item.description+' - '+financeMoney(item.amount_minor,invoice.currency)));box.append(financeElement('p',invoice.reason));}
  else{box.append(financeElement('p','Selected filters; outstanding amounts as of now.'));for(const b of balances)box.append(financeElement('p',b.counterparty+' - '+(['SALE','OPENING_RECEIVABLE'].includes(b.kind)?'Receivable: ':'Payable: ')+financeMoney(b.outstanding_minor,b.currency)+'; due '+(b.due_on||'not recorded')+(b.disputed?' — disputed':'')));}
  const print=financeElement('button','Print this document');print.type='button';print.onclick=()=>{document.body.classList.add('printingFinance');try{window.print();}finally{document.body.classList.remove('printingFinance');}};box.append(print);box.scrollIntoView({block:'start'});
}
let pendingReceipt=null;
async function saveFinanceReceipt(){const button=document.getElementById('financeReceiptSave');button.disabled=true;try{const file=document.getElementById('financeReceiptFile').files[0],reference=document.getElementById('farmMoneyReference').value;if(!file||!reference)throw new Error('Choose a related financial record and receipt photo.');if(file.size>8*1024*1024||!['image/jpeg','image/png'].includes(file.type))throw new Error('Choose a JPEG or PNG up to8 MiB.');const signature=reference+':'+file.name+':'+file.size+':'+file.lastModified;if(pendingReceipt&&pendingReceipt.signature!==signature)throw new Error('Retry the previous receipt first; its save result is unknown.');if(!pendingReceipt){const image=await createImageBitmap(file);try{if(image.width*image.height>24000000)throw new Error('Receipt image is too large.');const ratio=Math.min(1,1280/Math.max(image.width,image.height)),canvas=document.createElement('canvas');canvas.width=Math.max(1,Math.round(image.width*ratio));canvas.height=Math.max(1,Math.round(image.height*ratio));canvas.getContext('2d').drawImage(image,0,0,canvas.width,canvas.height);pendingReceipt={signature,body:{operation:'receipt',event_id:crypto.randomUUID(),reference,image_base64:canvas.toDataURL('image/png').split(',')[1]}};}finally{image.close();}}await farmPost('/api/farm/finance',pendingReceipt.body);pendingReceipt=null;document.getElementById('financeReceiptStatus').textContent='Private receipt saved. Payment status unchanged.';await refreshFinance();}catch(error){if(error.status>=400&&error.status<500)pendingReceipt=null;document.getElementById('financeReceiptStatus').textContent=error.message;}finally{button.disabled=false;}}
