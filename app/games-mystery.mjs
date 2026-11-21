// Scenario packs contain data only. Rules and save replay live here.
const ID = /^[a-z][a-z0-9-]{0,31}$/;
export const PACK_LIMIT = 96 * 1024;

function require(condition, message) {
  if (!condition) throw new Error(message);
}

function fields(value, names, context) {
  require(value && typeof value === 'object' && !Array.isArray(value), context);
  require(Object.keys(value).length === names.length && names.every(name => Object.hasOwn(value, name)), context);
}

function bilingual(value, max, context) {
  fields(value, ['ht', 'en'], context);
  for (const text of [value.ht, value.en]) {
    require(typeof text === 'string' && text.trim() && text.length <= max, context);
    // Packs are plain prose. Reject markup and invisible control characters at import.
    require(!/[<>]/.test(text) && !/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f\u202a-\u202e\u2066-\u2069]/u.test(text), 'unsafe text');
  }
}

function list(value, min, max, context) {
  require(Array.isArray(value) && value.length >= min && value.length <= max, context);
}

function flags(value, known, context) {
  list(value, 0, known.size, context);
  require(value.every(flag => known.has(flag)) && new Set(value).size === value.length, context);
}

function condition(rule, known) {
  fields(rule, ['all', 'none'], 'condition');
  flags(rule.all, known, 'required flags');
  flags(rule.none, known, 'excluded flags');
  require(!rule.all.some(flag => rule.none.includes(flag)), 'contradictory condition');
}

function matches(rule, active) {
  return rule.all.every(flag => active.includes(flag)) && rule.none.every(flag => !active.includes(flag));
}

export function options(pack, state) {
  return pack.rounds[state.round].choices.filter(choice => matches(choice.when, state.flags));
}

export function validatePack(pack) {
  fields(pack, ['version', 'id', 'title', 'intro', 'flags', 'roles', 'cases', 'rounds', 'endings'], 'pack fields');
  require(pack.version === 1 && ID.test(pack.id) && typeof pack.id === 'string', 'pack version or ID');
  bilingual(pack.title, 100, 'title');
  bilingual(pack.intro, 1600, 'introduction');
  list(pack.flags, 1, 24, 'flags');
  const known = new Set(pack.flags);
  require(known.size === pack.flags.length && pack.flags.every(flag => typeof flag === 'string' && ID.test(flag)), 'flag names');
  list(pack.roles, 8, 8, 'eight roles required');
  pack.roles.forEach(role => bilingual(role, 100, 'role name'));
  for (const lang of ['ht', 'en'])
    require(new Set(pack.roles.map(role => role[lang])).size === 8, 'role names must be distinct');
  list(pack.cases, 1, 3, 'cases');
  for (const entry of pack.cases) {
    fields(entry, ['flags', 'clues'], 'case fields');
    flags(entry.flags, known, 'case flags');
    list(entry.clues, 8, 8, 'eight clues required');
    entry.clues.forEach(clue => bilingual(clue, 850, 'private clue'));
  }
  list(pack.rounds, 4, 7, 'round count');
  for (const round of pack.rounds) {
    fields(round, ['title', 'body', 'discussion', 'choices'], 'round fields');
    bilingual(round.title, 100, 'round title');
    bilingual(round.body, 1400, 'round body');
    bilingual(round.discussion, 400, 'discussion prompt');
    list(round.choices, 2, 4, 'choice count');
    for (const choice of round.choices) {
      fields(choice, ['label', 'result', 'when', 'add'], 'choice fields');
      bilingual(choice.label, 240, 'choice label');
      bilingual(choice.result, 850, 'consequence');
      condition(choice.when, known);
      flags(choice.add, known, 'choice effects');
    }
  }
  list(pack.endings, 2, 8, 'endings');
  for (const ending of pack.endings) {
    fields(ending, ['title', 'body', 'when'], 'ending fields');
    bilingual(ending.title, 100, 'ending title');
    bilingual(ending.body, 1400, 'ending body');
    condition(ending.when, known);
  }
  require(new TextEncoder().encode(JSON.stringify(pack)).length <= PACK_LIMIT, 'pack size');

  const reached = new Set();
  const reachedChoices = new Set();
  function explore(round, active) {
    if (round === pack.rounds.length) {
      const endings = pack.endings.filter(ending => matches(ending.when, active));
      require(endings.length === 1, 'each path must reach exactly one ending');
      reached.add(endings[0]);
      return;
    }
    const choices = options(pack, {round, flags:active});
    require(choices.length >= 2, 'a reachable round must offer at least two choices');
    for (const choice of choices) {
      reachedChoices.add(choice);
      explore(round + 1, [...new Set([...active, ...choice.add])]);
    }
  }
  for (const entry of pack.cases) explore(0, entry.flags);
  require(reached.size === pack.endings.length, 'unreachable ending');
  require(pack.rounds.every(round => round.choices.every(choice => reachedChoices.has(choice))), 'unreachable choice');
  return pack;
}

export function startMystery(pack, players, seed) {
  require(Number.isInteger(players) && players >= 2 && players <= 8, 'players');
  require(Number.isInteger(seed) && seed >= 0 && seed <= 0xffffffff, 'seed');
  // Deal all eight clues, with each player receiving at least one role.
  const roles = Array.from({length:players}, () => []);
  const rotation = Math.floor(seed / pack.cases.length) % 8;
  for (let i = 0; i < 8; i++) roles[i % players].push((i + rotation) % 8);
  const caseIndex = seed % pack.cases.length;
  return {
    players, seed, caseIndex, roles, round:0, player:0, phase:'clues',
    flags:[...pack.cases[caseIndex].flags], votes:[], history:[], actions:[],
  };
}

export function stepMystery(pack, state, action) {
  const next = {...state, actions:[...state.actions, action]};
  if (state.phase === 'clues' && action === 'share') {
    if (state.player + 1 < state.players) return {...next, player:state.player + 1};
    return {...next, phase:'discussion', player:0};
  }
  if (state.phase === 'discussion' && action === 'vote') return {...next, phase:'ballot'};
  if (state.phase === 'ballot' && Number.isInteger(action)) {
    const choices = options(pack, state);
    require(action >= 0 && action < choices.length, 'invalid ballot');
    const votes = [...state.votes, action];
    if (votes.length < state.players) return {...next, votes, player:state.player + 1};
    const counts = choices.map((_, index) => votes.filter(vote => vote === index).length);
    const best = Math.max(...counts);
    const lead = state.round % state.players;
    const ordered = [...votes.slice(lead), ...votes.slice(0, lead)];
    const winner = ordered.find(vote => counts[vote] === best);
    const choice = choices[winner];
    return {
      ...next, votes:[], player:0, phase:'result',
      flags:[...new Set([...state.flags, ...choice.add])],
      history:[...state.history, {choice, counts, tied:counts.filter(count => count === best).length > 1}],
    };
  }
  if (state.phase === 'result' && action === 'continue') {
    const round = state.round + 1;
    return {...next, round, phase:round === pack.rounds.length ? 'ending' : 'discussion'};
  }
  throw new Error('Action not available in this phase');
}

export function endingFor(pack, state) {
  require(state.phase === 'ending', 'adventure in progress');
  return pack.endings.find(ending => matches(ending.when, state.flags));
}

export function restoreMystery(pack, save) {
  fields(save, ['players', 'seed', 'actions'], 'save fields');
  list(save.actions, 0, 8 + pack.rounds.length * 10, 'save actions');
  return save.actions.reduce((state, action) => stepMystery(pack, state, action),
    startMystery(pack, save.players, save.seed));
}
