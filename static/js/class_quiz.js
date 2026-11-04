// Class-quiz client helpers. The SERVER owns the deadline, authorization and grading;
// this script only renders the timer, autosaves, and reports browser events.
// Tab/window monitoring is a deterrent, not a guarantee: a determined student can use
// a second device. Teachers should treat pauses as signals, not proof.
function _csrf(form){ return form.querySelector('input[name=csrfmiddlewaretoken]').value; }
function _post(url, form, data){
  const body = new URLSearchParams(data);
  return fetch(url, {method:'POST', headers:{'X-CSRFToken':_csrf(form), 'Content-Type':'application/x-www-form-urlencoded'}, body, credentials:'same-origin'});
}
function initClassQuiz(form){
  let left = parseInt(form.dataset.remaining, 10);
  const el = document.getElementById('time'), state = document.getElementById('savestate');
  const fmt = s => String(Math.floor(s/60)).padStart(2,'0')+':'+String(s%60).padStart(2,'0');
  el.textContent = fmt(left);
  const tick = setInterval(()=>{ left--; el.textContent = fmt(Math.max(left,0));
    if (left <= 60) el.style.color = 'var(--bad)';
    if (left <= 0){ clearInterval(tick); form.submit(); } }, 1000);
  form.querySelectorAll('.opt').forEach(label=>label.addEventListener('click', ()=>{
    const group = label.querySelector('input').name;
    form.querySelectorAll(`input[name="${group}"]`).forEach(i=>i.closest('.opt').classList.remove('sel'));
    label.classList.add('sel'); label.querySelector('input').checked = true; save(); }));
  async function save(){
    const data = new FormData(form); data.delete('csrfmiddlewaretoken');
    try{ const r = await _post(form.dataset.saveUrl, form, data);
      if (r.status === 409) { location.reload(); return; }
      state.textContent = 'Saved '+new Date().toLocaleTimeString(); }
    catch(e){ state.textContent = 'Offline — will retry.'; }
  }
  form.querySelectorAll('textarea').forEach(t=>t.addEventListener('change', save));
  setInterval(save, 15000);
  // Server-side sync: pause/expiry decided by the server, not the browser.
  setInterval(async ()=>{ try{ const r = await fetch(form.dataset.statusUrl, {credentials:'same-origin'}); const j = await r.json();
    if (j.status !== 'in_progress') location.reload(); else if (typeof j.remaining === 'number') left = j.remaining; }catch(e){} }, 10000);
  if (form.dataset.supervised === '1'){
    let reported = false;
    const report = async kind => { if (reported) return; reported = true; await save().catch(()=>{});
      try{ await _post(form.dataset.eventUrl, form, {kind}); }catch(e){} location.reload(); };
    document.addEventListener('visibilitychange', ()=>{ if (document.hidden) report('tab_hidden'); });
    window.addEventListener('blur', ()=>report('window_blur'));
  }
}
function pollForAuthorization(box){
  const state = document.getElementById('vstate');
  setInterval(async ()=>{ try{ const r = await fetch(box.dataset.statusUrl, {credentials:'same-origin'}); const j = await r.json();
    if (j.status === 'in_progress') { state.textContent = 'Verified — starting…'; location.href = box.dataset.takeUrl; }
    else if (j.status === 'submitted') location.href = box.dataset.takeUrl; }catch(e){} }, 3000);
}
