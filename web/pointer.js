/* Median rejects isolated jumps. A target must persist before cursor magnetism engages. */
class GazePointer {
  constructor(){this.reset();}
  reset(){this.history=[];this.position=null;this.target=null;this.pending=null;this.since=0;this.last=0;}
  update(x,y,now,regions){
    if(this.last&&now-this.last>350)this.reset();
    const dt=this.last?Math.min(80,now-this.last):33;this.last=now;
    this.history.push([x,y]);if(this.history.length>5)this.history.shift();
    const median=i=>this.history.map(p=>p[i]).sort((a,b)=>a-b)[Math.floor(this.history.length/2)];
    const mx=median(0),my=median(1);
    if(!this.position)this.position=[mx,my];
    const distance=Math.hypot(mx-this.position[0],my-this.position[1]);
    const alpha=1-Math.exp(-dt/(distance>70?65:150));
    if(distance>6){this.position[0]+=alpha*(mx-this.position[0]);this.position[1]+=alpha*(my-this.position[1]);}
    const [sx,sy]=this.position;
    // Cursor always follows the filtered gaze; selection must never trap movement.
    const rawContains=r=>mx>=r.x&&mx<=r.x+r.w&&my>=r.y&&my<=r.y+r.h;
    this.target=regions.find(r=>r.key===this.target?.key)||null;
    if(this.target&&!rawContains(this.target))this.target=null;
    const candidate=regions.filter(r=>rawContains(r)).sort((a,b)=>a.w*a.h-b.w*b.h)[0]||null;
    if(candidate?.key!==this.pending?.key){this.pending=candidate;this.since=now;}
    if(now-this.since>=180)this.target=candidate;
    return {x:sx,y:sy,target:this.target};
  }
}
if(typeof module!=='undefined')module.exports={GazePointer};

// Selection progress is independent of cursor movement. Missing samples never add time.
class GazeSelection {
  constructor(){this.reset();}
  reset(){this.target=null;this.pending=null;this.since=0;this.last=0;this.seen=0;this.elapsed=0;}
  update(x,y,now,regions){
    const dt=this.last?Math.min(80,Math.max(0,now-this.last)):0;this.last=now;
    this.target=regions.find(r=>r.key===this.target?.key)||null;
    const hit=regions.filter(r=>x>=r.x&&x<=r.x+r.w&&y>=r.y&&y<=r.y+r.h).sort((a,b)=>a.w*a.h-b.w*b.h)[0]||null;
    if(hit?.key!==this.pending?.key){this.pending=hit;this.since=now;}
    if(hit&&hit.key!==this.target?.key&&now-this.since>=180){this.target=hit;this.elapsed=0;this.seen=now;}
    const inside=!!hit&&hit.key===this.target?.key;
    if(inside){this.elapsed+=dt;this.seen=now;}
    else if(now-this.seen>300){this.target=null;this.elapsed=0;}
    return {target:this.target,elapsed:this.elapsed,inside};
  }
}
if(typeof module!=='undefined')module.exports.GazeSelection=GazeSelection;
