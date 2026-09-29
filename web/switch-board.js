// Optional keyboard/HID-keyboard pathway. Does not claim CDC/Pico integration.
let switchBoard=null;
function openSwitchBoard(){
  if(state.view!=='kiosk'){toast('메가커피 연습 화면에서 사용할 수 있습니다.');return;}
  state.paused=true;resetGaze();controls();
  const items=visibleDomRegions().map(r=>({...r,label:r.element.getAttribute('aria-label')||r.label}));
  if(!items.length){toast('현재 화면에서 선택 가능한 버튼을 찾지 못했습니다.');return;}
  const panel=document.createElement('section');panel.id='switchBoard';panel.setAttribute('role','dialog');panel.setAttribute('aria-label','물리 버튼 선택');
  switchBoard={panel,items,index:0,confirm:false};document.body.append(panel);renderSwitchBoard();
}
function closeSwitchBoard(){if(!switchBoard)return;switchBoard.panel.remove();switchBoard=null;invalidateRegions();}
function renderSwitchBoard(){
  const b=switchBoard;if(!b)return;b.panel.replaceChildren();
  const title=document.createElement('h2');title.textContent=b.confirm?'이 항목을 실행할까요?':'버튼으로 선택';b.panel.append(title);
  const hint=document.createElement('p');hint.textContent='← → 이전·다음 / Enter 또는 Space 선택 / Esc 취소 · 고개 조작은 일시정지됩니다.';b.panel.append(hint);
  const grid=document.createElement('div');grid.className='switch-grid';b.panel.append(grid);
  const entries=b.confirm?[{label:'실행: '+b.items[b.index].label,action:()=>executeSwitchItem()},{label:'돌아가기',action:()=>{b.confirm=false;renderSwitchBoard();}}]:b.items.slice(Math.floor(b.index/4)*4,Math.floor(b.index/4)*4+4).map((r,j)=>({label:r.label,action:()=>{b.index=Math.floor(b.index/4)*4+j;b.confirm=true;renderSwitchBoard();}}));
  entries.forEach((entry,j)=>{const button=document.createElement('button');button.textContent=entry.label;button.onclick=entry.action;button.className=(b.confirm?j===0:j===b.index%4)?'switch-current':'';grid.append(button);});
  const footer=document.createElement('p');footer.textContent=`${b.index+1} / ${b.items.length}`;b.panel.append(footer);
  for(const [label,action] of [['이전',()=>moveSwitch(-1)],['다음',()=>moveSwitch(1)],['닫기 · Esc',closeSwitchBoard]]){const button=document.createElement('button');button.textContent=label;button.onclick=action;b.panel.append(button);}
}
function moveSwitch(delta){const b=switchBoard;if(!b)return;b.confirm=false;b.index=(b.index+delta+b.items.length)%b.items.length;renderSwitchBoard();}
function executeSwitchItem(){
  const b=switchBoard,r=b.items[b.index];
  // Recheck the original live control; never execute a stale/hidden menu from another screen.
  if(!visibleDomRegions().some(v=>v.element===r.element)){closeSwitchBoard();toast('화면이 바뀌었습니다. 선택 화면을 다시 열어 주세요.');return;}
  closeSwitchBoard();r.element.click();invalidateRegions();
}
function switchKeys(e){
  if(e.code==='F8'&&!e.repeat){e.preventDefault();if(!switchBoard)openSwitchBoard();return true;}
  if(!switchBoard)return false;
  if(['ArrowLeft','ArrowUp','ArrowRight','ArrowDown','Enter','Space','Escape'].includes(e.code)){
    e.preventDefault();e.stopPropagation();if(e.repeat)return true;
    if(e.code==='Escape'){if(switchBoard.confirm){switchBoard.confirm=false;renderSwitchBoard();}else closeSwitchBoard();}
    else if(['ArrowLeft','ArrowUp'].includes(e.code))moveSwitch(-1);
    else if(['ArrowRight','ArrowDown'].includes(e.code))moveSwitch(1);
    else if(switchBoard.confirm)executeSwitchItem();else{switchBoard.confirm=true;renderSwitchBoard();}
    return true;
  }
  return false;
}
