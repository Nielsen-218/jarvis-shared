import assert from 'node:assert/strict';
import {newWould,restoreWould,stepWould,wouldActs,wouldCardFor,wouldCards} from '../../app/games-would.mjs';

assert.equal(wouldCards.length,12);
assert.equal(wouldActs.length,3);
for(const card of wouldCards){
  for(const language of ['ht','en']){
    assert(card.question[language]);
    assert.equal(card.choices.length,2);
    assert(card.choices[0][language]&&card.choices[1][language]&&
      card.choices[0][language]!==card.choices[1][language]);
  }
}
assert.throws(()=>newWould(['Nia',' nia ']),/players/);
assert.throws(()=>restoreWould({names:['Nia','Jo'],replay:0,actions:[{choice:0}]}),/action/);
let early=newWould(['Nia','Jo']);
early=stepWould(early,'ready');early=stepWould(early,{choice:0});early=stepWould(early,{predict:1});
assert.equal(early.phase,'cover','A zero-score player cannot double');
function match(replay,winnerSeat){
  let game=newWould(['Nia','Jo'],replay);
  const act=action=>{game=stepWould(game,action);assert.deepEqual(restoreWould(game),game);};
  for(let round=0;round<12;round++){
    assert.equal(game.round,round);
    assert(wouldCardFor(game));
    for(let turn=0;turn<2;turn++){
      const seat=game.seat;
      act('ready');
      act({choice:(seat+round)%2});
      const truth=(1-seat+round)%2;
      act({predict:seat===winnerSeat?truth:1-truth});
      if(game.phase==='risk')act({double:seat===winnerSeat&&game.players[seat].double});
    }
    assert.equal(game.phase,'result');
    assert.equal(game.history.length,round+1);
    assert(game.players.every(player=>player.score>=0));
    act('continue');
  }
  assert.equal(game.phase,'end');
  assert(game.players[winnerSeat].score>game.players[1-winnerSeat].score);
  return game.players.map(player=>player.score);
}
const first=match(0,0),second=match(1,1);
let tie=newWould(['Nia','Jo']);
const act=action=>tie=stepWould(tie,action);
for(let round=0;round<12;round++){
  for(let turn=0;turn<2;turn++){
    const seat=tie.seat;act('ready');act({choice:(seat+round)%2});act({predict:(1-seat+round)%2});
    if(tie.phase==='risk')act({double:false});
  }
  act('continue');
}
assert.deepEqual(tie.players.map(player=>player.score),[24,24]);
console.log(JSON.stringify({first,second,tie:tie.players.map(player=>player.score),rounds:12}));
