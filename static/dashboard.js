
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function api(path, opts) {
  const response = await fetch(path, opts);
  let data;
  try { data = await response.json(); }
  catch { throw new Error(`Invalid server response (${response.status}) for ${path}`); }
  if (!response.ok) throw new Error(data.reason || data.message || `Request failed (${response.status}) for ${path}`);
  return data;
}
function reportError(error) {
  const target = document.getElementById('dashboardError');
  target.textContent = error.message || String(error);
  target.classList.remove('hidden');
}
function clearError() {
  document.getElementById('dashboardError').classList.add('hidden');
}
let navigationRevision=0;
async function show(id, agent=currentAgent) {
  const revision=++navigationRevision;
  await refreshChiefContext();
  if(revision!==navigationRevision)return;
  currentAgent=agent;
  if(!canPage(id))throw new Error('This page is not available to your current role and domain scope.');
  if(id==='overview') currentAgent=null;
  if(['health','components','capabilities','models','policies','runtime','integrations','devices','updates','settings','users','domains','ai','notifications','reports','audit'].includes(id))currentAgent=null;
  if(agentDefinitions.some(a=>(a.pages||[]).some(p=>p[0]===id))) currentAgent=agentDefinitions.find(a=>(a.pages||[]).some(p=>p[0]===id)).id;
  renderNavigation(id);
  const sections = [...document.querySelectorAll('main > section')];
  const target = sections.find(section => section.id === id);
  if (!target) throw new Error('Dashboard page is not registered: ' + id);
  sections.forEach(section => section.classList.toggle('hidden', section !== target));
  try {await load(id);} finally {applyChiefVisibility();}
  const heading=target.querySelector('h1');if(heading){heading.tabIndex=-1;heading.focus({preventScroll:true});}
}
async function load(id) {
  if(id==='farmRecords'){await loadFarmRecords();return;}
  if(id==='users'){await loadUsers();return;}
  if(foundationPages.has(id)){await loadFoundation(id);return;}
  const loaders = {overview:loadChief, jobOverview:loadJobOverview, actions:loadActions, jobs:loadJobs,
    applications:loadApps, applicationArchive:loadApplicationArchive,
    sources:loadSources, reports:loadReports, cvs:loadCVs, profiles:loadProfiles,
    facts:loadFacts, audit:loadAudit, scheduler:loadScheduler, settings:loadSettings, domains:loadDomains, notifications:loadNotifications, agentDetail:loadAgentHome};
  if (loaders[id]) await loaders[id]();
}
async function loadJobOverview(){let d=await api('/api/ui/job-state');for(const [id,key] of Object.entries({jobsCount:'jobs',apps:'applications',actionsCount:'actions',notifs:'notifications'}))document.getElementById(id).textContent=d.counts[key];document.getElementById('workerStatus').innerHTML='<b>'+esc(statusText(d.worker.status))+'</b> — '+esc(d.worker.message)+'<br><span class="muted">Updated '+esc(d.worker.updated_at)+'</span>';document.getElementById('commands').innerHTML=d.commands.length?d.commands.map(x=>'<div class="item"><b>#'+x.id+'</b> <span class="pill">'+esc(statusText(x.status))+'</span><br>'+esc(x.instruction)+(x.result?'<pre>'+esc(x.result)+'</pre>':'')+'</div>').join(''):'No commands yet.'}
function actionPreview(action) {
  let payload={};try{payload=JSON.parse(action.payload_json||'{}');payload=payload.payload||payload}catch{}
  const fields=Array.isArray(payload.fields)?payload.fields:[];
  return (payload.url?'<p><b>Target:</b> '+esc(payload.url)+'</p>':'')+(fields.length?'<table><tr><th>Form field</th><th>Proposed value</th><th>Evidence</th></tr>'+fields.map(f=>'<tr><td>'+esc(f.label)+'</td><td>'+esc(f.value)+'</td><td>Fact #'+esc(f.fact_id??'missing')+'</td></tr>').join('')+'</table>':'')+(payload.application_id?'<button class="action" data-action="show-application" data-id="'+esc(payload.application_id)+'">Review application and evidence</button>':'');
}
async function loadActions(){chiefApprovalAccess=await api('/api/ui/approval-access');let d=await api('/api/actions');document.getElementById('actionsCount').textContent=d.length;document.getElementById('actionList').innerHTML=d.length?d.map(x=>'<div class="item"><b>'+esc(x.name)+'</b> <span class="pill">'+esc(x.action)+'</span><p>'+esc(x.description)+'</p>'+actionPreview(x)+'<button class="action" data-action="resolve-action" data-id="'+esc(x.id)+'" data-status="APPROVED">Approve this action</button><button class="action danger" data-action="resolve-action" data-id="'+esc(x.id)+'" data-status="REJECTED">Reject</button></div>').join(''):'No pending actions.'}
async function resolveAction(id,status){await api('/api/actions/'+id,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({status})});await loadActions();await loadJobOverview()}
async function loadJobs(){let d=await api('/api/jobs');document.getElementById('jobList').innerHTML=d.length?'<table><tr><th>Rank</th><th>Job</th><th>Company</th><th>Platform</th><th>Fit</th><th>Scam</th><th>Status</th></tr>'+d.map(x=>'<tr><td>'+esc(x.rank_score??'—')+'</td><td>'+esc(x.title)+'</td><td>'+esc(x.company)+'</td><td>'+esc(x.platform)+'</td><td>'+esc(x.fit_score)+'</td><td>'+esc(statusText(x.scam_status))+'</td><td>'+esc(statusText(x.status))+'</td></tr>').join('')+'</table>':'No jobs yet.'}
async function loadApps(){let d=await api('/api/applications');document.getElementById('appList').innerHTML=d.length?d.map(x=>'<div class="item"><b>'+esc(x.title)+'</b> — '+esc(x.company)+' <span class="pill">'+esc(statusText(x.status))+'</span><br>'+esc(x.notes)+'<br><button class="action" data-action="show-application" data-id="'+esc(x.id)+'">View full record</button></div>').join(''):'No applications yet.'}
async function loadApplicationArchive(){let d=await api('/api/applications');document.getElementById('applicationArchiveList').innerHTML=d.length?'<table><tr><th>Application</th><th>Job</th><th>Company</th><th>Status</th><th>CV used</th><th>Created</th><th></th></tr>'+d.map(x=>'<tr><td>'+esc(x.id)+'</td><td>'+esc(x.title)+'</td><td>'+esc(x.company)+'</td><td>'+esc(statusText(x.status))+'</td><td>'+esc(x.cv_path||'Not recorded')+'</td><td>'+esc(x.created_at)+'</td><td><button class="action" data-action="show-application" data-id="'+esc(x.id)+'">Full view</button></td></tr>').join('')+'</table>':'No application records yet.'}
async function retryApplication(id){let r=await api('/api/applications/'+encodeURIComponent(id)+'/retry',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});alert((r.message||r.reason||'Retry request processed.')+'\nStatus: '+r.status);await showApplication(id)}
async function showApplication(id){await show('applicationArchive');let d=await api('/api/applications/'+encodeURIComponent(id));if(!d||!d.id){document.getElementById('applicationDetail').innerHTML='<h2>Not found</h2>';return}let coverage=await api('/api/applications/'+encodeURIComponent(id)+'/coverage');let coverageHtml='<h3>Application record coverage</h3><p>'+esc(coverage.notice)+'</p>'+coverage.items.map(x=>'<div class="item"><b>'+esc(x.label)+'</b> — '+esc({RECORDED:'Recorded',NOT_RECORDED:'Not recorded',REVIEW_REQUIRED:'Needs review',REFERENCES_MATCH:'References match'}[x.status])+'<p>'+esc(x.note)+'</p></div>').join('');let evidence=await api('/api/applications/'+encodeURIComponent(id)+'/evidence');let evidenceHtml='<h3>Claim evidence</h3><p>'+esc(evidence.message)+'</p>'+(evidence.claims.length?evidence.claims.map(c=>'<div class="item"><b>'+esc(c.text)+'</b><p>'+(c.supported?'Confirmed fact #':'Needs review — fact #')+esc(c.fact_id??'missing')+'</p><p>'+esc(c.fact?.source_detail||c.fact?.source_type||'No evidence reference')+'</p></div>').join(''):'<p>No referenced claims recorded. An older draft may need to be regenerated.</p>');let snaps=d.snapshots||[],ev=d.events||[];let snap=snaps[0]||{};let cv={};try{cv=JSON.parse(snap.cv_snapshot_json||'{}')}catch(e){}let fields=[];try{fields=JSON.parse(snap.form_fields_json||'[]')}catch(e){}let rec=await api('/api/applications/'+encodeURIComponent(id)+'/recovery');let recoveryHtml='';if(rec.attempt){let a=rec.attempt;let canRetry=rec.decision&&rec.decision.action==='RETRY';recoveryHtml='<hr><h3>Recovery</h3><p><b>Last outcome:</b> '+esc(statusText(a.outcome))+' · <b>Attempt:</b> '+esc(a.id)+'</p><p>'+esc(rec.decision?rec.decision.reason:'')+'</p>'+(canRetry?'<button class="action" data-action="retry-application" data-id="'+esc(id)+'">↻ Ask worker to retry application</button>':'<p class="muted">This outcome is not safe for automatic retry. The worker will not resubmit until the outcome is resolved.</p>')}else{recoveryHtml='<hr><h3>Recovery</h3><p class="muted">No submission failure/recovery record yet.</p>'}document.getElementById('applicationDetail').innerHTML='<h2>'+esc(d.title)+' — '+esc(d.company)+'</h2><p><b>Status:</b> '+esc(statusText(d.status))+' · <b>Application ID:</b> '+esc(d.id)+'</p><p><b>Job URL:</b> '+esc(d.job_url||'—')+'<br><b>Application source:</b> '+esc(snap.source_url||d.job_url||'—')+'</p>'+coverageHtml+recoveryHtml+evidenceHtml+'<hr><h3>CV used</h3><p><b>Name:</b> '+esc(snap.cv_name||'—')+'<br><b>Variant:</b> '+esc(snap.cv_variant||'—')+'<br><b>Original CV path:</b> '+esc(snap.cv_path||d.cv_path||'—')+'</p><pre>'+esc(JSON.stringify(cv,null,2))+'</pre><h3>Cover letter</h3><pre>'+esc(snap.cover_letter_text||'')+'</pre><h3>Application form structure / safely filled fields</h3><pre>'+esc(JSON.stringify(fields,null,2))+'</pre><h3>History</h3>'+(ev.length?ev.map(x=>'<div class="item"><b>'+esc(x.event_type)+'</b> <span class="pill">'+esc(statusText(x.status))+'</span><br>'+esc(x.event_time)+'<br>'+esc(x.details)+'</div>').join(''):'No application events recorded.')}


let selectedSite = null;
let siteRecords = [];
async function postJson(path, value) {
  return api(path, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(value)});
}
function safeSiteLink(url) {
  try { const parsed=new URL(url); return parsed.protocol==='https:' && !parsed.username && !parsed.password ? '<a href="'+esc(parsed.href)+'" target="_blank" rel="noopener noreferrer">Open website ↗</a>' : 'Website requires review'; }
  catch { return 'Invalid website URL'; }
}
async function loadSources() {
  const [sources, access, session] = await Promise.all([api('/api/sources'),api('/api/site-access'),api('/api/session-control')]);
  sessionSignInEnabled=session.sign_in_enabled;
  siteRecords=access;
  const cards=sources.map(source=>({name:source.name,url:source.url,notes:source.notes,verification:source.verification_status}));
  for(const row of access) if(!cards.some(c=>{try{return new URL(c.url).hostname===row.domain}catch{return false}})) cards.push({name:row.name,url:row.url,notes:row.last_reason,verification:'User-managed reading'});
  document.getElementById('sourceList').innerHTML=cards.length?cards.map(c=>'<div class="item"><button class="action" data-action="select-site" data-url="'+esc(c.url)+'" data-name="'+esc(c.name)+'">'+esc(c.name)+'</button> <span class="pill">'+esc(c.verification)+'</span><p>'+esc(c.url)+'</p><p>'+esc(c.notes)+'</p></div>').join(''):'No websites yet.';
  if(selectedSite) renderSite();
}
function renderSite() {
  const row=siteRecords.find(r=>r.domain===selectedSite.domain);
  const status=row?.status||'NOT_MANAGED';
  const btn=(action,label,danger=false)=>'<button class="action '+(danger?'danger':'')+'" data-action="site-control" data-operation="'+action+'">'+label+'</button>';
  let controls='';
  if(status==='NOT_MANAGED') controls=btn('register','Set up access controls');
  else {
    if(sessionSignInEnabled && status==='SIGNING_IN') controls+=btn('save','I have signed in — save session');
    else if(sessionSignInEnabled && status!=='SAVE_REQUESTED') controls+=btn('login','Open dedicated sign-in window on Folio');
    if(['DISABLED','REVOKED','LOGIN_REQUIRED'].includes(status)) controls+=btn('public','Authorize public reading');
    if(status==='ACTIVE') controls+=btn('pause','Pause indefinitely')+'<label for="sitePauseHours">Suspend for hours</label><input id="sitePauseHours" type="number" min="0.01" max="8760" value="24">'+btn('suspend','Suspend for this duration');
    if(status==='PAUSED') controls+=btn('resume','Resume access');
    if(status!=='REVOKED') controls+=btn('revoke','Revoke saved access',true);
    controls+=btn('dismiss','Dismiss this recommendation');
  }
  document.getElementById('sourceDetail').innerHTML='<h2>'+esc(row?.name||selectedSite.name)+'</h2><p>'+safeSiteLink(row?.url||selectedSite.url)+'</p><p><b>Access:</b> '+esc(status.replaceAll('_',' '))+'</p>'+(row?.resume_at?'<p>Resumes '+esc(new Date(row.resume_at*1000).toLocaleString())+'</p>':'')+'<p>'+esc(row?.message||'')+'</p><p>'+esc(row?.last_reason||'')+'</p>'+(row?.dismissed?'<p>Recommendation dismissed. A fresh useful reason can bring it back.</p>':'')+controls+'<p class="muted">Session controls are shown here. Real sign-in is reserved for Folio; it is disabled in the VM test environment. The worker reads job pages; this does not authorize applications or employer messages. Pause prevents new reads and discards results from reads already in progress. Revoke removes the saved session from this VM, not other browsers.</p>';
}
async function selectSite(url,name) {
  selectedSite={url,name,domain:new URL(url).hostname.replace(/^www\./,'')};
  renderSite();
  document.getElementById('sourceDetail').scrollIntoView({block:'nearest'});
}
async function addSite() {
  const row=await postJson('/api/site-access',{url:document.getElementById('siteUrl').value,name:document.getElementById('siteName').value});
  selectedSite={url:row.url,name:row.name,domain:row.domain};
  await loadSources();
}
async function siteControl(operation) {
  if(operation==='register') await postJson('/api/site-access',{url:selectedSite.url,name:selectedSite.name});
  else {
    if(operation==='revoke' && !confirm('Remove Chief Agent’s saved session and stop access to this website?')) return;
    const body={action:operation==='suspend'?'pause':operation};
    if(operation==='suspend') body.hours=Number(document.getElementById('sitePauseHours').value);
    await postJson('/api/site-access/'+encodeURIComponent(selectedSite.domain),body);
  }
  await loadSources();
}

async function loadCVs(){let d=await api('/api/cvs');document.getElementById('cvList').innerHTML=d.length?d.map(x=>'<div class="item"><b>'+esc(x.name)+'</b> <span class="pill">'+esc(x.variant||'general')+'</span><br>Role: '+esc(x.role_type||'—')+' · '+esc(x.file_type)+' · '+(x.active?'Active':'Inactive')+'<br><a class="download" href="/api/cvs/'+encodeURIComponent(x.id)+'/download" download>Download original document</a><br><button class="action" data-action="toggle-cv" data-id="'+esc(x.id)+'" data-enabled="'+(x.active?'false':'true')+'">'+(x.active?'Deactivate':'Activate')+'</button></div>').join(''):'No CVs uploaded yet.'}
async function uploadCV(){let f=document.getElementById('cvFile').files[0];if(!f){document.getElementById('cvResult').textContent='Choose a file first.';return}if(f.size>8*1024*1024)throw new Error('Maximum upload size is 8 MiB.');let b64=await new Promise((res,rej)=>{let r=new FileReader();r.onload=()=>res(String(r.result).split(',')[1]);r.onerror=rej;r.readAsDataURL(f)});let d=await api('/api/cvs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:document.getElementById('cvName').value||f.name,role_type:document.getElementById('cvRole').value,variant:document.getElementById('cvVariant').value,notes:document.getElementById('cvNotes').value,filename:f.name,content_b64:b64})});document.getElementById('cvResult').textContent=JSON.stringify(d,null,2);await loadCVs()}
async function deleteCV(id){await api('/api/cvs/'+id,{method:'DELETE'});await loadCVs()}
async function toggleCV(id,active){await api('/api/cvs/'+id,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({active})});await loadCVs()}
async function factStatus(id,status){await api('/api/facts/'+id,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({status})});await loadFacts()}
async function loadProfiles(){let d=await api('/api/profiles');document.getElementById('profileList').innerHTML=d.length?d.map(x=>'<div class="item"><b>'+esc(x.label)+'</b> <span class="pill">'+esc(x.verification_status)+'</span> '+(x.enabled?'Enabled':'Disabled')+'<br>'+esc(x.url)+'<br>Type: '+esc(x.profile_type)+' · Every '+esc(x.check_interval_days)+' days · Last: '+esc(x.last_status)+'<br><button class="action" data-action="toggle-profile" data-id="'+esc(x.id)+'" data-enabled="'+(x.enabled?'false':'true')+'">'+(x.enabled?'Disable':'Enable')+'</button><button class="action danger" data-action="delete-profile" data-id="'+esc(x.id)+'">Remove</button></div>').join(''):'No profile links configured.'}
async function addProfile(){let d=await api('/api/profiles',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({label:document.getElementById('profileLabel').value,url:document.getElementById('profileUrl').value,profile_type:document.getElementById('profileType').value,check_interval_days:Number(document.getElementById('profileInterval').value||7)})});document.getElementById('profileResult').textContent=JSON.stringify(d,null,2);await loadProfiles()}
async function toggleProfile(id,enabled){await api('/api/profiles/'+id,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled})});await loadProfiles()}
async function deleteProfile(id){await api('/api/profiles/'+id,{method:'DELETE'});await loadProfiles()}
async function loadAudit(){let d=await api('/api/audit');document.getElementById('auditText').textContent=d.map(x=>'['+x.event_time+'] ['+x.category+'] ['+x.actor+'] '+x.action+' — '+x.status+(x.details?'\n  '+x.details:'')).join('\n')||'No audit events yet.'}
async function generateAudit(){let d=await api('/api/audit/generate',{method:'POST'});document.getElementById('auditText').textContent=(d.path||'')+'\n\n'+(d.preview||'');await loadReports();}

async function sendCommand(){let text=document.getElementById('cmd').value.trim();if(!text)return;let d=await api('/api/command',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({instruction:text})});document.getElementById('cmdResult').textContent=JSON.stringify(d,null,2);document.getElementById('cmd').value='';await loadJobOverview()}
function bindActions() {
  document.addEventListener('submit', event => event.preventDefault());
  document.addEventListener('click', async event => {
    const button = event.target.closest('button[data-action]');
    if (!button || button.disabled) return;
    const {action, id, status, enabled, target} = button.dataset;
    const actions = {...chiefActions(button), ...foundationActions(button), ...userActions(button),
      'refresh-domains':loadDomains,
      'add-site': addSite, 'select-site': () => selectSite(button.dataset.url,button.dataset.name),
      'site-control': () => siteControl(button.dataset.operation),
      show: () => show(target), 'send-command': sendCommand, 'upload-cv': uploadCV,
      'add-profile': addProfile, 'load-audit': loadAudit, 'generate-audit': generateAudit,
      'resolve-action': () => resolveAction(id, status),
      'show-application': () => showApplication(id),
      'retry-application': () => retryApplication(id),
      'toggle-cv': () => toggleCV(id, enabled === 'true'),
      'fact-status': () => factStatus(id, status),
      'toggle-profile': () => toggleProfile(id, enabled === 'true'),
      'delete-profile': () => deleteProfile(id)
    };
    if (!actions[action]) return;
    clearError();
    button.disabled = true;
    try { await actions[action](); }
    catch (error) { reportError(error); }
    finally { button.disabled = false; applyChiefVisibility(); }
  });
}
function initDashboard() {
  bindActions();
  document.body.classList.toggle('sidebarCollapsed',localStorage.getItem('chiefSidebar')==='collapsed');
  initializeChief().catch(reportError);
  setInterval(()=>{refreshNotifications().catch(reportError); if(!document.getElementById('overview').classList.contains('hidden'))loadChief().then(applyChiefVisibility).catch(reportError); if(!document.getElementById('jobOverview').classList.contains('hidden'))loadJobOverview().then(applyChiefVisibility).catch(reportError);},5000);
}
if (document.readyState !== 'complete') document.addEventListener('DOMContentLoaded', initDashboard);
else initDashboard();
