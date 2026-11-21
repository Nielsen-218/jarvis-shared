// Local, shared-device party rounds. These are not network rooms or server scores.
import {text} from './games-story.mjs';

export const dilemmas = [
  {question:text('Ki pouvwa ou ta chwazi?', 'Which power would you choose?'),
   choices:[text('Pale ak bèt yo', 'Talk to animals'),text('Respire anba dlo', 'Breathe underwater')]},
  {question:text('Ki kote fanmi an ta pase yon wikenn?', 'Where would the family spend a weekend?'),
   choices:[text('Nan yon kay nan pyebwa', 'In a treehouse'),text('Nan yon chato', 'In a castle')]},
  {question:text('Ki ti sipriz ou ta renmen?', 'Which surprise would you like?'),
   choices:[text('Yon robo ki fè manje', 'A robot that cooks'),text('Yon sak ki toujou gen ti goute', 'A bag that always has snacks')]},
  {question:text('Ki avanti ou ta chwazi?', 'Which adventure would you choose?'),
   choices:[text('Eksplore fon lanmè', 'Explore the deep sea'),text('Vizite Lalin nan', 'Visit the Moon')]},
  {question:text('Ki jan nou ta fè fèt la?', 'How would we celebrate?'),
   choices:[text('Yon konkou dans', 'A dance contest'),text('Yon sware sinema', 'A movie night')]},
];

export const facts = [
  {question:text('Yon poulp gen twa kè.', 'An octopus has three hearts.'), truth:true,
   explanation:text('De kè voye san nan branchi yo; twazyèm nan voye l nan rès kò a.', 'Two hearts pump blood to the gills; the third pumps it to the rest of the body.')},
  {question:text('Solèy la se yon planèt.', 'The Sun is a planet.'), truth:false,
   explanation:text('Solèy la se yon zetwal. Latè se yon planèt ki vire toutotou li.', 'The Sun is a star. Earth is a planet that orbits it.')},
  {question:text('Tout zwazo kapab vole.', 'All birds can fly.'), truth:false,
   explanation:text('Gen zwazo, tankou pengwen ak otrich, ki pa vole.', 'Some birds, such as penguins and ostriches, cannot fly.')},
  {question:text('Yon triyang gen twa kote.', 'A triangle has three sides.'), truth:true,
   explanation:text('Twa kote ak twa ang fè yon triyang.', 'Three sides and three angles make a triangle.')},
  {question:text('Lalin nan fè pwòp limyè pa li tankou Solèy la.', 'The Moon makes its own light like the Sun.'), truth:false,
   explanation:text('Limyè nou wè sou Lalin nan se limyè Solèy la li reflete.', 'The moonlight we see is reflected sunlight.')},
];

function requireKind(state, kind) {
  if (!state || state.kind !== kind) throw new Error('Wrong game');
}
function requireIndex(value, length) {
  if (!Number.isInteger(value) || value < 0 || value >= length) throw new Error('Invalid choice');
}
export function startVote(players) {
  if (!Number.isInteger(players) || players < 2 || players > 8) throw new Error('Choose 2–8 players');
  return {kind:'vote', players, round:0, votes:[], results:[], phase:'vote'};
}
export function castVote(state, choice) {
  requireKind(state,'vote'); requireIndex(choice,2);
  if (state.phase !== 'vote') throw new Error('Voting closed');
  const votes=[...state.votes,choice];
  return {...state,votes,phase:votes.length===state.players?'result':'handoff'};
}
export function readyVoter(state) {
  requireKind(state,'vote');
  if (state.phase !== 'handoff') throw new Error('No handoff pending');
  return {...state,phase:'vote'};
}
export function tally(state) {
  requireKind(state,'vote');
  if (state.phase !== 'result') throw new Error('Results are not ready');
  const counts=[state.votes.filter(v=>v===0).length,state.votes.filter(v=>v===1).length];
  return {counts,tie:counts[0]===counts[1],winner:counts[0]===counts[1]?null:counts[0]>counts[1]?0:1};
}
export function nextVote(state) {
  const result=tally(state);
  const results=[...state.results,{round:state.round,...result}];
  return {...state,round:state.round+1,votes:[],results,phase:results.length===dilemmas.length?'complete':'vote'};
}
export function startQuiz() {return {kind:'quiz',round:0,answers:[],phase:'question'};}
export function answerQuiz(state, choice) {
  requireKind(state,'quiz'); requireIndex(choice,2);
  if (state.phase !== 'question') throw new Error('Question already answered');
  return {...state,answers:[...state.answers,(choice===0)===facts[state.round].truth],phase:'answer'};
}
export function nextQuiz(state) {
  requireKind(state,'quiz');
  if(state.phase !== 'answer') throw new Error('Answer the current question first');
  const round=state.round+1;
  return {...state,round,phase:round===facts.length?'complete':'question'};
}
export function quizScore(state) {
  requireKind(state,'quiz');
  return state.answers.filter(Boolean).length;
}
