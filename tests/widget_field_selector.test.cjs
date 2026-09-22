const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
class Element {
  constructor(){this.value='';this.textContent='';this.children=[];this.disabled=false;}
  set value(v){this._value=String(v);}
  get value(){return this._value;}
  appendChild(e){this.children.push(e);}
}
const roles={};['field','device','form','fields','inputs','message','reload','undo','save','ack','applied','hint','detail','duration','start','stop','manualHint','commandMessage'].forEach(k=>roles[k]=new Element());
const keys=['controlMode','criticalMoisture','minMoistureThreshold','targetMoisture','maxMoistureThreshold','floodMoistureThreshold','maxWaterPerCycle','maxWaterPerDay','maxDurationSec'];
const a=Object.fromEntries(keys.map((k,i)=>[k,['AUTO',20,45,60,85,95,100,500,600][i]]));
const b={...a,targetMoisture:65};const saved={one:{...a},two:{...b}};const writes=[];
const observable=(v)=>({subscribe(ok){ok(v);return {unsubscribe(){}};}});
const service={getEntityAttributes(id,scope){assert.equal(scope,'SHARED_SCOPE');return observable(Object.entries(saved[id.id]).map(([key,value])=>({key,value})));},
 saveEntityAttributes(id,scope,rows){writes.push(id.id);saved[id.id]=Object.fromEntries(rows.map(r=>[r.key,r.value]));return observable(null);}};
const rpcCalls=[];let rpcOk,rpcFail;const rpcService={sendTwoWayRpcCommand(id,body){rpcCalls.push({id,body});return {subscribe(ok,fail){rpcOk=ok;rpcFail=fail;return {unsubscribe(){}};}};}};
let confirms=false;
const self={ctx:{$container:[{querySelector(s){return roles[s.match(/"(.*?)"/)[1]];}}],$scope:{$injector:{get(name){return name==='deviceService'?rpcService:service;}}},servicesMap:new Map([['attributeService','attr'],['deviceService','deviceService']]),datasources:[{entityId:'one',entityType:'DEVICE',entityName:'SI Smart Valve 1'},{entityId:'two',entityType:'DEVICE',entityName:'SI Smart Valve 2'}],data:[]}};
const context=vm.createContext({self,document:{createElement(){return new Element();}},window:{confirm(){return confirms;}},setInterval(){return 1;},clearInterval(){},console});
vm.runInContext(fs.readFileSync('implementation/coreiot/widgets/field_selector/controller.js','utf8'),context);
self.onInit();assert.equal(context.active,0);assert.equal(context.controls.targetMoisture.value,'60');
context.controls.targetMoisture.value='61';roles.form.oninput();roles.field.value='1';roles.field.onchange();
assert.equal(context.active,0,'cancelled switch preserves selected Field');assert.equal(context.controls.targetMoisture.value,'61');
confirms=true;roles.field.value='1';roles.field.onchange();assert.equal(context.controls.targetMoisture.value,'65');
context.controls.targetMoisture.value='66';roles.form.oninput();roles.form.onsubmit({preventDefault(){}});
assert.deepEqual(writes,['two']);assert.equal(saved.one.targetMoisture,60);assert.equal(saved.two.targetMoisture,66);
assert.match(roles.ack.textContent,/Đang chờ/);
function report(target,effective,status='APPLIED'){
 self.ctx.data=Object.entries({fieldConfigReceivedAt:12,fieldConfigAppliedAt:12,fieldConfigStatus:status,fieldConfigRequested:target,fieldConfigEffective:effective}).map(([name,value])=>({datasource:{entityId:'two'},dataKey:{name},data:[[12,value]]}));self.onDataUpdated();
}
report(b,b);assert.ok(context.pending.two,'old/different payload must not acknowledge save');
report(saved.two,saved.two);assert.equal(context.pending.two,undefined);assert.match(roles.ack.textContent,/Đã đồng bộ/);
report(saved.two,{...saved.two,targetMoisture:60});assert.match(roles.ack.textContent,/Chưa đồng bộ/);assert.doesNotMatch(roles.ack.textContent,/CONFIG_APPLIED|Đã đồng bộ/);assert.match(roles.detail.textContent,/60/);
context.controls.targetMoisture.value='40';roles.form.oninput();roles.form.onsubmit({preventDefault(){}});assert.equal(writes.length,1,'invalid threshold order never writes');
context.controls.targetMoisture.value='67';saved.two.maxDurationSec=300;roles.form.oninput();roles.form.onsubmit({preventDefault(){}});assert.equal(writes.length,1,'concurrent edit never overwritten');assert.match(roles.message.textContent,/thay đổi từ nơi khác/);

context.manualCommand('TURN_ON');assert.equal(rpcCalls.length,0,'AUTO never starts');
saved.two.controlMode='MANUAL';context.load(1);roles.duration.value='60';
function fresh(){report(saved.two,saved.two);self.ctx.data.forEach(e=>e.data[0][0]=Date.now());self.onDataUpdated();}
fresh();assert.equal(roles.start.disabled,false);
context.controls.targetMoisture.value='68';roles.form.oninput();context.manualCommand('TURN_ON');assert.equal(rpcCalls.length,0,'dirty form never starts');roles.undo.onclick();
roles.duration.value='301';context.manualCommand('TURN_ON');assert.equal(rpcCalls.length,0,'overlong run never sends');roles.duration.value='60';
context.manualCommand('TURN_ON');assert.equal(rpcCalls.length,1);assert.equal(rpcCalls[0].id,'two');assert.equal(rpcCalls[0].body.params.source,'MANUAL');assert.equal(rpcCalls[0].body.params.runDurationSeconds,60);assert.equal(rpcCalls[0].body.persistent,false);
context.manualCommand('TURN_ON');assert.equal(rpcCalls.length,1,'double click blocked');
rpcOk({commandId:'wrong',success:true,state:'ON',reason:'EXECUTED'});assert.match(roles.commandMessage.textContent,/Chưa xác nhận/);
context.manualCommand('TURN_ON');rpcOk({commandId:rpcCalls[1].body.params.commandId,success:true,state:'ON',reason:'EXECUTED'});assert.match(roles.commandMessage.textContent,/xác nhận đã bật/);
self.ctx.data.forEach(e=>e.data[0][0]=1);self.onDataUpdated();assert.equal(roles.start.disabled,true,'stale telemetry blocks start');
context.manualCommand('TURN_OFF');assert.equal(rpcCalls[2].body.method,'TURN_OFF','stop available even with stale config');rpcFail();assert.match(roles.commandMessage.textContent,/có thể đã tới/);
assert.equal(rpcCalls.length,3,'timeout never retries automatically');
fresh();context.manualCommand('TURN_ON');rpcOk({commandId:rpcCalls[3].body.params.commandId,success:false,state:'OFF',reason:'SAFETY_BLOCK'});assert.match(roles.commandMessage.textContent,/từ chối/);
self.onDestroy();assert.equal(context.dead,true);
console.log('PASS: selector loads, discard/cancel, selected-device-only save, matching ACK, validation, concurrent edit, destroy');
