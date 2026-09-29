/* 버튼의 인식 범위를 보이는 크기보다 넓힌다.

   아이폰 키보드가 쓰는 동적 키 영역과 같은 발상이다. 보이는 키보다 누를 수 있는 영역을 크게
   두고, 지금 누르려는 것으로 보이는 키에 더 넓게 준다.

   중요한 제약이 하나 있다. 버튼 안에 실제로 들어온 포인터는 언제나 그 버튼이 이긴다. 넓힌
   영역과 고정 보정은 버튼 바깥에 있을 때만 쓴다. 예전에 확장 영역이 선택을 붙잡아 아래
   메뉴에서 위 메뉴로 빠져나오지 못하던 문제가 있었고, 이 규칙이 그것을 막는다. */
function rectDistance(x, y, r) {
  const dx = Math.max(r.x-x, 0, x-(r.x+r.w));
  const dy = Math.max(r.y-y, 0, y-(r.y+r.h));
  return Math.hypot(dx, dy);
}

class TargetField {
  // reach: 버튼 밖에서 인식하는 최대 거리(px). sticky: 지금 고른 버튼에 주는 여유.
  // lead: 포인터가 향하는 쪽 버튼에 주는 여유.
  constructor({reach=64, sticky=26, lead=20, minSpeed=0.05}={}) {
    Object.assign(this, {reach, sticky, lead, minSpeed});
    this.reset();
  }

  reset(){this.last=null;this.velocity=[0,0];}

  measure(x, y, now) {
    if(this.last){
      const dt=Math.min(120, Math.max(1, now-this.last[2]));
      const decay=Math.exp(-dt/120);
      this.velocity=[this.velocity[0]*decay+(x-this.last[0])/dt*(1-decay),
                     this.velocity[1]*decay+(y-this.last[1])/dt*(1-decay)];
    }
    this.last=[x,y,now];
  }

  // 포인터가 향하는 방향과 버튼 방향이 얼마나 일치하는지. 0~1.
  approach(x, y, r) {
    const speed=Math.hypot(this.velocity[0], this.velocity[1]);
    if(speed<this.minSpeed)return 0;
    const dx=r.x+r.w/2-x, dy=r.y+r.h/2-y;
    const span=Math.hypot(dx, dy);
    if(span<1)return 0;
    return Math.max(0, (this.velocity[0]*dx+this.velocity[1]*dy)/(speed*span));
  }

  smallest(list){return list.slice().sort((a,b)=>a.w*a.h-b.w*b.h)[0]||null;}

  // 반환: 이 좌표가 겨냥하는 버튼. 없으면 null.
  pick(x, y, now, regions, currentKey=null) {
    this.measure(x, y, now);
    if(!regions||!regions.length)return null;
    const inside=regions.filter(r=>rectDistance(x,y,r)===0);
    if(inside.length)return this.smallest(inside);   // 안에 들어왔으면 무조건 그 버튼
    if(this.reach<=0)return null;
    let best=null, bestScore=Infinity;
    for(const r of regions){
      const distance=rectDistance(x,y,r);
      const bonus=(r.key===currentKey?this.sticky:0)+this.lead*this.approach(x,y,r);
      if(distance>this.reach+bonus)continue;
      const score=distance-bonus;
      if(score<bestScore||(score===bestScore&&best&&r.w*r.h<best.w*best.h)){
        best=r;bestScore=score;
      }
    }
    return best;
  }
}
if(typeof module!=='undefined')module.exports={TargetField, rectDistance};

/* Median rejects isolated jumps. A target must persist before cursor magnetism engages. */
class GazePointer {
  constructor(field){this.field=field||new TargetField();this.reset();}
  reset(){this.history=[];this.position=null;this.target=null;this.pending=null;this.since=0;this.last=0;this.field.reset();}
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
    this.target=regions.find(r=>r.key===this.target?.key)||null;
    const candidate=this.field.pick(mx,my,now,regions,this.target?.key||null);
    if(this.target&&candidate?.key!==this.target.key)this.target=null;
    if(candidate?.key!==this.pending?.key){this.pending=candidate;this.since=now;}
    if(now-this.since>=180)this.target=candidate;
    return {x:sx,y:sy,target:this.target};
  }
}
if(typeof module!=='undefined')module.exports.GazePointer=GazePointer;

// Selection progress is independent of cursor movement. Missing samples never add time.
class GazeSelection {
  constructor(field){this.field=field||new TargetField();this.reset();}
  reset(){this.target=null;this.pending=null;this.since=0;this.last=0;this.seen=0;this.elapsed=0;this.field.reset();}
  update(x,y,now,regions){
    const dt=this.last?Math.min(80,Math.max(0,now-this.last)):0;this.last=now;
    this.target=regions.find(r=>r.key===this.target?.key)||null;
    const hit=this.field.pick(x,y,now,regions,this.target?.key||null);
    if(hit?.key!==this.pending?.key){this.pending=hit;this.since=now;}
    if(hit&&hit.key!==this.target?.key&&now-this.since>=180){this.target=hit;this.elapsed=0;this.seen=now;}
    const inside=!!hit&&hit.key===this.target?.key;
    if(inside){this.elapsed+=dt;this.seen=now;}
    else if(now-this.seen>300){this.target=null;this.elapsed=0;}
    return {target:this.target,elapsed:this.elapsed,inside};
  }
}
if(typeof module!=='undefined')module.exports.GazeSelection=GazeSelection;
