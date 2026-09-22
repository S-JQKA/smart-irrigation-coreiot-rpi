/* Tenant custom latest-values widget. Uses platform services, never tokens. */
var definitions = [
 ['controlMode','Chế độ điều khiển','mode'],
 ['criticalMoisture','Ngưỡng khô nghiêm trọng (%)','moisture'],
 ['minMoistureThreshold','Ngưỡng bắt đầu tưới (%)','moisture'],
 ['targetMoisture','Độ ẩm mục tiêu (%)','moisture'],
 ['maxMoistureThreshold','Độ ẩm tối đa (%)','moisture'],
 ['floodMoistureThreshold','Ngưỡng nguy cơ ngập (%)','moisture'],
 ['maxWaterPerCycle','Nước tối đa mỗi lần tưới (L)','water'],
 ['maxWaterPerDay','Nước tối đa mỗi ngày (L)','water'],
 ['maxDurationSec','Thời gian tưới tối đa (s)','seconds']
];
var keys = definitions.map(function(d){return d[0];});
var root, service, devices=[], active=-1, original=null, dirty=false, busy=false, generation=0, dead=false;
var pending={}, subscriptions=[], ui={}, controls={};
var deviceService, commandBusy=false, commandMessages={}, commandSequence=0;
function recentConfig(id){
 var stamp=0;(self.ctx.data||[]).forEach(function(e){var eid=(e.datasource||{}).entityId;if(eid&&typeof eid==='object')eid=eid.id;
  if(eid===id&&e.dataKey.name==='fieldConfigEffective'&&e.data&&e.data.length)stamp=Number(e.data[e.data.length-1][0]);});
 return stamp>0&&Date.now()-stamp>=-30000&&Date.now()-stamp<30000;
}
function manualReady(){if(active<0||!original)return false;var id=devices[active].id.id,t=telemetry(id);
 return !busy&&!dirty&&!pending[id]&&original.controlMode==='MANUAL'&&t.fieldConfigStatus==='APPLIED'&&same(original,object(t.fieldConfigEffective))&&recentConfig(id);
}
function manualButtons(){if(!ui.start)return;var ready=manualReady();
 ui.start.disabled=commandBusy||!ready||!deviceService;ui.stop.disabled=commandBusy||active<0||!deviceService;
 ui.duration.disabled=commandBusy;ui.field.disabled=busy||commandBusy;
 ui.manualHint.textContent=ready?'Chọn thời gian rồi bấm Tưới. Gateway có thể dừng sớm khi chạm giới hạn an toàn.':'Lưu chế độ Thủ công và chờ cấu hình đồng bộ với dữ liệu Gateway mới để bật tưới.';
 if(original)ui.duration.max=String(Math.min(300,Number(original.maxDurationSec)||300));
 ui.commandMessage.textContent=active>=0?(commandMessages[devices[active].id.id]||''):'';
}
function manualCommand(method){
 if(dead||commandBusy||active<0||!deviceService)return;
 var target=devices[active],id=target.id.id,duration=Number(ui.duration.value),limit=Math.min(300,Number(original&&original.maxDurationSec)||300);
 if(method==='TURN_ON'&&(!manualReady()||!Number.isInteger(duration)||duration<1||duration>limit)){
  commandMessages[id]='Cần cấu hình Thủ công đã đồng bộ và thời gian nguyên từ 1 đến '+limit+' giây.';manualButtons();return;
 }
 var now=Date.now(),commandId='widget-manual-'+id+'-'+now+'-'+(++commandSequence);
 var params={commandId:commandId,source:'MANUAL',requestedAt:now,ttlSeconds:15};
 if(method==='TURN_ON')params.runDurationSeconds=duration;
 commandBusy=true;commandMessages[id]='Đang chờ Gateway xác nhận…';manualButtons();
 function finish(message){if(dead)return;commandBusy=false;commandMessages[id]=message;manualButtons();}
 try{subscriptions.push(deviceService.sendTwoWayRpcCommand(id,{method:method,params:params,timeout:15000,persistent:false}).subscribe(function(reply){
  reply=object(reply);
  if(!reply||reply.commandId!==commandId){finish('Chưa xác nhận được kết quả lệnh. Kiểm tra trạng thái van.');return;}
  if(reply.success===true&&reply.reason==='EXECUTED'&&reply.state===(method==='TURN_ON'?'ON':'OFF')){
   finish(method==='TURN_ON'?'✓ Gateway xác nhận đã bật tưới. Thời gian yêu cầu: '+duration+' giây.':'✓ Gateway xác nhận đã dừng tưới.');
  }else{finish('Gateway từ chối lệnh: '+String(reply.detail||reply.reason||'không rõ lý do')+'.');}
 },function(){finish('Chưa nhận được xác nhận; lệnh có thể đã tới Gateway. Kiểm tra van hoặc bấm Dừng tưới.');}));}
 catch(e){finish('Không gửi được lệnh. Kiểm tra dịch vụ RPC và quyền điều khiển thiết bị.');}
}
function same(a,b){return !!a && !!b && keys.every(function(k){return a[k]===b[k];});}
function object(value){if(typeof value==='string'){try{return JSON.parse(value);}catch(e){return null;}}return value;}
function validate(values){
 if(['AUTO','MANUAL','DISABLED'].indexOf(values.controlMode)<0)return 'Hãy chọn chế độ điều khiển.';
 for(var i=1;i<keys.length;i++){if(typeof values[keys[i]]!=='number'||!Number.isFinite(values[keys[i]]))return 'Điền đủ các ô số bằng giá trị hợp lệ.';}
 var moisture=keys.slice(1,6).map(function(k){return values[k];});
 if(moisture.some(function(v){return v<0||v>100;}))return 'Độ ẩm phải từ 0 đến 100%.';
 if(moisture.some(function(v,i){return i>0&&v<=moisture[i-1];}))return 'Năm ngưỡng độ ẩm phải tăng dần theo thứ tự trên form.';
 if(values.maxWaterPerCycle<=0||values.maxWaterPerDay<=0)return 'Hạn mức nước phải lớn hơn 0 L.';
 if(values.maxWaterPerDay<values.maxWaterPerCycle)return 'Hạn mức ngày phải lớn hơn hoặc bằng hạn mức mỗi lần.';
 if(!Number.isInteger(values.maxDurationSec)||values.maxDurationSec<1)return 'Thời gian tối đa phải là số giây nguyên, ít nhất 1.';
 return '';
}
function values(){var v={};definitions.forEach(function(d){v[d[0]]=d[2]==='mode'?controls[d[0]].value:(controls[d[0]].value.trim()===''?null:Number(controls[d[0]].value));});return v;}
function fill(v){keys.forEach(function(k){controls[k].value=v[k]===undefined||v[k]===null?'':v[k];});}
function buttons(){ui.fields.disabled=busy||!original;ui.field.disabled=busy;ui.save.disabled=busy||!original||!dirty;ui.undo.disabled=busy||!dirty;ui.reload.disabled=busy;manualButtons();}
function telemetry(id){var result={};(self.ctx.data||[]).forEach(function(entry){
 var ds=entry.datasource||{}, eid=ds.entityId;
 if(eid&&typeof eid==='object')eid=eid.id;
 if(eid===id&&entry.data&&entry.data.length)result[entry.dataKey.name]=entry.data[entry.data.length-1][1];
 });return result;}
function ack(){if(active<0)return;var id=devices[active].id.id,t=telemetry(id),p=pending[id];
 var effective=object(t.fieldConfigEffective),requested=object(t.fieldConfigRequested);
 var tone='neutral',title='Chưa có phản hồi',hint='Chờ dữ liệu từ Gateway.';
 var reasons={INVALID_THRESHOLD_ORDER:'Các ngưỡng độ ẩm chưa đúng thứ tự.',INVALID_THRESHOLD_RANGE:'Độ ẩm phải từ 0 đến 100%.',INVALID_QUOTA_RANGE:'Hạn mức phải là số hợp lệ lớn hơn 0.',DAILY_QUOTA_BELOW_CYCLE:'Hạn mức ngày phải ≥ hạn mức mỗi lần.',DURATION_MUST_BE_INTEGER_SECONDS:'Thời gian phải là số giây nguyên.',INITIAL_CONFIG_MISMATCH_SCENARIO:'Cấu hình ban đầu chưa khớp scenario.',INITIAL_QUOTA_EXCEEDS_SCENARIO:'Hạn mức ban đầu vượt giới hạn scenario.'};
 if(p && Number(t.fieldConfigReceivedAt)>p.previous && same(requested,p.values)){
  if((t.fieldConfigStatus==='APPLIED'&&same(effective,p.values))||t.fieldConfigStatus==='REJECTED'){delete pending[id];p=null;}
 }
 if(p){tone='pending';title='Đang chờ Gateway';hint='Cấu hình đã lưu trên CoreIoT.';
  if(Date.now()-p.at>30000){tone='warning';title='Chưa nhận xác nhận';hint='Đã chờ hơn 30 giây. Kiểm tra Gateway và kết nối.';}
 }else if(t.fieldConfigStatus==='REJECTED'){tone='error';title='Bị từ chối';hint=reasons[t.fieldConfigReason]||'Gateway chưa chấp nhận cấu hình. Xem chi tiết để biết lý do.';
 }else if(t.fieldConfigStatus==='LOCAL_ONLY'){tone='neutral';title='Dùng cấu hình cục bộ';hint='Phiên này chưa nhận cấu hình từ dashboard.';
 }else if(original&&effective&&!same(original,effective)){tone='warning';title='Chưa đồng bộ';hint='Gateway đang dùng cấu hình khác với bản đã lưu.';
 }else if(t.fieldConfigStatus==='APPLIED'&&original&&same(original,effective)){tone='success';title='Đã đồng bộ';hint='Gateway đã áp dụng cấu hình đã lưu.';
 }else if(t.fieldConfigStatus==='WAITING'){tone='pending';title='Chờ cấu hình ban đầu';hint='Gateway chưa nhận đủ cấu hình hợp lệ.';}
 ui.ack.className='sf-badge sf-'+tone;ui.ack.textContent=({success:'✓ ',warning:'! ',error:'× ',pending:'◷ ',neutral:'○ '})[tone]+title;
 ui.hint.textContent=hint;
 ui.applied.textContent=Number(t.fieldConfigAppliedAt)>0?'Áp dụng gần nhất · '+new Date(Number(t.fieldConfigAppliedAt)).toLocaleString('vi-VN'):'';
 var differences=original&&effective?definitions.filter(function(d){return original[d[0]]!==effective[d[0]];}).map(function(d){return d[1]+': đã lưu '+String(original[d[0]]===undefined?'chưa có':original[d[0]])+' → Gateway '+String(effective[d[0]]===undefined?'chưa có':effective[d[0]]);}):[];
 ui.detail.textContent=['Mã phản hồi: '+String(t.fieldConfigReason||'chưa có')].concat(differences,['Phản hồi gần nhất không xác nhận Gateway hiện đang online.']).join('\n');
 manualButtons();
}
function load(index){
 active=index;var ticket=++generation;original=null;dirty=false;busy=true;fill({});buttons();
 ui.device.textContent='Thiết bị: '+devices[index].name;ui.message.textContent='Đang đọc Shared attributes…';ack();
 subscriptions.push(service.getEntityAttributes(devices[index].id,'SHARED_SCOPE',keys).subscribe(function(rows){
  if(dead||ticket!==generation)return;
  original={};rows.forEach(function(row){if(keys.indexOf(row.key)>=0)original[row.key]=row.value;});
  fill(original);busy=false;buttons();
  ui.message.textContent=keys.some(function(k){return original[k]===undefined;})?'Một số thuộc tính chưa có. Điền đầy đủ trước khi lưu; widget không tự đặt hạn mức mặc định.':'';ack();
 },function(){if(dead||ticket!==generation)return;busy=false;ui.message.textContent='Không đọc được cấu hình. Kiểm tra quyền và alias thiết bị rồi bấm Tải lại.';buttons();}));
}
self.onInit=function(){
 root=self.ctx.$container[0];['field','device','form','fields','inputs','message','reload','undo','save','ack','applied','hint','detail'].forEach(function(k){ui[k]=root.querySelector('[data-role="'+k+'"]');});
 var injector=self.ctx.$scope.$injector;service=injector.get(self.ctx.servicesMap.get('attributeService'));
 ['duration','start','stop','manualHint','commandMessage'].forEach(function(k){ui[k]=root.querySelector('[data-role="'+k+'"]');});
 try{deviceService=injector.get(self.ctx.servicesMap.get('deviceService'));}catch(e){deviceService=null;}
 ui.start.onclick=function(){manualCommand('TURN_ON');};ui.stop.onclick=function(){manualCommand('TURN_OFF');};
 (self.ctx.datasources||[]).forEach(function(ds){var id=ds.entityId;if(id&&typeof id==='object')id=id.id;
  if(id&&ds.entityType==='DEVICE'&&!devices.some(function(d){return d.id.id===id;}))devices.push({id:{entityType:'DEVICE',id:id},name:ds.entityName||ds.name||id});});
 devices.sort(function(a,b){return a.name.localeCompare(b.name);});
 definitions.forEach(function(d){var label=document.createElement('label');label.textContent=d[1];var input=document.createElement(d[2]==='mode'?'select':'input');input.required=true;
  if(d[2]==='mode'){[['','Chọn chế độ'],['AUTO','Tự động'],['MANUAL','Thủ công'],['DISABLED','Vô hiệu hóa tưới']].forEach(function(o){var option=document.createElement('option');option.value=o[0];option.textContent=o[1];input.appendChild(option);});}
  else{input.type='number';input.step=d[2]==='seconds'?'1':'any';input.min=d[2]==='moisture'?'0':d[2]==='seconds'?'1':'0.000001';if(d[2]==='moisture')input.max='100';}
  input.name=d[0];controls[d[0]]=input;label.appendChild(input);ui.inputs.appendChild(label);
 });
 devices.forEach(function(d,i){var o=document.createElement('option');o.value=String(i);o.textContent=d.name.replace('SI Smart Valve ','Field ');ui.field.appendChild(o);});
 ui.form.oninput=function(){dirty=!same(values(),original);buttons();};
 ui.field.onchange=function(){var i=Number(ui.field.value);if(dirty&&!window.confirm('Bạn có thay đổi chưa lưu. Bỏ thay đổi để chuyển Field?')){ui.field.value=String(active);return;}load(i);};
 ui.undo.onclick=function(){fill(original||{});dirty=false;ui.message.textContent='';buttons();};
 ui.reload.onclick=function(){if(active>=0&&(!dirty||window.confirm('Bỏ thay đổi chưa lưu và tải lại?')))load(active);};
 ui.form.onsubmit=function(event){event.preventDefault();if(busy||active<0||!original)return;var data=values(),error=validate(data);if(error){ui.message.textContent=error;return;}
  var target=devices[active],ticket=generation,before=Number(telemetry(target.id.id).fieldConfigReceivedAt)||0;busy=true;buttons();
  // Re-read before writing to catch changes made by another form/operator.
  subscriptions.push(service.getEntityAttributes(target.id,'SHARED_SCOPE',keys).subscribe(function(rows){
   if(dead||ticket!==generation)return;var current={};rows.forEach(function(r){current[r.key]=r.value;});
   if(!same(current,original)){busy=false;ui.message.textContent='Cấu hình đã thay đổi từ nơi khác. Bấm Tải lại rồi chỉnh lại để tránh ghi đè.';buttons();return;}
   subscriptions.push(service.saveEntityAttributes(target.id,'SHARED_SCOPE',keys.map(function(k){return {key:k,value:data[k]};})).subscribe(function(){
    if(dead||ticket!==generation)return;original=data;dirty=false;busy=false;pending[target.id.id]={values:data,previous:before,at:Date.now()};ui.message.textContent='Đã lưu cấu hình cho '+target.name+'.';buttons();ack();
   },function(){if(dead||ticket!==generation)return;busy=false;ui.message.textContent='Lưu không thành công. Kiểm tra kết nối/quyền; có thể tải lại để kiểm tra giá trị trên CoreIoT.';buttons();}));
  },function(){if(dead||ticket!==generation)return;busy=false;ui.message.textContent='Không kiểm tra được cấu hình hiện tại; chưa gửi thay đổi.';buttons();}));
 };
 if(devices.length!==2){ui.message.textContent='Cần cấu hình đúng hai datasource Device khác nhau: SI Smart Valve 1 và SI Smart Valve 2.';buttons();ui.field.disabled=true;return;}
 load(0);self.sfTimer=setInterval(ack,2000);
};
self.onDataUpdated=function(){if(root&&!dead)ack();};
self.onDestroy=function(){dead=true;generation++;clearInterval(self.sfTimer);subscriptions.forEach(function(s){s.unsubscribe();});};
self.typeParameters=function(){return {maxDatasources:2,datasourcesOptional:false,dataKeysOptional:false};};
