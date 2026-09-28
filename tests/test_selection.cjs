const assert=require('node:assert/strict');
const {GazeSelection}=require('../web/pointer.js');
const regions=[{key:'a',x:0,y:0,w:100,h:100},{key:'b',x:0,y:100,w:100,h:100}];
const s=new GazeSelection();let r,t=1000;
for(let i=0;i<55;i++){r=s.update(50,i%8===7?105:50,t+=33,regions);}
assert.equal(r.target.key,'a');assert.ok(r.elapsed>=900,'brief edge excursions must not prevent selection');
const elapsed=r.elapsed;
for(let i=0;i<5;i++)r=s.update(NaN,NaN,t+=33,regions);
assert.equal(r.elapsed,elapsed,'missing face must not advance dwell');
for(let i=0;i<10;i++)r=s.update(50,150,t+=33,regions);
assert.equal(r.target.key,'b');assert.ok(r.elapsed<400,'new menu starts fresh');
for(let i=0;i<12;i++)r=s.update(NaN,NaN,t+=33,regions);
assert.equal(r.target,null);
console.log('selection: jitter, missing samples, deliberate switch, timeout passed');
