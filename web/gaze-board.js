'use strict';
const gazeBoard={open:false,items:[],page:0,zone:null,zoneSince:0,candidateZone:null,candidateSince:0,fired:false,lastAction:0};
const GAZE_BOARD_DWELL=1050, GAZE_BOARD_STABLE=180;
function openGazeBoard(){
  if(!state.camera||!state.calibrated){toast('카메라를 켜고 캘리브레이션을 먼저 해 주세요.');return;}
  if(!state.gazeZones){toast('시선 방향이 충분히 구분되지 않았습니다. 카메라를 눈높이에 두고 다시 캘리브레이션해 주세요.');return;}
  if(state.view!=='kiosk'){toast('키오스크 화면에서 사용할 수 있습니다.');return;}
  gazeBoard.items=visibleDomRegions().filter(r=>r.element&&r.element.isConnected);
  if(!gazeBoard.items.length){toast('화면에서 선택할 수 있는 메뉴를 찾지 못했습니다.');return;}
  gazeBoard.page=0;gazeBoard.open=true;gazeBoard.zone=null;gazeBoard.candidateZone=null;gazeBoard.fired=false;
  $('gazeBoard').hidden=false;$('gaze').hidden=true;renderGazeBoard();
  $('gazeMenuMode').textContent='큰 메뉴 선택 닫기';
}
function closeGazeBoard(){
  gazeBoard.open=false;gazeBoard.zone=null;gazeBoard.candidateZone=null;gazeBoard.fired=false;
  $('gazeBoard').hidden=true;$('gazeMenuMode').textContent='큰 메뉴 시선 선택';
  $('selection').textContent='바라보는 메뉴가 여기에 표시됩니다.';
}
function renderGazeBoard(){
  const root=$('gazeBoard');root.replaceChildren();
  const pages=Math.ceil(gazeBoard.items.length/7),start=gazeBoard.page*7;
  const slots=Array(9).fill(null);
  slots[0]={nav:-1,label:'이전 메뉴'};slots[8]={nav:1,label:'다음 메뉴'};
  let idx=start;
  for(let z=1;z<8&&idx<gazeBoard.items.length;z++){slots[z]={item:gazeBoard.items[idx],itemIndex:idx,label:gazeBoard.items[idx].label};idx++;}
  root.style.setProperty('--board-page',`${gazeBoard.page+1} / ${pages}`);
  slots.forEach((slot,z)=>{
    if(!slot){const empty=document.createElement('div');empty.className='gaze-tile empty';empty.dataset.zone=String(z);empty.dataset.empty='true';root.append(empty);return;}
    const tile=document.createElement('div');tile.className='gaze-tile';tile.dataset.zone=String(z);tile.textContent=slot.label;
    if(slot.nav){tile.classList.add('gaze-nav');tile.dataset.nav=String(slot.nav);tile.classList.toggle('disabled',slot.nav<0?gazeBoard.page===0:gazeBoard.page>=pages-1);}
    if(slot.itemIndex!==undefined)tile.dataset.itemIndex=String(slot.itemIndex);
    root.append(tile);
  });
  const title=document.createElement('div');title.className='gaze-board-title';title.innerHTML='<strong>큰 메뉴 선택</strong><span>메뉴를 바라보고 1초 기다리세요 · Esc 닫기</span><b></b>';root.append(title);
  const progress=document.createElement('div');progress.className='gaze-board-progress';progress.innerHTML='<i></i>';root.append(progress);
}
function updateGazeBoard(zone,now){
  if(!gazeBoard.open)return;
  if(zone==null||zone<0||zone>8){gazeBoard.zone=null;gazeBoard.candidateZone=null;gazeBoard.fired=false;$('gazeBoard').querySelectorAll('.gaze-tile.active').forEach(e=>e.classList.remove('active'));$('gazeBoard').querySelector('.gaze-board-progress i').style.width='0%';return;}
  const root=$('gazeBoard'),tile=root.querySelector(`.gaze-tile[data-zone="${zone}"]`);
  if(!tile){gazeBoard.zone=null;return;}
  if(gazeBoard.zone!==zone){
    if(gazeBoard.candidateZone!==zone){gazeBoard.candidateZone=zone;gazeBoard.candidateSince=now;return;}
    if(now-gazeBoard.candidateSince<GAZE_BOARD_STABLE)return;
    gazeBoard.zone=zone;gazeBoard.candidateZone=null;gazeBoard.zoneSince=now;gazeBoard.fired=false;
    root.querySelectorAll('.gaze-tile.active').forEach(e=>e.classList.remove('active'));tile.classList.add('active');
  }else gazeBoard.candidateZone=null;
  const elapsed=now-gazeBoard.zoneSince,progress=root.querySelector('.gaze-board-progress i');
  progress.style.width=`${Math.max(0,Math.min(100,elapsed/GAZE_BOARD_DWELL*100))}%`;
  if(gazeBoard.fired||elapsed<GAZE_BOARD_DWELL||now-gazeBoard.lastAction<250)return;
  gazeBoard.fired=true;gazeBoard.lastAction=now;
  if(tile.dataset.empty)return;
  if(tile.dataset.nav){const next=gazeBoard.page+Number(tile.dataset.nav);if(next>=0&&next<Math.ceil(gazeBoard.items.length/7))gazeBoard.page=next;gazeBoard.zone=null;gazeBoard.candidateZone=null;renderGazeBoard();return;}
  const item=gazeBoard.items[Number(tile.dataset.itemIndex)];
  if(!item?.element?.isConnected||!visibleDomRegions().some(r=>r.element===item.element)){toast('메뉴 화면이 바뀌었습니다. 다시 선택해 주세요.');gazeBoard.items=visibleDomRegions().filter(r=>r.element);gazeBoard.page=0;renderGazeBoard();return;}
  closeGazeBoard();item.element.click();invalidateRegions();
}
function gazeBoardKey(e){
  if(!gazeBoard.open)return false;
  if(e.key==='Escape'){e.preventDefault();closeGazeBoard();return true;}
  if(e.key==='ArrowLeft'||e.key==='ArrowRight'||e.key==='ArrowUp'||e.key==='ArrowDown'){
    e.preventDefault();const delta=e.key==='ArrowLeft'?-1:e.key==='ArrowRight'?1:e.key==='ArrowUp'?-3:3;
    const next=((gazeBoard.zone??4)+delta+9)%9,now=performance.now();gazeBoard.zone=next;gazeBoard.candidateZone=null;gazeBoard.zoneSince=now-GAZE_BOARD_DWELL;gazeBoard.fired=false;
    const tile=$('gazeBoard').querySelector(`.gaze-tile[data-zone="${next}"]`);$('gazeBoard').querySelectorAll('.gaze-tile.active').forEach(e=>e.classList.remove('active'));tile?.classList.add('active');return true;
  }
  if(e.key==='Enter'||e.code==='Space'){e.preventDefault();if(gazeBoard.zone!=null){gazeBoard.zoneSince=performance.now()-GAZE_BOARD_DWELL;updateGazeBoard(gazeBoard.zone,performance.now());}return true;}
  return true;
}
