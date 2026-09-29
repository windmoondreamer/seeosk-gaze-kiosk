(async()=>{
  const results=[];
  const wait=ms=>new Promise(r=>setTimeout(r,ms));
  const until=async(fn,timeout=20000)=>{const t=Date.now();while(!fn()){if(Date.now()-t>timeout)throw Error('Condition timed out: '+fn.toString());await wait(100);}};
  const check=(name,condition)=>{results.push({name,passed:!!condition});if(!condition)throw Error(name);};
  const realSend=send;
  try {
    await until(()=>state.ready&&state.regions.length>5,45000);
    check('local kiosk iframe loads with interactive regions',state.regions.length>5);
    let cursorResult;
    const originalReceive=window.receive;
    window.receive=m=>{if(m.type==='cursor_test_result')cursorResult=m;else originalReceive(m);};
    realSend('cursor_test',{x:420,y:310});
    await until(()=>cursorResult);
    check('actual macOS cursor reaches web coordinate (error '+cursorResult.error_px+'px)',cursorResult.passed);
    window.receive=originalReceive;
    check('800 supplied images available',SAMPLE_FILES.length===800);
    const doc=frameDocument();
    check('kiosk opens directly into menu',doc.getElementById('mega_order').style.display==='block');
    const item=doc.querySelector('[onclick^="option("]');item.click();
    await wait(350);
    check('kiosk menu click adds an order',$('kiosk').contentWindow.order_list.length===1);
    doc.getElementById('plus_1').click();
    check('cart plus increases quantity',$('kiosk').contentWindow.total_list[0]===2);
    doc.getElementById('minus_1').click();
    check('cart minus decreases quantity',$('kiosk').contentWindow.total_list[0]===1);
    doc.getElementById('delete_1').click();
    check('cart X removes item and hides stale row',$('kiosk').contentWindow.order_list.length===0&&doc.getElementById('order_1').style.display==='none'&&$('kiosk').contentWindow.total_list[1]===0);
    doc.querySelector('[id="커피프라페"]').click();
    check('coffee frappe handler adds the correct product',$('kiosk').contentWindow.order_list[0].name==='커피프라페');
    doc.getElementById('delete_1').click();item.click();await wait(600);
    const press=code=>keyHandler(new KeyboardEvent('keydown',{code,key:code==='Space'?' ':code,cancelable:true}));
    press('F8');check('physical-key pathway opens large cards without camera',!!switchBoard&&!state.camera);
    const frappeIndex=switchBoard.items.findIndex(r=>r.element.id==='커피프라페');
    check('frappe is reachable in button pathway',frappeIndex>=0);
    for(let i=0;i<frappeIndex;i++)press('ArrowRight');
    press('Enter');check('button selection requires confirmation',switchBoard.confirm);
    press('Enter');check('physical-key pathway orders frappe',$('kiosk').contentWindow.order_list.some(r=>r.name==='커피프라페'));
    $('kiosk').contentWindow.order_list=[];$('kiosk').contentWindow.open_order_list([]);item.click();await wait(600);
    switchView('sample');
    await until(()=>state.regions.length>0&&state.regions[0].key.startsWith('yolo'));
    check('real YOLO weights detect supplied image',state.regions.length>=5);
    check('YOLO boxes respect image viewport',state.regions.every(r=>r.w>0&&r.h>0&&r.x>=0&&r.y>=0));
    const sampleBoxes=state.regions.length;
    switchView('kiosk');await wait(600);
    $('regionMode').value='yolo';invalidateRegions();
    await until(()=>$('regionCount').textContent.includes('YOLO'),30000);
    check('real model also processes native kiosk snapshot',state.regions.every(r=>r.w>0&&r.h>0));
    const kioskBoxes=state.regions.length;
    $('regionMode').value='dom';invalidateRegions();await wait(500);
    // Head mode starts without gaze calibration or operating-system setup.
    $('clickMode').value='dwell';$('clickMode').onchange();
    $('clickMode').value='mouth';$('clickMode').onchange();
    const sent=[];send=(cmd,args={})=>sent.push({cmd,...args});
    receive({type:'started'});recenter();
    check('recenter requests internal head control',sent.some(m=>m.cmd==='recenter'));
    check('no macOS head-pointer settings control',!$('macHeadPointer')&&!$('headPointerGuide'));
    receive({type:'head_ready'});
    check('head tracking becomes usable without gaze profile',state.headReady);
    state.regions=visibleDomRegions();renderRegions();
    const target=state.regions.find(r=>r.element?.getAttribute('onclick')?.startsWith('option('));
    const trackEvent={type:'tracking',valid:true,x:target.x+target.w/2,y:target.y+target.h/2,reason:'테스트 입력',fps:30};
    for(let i=0;i<12;i++){receive(trackEvent);await wait(33);}
    check('tracking dispatches native cursor movement',sent.some(m=>m.cmd==='move_cursor'&&Math.abs(m.x-trackEvent.x)<2));
    check('gaze maps into kiosk target',state.active?.key===target.key&&!$('gaze').hidden);
    const beforeDwell=$('kiosk').contentWindow.total_list[0];
    $('clickMode').value='dwell';
    for(let i=0;i<42;i++){receive(i%8===7?{type:'tracking',valid:false,reason:'짧은 깜빡임'}:trackEvent);await wait(33);}
    await wait(500);
    const afterDwell=$('kiosk').contentWindow.total_list[0];
    check('dwell with brief face loss adds exactly one order',afterDwell===beforeDwell+1);
    receive(trackEvent);state.dwellStart=performance.now()-1300;receive(trackEvent);await wait(200);
    check('remaining gaze does not repeatedly order',$('kiosk').contentWindow.total_list[0]===afterDwell&&!!state.blockedRect);
    state.blockedRect=null;state.firedKey=null;
    $('clickMode').value='mouth';
    receive(trackEvent);await wait(50);
    const beforeMouth=$('kiosk').contentWindow.total_list[0];
    receive({type:'gesture_click',gesture:'jawopen',x:trackEvent.x,y:trackEvent.y});
    await wait(250);
    check('mouth open selects the pointed menu',$('kiosk').contentWindow.total_list[0]===beforeMouth+1);
    state.regions=visibleDomRegions();renderRegions();
    receive(trackEvent);await wait(50);
    const beforePaused=$('kiosk').contentWindow.total_list[0];
    pause();receive({type:'gesture_click',gesture:'jawopen'});await wait(150);
    check('mouth click is ignored while paused',$('kiosk').contentWindow.total_list[0]===beforePaused);
    pause();receive(trackEvent);await wait(50);
    receive({type:'tracking',valid:true,x:trackEvent.x,y:trackEvent.y,reason:'입벌림',fps:30,mouth:0.62,mouth_state:'조준'});
    check('mouth meter reflects the engine value',$('mouthFill').style.width==='62%');
    $('sensitivity').value='40';$('sensitivity').oninput();
    check('sensitivity slider reports a 0-1 value',sent.some(m=>m.cmd==='sensitivity'&&Math.abs(m.value-0.4)<1e-6));
    receive({type:'sensitivity',value:0.25});
    check('engine sensitivity updates the slider',$('sensitivity').value==='25'&&$('sensitivityValue').textContent==='25%');
    // 화면 전체 모드: 고개 범위를 디스플레이에 맞추고 입벌림을 실제 클릭으로 내보냅니다.
    $('pointerScope').value='screen';$('pointerScope').onchange();
    check('screen scope asks the app for system-wide pointing',
      sent.some(m=>m.cmd==='pointer_scope'&&m.scope==='screen'));
    check('screen scope maps head movement onto the display',
      sent.some(m=>m.cmd==='configure'&&m.geometry.width===screen.width&&m.geometry.height===screen.height));
    state.regions=visibleDomRegions();renderRegions();
    receive(trackEvent);await wait(50);
    check('screen scope hides the in-app pointer dot',$('gaze').hidden);
    const beforeScope=$('kiosk').contentWindow.total_list[0];
    receive({type:'gesture_click',gesture:'jawopen'});await wait(150);
    check('screen scope sends a real system click instead of an in-app one',
      sent.some(m=>m.cmd==='system_click')&&$('kiosk').contentWindow.total_list[0]===beforeScope);
    receive({type:'accessibility_status',trusted:false});
    check('missing accessibility permission surfaces a settings shortcut',!$('accessibilitySettings').hidden);
    receive({type:'accessibility_status',trusted:true});
    check('granted accessibility permission hides the shortcut',$('accessibilitySettings').hidden);
    $('pointerScope').value='app';$('pointerScope').onchange();
    check('app scope restores window-sized head mapping',
      sent.filter(m=>m.cmd==='configure').pop().geometry.width===innerWidth);
    state.regions=visibleDomRegions();renderRegions();
    // 버튼 인식범위: 보이는 사각형 밖이어도 겨냥한 버튼이 잡혀야 합니다.
    const wide=state.regions.find(r=>r.element?.getAttribute('onclick')?.startsWith('option('));
    // 어느 버튼에도 속하지 않으면서 wide 가 가장 가까운 지점을 찾습니다.
    const outsideAll=(px,py)=>state.regions.every(r=>rectDistance(px,py,r)>0);
    const nearestIs=(px,py,key)=>state.regions.slice().sort(
      (a,b)=>rectDistance(px,py,a)-rectDistance(px,py,b))[0]?.key===key;
    let justOutside=null;
    for(const [dx,dy] of [[0,1],[0,-1],[-1,0],[1,0],[-1,-1],[1,-1],[-1,1],[1,1]]){
      for(const gap of [10,16,24,34]){
        const px=wide.x+wide.w/2+dx*(wide.w/2+gap), py=wide.y+wide.h/2+dy*(wide.h/2+gap);
        if(outsideAll(px,py)&&nearestIs(px,py,wide.key)){justOutside={x:px,y:py};break;}
      }
      if(justOutside)break;
    }
    check('a near-miss point outside every button exists to test with',!!justOutside);
    $('hitReach').value='0';$('hitReach').oninput();
    check('reach off keeps the strict rectangle',
      targetField.pick(justOutside.x,justOutside.y,performance.now(),state.regions)===null);
    $('hitReach').value='64';$('hitReach').oninput();
    check('reach picks up a near miss just outside the button',
      targetField.pick(justOutside.x,justOutside.y,performance.now(),state.regions)?.key===wide.key);
    check('reach label follows the slider',$('hitReachValue').textContent==='보통');
    // 회귀 방지: 넓힌 영역이 선택을 가두면 안 됩니다.
    const other=state.regions.find(r=>r.key!==wide.key&&r.element);
    check('a pointer inside another button still wins over the held one',
      targetField.pick(other.x+other.w/2,other.y+other.h/2,performance.now(),state.regions,wide.key)?.key===other.key);
    $('hitReach').value='64';$('hitReach').oninput();
    // 미리보기 배경 흐리기: 기본으로 켜져 있고 토글이 엔진까지 전달되어야 합니다.
    check('preview blur is on by default',$('blurPreview').checked);
    $('blurPreview').checked=false;$('blurPreview').onchange();
    check('turning blur off reaches the engine',
      sent.some(m=>m.cmd==='blur_preview'&&m.value===false));
    receive({type:'blur_preview',value:true});
    check('the engine can restore the blur checkbox',$('blurPreview').checked);
    $('clickMode').value='mouth';
    receive({type:'tracking',valid:false,reason:'얼굴 미검출'});
    await wait(450);
    check('face loss clears target and cursor',state.active===null&&$('gaze').hidden);
    receive(trackEvent);pause();
    check('pause clears gaze',state.paused&&$('gaze').hidden);pause();
    recenter();check('recenter suspends selection until neutral is captured',!state.headReady);
    receive({type:'head_ready'});
    receive({type:'window_changed'});
    check('window changes do not require gaze calibration',state.headReady);
    receive({type:'stopped'});send=realSend;
    $('trackingHint').textContent='카메라를 켜고 고개로 메뉴를 선택하세요.';
    $('toast').hidden=true;switchView('kiosk');await wait(400);
    realSend('smoke_result',{passed:true,results,sample_boxes:sampleBoxes,kiosk_boxes:kioskBoxes});
  }catch(error){send=realSend;realSend('smoke_result',{passed:false,results,error:String(error),stack:error.stack,ready:state.ready,regions:state.regions.length,frameAccessible:!!$('kiosk').contentDocument});}
})();
