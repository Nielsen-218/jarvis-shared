import assert from 'node:assert/strict';
import {bluffActs,bluffCardFor,bluffCards,newBluff,restoreBluff,stepBluff,storytellerSeat} from '../../app/games-bluff.mjs';

assert.equal(bluffCards.length,12);
assert.equal(bluffActs.length,3);
for(const card of bluffCards){
  for(const language of ['ht','en'])
    assert(card.prompt[language] && card.yes[language] && card.no[language] && card.yes[language]!==card.no[language]);
}
assert.throws(()=>newBluff(['Nia',' nia ']),/players/);
assert.throws(()=>restoreBluff({names:['Nia','Jo'],replay:0,actions:['ready',{claim:true}]}),/action/);
let caught=newBluff(['Nia','Jo']);
for(const action of ['ready',{truth:true},'wild',{claim:false},'ready',{believe:false}]) caught=stepBluff(caught,action);
assert.equal(caught.history[0].points,3);
assert.deepEqual(caught.players.map(player=>player.score),[0,3]);
function play(replay,winnerSeat) {
  let game=newBluff(['Nia','Jo'],replay);
  const act=action=>{game=stepBluff(game,action);assert.deepEqual(restoreBluff(game),game);};
  for(let round=0;round<12;round++){
    assert.equal(game.phase,'cover-storyteller');
    assert.equal(game.round,round);
    assert(bluffCardFor(game));
    const storyteller=storytellerSeat(game),detective=1-storyteller;
    act('ready');
    const truth=round%2===0;
    act({truth});
    if(storyteller===winnerSeat && game.players[storyteller].wild && round<4){
      act('wild');
      assert.throws(()=>stepBluff(game,{claim:truth}),/action/);
    }
    const claim=storyteller===winnerSeat?!truth:truth;
    act({claim});
    assert.equal(game.phase,'cover-detective');
    act('ready');
    act({believe:detective===winnerSeat ? claim===truth : claim!==truth});
    assert.equal(game.phase,'result');
    assert.equal(game.history.length,round+1);
    act('continue');
  }
  assert.equal(game.phase,'end');
  assert.equal(game.history.length,12);
  assert(game.players[winnerSeat].score>game.players[1-winnerSeat].score);
  assert(game.players.every(player=>!player.wild || player.score>=0));
  return game.players.map(player=>player.score);
}
const first=play(0,0),second=play(1,1);
let tie=newBluff(['Nia','Jo']);
const act=action=>tie=stepBluff(tie,action);
for(let round=0;round<12;round++){
  act('ready');act({truth:true});act({claim:true});act('ready');act({believe:true});act('continue');
}
assert.deepEqual(tie.players.map(player=>player.score),[12,12]);
console.log(JSON.stringify({first,second,tie:tie.players.map(player=>player.score),rounds:12}));
