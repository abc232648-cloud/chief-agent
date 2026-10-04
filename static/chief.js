let currentAgent=null, agentDefinitions=[], sessionSignInEnabled=false;
const systemPages=[['overview','Home'],['domains','Agents'],['actions','Approvals'],['notifications','Notifications'],['reports','Reports'],['audit','Activity'],['scheduler','Schedules'],['settings','Settings'],['ai','AI routing']];
const statusText=value=>({UNKNOWN:'Not established',SUBMISSION_UNKNOWN:'Submission confirmation missing — review required',WORKER_NOT_CONNECTED:'Worker connection lost or not started',IDLE:'Ready · no task running',WORKING:'Working',PAUSED:'Paused',DISABLED:'Access disabled',NO_ACTION:'No action was selected',INTERRUPTED:'Interrupted by service restart'}[value]||String(value||'No status recorded').replaceAll('_',' '));
const dateText=value=>value?new Date(typeof value==='number'?value*1000:value).toLocaleString():'No activity recorded';
const durationText=value=>Math.floor(value/3600)+'h '+Math.floor(value%3600/60)+'m '+Math.floor(value%60)+'s';
function renderNavigation(selected) {renderChiefNavigation(selected);}
async function initializeChief(){await refreshChiefContext();await show('overview');await refreshNotifications();}
function controlButtons(agent){return '<div class="agentControls">'+[['enabled','Access enabled'],['autostart','Start with Chief Agent']].map(([key,label])=>'<button class="switch" role="switch" aria-checked="'+!!agent[key]+'" data-action="agent-control" data-id="'+esc(agent.id)+'" data-key="'+key+'" data-value="'+!agent[key]+'">'+label+' <b>'+ (agent[key]?'On':'Off')+'</b></button>').join('')+'<button class="action" data-action="agent-control" data-id="'+esc(agent.id)+'" data-key="running" data-value="'+!agent.running+'"'+(!agent.enabled?' disabled':'')+'>'+(agent.running?'Pause agent':'Start agent')+'</button></div>';}
function agentCard(agent,controls=false,openLink=true){return '<article class="card agentCard"><p class="eyebrow">SPECIALIST AGENT</p><h2>'+esc(agent.label)+'</h2><p class="pill">'+esc(statusText(agent.status))+'</p><p>'+esc(agent.description)+'</p><dl><dt>Current task</dt><dd>'+esc(agent.current_task||'No task running')+'</dd><dt>Last activity</dt><dd>'+esc(dateText(agent.last_activity))+'</dd><dt>Recorded working time</dt><dd>'+esc(durationText(agent.working_seconds))+'</dd></dl>'+(controls?controlButtons(agent):'')+(openLink?'<button class="primary" data-action="open-agent" data-id="'+esc(agent.id)+'">Open '+esc(agent.label)+'</button>':'')+'</article>';}
async function loadChief(){const d=await api('/api/ui/overview');agentDefinitions=d.agents;
  document.getElementById('chiefMetrics').innerHTML=[['Enabled agents',d.agents.filter(a=>a.enabled).length],['Working now',d.agents.filter(a=>a.status==='WORKING').length],['Awaiting approval',d.counts.actions],['Unread notifications',d.counts.notifications]].map(([label,n])=>'<div class="card"><p class="muted">'+label+'</p><strong class="stat">'+n+'</strong></div>').join('');
  document.getElementById('chiefAgents').innerHTML=d.agents.map(a=>agentCard(a)).join('');
  document.getElementById('chiefAttention').innerHTML='<p>'+esc(d.worker_connected?'Worker connected. Agent controls govern what may run.':'Worker is not reporting a recent heartbeat. Check the service before expecting work.')+'</p><p>Service started: '+esc(dateText(d.service_started_at))+'</p><button class="action" data-action="show" data-target="actions">Review approvals ('+d.counts.actions+')</button><button class="action" data-action="show" data-target="notifications">Open notifications</button>';
}
async function loadDomains(){const d=await api('/api/ui/overview');agentDefinitions=d.agents;document.getElementById('domainList').innerHTML=d.agents.map(a=>agentCard(a,true)).join('');}
async function openAgent(id){await show('agentDetail',id);}
async function loadAgentHome(){const revision=navigationRevision,selected=currentAgent;
  const active=()=>revision===navigationRevision&&selected===currentAgent&&!document.getElementById('agentDetail').classList.contains('hidden');
  if(!active())return;
  const d=await api('/api/ui/overview');if(!active())return;
  const agent=d.agents.find(a=>a.id===selected);if(!agent){await show('domains');return;}
  const records=await api('/api/domains/'+encodeURIComponent(agent.id));
  if(!active())return;
  document.getElementById('agentHome').innerHTML='<p class="eyebrow">SPECIALIST WORKSPACE</p><h1>'+esc(agent.label)+'</h1>'+agentCard(agent,true,false)+'<div class="card"><h2>Workspace</h2>'+((agent.pages||[]).length?agent.pages.map(([id,label])=>'<button class="action" data-action="show" data-target="'+esc(id)+'">'+esc(label)+'</button>').join(''):'<p>This minimal workspace is ready for future capabilities. Existing local records: '+records.records.length+'. Reminders: '+records.reminders.length+'.</p>')+'</div><div class="card"><h2>Recent activity</h2>'+(agent.recent_activity.length?agent.recent_activity.map(r=>'<div class="item"><b>'+esc(r.task)+'</b><p>'+esc(statusText(r.outcome))+' · '+esc(dateText(r.started))+'</p></div>').join(''):'<p>No recorded activity yet.</p>')+'</div>';
  if(agent.id==='farming'){
    const status=await api('/api/farm/assistant');if(!active())return;
    const panel=document.createElement('article');panel.className='card';panel.id='farmHomeAI';
    panel.innerHTML='<h2>Ask Farm Agent</h2><p>'+esc(status.notice)+'</p><p>Farm access and background worker controls above do not activate an AI model.</p><button class="primary" data-action="farm-ai-setup">'+(status.can_configure?'Choose connection / activate Farm AI':'Open Ask Farm Agent')+'</button>';
    document.getElementById('agentHome').querySelector('.agentCard').after(panel);
  }
  renderNavigation('agentDetail');
}
function noticeCard(n){const page=['sources','actions','reports','facts','scheduler','applicationArchive'].includes(n.related_page)?n.related_page:'';const agent=agentDefinitions.find(a=>a.id===n.domain);return '<article class="item '+(!n.read?'unread':'')+'"><b>'+esc(n.title)+'</b><p>'+esc(n.body)+'</p><small>'+esc(agent?.label||'Chief Agent')+' · '+esc(n.severity)+' · '+esc(dateText(n.created_at))+'</small><div><button class="action" data-action="notice-read" data-id="'+n.id+'" data-value="'+!n.read+'">'+(n.read?'Mark unread':'Mark read')+'</button>'+(page?'<button class="action" data-action="notice-open" data-id="'+n.id+'" data-target="'+page+'">Open related item</button>':'')+'</div></article>';}
let noticeRefreshRunning=false;
async function refreshNotifications(){if(!canPage('notifications'))return;if(noticeRefreshRunning)return;noticeRefreshRunning=true;try{
  const [items,state,prefs]=await Promise.all([api('/api/notifications'),api('/api/state'),api('/api/notification-preferences')]);
  document.getElementById('notificationBadge').textContent=state.counts.notifications;
  document.getElementById('notificationPreview').innerHTML=items.filter(n=>!n.read).slice(0,5).map(noticeCard).join('')||'<p>No unread notifications.</p>';
  const fresh=items.filter(n=>!n.presented&&!n.read);
  for(const n of fresh){await postJson('/api/notifications/'+n.id,{field:'presented',value:true});}
  const alerts=prefs.delivery==='quiet'?[]:fresh.filter(n=>prefs.delivery==='all'||n.severity==='CRITICAL');
  if(alerts.length){const toast=document.createElement('div');toast.className='toast';toast.textContent=alerts.length===1?alerts[0].title:alerts.length+' new notifications';const button=document.createElement('button');button.className='action';button.dataset.action='show';button.dataset.target='notifications';button.textContent='View';toast.appendChild(button);document.getElementById('toastRegion').appendChild(toast);setTimeout(()=>toast.remove(),8000);}
}finally{noticeRefreshRunning=false;}}
async function loadNotifications(){const prefs=await api('/api/notification-preferences');document.getElementById('noticeDelivery').value=prefs.delivery;document.getElementById('noticeSort').value=prefs.sort;const select=document.getElementById('noticeAgent');const previous=select.value;select.innerHTML='<option value="">All agents</option><option value="system">Chief Agent</option>'+agentDefinitions.map(a=>'<option value="'+esc(a.id)+'">'+esc(a.label)+'</option>').join('');select.value=previous;await filterNotifications();}
async function filterNotifications(){const query=new URLSearchParams();for(const [id,key] of [['noticeQuery','q'],['noticeAgent','agent'],['noticeRead','read'],['noticeSeverity','severity'],['noticeSort','sort'],['noticeStart','start'],['noticeEnd','end']]){let value=document.getElementById(id).value;if(value){if(['start','end'].includes(key))value=new Date(value).toISOString();query.set(key,value);}}const rows=await api('/api/notifications?'+query);document.getElementById('notificationList').innerHTML=rows.map(noticeCard).join('')||'No notifications match these filters.';}
async function updateNotice(id,value){await postJson('/api/notifications/'+id,{value});await refreshNotifications();if(!document.getElementById('notifications').classList.contains('hidden'))await filterNotifications();}
async function loadReports(){const rows=await api('/api/reports');document.getElementById('reportList').innerHTML=rows.map(r=>'<article class="item"><h2>'+esc(r.period)+'</h2><p>'+esc(r.created_at)+'</p><button class="action" data-action="report-preview" data-id="'+r.id+'">View report</button>'+['txt','pdf','json'].map(format=>'<a class="download" href="/api/reports/'+r.id+'/download?format='+format+'" download>Download '+format.toUpperCase()+'</a>').join('')+'</article>').join('')||'No reports have been generated yet.';}
async function loadFacts(){const rows=await api('/api/facts');document.getElementById('factList').innerHTML=rows.map(f=>'<article class="item"><b>'+esc(f.text)+'</b><p>'+esc(statusText(f.status))+' · '+esc(f.source_detail||f.source_type)+'</p><details><summary>Edit statement</summary><label for="editFact'+f.id+'">Revised fact</label><textarea id="editFact'+f.id+'">'+esc(f.text)+'</textarea><button class="action" data-action="edit-fact" data-id="'+f.id+'">Save and require reconfirmation</button></details>'+(f.status==='PROPOSED'?'<button class="action" data-action="fact-status" data-id="'+f.id+'" data-status="USER_CONFIRMED">Confirm fact</button>':'')+(f.status!=='REVOKED'?'<button class="action danger" data-action="fact-status" data-id="'+f.id+'" data-status="REVOKED">Revoke / remove from active facts</button>':'<p>Removed from active facts; historical evidence retained.</p>')+'</article>').join('')||'No candidate facts recorded.';}
async function loadScheduler(){if(canGlobal('installation.manage'))await loadGeneralSchedules();const d=await api('/api/scheduler');const summaries=canGlobal('installation.manage')?await api('/api/summary-settings'):{};let summaryBox=document.getElementById('summaryControls');if(!summaryBox){summaryBox=document.createElement('div');summaryBox.id='summaryControls';summaryBox.className='card';document.getElementById('schedulerStatus').after(summaryBox);}summaryBox.innerHTML='<h2>Activity summaries</h2><p>Daily at the audit time; weekly on Sunday; monthly on the last day. Uses recorded activity and your current approval backlog.</p>'+Object.entries(summaries).map(([period,enabled])=>'<button class="switch" role="switch" aria-checked="'+enabled+'" data-action="summary-toggle" data-id="'+period+'" data-value="'+!enabled+'">'+period+' summary '+(enabled?'On':'Off')+'</button>').join('');document.getElementById('schedulerStatus').textContent='Timezone: '+d.timezone+' · Daily audit: '+d.audit_time+'. Pausing an agent also stops its scheduled work.';document.getElementById('monitoringList').innerHTML=d.tasks.filter(t=>t.status!=='REMOVED').map(t=>'<article class="item"><h3>'+esc(t.task_type.replaceAll('_',' '))+'</h3><p>Job Agent · '+(t.enabled?'Enabled':'Paused')+' · Last: '+esc(dateText(t.last_run_at))+'</p><p>Next due: '+esc(t.next_due_at?dateText(t.next_due_at):'Next eligible scheduler cycle')+'</p><label for="scheduleInterval'+t.id+'">Repeat every (days)</label><input id="scheduleInterval'+t.id+'" type="number" min="1" max="365" value="'+t.interval_days+'"><button class="action" data-action="edit-schedule" data-id="'+t.id+'">Save interval</button><button class="action" data-action="pause-schedule" data-id="'+t.id+'" data-value="'+!t.enabled+'">'+(t.enabled?'Pause':'Resume')+'</button><button class="action danger" data-action="remove-schedule" data-id="'+t.id+'">Remove schedule</button></article>').join('')||'No active schedules.';}
async function loadSettings(){const d=await api('/api/settings/email');const fields=[['EMAIL_PROVIDER','Provider',d.provider],['SMTP_HOST','Mail server',d.host],['SMTP_PORT','Port',d.port],['SMTP_USERNAME','Account',d.username],['EMAIL_SENDER','Sender',d.sender],['EMAIL_RECIPIENT','Recipient',d.recipient],['SMTP_PASSWORD','Password (leave blank to keep saved password)','']];document.getElementById('emailConfig').textContent=(d.configured&&d.password_configured?'Configured':'Setup incomplete')+' · Password '+(d.password_configured?'saved privately':'not configured');document.getElementById('emailSettingsForm').innerHTML=fields.map(([name,label,value])=>'<label for="email'+name+'">'+label+'</label><input id="email'+name+'" name="'+name+'" type="'+(name==='SMTP_PASSWORD'?'password':name==='SMTP_PORT'?'number':'text')+'" value="'+esc(value)+'" autocomplete="'+(name==='SMTP_PASSWORD'?'new-password':'off')+'">').join('')+'<button type="button" class="primary" data-action="save-email">Save email settings</button><button type="button" class="action" data-action="test-email">Send test email</button><p>Changes apply to the next delivery. Credentials remain on this machine and are never returned in API responses.</p>';}
function chiefActions(button){const b=button.dataset;return {
 'workspace-home':()=>currentAgent?openAgent(currentAgent):show('overview'),
 'farm-ai-setup':async()=>{await show('farmRecords');const card=document.getElementById('farmAssistantCard');openFarmSection('farmAssistantCard');card.scrollIntoView({block:'start'});card.setAttribute('tabindex','-1');card.focus({preventScroll:true});},
 'toggle-sidebar':()=>{const closed=document.body.classList.toggle('sidebarCollapsed');localStorage.setItem('chiefSidebar',closed?'collapsed':'open');button.setAttribute('aria-expanded',String(!closed));},
 'open-agent':()=>openAgent(b.id),
 'agent-control':async()=>{const result=await postJson('/api/agent-controls/'+b.id,{[b.key]:b.value==='true'});document.getElementById('domainResult').textContent=result.message;await loadDomains();if(currentAgent)await loadAgentHome();},
 'notice-read':()=>updateNotice(b.id,b.value==='true'),
 'notice-open':async()=>{await updateNotice(b.id,true);await show(b.target);document.getElementById('notificationDropdown').open=false;},
 'read-all':()=>updateNotice('all',true),
 'filter-notifications':filterNotifications,
 'save-notice-preferences':async()=>{await postJson('/api/notification-preferences',{delivery:document.getElementById('noticeDelivery').value,sort:document.getElementById('noticeSort').value});await refreshNotifications();},
 'report-preview':async()=>{const d=await api('/api/reports/'+b.id+'/preview');const view=document.getElementById('reportPreview');view.textContent=d.text+(d.truncated?'\nPreview truncated; download the report for all content.':'');view.classList.remove('hidden');},
 'add-fact':async()=>{await postJson('/api/facts',{text:document.getElementById('newFactText').value,source_detail:document.getElementById('newFactSource').value});document.getElementById('newFactText').value='';await loadFacts();},
 'edit-fact':async()=>{await postJson('/api/facts/'+b.id+'/edit',{text:document.getElementById('editFact'+b.id).value});await loadFacts();},
 'summary-toggle':async()=>{await postJson('/api/summary-settings',{[b.id]:b.value==='true'});await loadScheduler();},
 'add-schedule':async()=>{await postJson('/api/schedules',{task_type:document.getElementById('scheduleType').value,interval_days:Number(document.getElementById('scheduleDays').value)});await loadScheduler();},
 'edit-schedule':async()=>{await postJson('/api/schedules/'+b.id,{interval_days:Number(document.getElementById('scheduleInterval'+b.id).value)});await loadScheduler();},
 'pause-schedule':async()=>{await postJson('/api/schedules/'+b.id,{enabled:b.value==='true'});await loadScheduler();},
 'remove-schedule':async()=>{if(confirm('Remove this schedule? Already queued work is not cancelled.')){await postJson('/api/schedules/'+b.id,{remove:true});await loadScheduler();}},
 'save-email':async()=>{const body=Object.fromEntries([...document.querySelectorAll('#emailSettingsForm input')].map(i=>[i.name,i.value]));const d=await postJson('/api/settings/email',body);document.getElementById('emailResult').textContent=d.message;await loadSettings();},
 'test-email':async()=>{if(confirm('Send a test message to the saved email recipient?')){const d=await postJson('/api/settings/email/test',{});document.getElementById('emailResult').textContent=d.message;}}
};}

// Keep rejected exports in the interface; never save an error object as a report.
document.addEventListener('click', async event => {
  const link=event.target.closest('a.download[href^="/api/reports/"]');
  if(!link)return;
  event.preventDefault();
  const view=document.getElementById('reportPreview');
  try{
    const response=await fetch(link.getAttribute('href'),{cache:'no-store',credentials:'same-origin'});
    if(!response.ok){let data={};try{data=await response.json();}catch{}throw new Error(data.reason||'Report download unavailable.');}
    if(!response.headers.get('Content-Disposition')?.startsWith('attachment;'))throw new Error('Report download requires a valid signed-in session.');
    const blob=await response.blob();const url=URL.createObjectURL(blob);
    const save=document.createElement('a');save.href=url;
    const id=link.getAttribute('href').match(/\/reports\/(\d+)\//)?.[1]||'report';
    const format=new URL(link.href).searchParams.get('format')||'txt';
    save.download='reports-'+id+'.'+format;document.body.appendChild(save);save.click();save.remove();
    setTimeout(()=>URL.revokeObjectURL(url),1000);
  }catch(error){view.textContent=error.message;view.classList.remove('hidden');}
});

async function loadGeneralSchedules(){
  const d=await api('/api/general-schedules');
  let box=document.getElementById('generalSchedules');
  if(!box){box=document.createElement('div');box.id='generalSchedules';box.className='card';document.getElementById('schedulerStatus').before(box);}
  box.innerHTML='<h2>Schedule a reminder</h2><p>Available for registered domains. These reminders appear in Chief; they do not send messages or operate cameras/equipment.</p><label>Domain<select id="generalDomain">'+d.domains.map(x=>'<option>'+esc(x)+'</option>').join('')+'</select></label><label>Reminder<input id="generalTitle" maxlength="240"></label><label>First run (this device’s local time)<input id="generalFirst" type="datetime-local"></label><label>Repeat every minutes (0 = once)<input id="generalInterval" type="number" min="0" max="525600" value="0"></label><p>Repeats use elapsed minutes; missed occurrences produce at most one reminder when service resumes.</p><button type="button" data-general-action="create">Review schedule</button><p id="generalStatus" role="status"></p>'+d.items.map(x=>'<article class="item"><h3>'+esc(x.title)+'</h3><p>'+esc(x.domain)+' · '+esc(x.status)+' · Next: '+esc(new Date(x.next_due*1000).toLocaleString())+'</p><button type="button" data-general-action="toggle" data-id="'+esc(x.id)+'" data-revision="'+x.revision+'" data-enabled="'+!x.enabled+'"'+(['COMPLETED','AUTHORITY_REVOKED','UNSUPPORTED'].includes(x.status)?' disabled':'')+'>'+(x.enabled?'Pause':'Resume')+'</button></article>').join('');
}
document.addEventListener('click',async event=>{
 const b=event.target.closest('[data-general-action]');if(!b)return;
 const status=document.getElementById('generalStatus');b.disabled=true;
 try{
  if(b.dataset.generalAction==='create'){
   const when=new Date(document.getElementById('generalFirst').value);
   if(!Number.isFinite(when.getTime()))throw new Error('Choose the first run time.');
   const body={domain:document.getElementById('generalDomain').value,action:'in_app_reminder',title:document.getElementById('generalTitle').value,first_run:when.toISOString(),timezone:Intl.DateTimeFormat().resolvedOptions().timeZone,interval_minutes:Number(document.getElementById('generalInterval').value)};
   if(!confirm('Create reminder “'+body.title+'” for '+body.domain+' at '+when.toLocaleString()+'? '+(body.interval_minutes?'Repeat every '+body.interval_minutes+' elapsed minutes.':'Run once.')+' No external action will occur.'))return;
   await postJson('/api/general-schedules',body);
  }else await postJson('/api/general-schedules/'+b.dataset.id,{enabled:b.dataset.enabled==='true',revision:Number(b.dataset.revision)});
  await loadGeneralSchedules();
 }catch(error){status.textContent=error.message;}finally{b.disabled=false;}
});
