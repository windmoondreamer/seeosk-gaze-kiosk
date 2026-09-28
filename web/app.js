'use strict';
const $ = id => document.getElementById(id);
const state = {ready:false,camera:false,calibrated:false,paused:false,calibrating:false,view:'kiosk',
  regions:[],active:null,lastGaze:0,sourceRequest:0,calGeneration:0,phase:'calibrate',point:0,
  pendingCalibration:false,headValid:false,sampleIndex:0,dwellStart:0,dwellKey:null,firedKey:null,blockedRect:null,awaySince:0};
const pointer=new GazePointer();
const selection=new GazeSelection();
const CAL = [.12,.5,.88].flatMap(y=>[.10,.5,.90].map(x=>[x,y]));
const CHECK = [[.23,.23],[.77,.76],[.23,.76]];
let calTimer, detectTimer, resizeTimer, toastTimer, snapshotRect;
function send(cmd, args={}) {
  window.webkit?.messageHandlers?.seeosk?.postMessage({cmd,...args});
}
function toast(message) { $('toast').textContent=message; $('toast').hidden=false; clearTimeout(toastTimer); toastTimer=setTimeout(()=>$('toast').hidden=true,5500); }
function badge(text,live=false){$('statusBadge').textContent=text;$('statusBadge').classList.toggle('live',live);}
function geometry(){return {width:innerWidth,height:innerHeight};}
function configure(){send('configure',{geometry:geometry()});}
function resetGaze(){
  pointer.reset();selection.reset();
  $('gaze').hidden=true; state.active=null;state.lastGaze=0;state.dwellStart=0;state.dwellKey=null;state.firedKey=null;
  document.querySelectorAll('.region.active').forEach(e=>e.classList.remove('active'));
  $('selection').textContent='바라보는 메뉴가 여기에 표시됩니다.';
}
function controls(){
  $('cameraToggle').textContent=state.camera?'카메라 끄기':'카메라 켜기';
  $('camera').disabled=state.camera||state.calibrating;
  $('pause').disabled=!state.camera||state.calibrating;
  $('pause').textContent=state.paused?'추적 다시 시작':'일시정지';
  $('calibrate').disabled=!state.ready||state.calibrating;
  $('cameraToggle').disabled=!state.ready||state.calibrating;
  if(state.calibrating)badge('캘리브레이션 중');
  else if(state.paused)badge('일시정지');
  else if(state.camera&&state.calibrated)badge('시선 추적 중',true);
  else if(state.camera)badge('캘리브레이션 필요');
  else badge(state.ready?'카메라 대기':'엔진 연결 중');
}
function pause(){if(!state.camera||state.calibrating)return;state.paused=!state.paused;resetGaze();controls();}
function beginCalibration(){
  $('result').hidden=true;
  if(!state.ready){toast('시선 추적 엔진을 준비 중입니다. 잠시 후 다시 눌러 주세요.');return;}
  if(!state.camera){state.pendingCalibration=true;startCamera();return;}
  state.calibrating=true;state.paused=false;state.calGeneration++;state.phase='calibrate';state.point=0;
  resetGaze();$('calibration').hidden=false;$('target').hidden=true;controls();
  send('calibrate',{generation:state.calGeneration});
}
function showPoint(){
  clearTimeout(calTimer);
  const points=state.phase==='check'?CHECK:CAL;
  const [x,y]=points[state.point];
  $('target').hidden=false;$('target').style.left=`${x*100}%`;$('target').style.top=`${y*100}%`;
  $('calTitle').textContent=state.phase==='check'?'보정 결과 확인하기':'시선 위치 맞추기';
  $('calProgress').textContent=`${state.phase==='check'?'확인':'보정'} ${state.point+1} / ${points.length}`;
  $('calInstruction').textContent='머리는 그대로, 눈동자만 점을 따라 움직여 주세요.';
  $('pointProgress').value=0;
  const generation=state.calGeneration;
  calTimer=setTimeout(()=>{if(state.calibrating&&generation===state.calGeneration)send('collect',{generation,index:state.point,phase:state.phase});},450);
}
function cancelCalibration(){
  clearTimeout(calTimer);state.pendingCalibration=false;
  if(state.calibrating)send('cancel');
  state.calibrating=false;$('calibration').hidden=true;resetGaze();controls();
}
function metrics(m){
  $('meanError').textContent=Math.round(m.mean_px);$('p95Error').textContent=Math.round(m.p95_px);
  $('calBadge').textContent=m.passed?'보정됨':'재보정 필요';
  $('calHint').textContent=m.passed?'별도 3점에서 확인한 오차입니다. 머리·카메라·창 위치가 바뀌면 다시 보정해 주세요.':'마우스 이동 중 · 오차가 큽니다. 조명과 자세를 맞춘 뒤 다시 보정해 주세요.';
}
function calibrationResult(m){
  clearTimeout(calTimer);state.calibrating=false;state.calibrated=!!(m.passed||m.pointer_usable);$('calibration').hidden=true;
  metrics(m);resetGaze();controls();
  $('resultTitle').textContent=m.passed?'시선 위치를 맞췄습니다':'한 번 더 맞춰 주세요';
  $('resultText').textContent=`확인용 3점에서 평균 ${Math.round(m.mean_px)}px\n95% 오차 ${Math.round(m.p95_px)}px\n\n${m.passed?'키오스크에서 파란 점이 시선을 따라가는지 확인해 주세요.':'머리를 고정하고 점을 끝까지 바라봐 주세요. 카메라를 눈높이에 두면 도움이 됩니다.'}`;
  $('result').hidden=true;

  toast(m.passed?'보정 완료 · 실제 마우스가 시선을 따라갑니다. Esc로 일시정지합니다.':'시선 마우스를 시작합니다. 보정 오차가 커서 재보정을 권장합니다.');
  invalidateRegions();
}
function startCamera(){
  $('trackingHint').textContent='카메라를 시작하고 있습니다…';
  send('start',{camera:$('camera').value==='none'||!$('camera').value?-1:Number($('camera').value)});
}
function frameDocument(){return $('kiosk').contentDocument;}
function contentRect(){
  const r=$(state.view==='kiosk'?'kiosk':'sample').getBoundingClientRect();
  return {x:r.x,y:r.y,w:r.width,h:r.height};
}
function visibleDomRegions(){
  const doc=frameDocument();if(!doc)return [];
  const outer=$('kiosk').getBoundingClientRect();
  return [...doc.querySelectorAll('[onclick],button')].flatMap((e,i)=>{
    const r=e.getBoundingClientRect();
    if(r.width<5||r.height<5||r.right<=0||r.bottom<=0||r.x>=outer.width||r.y>=outer.height)return [];
    const x=Math.max(0,r.x), y=Math.max(0,r.y), right=Math.min(outer.width,r.right),bottom=Math.min(outer.height,r.bottom);
    const hit=doc.elementFromPoint((x+right)/2,(y+bottom)/2);
    if(!(hit===e||e.contains(hit)))return [];
    return [{x:x+outer.x,y:y+outer.y,w:right-x,h:bottom-y,key:`dom-${i}`,element:e,
      label:(e.dataset.label||e.textContent.trim()||e.id||'메뉴').replaceAll('_',' ').slice(0,55)}];
  });
}
function renderRegions(){
  $('regionLayer').replaceChildren();
  for(const r of state.regions){
    const node=document.createElement('div');node.className='region';node.dataset.label=r.label;
    Object.assign(node.style,{left:`${r.x}px`,top:`${r.y}px`,width:`${r.w}px`,height:`${r.h}px`});
    r.node=node;$('regionLayer').append(node);
  }
  $('regionLayer').hidden=!$('showRegions').checked;
  $('regionCount').textContent=`${state.regions.length}개 영역 · ${$('regionMode').value==='dom'?'웹 버튼':'YOLO'}`;
}
function invalidateRegions(){
  state.sourceRequest++;state.regions=[];resetGaze();renderRegions();
  clearTimeout(detectTimer);detectTimer=setTimeout(updateRegions,220);
}
function updateRegions(){
  if(state.calibrating||!$('result').hidden)return;
  if(state.view==='kiosk'&&$('regionMode').value==='dom'){
    state.regions=visibleDomRegions();renderRegions();return;
  }
  if(!state.ready)return;
  const request=++state.sourceRequest;
  snapshotRect=contentRect();
  if(snapshotRect.w<5||snapshotRect.h<5)return;
  $('regionCount').textContent='영역 탐지 중…';
  if(state.view==='sample')send('detect_sample',{filename:SAMPLE_FILES[state.sampleIndex],request});
  else {
    document.body.classList.add('capturing');
    requestAnimationFrame(()=>requestAnimationFrame(()=>send('snapshot',{rect:snapshotRect,request})));
  }
}
function selectTarget(fromDwell=false){
  if(!state.active||!state.headValid||state.paused||state.calibrating||!$('result').hidden||performance.now()-state.lastGaze>300)return;
  const r=state.active;
  if(fromDwell){state.blockedRect={x:r.x,y:r.y,w:r.w,h:r.h};state.awaySince=0;}
  if(state.view==='sample'){toast(`선택 영역: ${r.label} · 학습 이미지는 화면이 전환되지 않습니다.`);return;}
  if(r.element){r.element.click();invalidateRegions();return;}
  const f=$('kiosk').getBoundingClientRect();
  const doc=frameDocument();
  const e=doc.elementFromPoint(r.x+r.w/2-f.x,r.y+r.h/2-f.y)?.closest('[onclick],button');
  if(e){e.click();invalidateRegions();}else toast('이 탐지 영역에는 연결된 웹 버튼이 없습니다.');
}
function track(m){
  if(m.preview){$('preview').src=`data:image/jpeg;base64,${m.preview}`;$('preview').hidden=false;$('previewPlaceholder').hidden=true;}
  $('fps').textContent=`${m.fps||'—'} FPS`;$('faceBadge').textContent=m.valid?'눈동자 감지됨':'위치 확인 필요';
  $('trackingHint').textContent=m.reason;state.headValid=m.valid;
  if(!m.valid&&state.calibrated&&!state.paused&&!state.calibrating){
    selection.update(NaN,NaN,performance.now(),state.regions);
    $('gaze').hidden=true;return;
  }
  if(!m.valid||!state.calibrated||state.paused||state.calibrating||!$('result').hidden||m.x===undefined){resetGaze();return;}
  const now=performance.now();state.lastGaze=now;
  const stabilized=pointer.update(m.x,m.y,now,state.regions);
  send('move_cursor',{x:stabilized.x,y:stabilized.y});
  $('gaze').hidden=false;$('gaze').style.left=`${stabilized.x}px`;$('gaze').style.top=`${stabilized.y}px`;
  const choice=selection.update(m.x,m.y,now,state.regions);
  const active=choice.target;
  if(state.active?.key!==active?.key){
    state.active?.node?.classList.remove('active');active?.node?.classList.add('active');
    state.dwellStart=now;state.dwellKey=active?.key;state.firedKey=null;
  }
  state.active=active;
  $('selection').textContent=active?active.label:'선택 영역 밖을 바라보고 있습니다.';
  if(state.blockedRect){
    const b=state.blockedRect;
    const inside=m.x>=b.x&&m.x<=b.x+b.w&&m.y>=b.y&&m.y<=b.y+b.h;
    if(inside)state.awaySince=0;
    else if(!state.awaySince)state.awaySince=now;
    else if(now-state.awaySince>=300){state.blockedRect=null;state.dwellStart=now;}
  }
  if(active&&$('dwell').checked&&state.firedKey!==active.key&&!state.blockedRect){
    const elapsed=choice.elapsed;
    $('selection').textContent=`${active.label} · ${Math.min(100,Math.round(elapsed/9))}%`;
    if(choice.inside&&elapsed>=900){state.firedKey=active.key;selectTarget(true);}
  }
}
function switchView(view){
  state.view=view;$('kiosk').hidden=view!=='kiosk';$('sample').hidden=view!=='sample';
  $('kioskTab').classList.toggle('selected',view==='kiosk');$('sampleTab').classList.toggle('selected',view==='sample');
  $('sampleControls').hidden=view!=='sample';$('resetKiosk').hidden=view!=='kiosk';
  $('regionMode').disabled=view==='sample';$('regionMode').value=view==='sample'?'yolo':'dom';
  $('sourceNote').textContent=view==='sample'?'합성 키오스크 화면 · 영역 선택 연습':'메가커피 화면 재현 · 연습용';
  if(view==='sample')loadSample(state.sampleIndex);else invalidateRegions();
}
function loadSample(index){
  state.sampleIndex=(index+SAMPLE_FILES.length)%SAMPLE_FILES.length;
  $('sampleList').value=String(state.sampleIndex);invalidateRegions();
  $('sample').src=`samples/${encodeURIComponent(SAMPLE_FILES[state.sampleIndex])}`;
}
window.receive=m=>{
  switch(m.type){
    case 'ready':state.ready=true;configure();controls();if(state.view==='sample'||$('regionMode').value==='yolo')updateRegions();break;
    case 'cameras':{
      $('camera').replaceChildren();
      for(const d of m.devices){const o=new Option(d.name,String(d.index));$('camera').add(o);}
      if(!m.devices.length)$('camera').add(new Option('카메라 없음','none'));
      const preferred=m.devices.find(d=>d.builtin)||m.devices.find(d=>!d.phone)||m.devices[0];
      if(preferred)$('camera').value=String(preferred.index);break;
    }
    case 'started':state.camera=true;state.paused=false;controls();$('permissions').hidden=true;
      if(state.pendingCalibration){state.pendingCalibration=false;beginCalibration();}break;
    case 'stopped':state.camera=false;state.calibrated=false;state.pendingCalibration=false;cancelCalibration();
      $('preview').hidden=true;$('preview').removeAttribute('src');$('previewPlaceholder').hidden=false;$('faceBadge').textContent='카메라 꺼짐';$('fps').textContent='— FPS';controls();break;
    case 'profile':state.calibrated=true;metrics(m.validation);controls();break;
    case 'tracking':track(m);break;
    case 'calibration_started':if(m.generation===state.calGeneration&&state.calibrating)showPoint();break;
    case 'calibration_progress':if(state.calibrating){$('pointProgress').value=m.progress;$('calInstruction').textContent=m.hint;}break;
    case 'sample_retry':if(m.generation===state.calGeneration&&state.calibrating){toast(m.message);showPoint();}break;
    case 'sample_done':
      if(!state.calibrating||m.generation!==state.calGeneration)return;
      state.point++;
      if(state.phase==='calibrate'&&state.point===CAL.length){state.phase='check';state.point=0;}
      showPoint();break;
    case 'calibration_result':if(m.generation===state.calGeneration&&state.calibrating)calibrationResult(m.validation);break;
    case 'calibration_failed':cancelCalibration();state.calibrated=false;controls();toast(m.message);break;
    case 'calibration_invalid':
      state.calibrated=false;state.calibrating=false;clearTimeout(calTimer);$('calibration').hidden=true;
      $('calBadge').textContent='보정 필요';$('meanError').textContent='—';$('p95Error').textContent='—';
      $('calHint').textContent=m.message;resetGaze();controls();break;
    case 'cancelled':state.calibrated=m.calibrated;controls();break;
    case 'window_changed':
      if(state.calibrating)cancelCalibration();configure();invalidateRegions();break;
    case 'focus':if(!m.active&&state.camera){state.paused=true;resetGaze();controls();}break;
    case 'regions':
      if(m.request!==state.sourceRequest)return;
      state.regions=m.regions.map((r,i)=>({...r,x:snapshotRect.x+r.x*snapshotRect.w,y:snapshotRect.y+r.y*snapshotRect.h,
        w:r.w*snapshotRect.w,h:r.h*snapshotRect.h,key:`yolo-${i}`,label:`${r.label} · ${Math.round(r.confidence*100)}%`}));
      renderRegions();
      if(!state.regions.length&&state.view==='kiosk')toast('학습 모델이 이 화면의 버튼을 찾지 못했습니다. 오른쪽에서 “웹 버튼 위치”를 선택해 주세요.');
      break;
    case 'capture_done':document.body.classList.remove('capturing');break;
    case 'detector_error':if(m.request!==state.sourceRequest)return;$('regionCount').textContent='영역 탐지 실패';toast(m.message);break;
    case 'permission_denied':state.pendingCalibration=false;$('permissions').hidden=false;toast(m.message);break;
    case 'engine_exit':state.ready=false;state.camera=false;state.calibrated=false;cancelCalibration();controls();toast('추적 엔진이 종료되었습니다. 앱을 다시 실행해 주세요.');break;
    case 'error':state.pendingCalibration=false;toast(m.message);$('trackingHint').textContent=m.message;break;
  }
};
$('cameraToggle').onclick=()=>state.camera?send('stop'):startCamera();
$('calibrate').onclick=beginCalibration;$('pause').onclick=pause;$('cancelCal').onclick=cancelCalibration;
$('retryCal').onclick=beginCalibration;$('closeResult').onclick=()=>{$('result').hidden=true;invalidateRegions();};
$('permissions').onclick=()=>send('permissions');$('fullscreen').onclick=()=>send('fullscreen');
$('kioskTab').onclick=()=>switchView('kiosk');$('sampleTab').onclick=()=>switchView('sample');
$('resetKiosk').onclick=()=>{$('kiosk').srcdoc=KIOSK_HTML;invalidateRegions();};
$('regionMode').onchange=invalidateRegions;$('showRegions').onchange=()=>{$('regionLayer').hidden=!$('showRegions').checked;};
$('dwell').onchange=()=>{selection.reset();state.dwellStart=performance.now();state.firedKey=null;};
$('prevSample').onclick=()=>loadSample(state.sampleIndex-1);$('nextSample').onclick=()=>loadSample(state.sampleIndex+1);
$('sampleList').onchange=()=>loadSample(Number($('sampleList').value));$('sample').onload=invalidateRegions;
SAMPLE_FILES.forEach((f,i)=>$('sampleList').add(new Option(`${i+1} / ${SAMPLE_FILES.length} · ${f}`,String(i))));
$('kiosk').onload=()=>{
  const doc=frameDocument();if(!doc)return;
  const style=doc.createElement('style');style.textContent=`html,body{margin:0;width:100%;height:100%;overflow:hidden;font-family:-apple-system,'Apple SD Gothic Neo',sans-serif}.kiosk_simulation{height:100%}.kiosk_mega_backdround{width:100%;height:100%;margin:0;border-radius:0;background:#eee}.kiosc_mega_screen{width:100%;height:100%;margin:0;border-radius:0;position:relative}#top_bar{height:5vh;align-items:center}#mega_menu_bar{height:7vh;align-items:center;font-size:13px;gap:6px;padding:0 6px}#mega_menu_bar>div{cursor:pointer}#mega_menu_table{height:57vh}#row{height:19vh;padding:4px;width:100%;box-sizing:border-box}#row>div{height:100%;background-size:contain;background-color:#fff;cursor:pointer;box-sizing:border-box}#nextpage{height:3vh}#pay{height:28vh}#screen_bottom{display:none}#order_lsit{font-size:12px;overflow:auto}#rest_time{visibility:hidden}#item_number{font-size:12px}#total_price{cursor:pointer;width:100%;text-align:center;font-size:18px}#card_img{display:none}#mega_top_bar_home{height:28px}#window_pay,#w_카드결제{z-index:10}#screen_to_window_pay{z-index:9}`;
  doc.head.append(style);
  $('kiosk').contentWindow.start_btn();
  doc.addEventListener('keydown',keyHandler);
  doc.addEventListener('click',()=>setTimeout(invalidateRegions,60));
  new MutationObserver(()=>{clearTimeout(detectTimer);detectTimer=setTimeout(invalidateRegions,60);}).observe(doc.body,{subtree:true,attributes:true,childList:true,characterData:true});
  invalidateRegions();
};
function keyHandler(e){
  if(switchKeys(e))return;
  if(e.key==='Escape'){e.preventDefault();if(state.calibrating)cancelCalibration();else if(!$('result').hidden)$('result').hidden=true;else pause();}
  if(e.code==='Space'&&!['INPUT','SELECT','BUTTON'].includes(e.target.tagName)){e.preventDefault();selectTarget();}
}
document.addEventListener('keydown',keyHandler);
window.addEventListener('resize',()=>{resetGaze();clearTimeout(resizeTimer);resizeTimer=setTimeout(()=>{if(state.calibrating)cancelCalibration();configure();invalidateRegions();},250);});
setInterval(()=>{if(state.lastGaze&&performance.now()-state.lastGaze>350)resetGaze();},100);
$('kiosk').srcdoc=KIOSK_HTML;
controls();send('ui_ready');

$('switchMode').onclick=()=>{if(!switchBoard)openSwitchBoard();};
