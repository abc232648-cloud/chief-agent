'use strict';
let chiefContext=null;
let chiefApprovalAccess={};
const chiefGroups=[['Main',[['overview','Overview']]],['Work & Control',[['scheduler','Tasks & Automations'],['actions','Approvals'],['notifications','Notifications'],['reports','Reports'],['audit','Audit / Activity'],['evidence','Shared Evidence'],['ledger','Decision Ledger'],['runbooks','Runbooks']]],['System',[['health','System Health'],['components','Component controls'],['capabilities','Capabilities'],['models','Models'],['policies','Policy'],['runtime','Agent Runtime'],['integrations','Integrations'],['devices','Devices'],['updates','Updates'],['settings','Settings'],['users','Users & access'],['domains','Manage Domains'],['ai','AI routing compatibility']]]];
function canPage(id){return !!chiefContext?.pages.includes(id);}
function canDomain(permission,domain='jobs'){return !!chiefContext?.domains.find(d=>d.id===domain)?.permissions[permission];}
function canGlobal(permission){return !!chiefContext?.global_permissions[permission];}
async function refreshChiefContext(){chiefContext=await api('/api/ui/context');agentDefinitions=chiefContext.domains;return chiefContext;}
function renderChiefNavigation(selected){
  const agent=agentDefinitions.find(a=>a.id===currentAgent);
  document.getElementById('workspaceName').textContent=agent?.label||'Chief Agent';
  document.getElementById('workspaceContext').textContent=agent?'SPECIALIST WORKSPACE':'SYSTEM WORKSPACE';
  const button=([id,label])=>canPage(id)?'<button data-action="show" data-target="'+esc(id)+'"'+(selected===id?' aria-current="page"':'')+'>'+esc(label)+'</button>':'';
  const group=([label,pages])=>{const content=pages.map(button).join('');return content?'<details class="navSection" open><summary>'+esc(label)+'</summary>'+content+'</details>':'';};
  const domains='<details class="navSection" open><summary>Domains</summary>'+agentDefinitions.map(d=>'<details class="domainNav" data-domain="'+esc(d.id)+'"'+(currentAgent===d.id?' open':'')+'><summary>'+esc(d.label)+'</summary><button data-action="open-agent" data-id="'+esc(d.id)+'">Agent home</button>'+(d.pages||[]).map(button).join('')+'</details>').join('')+'</details>';
  document.getElementById('navLinks').innerHTML=group(chiefGroups[0])+domains+chiefGroups.slice(1).map(group).join('');
  document.getElementById('notificationDropdown').hidden=!canPage('notifications');
}
function uiActionAllowed(b){
  const a=b.action;
  if(['user-create','user-disable','users-refresh'].includes(a))return !!chiefContext?.user_management?.roles.length;
  if(['model-register','model-check'].includes(a))return canGlobal('installation.manage');
  if(a==='workspace-home')return currentAgent?canDomain('work.read',currentAgent):canPage('overview');
  if(a==='show')return canPage(b.target);
  if(a==='farm-ai-setup')return canPage('farmRecords');
  if(a==='open-agent')return canDomain('work.read',b.id);
  if(a==='agent-control')return canDomain(b.key==='running'&&b.value==='false'?'safety.pause':'controls.manage',b.id);
  if(a==='resolve-action')return chiefApprovalAccess[String(b.id)]===true;
  if(a==='fact-status')return canDomain(b.status==='USER_CONFIRMED'?'work.approve':'work.manage');
  if(a==='retry-application')return canDomain('work.approve');
  if(['delete-profile','remove-schedule'].includes(a))return canDomain('work.delete');
  if(['add-fact','edit-fact','add-profile','toggle-profile','upload-cv','toggle-cv','add-schedule','edit-schedule','pause-schedule'].includes(a))return canDomain('work.manage');
  if(['n8n-report-preview','n8n-report-history','n8n-handshake','n8n-history','n8n-toggle','n8n-check','add-site','site-control','save-email','test-email','summary-toggle','save-notice-preferences','notice-read','notice-open','read-all','generate-audit','component-preview','component-apply','model-state'].includes(a))return canGlobal('installation.manage');
  if(a==='send-command')return canDomain('work.request');
  if(a==='foundation-refresh'||a==='toggle-sidebar')return true;
  return ['refresh-domains','select-site','load-audit','filter-notifications','report-preview','show-application'].includes(a);
}
function applyChiefVisibility(){
  document.querySelectorAll('button[data-action]').forEach(b=>{b.hidden=!uiActionAllowed(b.dataset);});
  document.getElementById('notificationDropdown').hidden=!canPage('notifications');
  const summaries=document.getElementById('summaryControls');if(summaries)summaries.hidden=!canGlobal('installation.manage');
}
document.addEventListener('toggle',event=>{
  const target=event.target;
  if(target.matches?.('.domainNav')&&target.open)document.querySelectorAll('.domainNav').forEach(d=>{if(d!==target)d.open=false;});
},true);
