'use strict';

let componentPreview=null;

function clearModelSetupSecrets(){for(const id of ['setupKey','setupPassword','assignmentPassword','n8nPassword']){const input=document.getElementById(id);if(input)input.value='';}}

window.addEventListener('pagehide',clearModelSetupSecrets);

document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='hidden')clearModelSetupSecrets();});

document.addEventListener('click',event=>{if(event.target.closest('button[data-action="show"],button[data-action="open-agent"]'))clearModelSetupSecrets();},{capture:true});

function modelSetupForm(){return '<article class="card"><h2>Add model / provider</h2><p>Saved inactive and unassigned. Adding a model does not change current routing. Use the provider’s exact model identifier; this does not download model weights or install runtimes.</p><label for="setupProvider">Provider identifier (groq, mistral, or another provider)</label><input id="setupProvider" maxlength="40" autocomplete="off"><label for="setupModel">Model identifier</label><input id="setupModel" maxlength="200" autocomplete="off"><label for="setupCost">Pricing status (not verified)</label><select id="setupCost"><option value="UNKNOWN">Unknown — review required</option><option value="FREE">Free — operator declaration</option><option value="PAID">Paid — activation blocked</option></select><label for="setupKey">API key (optional; stored encrypted, never displayed again)</label><input id="setupKey" type="password" maxlength="4096" autocomplete="new-password" spellcheck="false"><label for="setupPassword">Confirm your Chief password</label><input id="setupPassword" type="password" autocomplete="current-password"><button type="button" class="primary" data-action="model-register">Save inactive model</button><p id="modelSetupResult" role="status"></p></article>';}

function modelRegistrations(rows){return '<h2>Imported models awaiting review</h2><p>Saving a connection does not assign it. Use Agent assignment below to test and activate Farm AI. Connection checks alone do not activate a model.</p>'+rows.map(r=>'<article class="card"><h3>'+esc(r.provider_model)+'</h3><p>'+esc(r.provider)+' · '+esc(r.cost)+' · DISABLED / unassigned · Key '+(r.key_configured?'stored privately':'not supplied')+'</p>'+((['groq','mistral'].includes(r.provider)&&r.key_configured)?'<button type="button" data-action="model-check" data-id="'+esc(r.id)+'">Check connection</button>':'')+'</article>').join('');}

const foundationPages=new Set(['health','components','capabilities','models','policies','runtime','integrations','devices','updates','evidence','ledger','runbooks']);

function foundationTable(columns,rows){return '<div class="tableScroll"><table><thead><tr>'+columns.map(([label])=>'<th scope="col">'+esc(label)+'</th>').join('')+'</tr></thead><tbody>'+rows.map(row=>'<tr>'+columns.map(([,read])=>'<td>'+esc(typeof read==='function'?read(row):row[read])+'</td>').join('')+'</tr>').join('')+'</tbody></table></div>';}

function readableDetails(value){
  if(value===null||value===undefined)return 'Not recorded';
  if(typeof value==='boolean')return value?'Yes':'No';
  if(Array.isArray(value))return value.length?'<ul>'+value.map(item=>'<li>'+readableDetails(item)+'</li>').join('')+'</ul>':'None recorded';
  if(typeof value==='object')return '<dl>'+Object.entries(value).filter(([key])=>!['tests','frameworks'].includes(key)).map(([key,item])=>'<dt>'+esc(key.replaceAll('_',' ').replace(/^./,c=>c.toUpperCase()))+'</dt><dd>'+readableDetails(item)+'</dd>').join('')+'</dl>';
  return esc(String(value));
}
function foundationDetails(title,data){return '<details class="foundationDetails"><summary>'+esc(title)+'</summary>'+readableDetails(data)+'</details>';}
function capabilityName(id){
  const names={'chief':'Chief Agent','jobs':'Job Agent','farming':'Farm Agent','farm-assistant':'Farm assistant','farming-recorder':'Farm record service','jobs-worker':'Job worker','jobs.worker':'Job processing service','chief.gateway':'AI connection service','chief.database':'Database','chief.policy':'Action policy','chief.evidence':'Shared evidence','chief.decision_ledger':'Decision history','chief.model_registry':'Model management','chief.runtime':'Background worker','chief.browser':'Browser service','farming.assistant.ask':'Ask Farm Agent','farming.records.read':'Read Farm records','farming.records.write':'Record Farm information','farming.reminders.write':'Manage Farm reminders','jobs.execute':'Process Job work'};
  return names[id]||String(id||'Not specified').replace(/^chief\./,'').replace(/[._-]/g,' ').replace(/^./,c=>c.toUpperCase());
}
function capabilityCatalog(data){
  const maturity={LIMITED:'Limited scope',FOUNDATION:'Foundation only',EXPERIMENTAL:'Experimental',STABLE:'Stable',PLANNED:'Planned'};
  const dependencies=rows=>(rows||[]).map(d=>capabilityName(d.node?.id)).join(', ')||'None declared';
  return '<p>This catalog describes available building blocks and their limits. A listing does not mean the feature is running, tested on this device or authorized to act.</p><h2>Capabilities</h2>'+(data.capabilities||[]).map(c=>'<article class="card"><h3>'+esc(capabilityName(c.id))+'</h3><p>'+esc(c.description)+'</p><dl><dt>Responsible area</dt><dd>'+esc(capabilityName(c.owner))+'</dd><dt>Declared maturity</dt><dd>'+esc(maturity[c.maturity]||String(c.maturity||'Not specified').replaceAll('_',' ').toLowerCase())+'</dd><dt>Depends on</dt><dd>'+esc(dependencies(c.dependencies))+'</dd></dl></article>').join('')+'<h2>Supporting components</h2>'+foundationTable([['Component',r=>capabilityName(r.id)],['Type',r=>({CORE:'Core',SERVICE:'Service',DOMAIN:'Domain',AGENT:'Agent'}[r.kind]||'Component')],['Depends on',r=>dependencies(r.dependencies)]],data.components||[]);
}
function recordCards(rows){return rows.length?rows.map(r=>'<article class="card"><h2>'+esc(r.id)+'</h2>'+foundationDetails('View recorded metadata',r)+'</article>').join(''):'<p>No records in this domain. Nothing has been inferred or created.</p>';}

async function loadFoundation(page){

  const box=document.getElementById(page+'Content');box.textContent='Loading current authorized state…';

  if(['evidence','ledger','runbooks'].includes(page)){

    const picker=document.getElementById(page+'Domain');const selected=picker.value||currentAgent;

    picker.innerHTML=chiefContext.domains.map(d=>'<option value="'+esc(d.id)+'">'+esc(d.label)+'</option>').join('');

    if(chiefContext.domains.some(d=>d.id===selected))picker.value=selected;

    const d=await api('/api/ui/'+page+'/'+encodeURIComponent(picker.value));

    box.innerHTML='<p class="limitation">'+esc(d.limitation)+'</p><p>Up to '+esc(d.limit||100)+' records · '+esc(d.status)+'</p>'+recordCards(d.rows);return;

  }

  if(page==='health'){

    const rows=await api('/api/system-health');

    box.innerHTML='<p>Observed health is independent of desired mode. UNKNOWN means health has not been established; it is not a passing test.</p>'+foundationTable([['Component',r=>r.node.id],['Observed health','status'],['Desired mode','desired_mode'],['Observation / dependency reason','reason']],rows);return;

  }

  if(page==='components'){

    componentPreview=null;const rows=await api('/api/component-controls');

    box.innerHTML='<p>Domain enabled, autostart and running settings remain under Manage Domains. Changes here require a current dependency preview and confirmation.</p>'+rows.map((r,i)=>'<article class="card"><h2>'+esc(r.node.id)+'</h2><p>Desired mode: <b>'+esc(r.mode)+'</b> · Authority: '+esc(r.authority)+'</p>'+((r.supported_modes||[]).length?'<label for="mode'+i+'">Requested mode</label><select id="mode'+i+'">'+r.supported_modes.map(m=>'<option'+(m===r.mode?' selected':'')+'>'+esc(m)+'</option>').join('')+'</select><button class="action" data-action="component-preview" data-kind="'+esc(r.node.kind)+'" data-id="'+esc(r.node.id)+'" data-select="mode'+i+'">Review dependency impact</button>':'<p>No supported transition in this interface.</p>')+'</article>').join('')+'<div id="componentImpact" class="card" role="status"></div>';return;

  }

  const d=await api('/api/ui/'+page);

  if(page==='models'){

    box.innerHTML=modelSetupForm()+modelRegistrations(d.registrations||[])+ '<h2>Latest provider observations</h2><p>These are past request or worker observations, not a live connection check or model qualification. Chief can start without API keys; unavailable models affect only requests that need them.</p>'+foundationTable([['Provider',r=>r.provider],['Last result',r=>r.status],['What to do',r=>r.message],['Observed',r=>r.observed_at||'Not checked']],d.provider_observations||[]);

    await renderModelAssignment(box,d.registrations||[]);

    box.insertAdjacentHTML('beforeend','<p>'+esc(d.routing)+'</p><p class="limitation">'+esc(d.limitation)+'</p>'+d.models.map(m=>'<article class="card"><h2>'+esc(m.id)+'</h2><p>'+esc(m.provider)+' · '+esc(m.provider_model)+' · '+esc(m.cost)+'</p><p>Global state: <b>'+esc(m.state)+'</b></p>'+['ENABLED','SHADOW','DISABLED'].map(state=>'<button class="action" data-action="model-state" data-id="'+esc(m.id)+'" data-state="'+state+'"'+(state===m.state?' disabled':'')+'>'+esc(state)+'</button>').join('')+'</article>').join('')+foundationDetails('Job assignment (read-only)',{assignment:d.job_assignment,eligible_route_models:d.eligible_route_models})+foundationDetails('Installation policy (read-only)',d.policy));return;

  }

  if(page==='runtime'){

    box.innerHTML='<p class="limitation">No runtime is qualified or selected.</p><p>'+esc(d.limitation)+'</p>'+d.candidates.map(c=>'<article class="card"><h2>'+esc(c.product)+'</h2><p>Pinned candidate '+esc(c.version)+' · Not qualified</p>'+foundationTable([['Mandatory gate',r=>r[0]],['Evidence state',r=>r[1]]],c.qualification.gate_status)+'</article>').join('');return;

  }

  if(page==='capabilities'){
    box.innerHTML=capabilityCatalog(d);return;
  }
  if(page==='policies'){
    const names={LAW_REGULATION:'Applicable law and regulation',HARD_SAFETY_SECURITY:'Mandatory safety and security rules',PROFESSIONAL_FRAMEWORK:'Professional requirements',CHIEF:'Chief-wide rules',INSTALLATION_OWNER:'Owner settings',AGENT_PREFERENCE:'Agent preferences',MODEL_RECOMMENDATION:'AI recommendations'};
    const risks={READ:['Read information','Viewing information within the person’s permitted area.'],RECORD:['Record information','Adding or updating records; access and evidence rules still apply.'],ADVISE:['Give advice','Suggestions only. Advice cannot approve or perform an action.'],LOW_RISK_AUTOMATION:['Lower-risk automation','Routine automated work, subject to capability, policy and approval checks.'],PHYSICAL_FINANCIAL:['Physical or financial action','Actions involving equipment, money or the physical environment require their applicable safeguards.'],HIGH_IMPACT:['High-impact action','Actions with serious consequences require their applicable safeguards and approvals.']};
    box.innerHTML='<article class="card"><h2>Which rules take priority?</h2><p>Higher-priority requirements constrain lower-priority preferences. An AI recommendation cannot override safety rules or approval requirements.</p><ol>'+d.precedence.map(name=>'<li>'+esc(names[name]||name.replaceAll('_',' '))+'</li>').join('')+'</ol></article><article class="card"><h2>Action risk levels</h2><p>A risk label describes an action. It does not grant permission or replace approval.</p>'+foundationTable([['Action type',r=>r[0]],['Meaning',r=>r[1]]],d.risk_classes.map(r=>risks[r]||[r,'No description recorded.']))+'</article><article class="card"><h2>Current enforcement</h2><p>Job Agent still uses its existing policy checks to enforce decisions. The newer shared policy engine compares decisions only; it has not replaced those checks.</p><p>Policy editing is not available here.</p></article>';return;
  }
  if(page==='integrations'){
    const n=d.n8n;
    box.innerHTML='<article class="card"><h2>AI connections</h2><p>Manage private model connections and agent assignments in Models.</p><button type="button" class="action" data-action="show" data-target="models">Manage AI connections</button></article><article class="card"><h2>n8n workflow connection</h2><p>'+esc(n.message)+'</p><p>Configuration: '+(n.configured?'Present':'Required')+' · Connection: '+(n.enabled?'Enabled':'Disabled')+'</p>'+(n.problem?'<p>'+esc(n.problem)+'</p>':'')+'<p>Configure the local workflow address and private credentials in service settings. Only a connection on this computer is supported.</p><label for="n8nPassword">Confirm your Chief password to change the connection</label><input id="n8nPassword" type="password" autocomplete="current-password"><button class="action" data-action="n8n-toggle" data-enabled="'+(!n.enabled)+'"'+(!n.enabled&&!n.configured?' disabled':'')+'>'+(n.enabled?'Disable connection':'Enable connection')+'</button><button class="action" data-action="n8n-check"'+(!n.enabled?' disabled':'')+'>Refresh workflow status</button><p id="n8nStatus" role="status"></p><div id="n8nWorkflows"></div></article><article class="card"><h2>Test workflow handoff</h2><p>Sends synthetic identifiers only. No farm records, messages, payments or equipment actions. An unknown result is never retried automatically.</p><label>Test domain<select id="n8nTestDomain">'+agentDefinitions.map(x=>'<option value="'+esc(x.id)+'">'+esc(x.label)+'</option>').join('')+'</select></label><p>Use the password confirmation above before running a test. The separate handshake credential and supplied workflow must be configured first.</p><button class="action" data-action="n8n-handshake"'+(!n.enabled||!n.handshake_configured?' disabled':'')+'>Run synthetic test</button><button class="action" data-action="n8n-history">Refresh test history</button><p id="n8nHandshakeStatus" role="status"></p><div id="n8nHistory"></div></article><article class="card"><h2>Farm record-count preview</h2><p>Review counts of journal entries and corrections. These are not production or stock totals. No names, notes, financial amounts or Job records are sent.</p><p>Uses a separately configured local reporting workflow. Confirm your Chief password above.</p><button class="action" data-action="n8n-report-preview"'+(!n.enabled||!n.report_configured?' disabled':'')+'>Preview Farm record counts</button><button class="action" data-action="n8n-report-history">Recent previews</button><p id="n8nReportStatus" role="status"></p><div id="n8nReportResult"></div></article>';return;
  }
  if(page==='devices'){
    box.innerHTML='<article class="card"><h2>Device connections are not available yet</h2><p>Chief does not currently discover, connect to or control cameras, sensors or equipment.</p><p>Camera details and device-specific testing are still required before connection support can be added. No equipment actions are enabled.</p></article>';return;
  }
  if(page==='updates'){
    const history=d.history||{events:[]};
    box.innerHTML='<article class="card"><h2>Update activity</h2><p>'+esc(d.limitation)+'</p><p>Dates show when an operator recorded the event. Source and evidence fingerprints identify the referenced artifacts; they do not prove a successful installation.</p></article>'+((history.events||[]).length?foundationTable([['Recorded',r=>r.recorded_at],['Reported outcome',r=>r.outcome],['Source fingerprint',r=>r.source_sha256],['Evidence fingerprint',r=>r.evidence_sha256]],history.events):'<p>No update activity has been recorded. This does not mean Chief has never been updated.</p>')+(history.invalid_records?'<p role="alert">Some history records could not be read. Their outcome is unknown.</p>':'')+(history.has_more?'<p>Showing the latest 20 records. Older records remain stored for operator review.</p>':'');return;
  }
  box.innerHTML=(d.limitation?'<p class="limitation">'+esc(d.limitation)+'</p>':'')+foundationDetails('Current foundation contract',d);

}

function foundationActions(button){const b=button.dataset;return {

  'model-register':async()=>{

    const key=document.getElementById('setupKey'),password=document.getElementById('setupPassword'),result=document.getElementById('modelSetupResult');

    button.disabled=true;

    let body;

    try{

      if(!password.value)throw new Error('Confirm your Chief password before saving.');

      await postJson('/api/auth/reauthenticate',{password:password.value});password.value='';

      body={provider:document.getElementById('setupProvider').value.trim(),provider_model:document.getElementById('setupModel').value.trim(),cost:document.getElementById('setupCost').value,api_key:key.value};key.value='';

      await postJson('/api/model-setup',body);await loadFoundation('models');

      document.getElementById('modelSetupResult').textContent='Saved inactive. Current model assignments are unchanged.';

    }catch(error){result.textContent=error.message;}

    finally{key.value='';password.value='';if(body)body.api_key='';button.disabled=false;}

  },

  'model-check':async()=>{

    if(!confirm('Contact this provider using the saved API key? Provider terms or charges may apply. No prompt will be generated and the model will remain inactive.'))return;

    button.disabled=true;

    try{const result=await postJson('/api/model-setup/'+encodeURIComponent(b.id)+'/check',{confirmed:true});document.getElementById('modelSetupResult').textContent=result.message+(result.status==='CONNECTED'?(result.model_listed?' Model identifier is listed.':' Model identifier was not listed.'):'');}

    finally{button.disabled=false;}

  },

  'n8n-toggle':async()=>{const password=document.getElementById('n8nPassword');try{if(!password.value)throw new Error('Confirm your Chief password.');await postJson('/api/auth/reauthenticate',{password:password.value});password.value='';await postJson('/api/integrations/n8n',{enabled:b.enabled==='true'});await loadFoundation('integrations');}finally{password.value='';}},
  'n8n-handshake':async()=>{button.disabled=true;const password=document.getElementById('n8nPassword');try{if(!password.value)throw new Error('Confirm your Chief password above.');if(!confirm('Run the synthetic n8n handshake? No business data will be sent.'))return;await postJson('/api/auth/reauthenticate',{password:password.value});password.value='';const id=button.dataset.operationId||(button.dataset.operationId=crypto.randomUUID().replaceAll('-',''));const domain=button.dataset.operationDomain||(button.dataset.operationDomain=document.getElementById('n8nTestDomain').value);const d=await postJson('/api/integrations/n8n/handshake',{operation_id:id,domain,confirmed:true});document.getElementById('n8nHandshakeStatus').textContent=d.status+': '+d.message+' Reference: '+d.operation_id;delete button.dataset.operationId;delete button.dataset.operationDomain;}finally{password.value='';button.disabled=false;}},
  'n8n-history':async()=>{button.disabled=true;try{const d=await api('/api/integrations/n8n/handshakes');document.getElementById('n8nHistory').innerHTML=d.items.length?foundationTable([['Reference','operation_id'],['Domain','domain'],['Outcome','status'],['Details','message']],d.items):'<p>No test handoffs recorded.</p>';}finally{button.disabled=false;}},
  'n8n-report-preview':async()=>{button.disabled=true;const password=document.getElementById('n8nPassword');try{if(!password.value)throw new Error('Confirm your Chief password above.');if(!confirm('Send Farm journal record counts to the configured local workflow? No private record text is included.'))return;await postJson('/api/auth/reauthenticate',{password:password.value});password.value='';const id=button.dataset.operationId||(button.dataset.operationId=crypto.randomUUID().replaceAll('-',''));const d=await postJson('/api/integrations/n8n/report-preview',{operation_id:id,domain:'farming',confirmed:true});document.getElementById('n8nReportStatus').textContent=d.status+': '+d.notice;document.getElementById('n8nReportResult').innerHTML=d.snapshot?foundationTable([['Recorded at','as_of'],['Journal entries',r=>r.counts.journal_entries],['Current entries',r=>r.counts.current_entries],['Corrections',r=>r.counts.correction_entries]],[d.snapshot]):'';if(d.status==='COMPLETED')delete button.dataset.operationId;}finally{password.value='';button.disabled=false;}},
  'n8n-report-history':async()=>{const d=await api('/api/integrations/n8n/report-previews');document.getElementById('n8nReportResult').innerHTML=d.items.length?foundationTable([['Requested at','created_at'],['Outcome','status'],['Details','notice']],d.items):'<p>No previews recorded.</p>';},
  'n8n-check':async()=>{button.disabled=true;try{const d=await postJson('/api/integrations/n8n/check',{});document.getElementById('n8nStatus').textContent=d.message+(d.partial?' Showing the first page only.':'');document.getElementById('n8nWorkflows').innerHTML=foundationTable([['Workflow','name'],['Active in n8n',r=>r.active?'Yes':'No']],d.workflows);}finally{button.disabled=false;}},
  'foundation-refresh':()=>loadFoundation(b.target),

  'model-state':async()=>{if(confirm('Change global model state to '+b.state+'? Existing assignments cannot override a global disable.')){await postJson('/api/model-controls',{model:b.id,state:b.state});await loadFoundation('models');}},

  'component-preview':async()=>{

    const request={kind:b.kind,id:b.id,mode:document.getElementById(b.select).value};componentPreview=null;

    const preview=await postJson('/api/component-controls/preview',request);componentPreview={...request,token:preview.token};

    document.getElementById('componentImpact').innerHTML='<h2>Review change</h2><p>'+esc(request.id)+' → '+esc(request.mode)+'</p>'+foundationDetails('Affected dependencies and consumers',preview.affected)+'<label for="componentReason">Reason for this change</label><input id="componentReason" maxlength="1000" required><button class="primary" data-action="component-apply">Confirm reviewed change</button>';

    document.getElementById('componentImpact').scrollIntoView({block:'nearest'});

  },

  'component-apply':async()=>{

    if(!componentPreview)throw new Error('Review a current dependency preview first.');

    const reason=document.getElementById('componentReason').value.trim();if(!reason)throw new Error('Enter a reason for this change.');

    if(confirm('Apply this reviewed component change?')){const reviewed=componentPreview;componentPreview=null;await postJson('/api/component-controls/transition',{...reviewed,reason,confirmed:true});await loadFoundation('components');}

  }

};}



async function renderModelAssignment(box,rows){

  if(!canPage('farmRecords'))return;

  const status=await api('/api/farm/assistant');

  const card=document.createElement('article');card.className='card';card.id='modelAssignment';

  const heading=document.createElement('h2');heading.textContent='Agent assignment';card.append(heading);

  const info=document.createElement('p');info.textContent=status.notice;card.append(info);

  const agentLabel=document.createElement('label');agentLabel.textContent='Agent';

  const agent=document.createElement('select');agent.id='assignmentAgent';

  for(const [value,label,disabled] of [['farming','Farm Agent',false],['jobs','Job Agent — existing route; reassignment not available here',true]]){const o=document.createElement('option');o.value=value;o.textContent=label;o.disabled=disabled;agent.append(o);}agentLabel.append(agent);card.append(agentLabel);

  if(!status.can_configure){const note=document.createElement('p');note.textContent='Only the Owner can change the Farm AI assignment.';card.append(note);box.append(card);return;}

  const label=document.createElement('label');label.textContent='Saved Qwen connection';const select=document.createElement('select');select.id='assignmentConnection';label.append(select);

  farmOptions(select,rows.filter(r=>r.provider==='groq'&&r.provider_model.startsWith('qwen/')&&r.cost==='FREE'&&r.key_configured).map(r=>[r.id,r.provider_model+' — '+r.id.slice(-8)]),'Choose saved connection');card.append(label);

  const help=document.createElement('p');help.textContent='Use the exact provider model identifier, including qwen/. If no connection appears, save a Groq Qwen connection with a private key and confirmed free pricing first. Job routing stays unchanged.';card.append(help);

  const confirmation=document.createElement('label');const check=document.createElement('input');check.type='checkbox';check.id='assignmentFree';confirmation.append(check,document.createTextNode('I confirmed this Groq account uses the free plan. No paid fallback.'));card.append(confirmation);

  const pwlabel=document.createElement('label');pwlabel.textContent='Confirm your Chief password';const password=document.createElement('input');password.type='password';password.id='assignmentPassword';password.autocomplete='current-password';pwlabel.append(password);card.append(pwlabel);

  const result=document.createElement('p');result.id='assignmentResult';result.setAttribute('role','status');

  const button=document.createElement('button');button.type='button';button.className='primary';button.textContent='Test and activate for Farm Agent';

  button.onclick=async()=>{button.disabled=true;try{if(agent.value!=='farming'||!select.value||!check.checked)throw new Error('Choose a saved Qwen connection and confirm the free account.');if(!password.value)throw new Error('Confirm your Chief password.');await postJson('/api/auth/reauthenticate',{password:password.value});password.value='';await postJson('/api/farm/assistant/configure',{operation:'qualify',registration:select.value,free_account_confirmed:true});result.textContent='Farm Agent connection tested and active. Job assignment unchanged.';info.textContent=(await api('/api/farm/assistant')).notice;}catch(error){result.textContent=error.message;}finally{password.value='';button.disabled=false;}};

  card.append(button,result);box.append(card);

}

