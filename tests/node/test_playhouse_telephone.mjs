import assert from 'node:assert/strict';
import {newTelephone,restoreTelephone,roundCount,stageCount,stageKind,stepTelephone,validDrawing,telephoneStarters,starterFor} from '../../app/games-telephone.mjs';

const drawing=[{tool:'pen',color:'#2d1941',width:8,points:[[100,100],[400,400]]}];
const names=['Ana','Ben','Cleo','Dany','Eli','Fifi','Gio','Hana'];
assert(telephoneStarters.length>=10);
for(const n of [3,4,5,8])for(const length of ['quick','standard']){
 let game=newTelephone(names.slice(0,n),length,7),seen=[];
 const rounds=length==='quick'?1:2;
 for(let round=0;round<rounds;round++){
  assert.equal(game.phase,'handoff');
  for(let stage=0;stage<stageCount(n);stage++){
   const player=(round+stage)%n;seen.push(player);
   game=stepTelephone(game,'ready');
   const kind=stageKind(stage);
   game=stepTelephone(game,kind==='drawing'?{submit:true,strokes:drawing}:{submit:true,text:`Round ${round} turn ${stage}`});
   assert.equal(game.entries.at(-1).player,player);
   assert.equal(restoreTelephone({names:game.names,length,seed:game.seed,actions:game.actions}).phase,game.phase);
  }
  assert.equal(game.phase,'reveal');assert.equal(game.entries.length,stageCount(n));
  assert.deepEqual(new Set(game.entries.map(e=>e.player)).size,n);
  assert.equal(game.entries.at(-1).kind,'guess');
  game=stepTelephone(game,'next');
 }
 assert.equal(game.phase,'end');assert.equal(game.chains.length,rounds);
 assert.deepEqual(new Set(seen).size,n);
 assert.throws(()=>stepTelephone(game,'ready'));
}
for(const length of ['quick','standard']){
 let game=newTelephone(names.slice(0,2),length,7);
 assert.equal(game.variant,'duo');assert.equal(stageCount(2,game.variant),2);
 const counts=[0,0],rounds=roundCount(game);
 assert.equal(rounds,length==='quick'?2:4);
 for(let round=0;round<rounds;round++){
  assert.equal(game.phase,'handoff');
  assert.equal(stageKind(0,game.variant),'drawing');
  assert.equal(stageKind(1,game.variant),'guess');
  const prompt=starterFor(game);
  assert(!JSON.stringify(game.entries).includes(prompt.en));
  game=stepTelephone(game,'ready');
  game=stepTelephone(game,{submit:true,strokes:drawing});counts[round%2]++;
  assert.equal(game.phase,'handoff');assert.equal(game.entries.length,1);
  assert(!JSON.stringify(game.entries).includes(prompt.en));
  game=stepTelephone(game,'ready');
  game=stepTelephone(game,{submit:true,text:`Guess for sketch ${round}`});counts[(round+1)%2]++;
  assert.equal(game.phase,'reveal');
  game=stepTelephone(game,'next');
  assert.deepEqual(game.chains.at(-1).prompt,prompt);
  assert.equal(restoreTelephone({names:game.names,length,seed:game.seed,variant:game.variant,actions:game.actions}).phase,game.phase);
 }
 assert.deepEqual(counts,[rounds,rounds]);assert.equal(game.phase,'end');assert.equal(game.chains.length,rounds);
}
let legacy=newTelephone(['Ana','Ben'],'quick',7,'legacy');
for(const [kind,payload] of [['prompt',{submit:true,text:'A singing bicycle'}],['drawing',{submit:true,strokes:drawing}],['guess',{submit:true,text:'A noisy bike'}]]){
 legacy=stepTelephone(legacy,'ready');assert.equal(stageKind(legacy.stage,legacy.variant),kind);
 legacy=stepTelephone(legacy,payload);
}
assert.equal(legacy.phase,'reveal');
assert.equal(restoreTelephone({names:legacy.names,length:legacy.length,seed:legacy.seed,actions:legacy.actions}).phase,'reveal');
assert.throws(()=>newTelephone(['A','A']));
assert.throws(()=>newTelephone(['A']));
assert.throws(()=>newTelephone(names,'long'));
assert.equal(validDrawing([{tool:'pen',color:'#000',width:8,points:[[0,0],[1,1]]}]),false);
assert.equal(validDrawing([{tool:'pen',color:'#2d1941',width:8,points:[[0,0],[1001,1]]}]),false);
assert.equal(validDrawing([{tool:'pen',color:'#2d1941',width:8,points:[[0,0]]}]),false);
assert.equal(validDrawing(drawing),true);
assert.equal(validDrawing([{...drawing[0],points:Array(240).fill([0,0])}]),true);
assert.equal(validDrawing(Array(13).fill({...drawing[0],points:Array(240).fill([0,0])})),false);
let game=stepTelephone(newTelephone(names.slice(0,3)),'ready');
for(const text of ['<script>','hi','x'.repeat(81),'a\nb'])assert.throws(()=>stepTelephone(game,{submit:true,text}));
assert.throws(()=>stepTelephone(game,{submit:true,strokes:drawing}));
assert.throws(()=>restoreTelephone({names:['A','B'],length:'quick',seed:0,actions:Array(41).fill('ready')}));
console.log('Drawing Telephone reducer: two-player draw/guess relay, legacy save recovery, 3/4/5/8-player chains, both lengths and invalid content passed');
