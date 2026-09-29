'use strict';
const $ = id => document.getElementById(id);
const state = {ready:false,camera:false,headReady:false,paused:false,view:'kiosk',
  regions:[],active:null,lastGaze:0,sourceRequest:0,
  headValid:false,sampleIndex:0,dwellStart:0,dwellKey:null,firedKey:null,blockedRect:null,awaySince:0};
const pointer=new GazePointer();
const selection=new GazeSelection();
let detectTimer, resizeTimer, toastTimer, snapshotRect;
function send(cmd, args={}) {
  window.webkit?.messageHandlers?.seeosk?.postMessage({cmd,...args});
}
function toast(message) { $('toast').textContent=message; $('toast').hidden=false; clearTimeout(toastTimer); toastTimer=setTimeout(()=>$('toast').hidden=true,5500); }
function badge(text,live=false){$('statusBadge').textContent=text;$('statusBadge').classList.toggle('live',live);}
function screenScope(){return $('pointerScope').value==='screen';}
// 화면 전체 모드에서는 고개 범위를 디스플레이 전체에 대응시킵니다.
function geometry(){return screenScope()?{width:screen.width,height:screen.height}:{width:innerWidth,height:innerHeight};}
function configure(){send('configure',{geometry:geometry()});}
function resetGaze(){
  pointer.reset();selection.reset();
  $('gaze').hidden=true; state.active=null;state.lastGaze=0;state.dwellStart=0;state.dwellKey=null;state.firedKey=null;
  document.querySelectorAll('.region.active').forEach(e=>e.classList.remove('active'));
  $('selection').textContent='선택할 메뉴 위에 포인터를 멈춰 주세요.';
}
function controls(){
  $('cameraToggle').textContent=state.camera?'카메라 끄기':'카메라 켜기';
  $('camera').disabled=state.camera;
  $('pause').disabled=!state.camera;
  $('pause').textContent=state.paused?'추적 다시 시작':'일시정지';
  $('recenter').disabled=!state.ready;
  $('cameraToggle').disabled=!state.ready;
  if(state.paused)badge('일시정지');
  else if(state.camera&&state.headReady)badge('고개 조작 중',true);
  else if(state.camera)badge('중앙 맞추는 중');
  else badge(state.ready?'카메라 대기':'엔진 연결 중');
}
function pause(){if(!state.camera)return;state.paused=!state.paused;resetGaze();controls();}
function recenter(){
  if(!state.ready)return;
  state.headReady=false;state.paused=false;resetGaze();
  if(!state.camera)startCamera();else send('recenter');
  controls();
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
  if(!state.active||!state.headValid||state.paused||performance.now()-state.lastGaze>300)return;
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
  $('fps').textContent=`${m.fps||'—'} FPS`;$('faceBadge').textContent=m.valid?'얼굴 감지됨':'위치 확인 필요';
  $('trackingHint').textContent=m.reason;state.headValid=m.valid;
  if(m.mouth!==undefined){
    $('mouthFill').style.width=`${Math.min(100,Math.round(m.mouth*100))}%`;
    $('mouthState').textContent=`입벌림 ${m.mouth_state||''}`.trim();
  }
  if(!m.valid&&state.headReady&&!state.paused){
    selection.update(NaN,NaN,performance.now(),state.regions);
    $('gaze').hidden=true;return;
  }
  if(!m.valid||!state.headReady||state.paused||m.x===undefined){resetGaze();return;}
  const now=performance.now();state.lastGaze=now;
  const stabilized=pointer.update(m.x,m.y,now,state.regions);
  send('move_cursor',{x:stabilized.x,y:stabilized.y});
  $('gaze').hidden=false;$('gaze').style.left=`${stabilized.x}px`;$('gaze').style.top=`${stabilized.y}px`;
  // Use the same stabilized coordinates for cursor and menu hit testing.
  // Previously raw frame coordinates drove selection, so the cursor could
  // visibly sit on a button while the hit test flickered outside it.
  if(screenScope()){
    // 실제 시스템 커서가 포인터이므로 앱 안의 파란 점은 감춥니다.
    $('gaze').hidden=true;
    $('selection').textContent='화면 전체 조작 중 · 입을 벌리면 클릭';
    return;
  }
  const choice=selection.update(stabilized.x,stabilized.y,now,state.regions);
  const active=choice.target;
  if(state.active?.key!==active?.key){
    state.active?.node?.classList.remove('active');active?.node?.classList.add('active');
    state.dwellStart=now;state.dwellKey=active?.key;state.firedKey=null;
  }
  state.active=active;
  $('selection').textContent=active?active.label:'고개를 움직여 메뉴를 가리켜 주세요.';
  if(state.blockedRect){
    const b=state.blockedRect;
    const inside=m.x>=b.x&&m.x<=b.x+b.w&&m.y>=b.y&&m.y<=b.y+b.h;
    if(inside)state.awaySince=0;
    else if(!state.awaySince)state.awaySince=now;
    else if(now-state.awaySince>=300){state.blockedRect=null;state.dwellStart=now;}
  }
  if(active&&$('clickMode').value==='dwell'&&state.firedKey!==active.key&&!state.blockedRect){
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
    case 'ready':state.ready=true;configure();send('sensitivity',{value:Number($('sensitivity').value)/100});controls();if(state.view==='sample'||$('regionMode').value==='yolo')updateRegions();break;
    case 'cameras':{
      $('camera').replaceChildren();
      for(const d of m.devices){const o=new Option(d.name,String(d.index));$('camera').add(o);}
      if(!m.devices.length)$('camera').add(new Option('카메라 없음','none'));
      const preferred=m.devices.find(d=>d.builtin)||m.devices.find(d=>!d.phone)||m.devices[0];
      if(preferred)$('camera').value=String(preferred.index);break;
    }
    case 'started':state.camera=true;state.paused=false;controls();$('permissions').hidden=true;
      break;
    case 'stopped':state.camera=false;state.headReady=false;resetGaze();$('calBadge').textContent='자동 중앙 맞춤';
      $('preview').hidden=true;$('preview').removeAttribute('src');$('previewPlaceholder').hidden=false;$('faceBadge').textContent='카메라 꺼짐';$('fps').textContent='— FPS';controls();break;
    case 'head_ready':state.headReady=true;$('calBadge').textContent='준비 완료';controls();break;
    case 'centering':state.headReady=false;resetGaze();controls();break;
    case 'tracking':track(m);break;
    // 입벌림 클릭은 dwell과 같은 반복 잠금을 쓰지 않습니다. 입을 다물었다 다시 벌려야 다음 선택이 됩니다.
    case 'gesture_click':
      if($('clickMode').value!=='mouth'||state.paused||!state.headReady)break;
      if(screenScope())send('system_click');else selectTarget();break;
    case 'pointer_scope':
      $('accessibilitySettings').hidden=m.scope!=='screen'||m.accessibility;break;
    case 'accessibility_status':
      $('accessibilitySettings').hidden=!screenScope()||m.trusted;break;
    case 'accessibility_needed':
      $('accessibilitySettings').hidden=false;toast(m.message);break;
    case 'sensitivity':
      $('sensitivity').value=String(Math.round(m.value*100));
      $('sensitivityValue').textContent=`${Math.round(m.value*100)}%`;break;
    case 'window_changed':
      configure();invalidateRegions();break;
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
    case 'permission_denied':$('permissions').hidden=false;toast(m.message);break;
    case 'engine_exit':state.ready=false;state.camera=false;state.headReady=false;resetGaze();controls();toast('추적 엔진이 종료되었습니다. 앱을 다시 실행해 주세요.');break;
    case 'error':toast(m.message);$('trackingHint').textContent=m.message;break;
  }
};
$('cameraToggle').onclick=()=>state.camera?send('stop'):startCamera();
$('recenter').onclick=recenter;$('pause').onclick=pause;
$('permissions').onclick=()=>send('permissions');$('fullscreen').onclick=()=>send('fullscreen');
$('kioskTab').onclick=()=>switchView('kiosk');$('sampleTab').onclick=()=>switchView('sample');
$('resetKiosk').onclick=()=>{$('kiosk').srcdoc=KIOSK_HTML;invalidateRegions();};
$('regionMode').onchange=invalidateRegions;$('showRegions').onchange=()=>{$('regionLayer').hidden=!$('showRegions').checked;};
$('pointerScope').onchange=()=>{
  const wide=screenScope();
  $('scopeHint').textContent=wide
    ?'화면 전체에서 포인터가 움직이고 입벌림이 실제 클릭으로 나갑니다. 손쉬운 사용 권한이 필요합니다.'
    :'키오스크 화면 안에서만 포인터가 움직입니다.';
  send('pointer_scope',{scope:wide?'screen':'app'});
  resetGaze();configure();invalidateRegions();
};
$('accessibilitySettings').onclick=()=>send('accessibility_settings');
$('clickMode').onchange=()=>{
  selection.reset();state.dwellStart=performance.now();state.firedKey=null;
  $('mouthMeter').hidden=$('clickMode').value!=='mouth';
  $('mouthState').hidden=$('clickMode').value!=='mouth';
};
$('sensitivity').oninput=()=>{
  const percent=Number($('sensitivity').value);
  $('sensitivityValue').textContent=`${percent}%`;
  send('sensitivity',{value:percent/100});
};
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
  if(e.key==='Escape'){e.preventDefault();pause();}
  if(e.code==='Space'&&!['INPUT','SELECT','BUTTON'].includes(e.target.tagName)){e.preventDefault();selectTarget();}
}
document.addEventListener('keydown',keyHandler);
window.addEventListener('resize',()=>{resetGaze();clearTimeout(resizeTimer);resizeTimer=setTimeout(()=>{configure();invalidateRegions();},250);});
setInterval(()=>{if(state.lastGaze&&performance.now()-state.lastGaze>350)resetGaze();},100);
$('kiosk').srcdoc=KIOSK_HTML;
controls();
$('mouthMeter').hidden=$('clickMode').value!=='mouth';
$('mouthState').hidden=$('clickMode').value!=='mouth';
send('ui_ready');

$('switchMode').onclick=()=>{if(!switchBoard)openSwitchBoard();};
