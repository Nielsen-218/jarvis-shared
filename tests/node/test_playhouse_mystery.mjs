import assert from 'node:assert/strict';
import pack from '../../app/games-lantern.json' with {type:'json'};
import {validatePack, startMystery, stepMystery, options, endingFor, restoreMystery} from '../../app/games-mystery.mjs';

validatePack(pack);
const changed = modify => {
  const copy = structuredClone(pack);
  modify(copy);
  return copy;
};
assert.throws(() => validatePack(changed(p => delete p.roles[0].en)), /role/);
assert.throws(() => validatePack(changed(p => {p.title.en = '<img src=x onerror=alert(1)>';})), /unsafe text/);
assert.throws(() => validatePack(changed(p => {p.title.ht = 'Mistè\u202e';})), /unsafe text/);
assert.throws(() => validatePack(changed(p => {p.rounds[0].choices[0].script = 'alert(1)';})), /choice fields/);
assert.throws(() => validatePack(changed(p => {p.rounds[0].choices[0].add = ['invented'];})), /effects/);
assert.throws(() => validatePack(changed(p => {p.endings[0].when.all = [];})), /exactly one/);
assert.throws(() => validatePack(changed(p => {p.endings.pop();})), /exactly one/);
assert.throws(() => validatePack(changed(p => {p.rounds[0].choices[0].when.all = ['opened'];})), /two choices/);
assert.throws(() => validatePack(changed(p => {
  p.endings.push({...p.endings[0], when:{all:['west-case','east-case'],none:[]}});
})), /unreachable ending/);

const step = (state, action) => stepMystery(pack, state, action);
function ready(players, seed) {
  let state = startMystery(pack, players, seed);
  assert.equal(new Set(state.roles.flat()).size, 8);
  assert(state.roles.every(roles => roles.length > 0));
  assert.throws(() => step(state, 'vote'), /phase/);
  for (let player = 0; player < players; player++) {
    assert.equal(state.player, player);
    assert.equal(state.phase, 'clues');
    state = step(state, 'share');
  }
  return state;
}

function round(state, voteFor) {
  const choices = options(pack, state);
  const selected = choices.findIndex(voteFor);
  assert(selected >= 0);
  state = step(state, 'vote');
  for (let player = 0; player < state.players; player++) {
    assert.equal(state.player, player);
    assert.throws(() => step(state, 'continue'), /phase/);
    assert.throws(() => step(state, choices.length), /ballot/);
    state = step(state, selected);
  }
  assert.equal(state.phase, 'result');
  assert.equal(state.history.at(-1).counts[selected], state.players);
  return step(state, 'continue');
}

const has = flag => choice => choice.add.includes(flag);
function play(players, seed, route, recovery) {
  let state = ready(players, seed);
  const actions = [has(recovery ? 'crank' : 'witness'), has(route), has(recovery ? 'ledger' : 'safe'), has('proof')];
  for (const voteFor of actions) state = round(state, voteFor);
  const canOpen = options(pack, state).some(has('opened'));
  assert.equal(canOpen, recovery && route === (state.caseIndex === 0 ? 'west' : 'east'));
  state = round(state, canOpen ? has('opened') : has('safe'));
  state = round(state, canOpen ? has('recovered') : has('delay'));
  const restored = restoreMystery(pack, JSON.parse(JSON.stringify({players, seed, actions:state.actions})));
  assert.deepEqual(restored, state);
  return endingFor(pack, restored).title.en;
}
for (let players = 2; players <= 8; players++) {
  assert.equal(play(players, 0, 'west', true), 'The lantern shines again');
  assert.equal(play(players, 1, 'west', true), 'A festival in the rain');
  assert.equal(play(players, 1, 'east', true), 'The lantern shines again');
}
let state = ready(2, 0);
state = step(step(step(state, 'vote'), 0), 1);
assert.equal(state.history[0].tied, true);
assert(state.flags.includes('witness'));
state = step(state, 'continue');
state = step(step(step(state, 'vote'), 0), 1);
assert(state.flags.includes('east'), 'tie leadership rotates');
assert.throws(() => restoreMystery(pack, {players:2, seed:0, actions:[0]}), /phase/);
assert.notDeepEqual(startMystery(pack, 8, 0).roles, startMystery(pack, 8, 2).roles);
state = ready(2, 0);
for (const voteFor of [has('crank'), has('east'), has('ledger'), has('proof'),
  choice => choice.add.length === 0, has('delay')]) state = round(state, voteFor);
assert.equal(endingFor(pack, state).title.en, 'The search resumes tomorrow');
console.log('Mystery pack validation, all player counts, replay variants, ballots, consequences and save replay passed.');
