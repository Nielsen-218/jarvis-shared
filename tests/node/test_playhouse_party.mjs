import assert from 'node:assert/strict';
import {dilemmas,facts,startVote,castVote,readyVoter,tally,nextVote,startQuiz,answerQuiz,nextQuiz,quizScore} from '../../app/games-party.mjs';

for(const players of [2,3,8]){
 let state=startVote(players);
 for(let round=0;round<dilemmas.length;round++){
  for(let voter=0;voter<players;voter++){
   const before=JSON.stringify(state);
   const next=castVote(state,voter%2);
   assert.equal(JSON.stringify(state),before,'Reducer mutates original state');
   state=next;
   assert.throws(()=>castVote(state,0),'One vote per handoff');
   if(voter<players-1){assert.throws(()=>tally(state));state=readyVoter(state)}
  }
  const result=tally(state);
  assert.deepEqual(result.counts,[Math.ceil(players/2),Math.floor(players/2)]);
  assert.equal(result.tie,players%2===0);
  assert.equal(result.winner,players%2===0?null:0);
  state=nextVote(state);
 }
 assert.equal(state.phase,'complete');assert.equal(state.results.length,5);
 assert.throws(()=>castVote(state,0));assert.throws(()=>nextVote(state));
}
for(const bad of [1,9,2.5,'2',null])assert.throws(()=>startVote(bad));
for(const bad of [-1,2,'0',NaN])assert.throws(()=>castVote(startVote(2),bad));
assert.throws(()=>nextVote(startVote(2)));
assert.throws(()=>readyVoter(startVote(2)));
for(const allCorrect of [true,false]){
 let state=startQuiz();
 facts.forEach(f=>{
  const choice=(allCorrect?f.truth:!f.truth)?0:1;
  assert.throws(()=>nextQuiz(state));
  state=answerQuiz(state,choice);
  assert.throws(()=>answerQuiz(state,choice));
  state=nextQuiz(state);
 });
 assert.equal(state.phase,'complete');
 assert.equal(quizScore(state),allCorrect?facts.length:0);
 assert.throws(()=>answerQuiz(state,0));
}
for(const bad of [null,'0',-1,2])assert.throws(()=>answerQuiz(startQuiz(),bad));
for(const l of ['ht','en']){
 dilemmas.forEach(d=>assert(d.question[l]&&d.choices.every(c=>c[l])));
 facts.forEach(f=>assert(f.question[l]&&f.explanation[l]));
}
console.log('Party rounds: 2/3/8 players, ties, handoffs, quiz scoring, replay protection and bilingual content passed.');
