import lantern from './games-lantern.json' with {type:'json'};
import {PACK_LIMIT, validatePack, startMystery, stepMystery, restoreMystery, options, endingFor} from './games-mystery.mjs';

const SAVE_KEY = 'jarvis_playhouse_mystery_v1';

export function installMystery({open, close, say, pick, button, title, body, label, buttons, focus}) {
  validatePack(lantern);
  let pack = lantern;
  let state = null;
  let seed = 0;
  let revealed = false;
  let generation = 0;
  let message = '';
  let storageError = false;

  try {
    const raw = localStorage.getItem(SAVE_KEY);
    if (raw && raw.length <= PACK_LIMIT * 2) {
      const saved = JSON.parse(raw);
      const checked = validatePack(saved.pack);
      const restored = saved.save ? restoreMystery(checked, saved.save) : null;
      pack = checked;
      state = restored;
      seed = restored?.seed ?? 0;
    }
  } catch {
    message = 'damaged';
  }

  function save() {
    const session = state && {players:state.players, seed:state.seed, actions:state.actions};
    try {
      localStorage.setItem(SAVE_KEY, JSON.stringify({pack, save:session}));
      storageError = false;
    } catch {
      storageError = true;
    }
  }

  function stop() {
    generation++;
    revealed = false;
  }

  function showNotice() {
    if (!message && !storageError) return;
    const notice = document.createElement('p');
    notice.setAttribute('role', 'status');
    const messages = {
      invalid:say('Fichye a pa yon pak mistè ki valab.', 'This file is not a valid mystery pack.'),
      damaged:say('Nou pa t kapab relouvri pati ki te sove a.', 'The saved adventure could not be reopened.'),
      imported:say('Pak la pare pou nou jwe.', 'The imported pack is ready to play.'),
    };
    notice.textContent = [messages[message], storageError && say(
      'Navigatè a pa t kapab sove pati a. Nou ka kontinye jwe sou paj sa a.',
      'The browser could not save this adventure. You can keep playing on this page.',
    )].filter(Boolean).join(' ');
    buttons.append(notice);
  }

  function addImport() {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.json,application/json';
    input.hidden = true;
    input.addEventListener('change', async () => {
      const file = input.files[0];
      if (!file) return;
      const pending = ++generation;
      try {
        if (file.size > PACK_LIMIT) throw new Error('pack size');
        const imported = validatePack(JSON.parse(await file.text()));
        if (pending !== generation) return;
        pack = imported;
        state = null;
        seed = 0;
        message = 'imported';
        save();
      } catch {
        if (pending !== generation) return;
        message = 'invalid';
      }
      menu(true);
    });
    buttons.append(input);
    button(say('Enpòte yon pak mistè', 'Import a mystery pack'), () => input.click());
  }

  function progress(count) {
    document.querySelectorAll('.progress i').forEach((node, index) => node.classList.toggle('on', index < count));
  }

  function menu(moveFocus = false) {
    stop();
    buttons.replaceChildren();
    progress(0);
    label.textContent = pack.id === lantern.id ? say('2–8 jwè · 15–20 minit', '2–8 players · 15–20 minutes') :
      say('2–8 jwè · Pak mistè', '2–8 players · Mystery pack');
    title.textContent = pick(pack.title);
    body.textContent = pick(pack.intro);
    if (state) button(state.phase === 'ending' ? say('Gade rezilta a', 'View last ending') :
      say('Kontinye pati ki sove a', 'Resume saved adventure'), () => render(true));
    button(say('Chwazi kantite jwè', 'Choose player count'), () => {
      stop();
      buttons.replaceChildren();
      body.textContent = say('Chak moun ap gen pwòp wòl pa li. Pase aparèy la youn bay lòt lè jwèt la mande sa.', 'Everyone gets their own role. Pass the device when prompted.');
      for (let count = 2; count <= 8; count++) {
        button(say(count + ' jwè', count + ' players'), () => {
          seed = state ? (seed + 1) >>> 0 : seed;
          state = startMystery(pack, count, seed);
          save();
          render(true);
        });
      }
      button(say('Retounen', 'Back'), () => menu(true));
      focus();
    });
    if (state) {
      button(say('Chanje pak mistè', 'Change mystery pack'), () => {
        buttons.replaceChildren();
        body.textContent = say('Chanje pak la ap ranplase pati ki sove a.', 'Loading another pack replaces your saved adventure.');
        addImport();
        button(say('Retounen', 'Back'), () => menu(true));
        focus();
      });
    } else addImport();
    if (pack !== lantern) button(say('Retounen nan Mistè Lantèn nan', 'Return to The Missing Lantern'), () => {
      buttons.replaceChildren();
      body.textContent = say('Sa ap ranplase pak ak pati ki sove a.', 'This replaces the saved pack and adventure.');
      button(say('Chaje Mistè Lantèn nan', 'Load The Missing Lantern'), () => {
        pack = lantern;
        state = null;
        seed = 0;
        message = '';
        save();
        menu(true);
      });
      button(say('Retounen', 'Back'), () => menu(true));
      focus();
    });
    showNotice();
    if (moveFocus) focus();
  }

  function act(action) {
    state = stepMystery(pack, state, action);
    revealed = false;
    save();
    render(true);
  }

  function render(moveFocus = false) {
    buttons.replaceChildren();
    const {phase, player, round} = state;
    const scene = pack.rounds[round];
    const who = say('Jwè ', 'Player ') + (player + 1);
    label.textContent = phase === 'clues' ? say('Endis prive', 'Private clues') :
      phase === 'ending' ? say('Mistè a fini', 'Mystery complete') :
      say('Desizyon ', 'Decision ') + (round + 1) + ' / ' + pack.rounds.length;
    progress(Math.ceil(round * 3 / pack.rounds.length));
    title.textContent = scene ? pick(scene.title) : pick(pack.title);
    if (phase === 'clues' || phase === 'ballot') {
      title.textContent = who;
      if (!revealed) {
        body.textContent = say('Pase aparèy la bay jwè ', 'Pass the device to player ') + (player + 1) + '. ' +
          say('Se moun sa a sèlman ki dwe gade ekran an.', 'Only that player should look at the screen.');
        button(say('Mwen pare', 'I’m ready'), () => {revealed = true; render(true);});
      } else if (phase === 'clues') {
        body.textContent = state.roles[player].map(index =>
          pick(pack.roles[index]) + '\n' + pick(pack.cases[state.caseIndex].clues[index]),
        ).join('\n\n') + '\n\n' + say('Pataje sa ki ka ede ekip la anvan ou pase aparèy la.', 'Share what can help your team before passing the device.');
        button(say('Mwen pataje endis mwen yo', 'I shared my clues'), () => act('share'));
      } else {
        body.textContent = pick(scene.title) + '\n\n' + say('Chwazi poukont ou.', 'Cast your own ballot.');
        options(pack, state).forEach((choice, index) => button(pick(choice.label), () => act(index)));
      }
    } else if (phase === 'discussion') {
      body.textContent = pick(scene.body) + '\n\n' + pick(scene.discussion) + '\n\n' +
        say('Si gen egalite, n ap kòmanse ak vòt jwè ', 'A tie is settled by the first tied ballot starting with player ') +
        (round % state.players + 1) + say(' epi n ap suiv lòd jwè yo.', ' and following player order.');
      button(say('Tout moun pale · Ann vote', 'Everyone spoke · Vote now'), () => act('vote'));
      if (state.history.length) {
        const journal = document.createElement('details');
        const heading = document.createElement('summary');
        heading.textContent = say('Desizyon nou te pran yo', 'Earlier decisions');
        const entries = document.createElement('ol');
        for (const {choice} of state.history) {
          const entry = document.createElement('li');
          entry.textContent = pick(choice.result);
          entries.append(entry);
        }
        journal.append(heading, entries);
        buttons.append(journal);
      }
    } else if (phase === 'result') {
      const {choice, counts, tied} = state.history.at(-1);
      body.textContent = pick(choice.label) + '\n\n' + pick(choice.result) + '\n\n' +
        say('Vòt pou chak chwa, nan lòd yo: ', 'Votes per choice, in order: ') + counts.join(' · ') +
        (tied ? '\n' + say('Nou sèvi ak règ egalite a.', 'The tie rule decided the outcome.') : '');
      button(say('Kontinye', 'Continue'), () => act('continue'));
    } else {
      const ending = endingFor(pack, state);
      title.textContent = pick(ending.title);
      body.textContent = pick(ending.body) + '\n\n' + say('Sa nou te deside', 'What you decided') + '\n' +
        state.history.map(({choice}, index) => (index + 1) + '. ' + pick(choice.label)).join('\n');
      button(say('Tounen nan meni mistè a', 'Back to mystery menu'), () => menu(true));
      button(say('Retounen nan jwèt yo', 'Back to games'), close);
    }
    showNotice();
    if (moveFocus) focus();
  }

  return {start(trigger) {menu(); open(trigger, 'mystery');}, stop};
}
