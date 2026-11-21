// Paired-device room UI. Only room identifiers persist locally; votes stay on server.
export function installRooms({open,close,say,pick,button,title,body,label,buttons}) {
  let room=null, timer=null, epoch=0, opener, busy=false;
  const $=s=>document.querySelector(s);
  const key='jarvis_playhouse_room';
  let errorNode;
  function saved(){try{return localStorage.getItem(key)}catch{return null}}
  function remember(id){try{id?localStorage.setItem(key,id):localStorage.removeItem(key)}catch{}}
  function stop(){epoch++;clearTimeout(timer);timer=null;busy=false;}
  async function request(path, data) {
    let token;try{token=localStorage.getItem('jarvis_token')}catch{}
    if(!token)throw new Error('not_paired');
    const response=await fetch('/api/playhouse/'+path,{
      method:data===undefined?'GET':'POST',
      headers:{'Authorization':'Bearer '+token,'Content-Type':'application/json'},
      body:data===undefined?undefined:JSON.stringify(data),
      signal:AbortSignal.timeout(6000),cache:'no-store',
    });
    if(response.status===401)throw new Error('not_paired');
    if(!response.ok){let value;try{value=await response.json()}catch{}
      throw new Error(typeof value?.detail==='string'?value.detail:'unavailable');}
    return response.json();
  }
  function failure(error) {
    if(!errorNode?.isConnected){errorNode=document.createElement('p');errorNode.setAttribute('role','status');buttons.append(errorNode);}
    const messages={
      not_paired:say('Louvri JARVIS epi konekte aparèy sa a anvan ou antre nan yon salon.', 'Open JARVIS and pair this device before joining a room.'),
      room_unavailable:say('Salon sa a fèmen oswa li ekspire. Verifye kòd la ak moun k ap òganize a.', 'This room is closed or expired. Check the code with the host.'),
      need_two_players:say('Omwen de jwè dwe antre anvan nou kòmanse.', 'At least two players must join before starting.'),
      too_many_attempts:say('Twòp tantativ. Tann yon minit epi eseye ankò.', 'Too many attempts. Wait a minute and try again.'),
      room_limit:say('Ou gen de salon aktif deja. Retounen nan salon ou a pou fèmen li.', 'You already have two active rooms. Resume your room to close it.'),
      stale_round:say('Tou a deja chanje. Rafrechi salon an.', 'The round has changed. Refresh the room.'),
      vote_locked:say('Vòt ou deja anrejistre.', 'Your vote is already recorded.'),
      already_started:say('Pati sa a deja kòmanse.', 'This game has already started.'),
      room_full:say('Salon an plen: uit jwè deja ladan l.', 'The room is full: eight players have joined.'),
    };
    errorNode.textContent=messages[error.message]||say('Salon an pa disponib kounye a. Verifye koneksyon an epi eseye ankò.', 'The room is unavailable right now. Check your connection and try again.');
  }
  function clear() {
    buttons.replaceChildren();errorNode=null;
    document.querySelectorAll('.progress i').forEach(n=>n.classList.remove('on'));
  }
  async function action(path,data) {
    if(busy)return;
    busy=true;clearTimeout(timer);const current=++epoch;
    buttons.querySelectorAll('button').forEach(b=>b.disabled=true);
    try{
      const next=await request(path,data);
      if(current!==epoch)return;
      if(next.closed){remember(null);room=null;menu(opener);return}
      room=next;remember(room.id);render();schedule();
    }catch(error){if(current===epoch)failure(error)}
    finally{if(current===epoch){busy=false;buttons.querySelectorAll('button').forEach(b=>b.disabled=false);if(room)schedule()}}
  }
  function schedule() {
    clearTimeout(timer);
    const current=epoch;
    timer=setTimeout(async()=>{
      if(current!==epoch||!room)return;
      if(busy||document.hidden){schedule();return}
      try {
        const next=await request('rooms/'+room.id);
        if(current!==epoch)return;
        const comparable=value=>JSON.stringify({...value,seconds_left:0});
        const changed=comparable(next)!==comparable(room);
        room=next;if(changed)render();
        const clock=$('#room-time');
        if(clock)clock.textContent=room.seconds_left+say(' segonn rete', ' seconds left');
        if(errorNode?.isConnected)errorNode.remove();
      }catch(error){
        if(current===epoch){
          if(error.message==='room_unavailable'||error.message==='not_paired'){
            stop();remember(null);room=null;clear();
            button(say('Retounen', 'Back'),close);
          }
          failure(error);
        }
      }
      if(current===epoch)schedule();
    },1500);
  }
  function render() {
    if(!room)return;
    // Keep focus stable while another device joins or votes.
    const focus=document.activeElement?.dataset.roomAction;
    clear();
    document.querySelectorAll('.progress i').forEach((node,index)=>node.classList.toggle('on',index<room.completed));
    label.textContent=say('Salon ', 'Room ')+room.code+' · '+room.players+say(' jwè',' players');
    title.textContent=pick(room.title);
    body.textContent=pick(room.body);
    const act=(name,choice)=>action('rooms/'+room.id+'/'+name,{round:room.round,...(choice===undefined?{}:{choice})});
    const add=(caption,handler,id)=>{button(caption,handler);buttons.lastElementChild.dataset.roomAction=id};
    if(room.phase==='lobby'){
      const code=document.createElement('div');code.className='room-invite';
      const caption=document.createElement('span');caption.textContent=say('Kòd salon an','Room code');
      const value=document.createElement('strong');value.id='room-invite-code';value.textContent=room.code;
      code.append(caption,value);buttons.append(code);
      body.textContent=say('Sou chak telefòn ki konekte ak JARVIS, ouvri Playhouse, antre nan Salon, epi tape kòd la. De a uit jwè. Salon an ekspire apre de zèdtan. Chak vòt dire 60 segonn; egalite chwazi premye opsyon an.', 'On each phone paired with JARVIS, open Playhouse, open Rooms and enter this code. Two to eight players. Rooms expire after two hours. Each vote lasts 60 seconds; ties select the first listed choice.');
      if(!room.player)add(say('Jwe sou aparèy sa a tou', 'Play on this device too'),()=>action('join',{code:room.code}),'join');
      if(room.host)add(say('Kòmanse', 'Start'),()=>act('start'),'start');
    }else if(room.phase==='voting'){
      const clock=document.createElement('p');clock.id='room-time';
      clock.textContent=room.seconds_left+say(' segonn rete', ' seconds left');buttons.append(clock);
      body.textContent+='\n\n'+room.voted+' / '+room.players+say(' vòt. Egalite: premye opsyon an.', ' votes. Tie: first listed option.');
      if(room.player && room.my_vote===null)room.choices.forEach((c,i)=>add(pick(c),()=>act('vote',i),'vote'+i));
      else {
        const status=document.createElement('p');status.setAttribute('role','status');
        status.textContent=room.my_vote!==null?say('Vòt ou anrejistre. N ap tann rezilta a.', 'Your vote is recorded. Waiting for the result.'):say('Ekran animatè a · n ap tann vòt yo.', 'Host screen · waiting for votes.');
        buttons.append(status);
      }
    }else if(room.phase==='waiting'){
      body.textContent=say('Tan an fini san okenn vòt. Animatè a kapab relouvri vòt la.', 'Time expired without any votes. The host can reopen voting.');
      if(room.host)add(say('Relouvri vòt la', 'Reopen voting'),()=>act('retry'),'retry');
    }else if(room.phase==='result'){
      body.textContent+='\n\n'+room.choices.map((c,i)=>pick(c)+': '+room.counts[i]).join('\n')+'\n\n'+say('Chwa gwoup la: ', 'Group choice: ')+pick(room.choices[room.selected]);
      if(room.host)add(say('Kontinye istwa a', 'Continue the story'),()=>act('next'),'next');
      else body.textContent+='\n'+say('Animatè a ap kontinye istwa a.', 'The host will continue the story.');
    }else if(room.phase==='complete'){
      label.textContent=say('Istwa a fini · ', 'Story complete · ')+room.completed+'/3';
    }
    add(say('Rafrechi', 'Refresh'),()=>action('rooms/'+room.id),'refresh');
    if(room.host)add(say('Fèmen salon an pou tout moun', 'Close room for everyone'),()=>act('close'),'close');
    if(focus)buttons.querySelector('[data-room-action="'+focus+'"]')?.focus();
  }
  function menu(trigger) {
    stop();room=null;opener=trigger;clear();
    title.textContent=say('Salon Fanmi · Gato a disparèt', 'Family Rooms · The Missing Cake');
    label.textContent=say('2 vòt ak yon fen · 2–8 jwè', '2 votes and an ending · 2–8 players');
    body.textContent=say('Kreye yon salon oswa antre ak kòd animatè a. Tout aparèy yo dwe konekte ak menm sèvè JARVIS la.', 'Create a room or enter your host’s code. All devices must be paired with the same JARVIS server.');
    button(say('Kreye yon salon', 'Create a room'),()=>action('rooms',{}));
    const field=document.createElement('label');field.htmlFor='room-code';field.textContent=say('Kòd salon an', 'Room code');
    const input=document.createElement('input');input.id='room-code';input.maxLength=6;input.autocomplete='off';input.autocapitalize='characters';input.spellcheck=false;
    buttons.append(field,input);
    button(say('Antre nan salon an', 'Join room'),()=>{
      const code=input.value.trim().toUpperCase();
      if(!/^[A-Z2-9]{6}$/.test(code)){failure(new Error('room_unavailable'));return}
      action('join',{code});
    });
    if(saved())button(say('Retounen nan salon mwen an', 'Resume my room'),()=>action('rooms/'+saved()));
    button(say('Retounen', 'Back'),close);
    open(trigger,'room');
  }
  return {menu,stop,render};
}
