// Teacher QR scanner. Uses the browser BarcodeDetector API when present; otherwise the
// teacher pastes the code shown under the student's QR. All checks happen server-side.
(function(){
  const csrf = document.querySelector('input[name=csrfmiddlewaretoken]').value;
  const result = document.getElementById('result');
  async function verify(token){
    result.textContent = 'Verifying…';
    try{
      const r = await fetch('/class-quizzes/teach/verify/', {method:'POST', credentials:'same-origin',
        headers:{'X-CSRFToken':csrf,'Content-Type':'application/json'}, body:JSON.stringify({token})});
      const j = await r.json();
      result.style.color = j.ok ? 'var(--good, #34d399)' : 'var(--bad)';
      result.textContent = j.ok ? `✔ ${j.student} authorized for “${j.quiz}”${j.resumed?' (resumed)':''}` : (j.error || 'Failed');
    }catch(e){ result.style.color='var(--bad)'; result.textContent='Network error.'; }
  }
  document.getElementById('manual-go').addEventListener('click', ()=>{ const v=document.getElementById('manual').value.trim(); if(v) verify(v); });
  const note = document.getElementById('cam-note'), video = document.getElementById('cam');
  document.getElementById('cam-start').addEventListener('click', async ()=>{
    if (!('BarcodeDetector' in window)) { note.textContent = 'This browser cannot scan QR codes — paste the code instead.'; return; }
    try{
      const stream = await navigator.mediaDevices.getUserMedia({video:{facingMode:'environment'}});
      video.srcObject = stream; await video.play();
      const det = new BarcodeDetector({formats:['qr_code']}); let busy = false, last = '';
      setInterval(async ()=>{ if (busy) return; busy = true;
        try{ const codes = await det.detect(video); if (codes.length && codes[0].rawValue !== last){ last = codes[0].rawValue; await verify(last); } }catch(e){}
        busy = false; }, 600);
    }catch(e){ note.textContent = 'Camera unavailable: '+e.message; }
  });
})();
