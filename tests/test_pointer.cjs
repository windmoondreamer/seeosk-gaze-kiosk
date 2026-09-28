const assert=require('node:assert/strict');
const {GazePointer}=require('../web/pointer.js');
const p=new GazePointer();
const regions=[{key:'a',x:0,y:0,w:200,h:200},{key:'b',x:200,y:0,w:200,h:200}];
let r;
for(let i=0;i<60;i++) r=p.update(100+(i%2?25:-25),100,i*33,regions);
assert.equal(r.target.key,'a');assert.ok(Math.abs(r.x-100)<30);
r=p.update(900,700,2000,regions);assert.equal(r.target.key,'a');
for(let i=1;i<30;i++)r=p.update(300,100,2000+i*33,regions);
assert.equal(r.target.key,'b');assert.ok(Math.abs(r.x-300)<10);
p.reset();r=p.update(100,100,4000,regions);assert.equal(r.target,null);
console.log('pointer: jitter, spike rejection, intentional target change, reset passed');
const vertical=[{key:'top',x:0,y:0,w:200,h:200},{key:'bottom',x:0,y:200,w:200,h:200}];
p.reset();let t=5000;
for(let i=0;i<30;i++)r=p.update(100,300,t+=33,vertical);
for(let i=0;i<4;i++)r=p.update(100,190,t+=33,vertical);
assert.notEqual(r.target?.key,'bottom','must release bottom within four samples of upward gaze');
assert.ok(r.y<280,'cursor must move upward without waiting for new menu lock');
for(let cycle=0;cycle<5;cycle++)for(const y of [90,310]){
 for(let i=0;i<25;i++)r=p.update(100,y,t+=33,vertical);
 assert.equal(r.target?.key,y<200?'top':'bottom');
 assert.ok(Math.abs(r.y-y)<15);
}
console.log('vertical release and repeated up/down passed');
