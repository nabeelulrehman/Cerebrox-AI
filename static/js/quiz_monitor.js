(() => {
  const root = document.querySelector('[data-quiz-monitor]');
  if (!root || !window.LivekitClient || root.dataset.started) return;
  root.dataset.started = 'true';
  const {Room, RoomEvent} = window.LivekitClient;
  const status = root.querySelector('[data-monitor-status]');
  const error = root.querySelector('[data-monitor-error]');
  const csrf = document.querySelector('[name=csrfmiddlewaretoken]')?.value || '';
  const room = new Room({adaptiveStream:true, dynacast:true});
  const tiles = new Map();
  const count = () => { const node=root.querySelector('[data-monitor-count]'); if(node) node.textContent=String(tiles.size); };
  const label = participant => participant.name || participant.identity;
  const addTile = participant => {
    const grid=root.querySelector('[data-monitor-grid]'); if(!grid) return null;
    let tile=tiles.get(participant.identity); if(tile) return tile;
    tile=document.createElement('article'); tile.className='glass p-3'; tile.dataset.identity=participant.identity;
    const heading=document.createElement('strong'); heading.textContent=label(participant);
    const video=document.createElement('div'); video.className='min-h-40 rounded bg-black mt-2'; video.dataset.video='';
    const participantState=document.createElement('p'); participantState.className='text-xs mt-1'; participantState.textContent='Connected';
    tile.append(heading,video,participantState); grid.append(tile); tiles.set(participant.identity,tile); count(); return tile;
  };
  const attach = (track, participant) => {
    const tile=addTile(participant); if(!tile) return;
    const node=track.attach(); node.autoplay=true; node.playsInline=true; node.className='w-full rounded';
    tile.querySelector('[data-video]').replaceChildren(node);
  };
  const markConnection = (identity, connected) => { const node=document.querySelector(`[data-student-identity="${CSS.escape(identity)}"] [data-stream-status]`); if(node) node.textContent=connected?'Camera connected':'Camera disconnected'; };
  const detachParticipant = participant => { const tile=tiles.get(participant.identity); if(tile) tile.remove(); tiles.delete(participant.identity); markConnection(participant.identity,false); count(); };
  room.on(RoomEvent.TrackSubscribed, (track, publication, participant) => attach(track, participant));
  room.on(RoomEvent.TrackUnsubscribed, track => track.detach().forEach(node=>node.remove()));
  room.on(RoomEvent.ParticipantConnected, participant => { addTile(participant); markConnection(participant.identity,true); });
  room.on(RoomEvent.ParticipantDisconnected, detachParticipant);
  room.on(RoomEvent.Disconnected, () => { status.textContent='Disconnected. Reconnect to continue monitoring.'; });
  const connect = async () => {
    status.textContent='Connecting securely...';
    const response=await fetch(root.dataset.tokenUrl,{method:'POST',headers:{'X-CSRFToken':csrf,'Content-Type':'application/x-www-form-urlencoded'},body:new URLSearchParams({csrfmiddlewaretoken:csrf})});
    const data=await response.json(); if(!response.ok) throw new Error(data.error||'Could not authorize the monitoring connection.');
    await room.connect(data.url,data.token);
    if(root.dataset.quizMonitor==='student') {
      const track=await window.LivekitClient.createLocalVideoTrack({resolution:{width:640,height:360}, facingMode:'user'});
      const video=root.querySelector('[data-local-camera]'); if(video) track.attach(video);
      await room.localParticipant.publishTrack(track);
      status.textContent='Live camera connected. Your teacher can see this video; it is not recorded.';
      room.on(RoomEvent.Disconnected,()=>track.stop());
    } else {
      room.remoteParticipants.forEach(participant=>{addTile(participant);markConnection(participant.identity,true);participant.trackPublications.forEach(publication=>{if(publication.track)attach(publication.track,participant);});});
      status.textContent='Monitoring live participants. Streams are not recorded.';
    }
  };
  connect().catch(err=>{status.textContent='Camera or monitoring connection unavailable.';error.textContent=err.message;});
})();
