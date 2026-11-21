import assert from 'node:assert/strict';
import {cardFor, knowCards, knowTopics, newKnow, restoreKnow, stepKnow, subjectSeat} from '../../app/games-know.mjs';

assert.equal(knowCards.length,12);
assert.equal(knowTopics.length,3);
for (const card of knowCards) {
  assert(card.question.ht && card.question.en);
  assert.equal(card.choices.length,3);
  for (const language of ['ht','en'])
    assert.equal(new Set(card.choices.map(choice=>choice[language])).size,3);
}
assert.throws(()=>newKnow(['Nia',' nia ']),/players/);
assert.throws(()=>restoreKnow({names:['Nia','Jo'],replay:0,actions:['ready',{guess:0}]}),/action/);
let noPoints=newKnow(['Nia','Jo']);
noPoints=stepKnow(noPoints,'ready');
noPoints=stepKnow(noPoints,{truth:0});
noPoints=stepKnow(noPoints,{forecast:false});
noPoints=stepKnow(noPoints,'ready');
assert.throws(()=>stepKnow(noPoints,'double'),/action/);

function match(replay,winner) {
  let game=newKnow(['Nia','Jo'],replay);
  const act=action=>{game=stepKnow(game,action);assert.deepEqual(restoreKnow(game),game);};
  for (let round=0;round<12;round++) {
    assert.equal(game.phase,'cover-subject');
    assert.equal(game.round,round);
    assert(cardFor(game));
    const subject=subjectSeat(game),guesser=1-subject;
    act('ready');
    const truth=round%3;
    act({truth});
    assert.equal(game.phase,'forecast');
    act({forecast:guesser===winner});
    assert.equal(game.phase,'cover-guesser');
    act('ready');
    if (guesser===winner && round<4) {
      if(round===0 || round===1) {
        act('hint');
        assert.throws(()=>stepKnow(game,{guess:(truth+1)%3}),/action/);
      } else if(round===2 || round===3) act('double');
    }
    act({guess:guesser===winner?truth:(truth+2)%3});
    assert.equal(game.phase,'result');
    assert(game.players.every(player=>player.score>=0));
    act('continue');
  }
  assert.equal(game.phase,'end');
  assert.equal(game.history.length,12);
  assert.equal(game.history.filter(entry=>entry.right).length,6);
  assert.equal(game.history.filter(entry=>entry.forecastRight).length,12);
  assert.equal(game.actions.length,74);
  assert(game.players[winner].score>game.players[1-winner].score);
  return game.players.map(player=>player.score);
}
const first=match(0,0);
const second=match(1,1);
let tie=newKnow(['Nia','Jo']);
const act=action=>tie=stepKnow(tie,action);
for(let round=0;round<12;round++){
  act('ready');act({truth:round%3});act({forecast:true});act('ready');act({guess:round%3});act('continue');
}
assert.deepEqual(tie.players.map(player=>player.score),[18,18]);
console.log(JSON.stringify({first,second,tie:tie.players.map(player=>player.score),rounds:12}));
