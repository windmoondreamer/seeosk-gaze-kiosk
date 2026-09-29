const assert=require('node:assert/strict');
const {TargetField,rectDistance}=require('../web/pointer.js');

// 가로로 40px 떨어진 두 버튼.
const pair=[{key:'a',x:0,y:0,w:200,h:100},{key:'b',x:240,y:0,w:200,h:100}];

assert.equal(rectDistance(100,50,pair[0]),0,'안쪽은 거리 0');
assert.equal(rectDistance(220,50,pair[0]),20,'오른쪽 바깥 거리');
assert.equal(rectDistance(-30,-40,pair[0]),50,'모서리 바깥은 대각 거리');

// 1. 보이는 버튼 안에 있으면 그 버튼.
let f=new TargetField();
assert.equal(f.pick(100,50,0,pair).key,'a');

// 2. 두 버튼 사이 빈 공간도 가까운 쪽이 잡힌다. 예전에는 아무것도 안 잡혔다.
f=new TargetField();
assert.equal(f.pick(210,50,0,pair).key,'a','왼쪽에 가까운 틈');
f=new TargetField();
assert.equal(f.pick(230,50,0,pair).key,'b','오른쪽에 가까운 틈');

// 3. 회귀 방지: 확장 영역이 선택을 가두면 안 된다.
//    b를 고른 상태라도 포인터가 a 안으로 들어오면 즉시 a여야 한다.
f=new TargetField({sticky:1000});
assert.equal(f.pick(100,50,0,pair,'b').key,'a','버튼 안에 들어오면 고정 보정을 이긴다');

// 4. 너무 멀면 아무것도 고르지 않는다.
f=new TargetField({reach:30});
assert.equal(f.pick(600,50,0,pair),null);

// 5. 고정 보정: 틈에서는 지금 고른 버튼이 유리하다.
f=new TargetField({reach:80,sticky:30,lead:0});
assert.equal(f.pick(232,50,0,pair,'a').key,'a','b가 조금 더 가까워도 유지');
f=new TargetField({reach:80,sticky:30,lead:0});
assert.equal(f.pick(232,50,0,pair).key,'b','고른 버튼이 없으면 가까운 쪽');

// 6. 향하는 방향 보정: 바깥에서 b 쪽으로 움직이면 b를 먼저 준다.
f=new TargetField({reach:120,sticky:0,lead:60});
let t=0;
for(let x=180;x<=215;x+=5)f.pick(x,150,t+=33,pair);   // 오른쪽·위로 접근
const toward=f.pick(218,140,t+=33,pair);
assert.equal(toward.key,'b','오른쪽으로 이동 중이면 오른쪽 버튼');

// 7. 확장을 끄면 예전처럼 엄격한 사각형 판정.
f=new TargetField({reach:0});
assert.equal(f.pick(210,50,0,pair),null);
assert.equal(f.pick(100,50,0,pair).key,'a');

// 8. 겹치면 작은 버튼이 이긴다(기존 규칙 유지).
const nested=[{key:'big',x:0,y:0,w:300,h:300},{key:'small',x:100,y:100,w:60,h:60}];
f=new TargetField();
assert.equal(f.pick(130,130,0,nested).key,'small');

console.log('target field: containment wins, gap fill, escape guard, reach, sticky, lead, off, nesting passed');
