const t=(ht,en)=>({ht,en});
export const telephoneStarters=[
  t('Yon kabrit ak linèt solèy sou Lalin nan','A goat in sunglasses on the Moon'),
  t('Yon gato k ap kouri pou l pa al nan fèt la','A cake running away from its party'),
  t('Yon jako k ap fè rap sou yon bato','A parrot rapping on a boat'),
  t('Yon robo k ap kwit mango nan lapli','A robot cooking mangoes in the rain'),
  t('Yon chat k ap dirije yon òkès poul','A cat conducting a chicken orchestra'),
  t('Yon pirat k ap chèche dènye ti bonbon an','A pirate hunting the last cookie'),
  t('Yon elefan k ap monte yon bisiklèt piti','An elephant riding a tiny bicycle'),
  t('Yon pwason k ap li yon liv anba dlo','A fish reading a book underwater'),
  t('Yon chato ki fèt ak pòpkòn','A castle made of popcorn'),
  t('Yon chen k ap fè yoga sou yon nyaj','A dog doing yoga on a cloud'),
  t('Yon dinozò k ap pote flè bay yon myèl','A dinosaur bringing flowers to a bee'),
  t('Yon astronòt k ap danse ak yon kabrit','An astronaut dancing with a goat'),
];
export const DRAW_COLORS=['#2d1941','#e04285','#2188a3','#c47821'];
export const stageKind=(stage,variant='chain')=>variant==='duo'?(stage===0?'drawing':'guess'):stage===0?'prompt':stage%2?'drawing':'guess';
export const stageCount=(players,variant='chain')=>variant==='duo'?2:players%2===0?players+1:players;
export const roundCount=state=>state.variant==='duo'?(state.length==='quick'?2:4):(state.length==='quick'?1:2);
export function playerFor(state){return (state.round+state.stage)%state.names.length;}
export function starterFor(state){return telephoneStarters[(state.seed+state.round*5)%telephoneStarters.length];}
function cleanText(value){
  if(typeof value!=='string')throw new Error('Invalid text');
  const trimmed=value.trim();
  if(trimmed.length<3||trimmed.length>80||/[\u0000-\u001f\u007f<>]/.test(trimmed))throw new Error('Invalid text');
  return trimmed;
}
export function validDrawing(strokes){
  if(!Array.isArray(strokes)||strokes.length<1||strokes.length>80)return false;
  let ink=false,totalPoints=0;
  for(const stroke of strokes){
    if(!stroke||!['pen','erase'].includes(stroke.tool)||
       (stroke.tool==='pen'&&!DRAW_COLORS.includes(stroke.color))||
       ![8,18].includes(stroke.width)||!Array.isArray(stroke.points)||
       stroke.points.length<1||stroke.points.length>240)return false;
    totalPoints+=stroke.points.length;if(totalPoints>3000)return false;
    if(stroke.tool==='pen'&&stroke.points.length>=2)ink=true;
    for(const point of stroke.points)
      if(!Array.isArray(point)||point.length!==2||point.some(value=>!Number.isInteger(value)||value<0||value>1000))return false;
  }
  return ink;
}
export function newTelephone(names,length='quick',seed=0,variant='auto'){
  if(!Array.isArray(names)||names.length<2||names.length>8||
     names.some(name=>typeof name!=='string'||!name.trim()||name.length>20)||
     new Set(names.map(name=>name.trim().toLowerCase())).size!==names.length||
     !['quick','standard'].includes(length)||!Number.isInteger(seed)||seed<0||seed>0xffffffff||
     !['auto','duo','chain','legacy'].includes(variant)||
     (variant==='duo'&&names.length!==2)||(variant==='legacy'&&names.length!==2)||
     (variant==='chain'&&names.length===2))
    throw new Error('Invalid Telephone game');
  const kind=variant==='auto'?(names.length===2?'duo':'chain'):variant;
  return {names:names.map(name=>name.trim()),length,seed,variant:kind,round:0,stage:0,phase:'handoff',
    entries:[],chains:[],actions:[]};
}
export function stepTelephone(state,action){
  const next={...state,actions:[...state.actions,action]};
  if(state.phase==='handoff'&&action==='ready')return {...next,phase:'entry'};
  if(state.phase==='entry'&&action&&typeof action==='object'&&action.submit){
    const kind=stageKind(state.stage,state.variant),entry={player:playerFor(state),kind};
    if(kind==='drawing'){
      if(!validDrawing(action.strokes))throw new Error('Invalid drawing');
      entry.strokes=action.strokes.map(stroke=>({...stroke,points:stroke.points.map(point=>[...point])}));
    }else entry.text=cleanText(action.text);
    const entries=[...state.entries,entry],stage=state.stage+1;
    return {...next,entries,stage,phase:stage===stageCount(state.names.length,state.variant)?'reveal':'handoff'};
  }
  if(state.phase==='reveal'&&action==='next'){
    const chains=[...state.chains,{round:state.round,entries:state.entries,
      ...(state.variant==='duo'?{prompt:starterFor(state)}:{})}];
    const round=state.round+1;
    return {...next,chains,round,stage:0,entries:[],phase:round===roundCount(state)?'end':'handoff'};
  }
  throw new Error('Invalid Telephone action');
}
export function restoreTelephone(save){
  if(!save||!Array.isArray(save.actions)||save.actions.length>40)throw new Error('Invalid save');
  const variant=save.variant||((save.names?.length===2)?'legacy':'chain');
  return save.actions.reduce(stepTelephone,newTelephone(save.names,save.length,save.seed,variant));
}
