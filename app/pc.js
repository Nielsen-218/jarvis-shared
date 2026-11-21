"use strict";
(() => {
  const $ = id => document.getElementById(id);
  let token = localStorage.getItem("jarvis_token"), imageURL = null, pollTimer = null, active = null, hybrid = null;
  if (location.hash.startsWith("#t=")) {
    token = decodeURIComponent(location.hash.slice(3)); localStorage.setItem("jarvis_token", token);
    history.replaceState(null, "", location.pathname);
  }
  async function api(path, method = "GET", body) {
    if (!token) throw Error("Open JARVIS and pair this browser first.");
    const r = await fetch(path, {method, headers:{Authorization:`Bearer ${token}`, "Content-Type":"application/json"},
      body:body === undefined ? undefined : JSON.stringify(body), cache:"no-store"});
    if (!r.ok) { const e = await r.json().catch(() => ({})); const error=Error(e.detail || `PC request failed (${r.status})`); error.status=r.status; throw error; }
    return r.json();
  }
  const say = (msg, bad) => { $("result").textContent = msg; $("result").className = bad ? "err" : ""; };
  const showError = e => say(e.message, true);
  function showImage(blob) {
    if (imageURL) URL.revokeObjectURL(imageURL);
    imageURL = URL.createObjectURL(blob); $("screen").src = imageURL; $("screen").hidden = false;
  }
  function render(job) {
    $("task").textContent = `${job.state.replaceAll("_", " ")} · ${job.detail || job.name || ""}`;
    const blocks = job.blocks || [];
    say(blocks.filter(b => b.type === "text").map(b => b.text).join("\n") || job.error || job.detail || job.state, !!job.error && job.state !== "done");
    hybrid=job.hybrid || null;
    $("review").hidden=!hybrid || !["needs_review","needs_budget"].includes(hybrid.state);
    $("workflow").hidden=!(hybrid?.verified && hybrid.run_id);
    $("approve-plan").hidden=!(hybrid?.state==="needs_review" && hybrid.approval_id);
    $("budget").hidden=!(hybrid?.state==="needs_budget" && hybrid.approval_id);
    $("review-note").textContent=hybrid?.detail || "";
    $("plan").textContent=hybrid?.plan ? JSON.stringify(hybrid.plan,null,2) : hybrid?.ratecard ? JSON.stringify(hybrid.ratecard,null,2) : "";
    for (const b of blocks) if (b.type === "image" && b.source?.type === "base64" && ["image/png","image/jpeg"].includes(b.source.media_type)) {
      const bytes = Uint8Array.from(atob(b.source.data), c => c.charCodeAt(0)); showImage(new Blob([bytes],{type:b.source.media_type}));
    }
  }
  async function poll() {
    if (!active) return;
    try {
      const job = await api(`/api/pc/tasks/${encodeURIComponent(active)}`); render(job);
      if (["running","cancel_requested"].includes(job.state)) pollTimer = setTimeout(poll,1000);
      else { active = null; sessionStorage.removeItem("jarvis_pc_job"); }
    } catch (e) {
      say(`${e.message} Task ${active} may still be running. Check connection before repeating it.`, true);
      if(e.status===404){active=null;sessionStorage.removeItem("jarvis_pc_job");}
    }
  }
  async function status() {
    $("refresh").disabled = true; $("connection").textContent = "Checking…"; $("connection").className = "ds-badge";
    try {
      const s = await api("/api/pc/status"); $("connection").textContent="PC connected"; $("connection").className="ds-badge ok";
      $("identity").textContent=`Windows JARVIS · ${s.admin ? "Administrator" : "Standard user"}`;
      const recent = s.recent?.[s.recent.length-1];
      if (s.active) { active=s.active.id; sessionStorage.setItem("jarvis_pc_job",active); clearTimeout(pollTimer); poll(); }
      else if (active) { clearTimeout(pollTimer); poll(); }
      else if(recent) render(recent);
      else $("task").textContent="No remote task running.";
      if(!s.active){
        const requests=await api("/api/pc/policy/pending"); const p=requests.pending?.[0];
        if(p){const state=p.kind==="cloud"?"needs_budget":"needs_review";
          render({state,hybrid:{...p.data,state,approval_id:p.id,detail:p.kind==="cloud"?
            (p.data.local_planning?.detail ? p.data.local_planning.detail+" " : "")+"A PC task needs an explicit budget for one cloud planning call.":"Review this exact prepared PC plan."}});}
      }
    } catch (e) { $("connection").textContent="PC unavailable"; $("connection").className="ds-badge danger"; $("identity").textContent=`${String(e.message).replace(/[.!?]?$/, ".")} Is the PC awake and JARVIS running? Then check the connection again.`; }
    finally { $("refresh").disabled = false; }
  }
  async function run(name, args, message) {
    if (active) return showError(Error("A task is already being tracked. Check status or stop it first."));
    const rid=crypto.randomUUID();
    // Save before POST: if its response is lost, reconnect by ID instead of repeating a side effect.
    active=rid; sessionStorage.setItem("jarvis_pc_job",rid); say("Sending to your PC…");
    try {
      const job=await api("/api/pc/tasks","POST",{name,arguments:args,request_id:rid,user_message:message});
      active=job.id; sessionStorage.setItem("jarvis_pc_job",active); render(job); poll();
    } catch (e) {
      say(`${e.message} Check connection before repeating this task.`, true);
      if([400,401,403,409,429].includes(e.status)){active=null;sessionStorage.removeItem("jarvis_pc_job");}
    }
  }
  $("refresh").onclick=status;
  async function captureScreen(){ try {
    if (!token) throw Error("Open JARVIS and pair this browser first.");
    say("Getting your PC screen…"); $("capture").disabled=true;
    const r=await fetch("/api/pc/screenshot",{headers:{Authorization:`Bearer ${token}`},cache:"no-store"});
    if (!r.ok) throw Error((await r.json()).detail || "PC screen unavailable.");
    showImage(await r.blob()); say("PC screenshot received.");
  } catch(e) {showError(e);} finally {$("capture").disabled=false;} }
  $("capture").onclick=captureScreen;
  // A screenshot request typed into the command box shouldn't be planned — grab the screen directly.
  const isScreenshot = t =>
    (/screen\s?shot|screen\s?grab|screen\s?cap/i.test(t) && /\b(pc|computer|desktop|screen|monitor)\b/i.test(t))
    || /^(?:show|see|view|let me see|lemme see|what'?s on|what is on)(?: me)?(?: the| my)?\s+(?:pc|computer|desktop)(?:\s+screen)?\s*(?:please|plz|pls)?[.!?]*$/i.test(t.trim());
  $("stop").onclick=async () => {try{const j=await api("/api/pc/cancel","POST",{});say(j.detail || j.state);}catch(e){showError(e);}};
  $("open").onsubmit=e=>{e.preventDefault();const app=$("app").value.trim();run("open_app",{app},`Open ${app} on my PC`);};
  $("website").onsubmit=e=>{e.preventDefault();const url=$("url").value.trim();run("open_website",{url},`Open ${url} on my PC`);};
  $("control").onsubmit=e=>{e.preventDefault();const task=$("command").value.trim();
    if(isScreenshot(task)){captureScreen();return;}
    run("control_computer",{task},task);};
  $("play").onclick=()=>run("media_control",{action:"play_pause"},"Play or pause media on my PC");
  $("mute").onclick=()=>run("media_control",{action:"mute"},"Toggle mute on my PC");
  async function approve(kind,usd){
    if(!hybrid?.approval_id || !hybrid?.task) return;
    const task=hybrid.task,id=hybrid.approval_id;
    clearTimeout(pollTimer);active=null;sessionStorage.removeItem("jarvis_pc_job");
    try{await api("/api/pc/policy/approve","POST",{id,kind,...(kind==="cloud"?{usd}:{})});
      await run("control_computer",{task},task);
    }catch(e){showError(e);}
  }
  $("approve-plan").onclick=()=>approve("plan");
  $("budget").onsubmit=e=>{e.preventDefault();const usd=Number($("usd").value);if(usd>0&&usd<=1)approve("cloud",usd);};
  $("save-workflow").onsubmit=async e=>{e.preventDefault();if(!hybrid?.verified)return;
    try{await api("/api/pc/workflows","POST",{run_id:hybrid.run_id,name:$("workflow-name").value,trigger:$("workflow-trigger").value});say("Verified workflow saved. Use its exact phrase, or say run PC workflow followed by the phrase.");}catch(err){showError(err);}};
  $("usage").onclick=async()=>{const b=$("usage");b.disabled=true;$("usage-result").textContent="Loading…";try{$("usage-result").textContent=JSON.stringify(await api("/api/pc/usage"),null,2);}catch(e){$("usage-result").textContent="";showError(e);}finally{b.disabled=false;}};
  active=sessionStorage.getItem("jarvis_pc_job"); status();
  document.addEventListener("visibilitychange",()=>{if(!document.hidden)status();});
  window.addEventListener("pagehide",()=>{clearTimeout(pollTimer);if(imageURL)URL.revokeObjectURL(imageURL);});
})();
