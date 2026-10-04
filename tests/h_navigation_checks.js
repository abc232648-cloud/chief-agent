const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const nodes={};const element=id=>nodes[id]||(nodes[id]={innerHTML:'',textContent:'',hidden:false,value:''});
const sandbox={document:{getElementById:element,querySelectorAll:()=>[],addEventListener:()=>{}},currentAgent:null,agentDefinitions:[],esc:s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))};
vm.createContext(sandbox);
const lifecycle={};sandbox.window={addEventListener:(name,fn)=>{lifecycle[name]=fn;}};
sandbox.document.addEventListener=(name,fn)=>{lifecycle[name]=fn;};
for(const n of ['chief-navigation.js','chief-foundation.js'])vm.runInContext(fs.readFileSync('static/'+n,'utf8'),sandbox);
for(const event of ['pagehide','visibilitychange']){
  element('setupKey').value='synthetic-secret';element('setupPassword').value='synthetic-password';
  sandbox.document.visibilityState='hidden';lifecycle[event]();
  assert.equal(element('setupKey').value,'');assert.equal(element('setupPassword').value,'');
}
vm.runInContext(`chiefContext={pages:['overview','domains','agentDetail','jobs','actions','evidence'],global_permissions:{},domains:[{id:'jobs',label:'Job <script>',pages:[['jobs','Opportunities']],permissions:{'work.read':true,'work.request':true}}]};agentDefinitions=chiefContext.domains;renderChiefNavigation('overview');`,sandbox);
assert.match(nodes.navLinks.innerHTML,/>Main</);assert.match(nodes.navLinks.innerHTML,/>Domains</);assert.match(nodes.navLinks.innerHTML,/>Work &amp; Control</);
assert(!nodes.navLinks.innerHTML.includes('data-target="models"'));assert(nodes.navLinks.innerHTML.includes('Job &lt;script&gt;'));assert(nodes.notificationDropdown.hidden);
assert.equal(vm.runInContext("uiActionAllowed({action:'model-state'})",sandbox),false);
assert.equal(vm.runInContext("uiActionAllowed({action:'model-register'})",sandbox),false);
assert.equal(vm.runInContext("uiActionAllowed({action:'model-check'})",sandbox),false);
assert.equal(vm.runInContext("uiActionAllowed({action:'fact-status',status:'USER_CONFIRMED'})",sandbox),false);
assert.equal(vm.runInContext("uiActionAllowed({action:'resolve-action',id:'1'})",sandbox),false);
vm.runInContext("chiefApprovalAccess={'1':true}",sandbox);assert.equal(vm.runInContext("uiActionAllowed({action:'resolve-action',id:'1'})",sandbox),true);
assert.equal(vm.runInContext("uiActionAllowed({action:'resolve-action',id:'2'})",sandbox),false);
assert.equal(vm.runInContext("uiActionAllowed({action:'unknown-action'})",sandbox),false);
assert(!vm.runInContext("foundationDetails('Evidence','<img src=x onerror=alert(1)>')",sandbox).includes('<img'));
vm.runInContext("chiefContext.pages.push('models','runtime','health');chiefContext.global_permissions['installation.manage']=true;renderChiefNavigation('models')",sandbox);
assert(nodes.navLinks.innerHTML.includes('aria-current="page">Models'));
assert.equal(vm.runInContext("uiActionAllowed({action:'model-state'})",sandbox),true);
assert.equal(vm.runInContext("uiActionAllowed({action:'model-register'})",sandbox),true);
assert.equal(vm.runInContext("uiActionAllowed({action:'model-check'})",sandbox),true);
console.log('Navigation, role visibility, delegated action scope and text escaping: PASS');
// Exercise the same preview/confirmation/token flow against a synthetic HTTP boundary.
(async()=>{
  const calls=[];
  sandbox.postJson=async(path,body)=>{calls.push({path,body});return {token:'fresh-token',affected:[{kind:'component',id:'dependent'}]};};
  sandbox.api=async()=>[];sandbox.confirm=()=>true;
  element('choice').value='MAINTENANCE';element('componentImpact').scrollIntoView=()=>{};
  await vm.runInContext("foundationActions({dataset:{kind:'component',id:'jobs-worker',select:'choice'}})['component-preview']()",sandbox);
  assert.equal(calls[0].path,'/api/component-controls/preview');
  element('componentReason').value='Synthetic reviewed change';
  await vm.runInContext("foundationActions({dataset:{}})['component-apply']()",sandbox);
  assert.equal(calls[1].body.token,'fresh-token');assert.equal(calls[1].body.confirmed,true);assert.equal(calls[1].body.reason,'Synthetic reviewed change');
  sandbox.confirm=()=>false;
  await vm.runInContext("foundationActions({dataset:{id:'jobs.qwen',state:'DISABLED'}})['model-state']()",sandbox);
  assert.equal(calls.length,2,'Declined model confirmation must not write');
  sandbox.api=async()=>({candidates:[{product:'Pinned runtime',version:'fixture',qualification:{gate_status:[['AUTHORITY','UNTESTED']]}}],limitation:'No evidence'});
  await vm.runInContext("loadFoundation('runtime')",sandbox);
  assert.match(element('runtimeContent').innerHTML,/UNTESTED/);assert(!element('runtimeContent').innerHTML.includes('<button'));
  console.log('Dependency preview token, confirmation, cancel and runtime rendering: PASS');
})().catch(error=>{console.error(error);process.exitCode=1;});
