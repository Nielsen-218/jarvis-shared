import assert from 'node:assert/strict';
import {bluffCard, challenge, movesFor, newRivalry, restoreRivalry, scenarios, stepRivalry} from '../../app/games-rivalry.mjs';

function play(replay, scenario) {
  const winnerSeat=replay%2;
  let game = newRivalry(['Nia','Jo'], replay, replay*1103, scenario);
  const act = action => {game = stepRivalry(game, action);};
  act('ready');
  act('ready');
  let beforeFinal;
  for (let round = 0; round < 12; round++) {
    if (round === 11) beforeFinal = game.players.map(player=>player.score);
    while (game.phase === 'play') {
      const step = challenge(game);
      const {seat} = game;
      if (step.kind === 'route') {
        act({move: round === 8 ? (seat === winnerSeat ? 3 : 1) : round === 4 && seat === winnerSeat ? 2 : 0});
      } else if (step.kind === 'bluff') {
        const {actual} = bluffCard(game);
        act({claim:(actual+1)%3});
        assert.deepEqual(restoreRivalry(JSON.parse(JSON.stringify(game))),game);
        act({believe:game.seat!==winnerSeat});
      } else {
        const missed = (round === 3 && seat===winnerSeat) || (round===6 && seat===winnerSeat) ||
          (round===11 && seat!==winnerSeat);
        if (step.kind==='wager') {
          act({stake:game.players[seat].coins>=2?2:0});
          assert.deepEqual(restoreRivalry(JSON.parse(JSON.stringify(game))),game);
          assert.throws(()=>stepRivalry(game,{stake:0}),/answer/);
        }
        act({answer:missed?'0':step.answer});
      }
    }
    assert.equal(game.phase,'result');
    assert(game.players.every(player=>player.coins>=0));
    act('continue');
  }
  assert.equal(game.phase,'end');
  assert.equal(game.log.length,24);
  assert.equal(game.actions.length,44);
  assert.deepEqual(restoreRivalry(JSON.parse(JSON.stringify(game))),game);
  assert(game.log.some(turn=>turn.shieldUsed));
  assert(game.log.some(turn=>turn.toll===2));
  return {winner:game.end, scenario, score:game.players.map(player=>player.score), beforeFinal,
    roles:game.players.map(player=>player.role),coins:game.players.map(player=>player.coins),bonuses:game.players.map(player=>player.bonus)};
}

assert.throws(()=>newRivalry(['Nia',' nia ']),/players/);
assert.throws(()=>newRivalry(['Nia','Jo'],0,-1),/players/);
assert.throws(()=>newRivalry(['Nia','Jo'],0,0.5),/players/);
assert.throws(()=>newRivalry(['Nia','Jo'],0,0,3),/players/);
assert.throws(()=>restoreRivalry({names:['Nia','Jo'],replay:0,actions:[{move:0}]}),/turn/);
assert.equal(scenarios.length,3);
for (const [index,scenario] of scenarios.entries()) {
  assert.equal(scenario.rounds.length,12);
  assert.equal(scenario.alternate.length,12);
  assert.equal(scenario.acts.length,3);
  assert.equal(scenario.endings.length,3);
  for (const language of ['ht','en']) {
    assert(scenario.title[language]);
    assert(scenario.acts.every(act=>act[language]));
    assert(scenario.endings.every(ending=>ending[language]));
  }
  for (let round=0;round<12;round++) {
    const step=scenario.rounds[round];
    assert.equal(step.kind,scenarios[0].rounds[round].kind,`Scenario ${index}, round ${round}`);
    if (step.kind==='route') {
      const moves=movesFor(round,index);
      assert.equal(moves.length,round===0?3:4);
      assert(moves.every(move=>move.ht && move.en));
    }
    if (step.kind==='bluff') continue;
    for (const variant of [step,scenario.alternate[round]].filter(Boolean)) {
      const prompt=variant.kind==='route' ? variant.title : variant.prompt;
      assert(prompt.ht && prompt.en,`Missing translation in scenario ${index}, round ${round}`);
      if (variant.kind==='order') assert(/^[123]{3}$/.test(variant.answer) && new Set(variant.answer).size===3);
      else if (variant.kind!=='route') assert(/^\d{1,3}$/.test(variant.answer));
    }
  }
}
assert(new Set([1,2,3,4,5,6].map(seed=>bluffCard(newRivalry(['Nia','Jo'],0,seed)).actual)).size>1);
assert.equal(bluffCard(newRivalry(['Nia','Jo'],0)).actual,0,'Legacy saves retain their badge order');
assert.equal(restoreRivalry({names:['Nia','Jo'],replay:0,actions:[]}).seed,null);
assert.deepEqual(newRivalry(['Nia','Jo'],0).players.map(player=>player.role),[0,1]);
assert.deepEqual(newRivalry(['Nia','Jo'],1).players.map(player=>player.role),[1,0]);
assert.equal(newRivalry(['Nia','Jo'],1).seat,1);
const first = play(0,0);
const second = play(1,1);
const third = play(2,2);
assert.equal(first.winner,0);
assert.equal(second.winner,1);
assert.equal(third.winner,0);
assert(first.beforeFinal[0]<first.beforeFinal[1]);
assert(second.beforeFinal[1]<second.beforeFinal[0]);
assert(third.beforeFinal[0]<third.beforeFinal[1]);

for (let seed=1;seed<=64;seed++) {
  let random=seed;
  const roll=limit=>{random=(Math.imul(random,1664525)+1013904223)>>>0;return random%limit;};
  let game=newRivalry(['Nia','Jo'],seed,seed*13007,seed%3);
  const act=action=>{
    game=stepRivalry(game,action);
    assert(game.players.every(player=>player.coins>=0 && player.score>=0));
  };
  act('ready');act('ready');
  for (let round=0;round<12;round++) {
    assert.equal(game.round,round);
    assert.equal(game.phase,'play');
    while(game.phase==='play') {
      const step=challenge(game);
      if(step.kind==='route') {
        const options=movesFor(round,game.scenario).map((_,index)=>index).filter(move=>
          move===0 || (game.players[game.seat].coins && (move!==2 || !game.players[game.seat].shield)));
        act({move:options[roll(options.length)]});
      } else if(step.kind==='bluff') {
        act(game.claim===null ? {claim:roll(3)} : {believe:!!roll(2)});
        assert.deepEqual(restoreRivalry(game),game);
      } else {
        if(step.kind==='wager') {
          act({stake:game.players[game.seat].coins>=2 && roll(2) ? 2 : 0});
          assert.deepEqual(restoreRivalry(game),game);
        }
        act({answer:roll(3) ? step.answer : '0'});
      }
    }
    assert.equal(game.phase,'result');
    assert.equal(game.log.length,2*(round+1));
    act('continue');
  }
  assert.equal(game.phase,'end');
  assert.equal(game.log.length,24);
  assert(game.players.every(player=>player.bonus===0 || player.bonus===3));
  const [a,b]=game.players;
  const diff=a.score-b.score || a.correct-b.correct || a.coins-b.coins;
  assert.equal(game.end,diff===0?null:diff>0?0:1);
  assert.deepEqual(restoreRivalry(game),game);
}
console.log(JSON.stringify({first,second,third,scenarios:scenarios.length}));
