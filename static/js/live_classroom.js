(() => {
  const root = document.querySelector('[data-classroom]');
  if (!root || !window.LivekitClient || root.dataset.livekitInitialized === 'true') return;
  root.dataset.livekitInitialized = 'true';

  const state = root.querySelector('[data-connection-state]');
  const grid = root.querySelector('[data-video-grid]');
  const screenStage = root.querySelector('[data-screen-share-stage]');
  const screenVideo = root.querySelector('[data-screen-share-video]');
  const screenTitle = root.querySelector('[data-screen-share-title]');
  const screenStatus = root.querySelector('[data-screen-share-status]');
  const participants = root.querySelector('[data-participant-list]');
  const count = root.querySelector('[data-participant-count]');
  const error = root.querySelector('[data-classroom-error]');
  const chatForm = root.querySelector('[data-chat-form]');
  const chatInput = chatForm.querySelector('input[name="message"]');
  const chatButton = chatForm.querySelector('button[type="submit"]');
  const chatMessages = root.querySelector('[data-chat-messages]');
  const recordingStatus = root.querySelector('[data-recording-status]');
  const recordingActions = root.querySelector('[data-recording-actions]');
  const recordingNotice = root.querySelector('[data-recording-notice]');
  const controls = {
    microphone: root.querySelector('[data-microphone]'),
    camera: root.querySelector('[data-camera]'),
    screen: root.querySelector('[data-screen-share]'),
    hand: root.querySelector('[data-raise-hand]'),
  };
  const csrf = chatForm.querySelector('input[name="csrfmiddlewaretoken"]').value;
  const { Room, RoomEvent, Track } = window.LivekitClient;
  const teacherIdentity = root.dataset.teacherIdentity;
  const room = new Room({ adaptiveStream: true, dynacast: true });
  const tiles = new Map();
  const attachedTracks = new Map();
  const raisedHands = new Set();
  let handRaised = false;
  let leaving = false;
  let galleryLayoutFrame = 0;
  let previousRecordingState = null;

  const showError = (message) => { error.textContent = message; };
  const participantName = (participant) => participant.name || participant.identity;
  const sourceOf = (publication, track) => publication?.source || track?.source;
  const trackKey = (track, participant) => `${participant.identity}:${track.sid}`;
  const cameraLabel = controls.camera.querySelector('[data-camera-label]');

  function updateGalleryLayout() {
    const { width, height } = grid.getBoundingClientRect();
    if (width <= 0 || height <= 0) return;
    const tileCount = Math.max(1, grid.querySelectorAll('.classroom-tile').length);
    const styles = window.getComputedStyle(grid);
    const columnGap = parseFloat(styles.columnGap) || 0;
    const rowGap = parseFloat(styles.rowGap) || 0;
    let best = { columns: 1, rows: tileCount, size: 0, spareSlots: Infinity };

    for (let columns = 1; columns <= tileCount; columns += 1) {
      const rows = Math.ceil(tileCount / columns);
      const cellWidth = (width - columnGap * (columns - 1)) / columns;
      const cellHeight = (height - rowGap * (rows - 1)) / rows;
      const size = Math.max(0, Math.floor(Math.min(cellWidth, cellHeight)));
      const spareSlots = columns * rows - tileCount;
      if (size > best.size || (size === best.size && spareSlots < best.spareSlots)) {
        best = { columns, rows, size, spareSlots };
      }
    }

    grid.style.setProperty('--gallery-columns', String(best.columns));
    grid.style.setProperty('--gallery-rows', String(best.rows));
    grid.style.setProperty('--gallery-tile-size', `${best.size}px`);
  }

  function scheduleGalleryLayout() {
    if (galleryLayoutFrame) cancelAnimationFrame(galleryLayoutFrame);
    galleryLayoutFrame = requestAnimationFrame(() => {
      galleryLayoutFrame = 0;
      updateGalleryLayout();
    });
  }

  if ('ResizeObserver' in window) {
    const galleryResizeObserver = new ResizeObserver(scheduleGalleryLayout);
    galleryResizeObserver.observe(grid);
  } else {
    window.addEventListener('resize', scheduleGalleryLayout);
  }

  function ensureTile(participant) {
    let tile = tiles.get(participant.identity);
    if (tile) return tile;
    tile = document.createElement('article');
    tile.className = 'classroom-tile';
    tile.dataset.participant = participant.identity;
    const label = document.createElement('span');
    label.className = 'classroom-tile-name';
    label.textContent = `${participantName(participant)}${participant.identity === teacherIdentity ? ' · Teacher' : ''}`;
    const placeholder = document.createElement('div');
    placeholder.className = 'classroom-camera-placeholder';
    placeholder.dataset.cameraPlaceholder = '';
    const avatar = document.createElement('span');
    avatar.className = 'classroom-camera-avatar';
    avatar.dataset.cameraAvatar = '';
    avatar.textContent = participantName(participant).trim().split(/\s+/).slice(0, 2)
      .map((part) => part[0] || '').join('').toUpperCase();
    const cameraStatus = document.createElement('span');
    cameraStatus.textContent = 'Camera is off';
    placeholder.append(avatar, cameraStatus);
    placeholder.hidden = true;
    tile.append(placeholder);
    tile.append(label);
    grid.append(tile);
    tiles.set(participant.identity, tile);
    // The empty-state paragraph is a grid item. Leaving it beside camera-off
    // participants creates an implicit row and places their fixed tiles below
    // the gallery's measured square grid area.
    root.querySelector('[data-empty-state]')?.remove();
    scheduleGalleryLayout();
    return tile;
  }

  function setCameraPlaceholder(tile, visible) {
    const placeholder = tile.querySelector('[data-camera-placeholder]');
    if (placeholder) placeholder.hidden = !visible;
  }

  function clearScreenShare(identity) {
    if (screenStage.dataset.identity && identity && screenStage.dataset.identity !== identity) return;
    screenVideo.replaceChildren();
    screenTitle.textContent = 'Screen share';
    screenStatus.textContent = '';
    screenStage.dataset.identity = '';
    screenStage.hidden = true;
  }

  function removeAttachedTrack(track, participant) {
    const key = trackKey(track, participant);
    const record = attachedTracks.get(key);
    if (!record) return;
    track.detach(record.element);
    record.element.remove();
    attachedTracks.delete(key);
    if (record.type === 'camera') {
      const tile = tiles.get(participant.identity);
      if (tile) setCameraPlaceholder(tile, true);
    }
    if (record.type === 'screen') clearScreenShare(participant.identity);
  }

  function removeParticipantTracks(participant, type) {
    for (const record of [...attachedTracks.values()]) {
      if (record.identity === participant.identity && record.type === type) {
        removeAttachedTrack(record.track, participant);
      }
    }
  }

  function attachCamera(track, participant) {
    const key = trackKey(track, participant);
    if (attachedTracks.has(key)) return;
    const tile = ensureTile(participant);
    const existing = [...attachedTracks.entries()].find(([, record]) =>
      record.identity === participant.identity && record.type === 'camera');
    if (existing) removeAttachedTrack(existing[1].track, participant);
    const video = document.createElement('video');
    video.autoplay = true;
    video.playsInline = true;
    video.muted = participant === room.localParticipant;
    video.dataset.trackSid = track.sid;
    video.dataset.cameraTrack = '';
    if (participant === room.localParticipant) video.dataset.localCamera = '';
    track.attach(video);
    tile.insertBefore(video, tile.firstChild);
    setCameraPlaceholder(tile, false);
    attachedTracks.set(key, { element: video, identity: participant.identity, track, type: 'camera' });
    root.querySelector('[data-empty-state]')?.remove();
  }

  function attachScreenShare(track, participant) {
    const key = trackKey(track, participant);
    if (attachedTracks.has(key)) return;
    for (const [oldKey, record] of [...attachedTracks.entries()]) {
      if (record.type === 'screen' && oldKey !== key) {
        removeAttachedTrack(record.track, { identity: record.identity });
      }
    }
    const video = document.createElement('video');
    video.autoplay = true;
    video.playsInline = true;
    video.controls = false;
    video.dataset.trackSid = track.sid;
    video.dataset.screenTrack = '';
    if (participant === room.localParticipant) {
      video.muted = true;
      video.dataset.localScreen = '';
    }
    track.attach(video);
    screenVideo.replaceChildren(video);
    screenTitle.textContent = participant === room.localParticipant
      ? 'Your screen preview'
      : `${participantName(participant)} is sharing a screen`;
    screenStatus.textContent = participant === room.localParticipant ? 'Shared with the class' : '';
    screenStage.dataset.identity = participant.identity;
    screenStage.hidden = false;
    attachedTracks.set(key, { element: video, identity: participant.identity, track, type: 'screen' });
    root.querySelector('[data-empty-state]')?.remove();
  }

  function removeParticipantTracksForType(type) {
    for (const record of [...attachedTracks.values()]) {
      if (record.type === type) {
        removeAttachedTrack(record.track, { identity: record.identity });
      }
    }
  }

  function attachTrack(track, publication, participant) {
    const source = sourceOf(publication, track);
    if (track.kind === Track.Kind.Video) {
      if (source === Track.Source.ScreenShare) attachScreenShare(track, participant);
      else attachCamera(track, participant);
      return;
    }
    if (track.kind !== Track.Kind.Audio || participant === room.localParticipant) return;
    const key = trackKey(track, participant);
    if (attachedTracks.has(key)) return;
    const audio = document.createElement('audio');
    audio.autoplay = true;
    audio.dataset.trackSid = track.sid;
    track.attach(audio);
    ensureTile(participant).append(audio);
    attachedTracks.set(key, { element: audio, identity: participant.identity, track, type: 'audio' });
  }

  function updateLocalControlState() {
    const publications = [...room.localParticipant.trackPublications.values()];
    const cameraOn = room.localParticipant.isCameraEnabled;
    const sharing = room.localParticipant.isScreenShareEnabled;
    controls.camera.dataset.enabled = String(cameraOn);
    controls.camera.setAttribute('aria-pressed', String(cameraOn));
    controls.camera.classList.toggle('is-active', cameraOn);
    cameraLabel.textContent = cameraOn ? 'Disable camera' : 'Enable camera';
    controls.screen.dataset.enabled = String(sharing);
    controls.screen.setAttribute('aria-pressed', String(sharing));
    controls.screen.classList.toggle('is-active', sharing);
    controls.screen.textContent = sharing ? 'Stop sharing' : 'Share screen';
    syncCameraTile(room.localParticipant, publications);
  }

  function syncCameraTile(participant, publications = [...participant.trackPublications.values()]) {
    const cameraPublication = publications.find((publication) => publication.source === Track.Source.Camera);
    const cameraTrack = [...attachedTracks.values()].find((record) =>
      record.identity === participant.identity && record.type === 'camera');
    const enabled = Boolean(cameraTrack && cameraPublication && !cameraPublication.isMuted
      && (participant !== room.localParticipant || participant.isCameraEnabled));
    const tile = tiles.get(participant.identity);
    if (!tile) return;
    if (cameraTrack) cameraTrack.element.hidden = !enabled;
    setCameraPlaceholder(tile, !enabled);
  }

  function refreshParticipants() {
    participants.replaceChildren();
    const roster = [room.localParticipant, ...room.remoteParticipants.values()];
    count.textContent = String(roster.length);
    for (const participant of roster) {
      const tile = ensureTile(participant);
      const item = document.createElement('li');
      item.className = 'classroom-participant';
      item.dataset.identity = participant.identity;
      const suffix = participant.identity === room.localParticipant.identity ? ' (You)' : '';
      const teacher = participant.identity === teacherIdentity ? ' · Teacher' : '';
      const muted = [...participant.trackPublications.values()].some((publication) =>
        publication.kind === Track.Kind.Audio && publication.isMuted);
      item.textContent = `${participantName(participant)}${teacher}${suffix}${muted ? ' · Microphone muted' : ''}${raisedHands.has(participant.identity) ? ' ✋' : ''}`;
      participants.append(item);
      for (const publication of participant.trackPublications.values()) {
        if (publication.track) attachTrack(publication.track, publication, participant);
      }
      syncCameraTile(participant);
    }
    scheduleGalleryLayout();
  }

  function removeParticipant(participant) {
    for (const record of [...attachedTracks.values()]) {
      if (record.identity === participant.identity) removeAttachedTrack(record.track, participant);
    }
    tiles.get(participant.identity)?.remove();
    tiles.delete(participant.identity);
    refreshParticipants();
    if (!tiles.size) {
      const empty = document.createElement('p');
      empty.className = 'classroom-placeholder';
      empty.dataset.emptyState = '';
      empty.textContent = 'Waiting for participants…';
      grid.append(empty);
    }
  }

  function appendMessage(sender, text, timestamp) {
    const item = document.createElement('li');
    item.className = 'classroom-message';
    item.append(document.createTextNode(`${sender}: ${text}`));
    const time = document.createElement('time');
    time.dateTime = timestamp;
    time.textContent = new Date(timestamp).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
    item.append(time);
    chatMessages.append(item);
    item.scrollIntoView({ block: 'nearest' });
  }

  function renderRecordingActions(data) {
    if (!recordingActions) return;
    const teacher = recordingActions.dataset.isTeacher === 'true';
    const unavailable = recordingActions.querySelector('[data-recording-unavailable]');
    if (unavailable) unavailable.hidden = data.recording_ready;
    recordingActions.querySelectorAll('[data-recording-start-form], [data-recording-stop-form], [data-recording-progress], [data-recording-play]')
      .forEach((node) => node.remove());
    if (data.status === 'completed' && data.recording_ready) {
      const link = document.createElement('a');
      link.className = 'classroom-control';
      link.dataset.recordingPlay = '';
      link.href = root.dataset.recordingPlayUrl;
      link.textContent = 'Play recording';
      recordingActions.append(link);
    }
    if (teacher && data.recording_ready) {
      if (data.status === 'starting' || data.status === 'stopping') {
        const progress = document.createElement('span');
        progress.className = 'classroom-control';
        progress.dataset.recordingProgress = '';
        progress.textContent = `Recording ${data.label.toLowerCase()}…`;
        recordingActions.append(progress);
      } else {
        const form = document.createElement('form');
        form.method = 'post';
        form.action = data.status === 'recording'
          ? root.dataset.recordingStopUrl : root.dataset.recordingStartUrl;
        form.dataset[data.status === 'recording' ? 'recordingStopForm' : 'recordingStartForm'] = '';
        const token = document.createElement('input');
        token.type = 'hidden';
        token.name = 'csrfmiddlewaretoken';
        token.value = csrf;
        form.append(token);
        if (data.status === 'recording') {
          const stop = document.createElement('button');
          stop.className = 'classroom-control classroom-end';
          stop.textContent = 'Stop recording';
          form.append(stop);
        } else {
          const label = document.createElement('label');
          const consent = document.createElement('input');
          consent.type = 'checkbox';
          consent.name = 'recording_consent';
          consent.required = true;
          label.append(consent, document.createTextNode(' I have informed everyone and obtained their consent to record.'));
          const start = document.createElement('button');
          start.className = 'classroom-control';
          start.textContent = 'Start recording';
          form.append(label, start);
        }
        recordingActions.append(form);
      }
    }
    const currentError = recordingActions.querySelector('[data-recording-error]');
    if (currentError) currentError.remove();
    if (teacher && data.error) {
      const paragraph = document.createElement('p');
      paragraph.className = 'classroom-error';
      paragraph.dataset.recordingError = '';
      paragraph.textContent = data.error;
      recordingActions.append(paragraph);
    }
  }

  async function refreshRecordingStatus() {
    if (!recordingStatus) return;
    try {
      const response = await fetch(root.dataset.recordingStatusUrl, {
        credentials: 'same-origin',
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
        cache: 'no-store',
      });
      if (!response.ok) return;
      const data = await response.json();
      recordingStatus.textContent = data.label;
      renderRecordingActions(data);
      if ((data.status === 'starting' || data.status === 'recording')
          && previousRecordingState !== data.status) {
        const announcement = data.status === 'recording'
          ? 'This class is now being recorded.' : 'The teacher is starting a class recording.';
        recordingNotice.textContent = announcement;
        recordingNotice.hidden = false;
        appendMessage('Classroom', announcement, new Date().toISOString());
      } else if (data.status !== 'starting' && data.status !== 'recording') {
        recordingNotice.hidden = true;
      }
      previousRecordingState = data.status;
    } catch {
      // Recording state remains available through the server-rendered status.
    }
  }

  if (recordingStatus) {
    refreshRecordingStatus();
    window.setInterval(refreshRecordingStatus, 3000);
  }

  function setConnected(enabled) {
    Object.values(controls).forEach((button) => { button.disabled = !enabled; });
    chatInput.disabled = !enabled;
    chatButton.disabled = !enabled;
  }

  room.on(RoomEvent.TrackSubscribed, (track, publication, participant) => {
    attachTrack(track, publication, participant);
    refreshParticipants();
  });
  room.on(RoomEvent.TrackUnsubscribed, (track, _publication, participant) => {
    removeAttachedTrack(track, participant);
    refreshParticipants();
  });
  room.on(RoomEvent.LocalTrackPublished, (publication, participant) => {
    if (publication.track) attachTrack(publication.track, publication, participant);
    updateLocalControlState();
    refreshParticipants();
  });
  room.on(RoomEvent.LocalTrackUnpublished, (publication, participant) => {
    if (publication.track) removeAttachedTrack(publication.track, participant);
    if (publication.source === Track.Source.ScreenShare) {
      removeParticipantTracks(participant, 'screen');
      clearScreenShare(participant.identity);
    } else if (publication.source === Track.Source.Camera) {
      removeParticipantTracks(participant, 'camera');
    }
    updateLocalControlState();
    refreshParticipants();
  });
  room.on(RoomEvent.TrackPublished, refreshParticipants);
  room.on(RoomEvent.TrackUnpublished, (publication, participant) => {
    if (publication.track) removeAttachedTrack(publication.track, participant);
    if (publication.source === Track.Source.ScreenShare) removeParticipantTracks(participant, 'screen');
    if (publication.source === Track.Source.Camera) removeParticipantTracks(participant, 'camera');
    refreshParticipants();
  });
  room.on(RoomEvent.TrackMuted, refreshParticipants);
  room.on(RoomEvent.TrackUnmuted, refreshParticipants);
  room.on(RoomEvent.TrackMuted, (_publication, participant) => {
    if (participant === room.localParticipant) updateLocalControlState();
  });
  room.on(RoomEvent.TrackUnmuted, (_publication, participant) => {
    if (participant === room.localParticipant) updateLocalControlState();
  });
  room.on(RoomEvent.ParticipantConnected, refreshParticipants);
  room.on(RoomEvent.ParticipantDisconnected, removeParticipant);
  room.on(RoomEvent.Reconnecting, () => { state.textContent = 'Reconnecting…'; });
  room.on(RoomEvent.Reconnected, () => { state.textContent = 'Connected'; });
  room.on(RoomEvent.Disconnected, () => {
    state.textContent = 'Disconnected';
    setConnected(false);
  });
  room.on(RoomEvent.DataReceived, (data, participant) => {
    let message;
    try {
      message = JSON.parse(new TextDecoder().decode(data));
    } catch {
      return;
    }
    if (message.type === 'chat' && typeof message.text === 'string') {
      appendMessage(participantName(participant), message.text, message.timestamp);
    } else if (message.type === 'hand' && typeof message.raised === 'boolean') {
      if (message.raised) raisedHands.add(participant.identity);
      else raisedHands.delete(participant.identity);
      refreshParticipants();
    }
  });

  async function toggleDevice(button, enabled, label, setter) {
    try {
      await setter(!enabled);
      button.dataset.enabled = String(!enabled);
      button.textContent = !enabled ? `Disable ${label}` : `Enable ${label}`;
    } catch (deviceError) {
      showError(deviceError.message || `Could not change ${label} settings. Check browser permissions.`);
    }
  }

  controls.microphone.addEventListener('click', () => toggleDevice(
    controls.microphone, controls.microphone.dataset.enabled === 'true', 'microphone',
    (enabled) => room.localParticipant.setMicrophoneEnabled(enabled),
  ));
  controls.camera.addEventListener('click', async () => {
    const enabled = !room.localParticipant.isCameraEnabled;
    try {
      await room.localParticipant.setCameraEnabled(enabled);
      updateLocalControlState();
      refreshParticipants();
    } catch (deviceError) {
      updateLocalControlState();
      showError(deviceError.message || 'Could not change camera settings. Check browser permissions.');
    }
  });
  controls.screen.addEventListener('click', async () => {
    try {
      const enabled = controls.screen.dataset.enabled !== 'true';
      await room.localParticipant.setScreenShareEnabled(enabled, {
        selfBrowserSurface: 'exclude',
        contentHint: 'detail',
      });
      updateLocalControlState();
      if (!enabled) clearScreenShare(room.localParticipant.identity);
      refreshParticipants();
    } catch (deviceError) {
      showError(deviceError.message || 'Screen sharing was not allowed.');
    }
  });
  controls.hand.addEventListener('click', async () => {
    handRaised = !handRaised;
    await room.localParticipant.publishData(
      new TextEncoder().encode(JSON.stringify({ type: 'hand', raised: handRaised })),
      { reliable: true },
    );
    controls.hand.textContent = handRaised ? 'Lower hand' : 'Raise hand';
    if (handRaised) raisedHands.add(room.localParticipant.identity);
    else raisedHands.delete(room.localParticipant.identity);
    refreshParticipants();
  });

  chatForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    const text = chatInput.value.trim();
    if (!text) return;
    const payload = { type: 'chat', text: text.slice(0, 1000), timestamp: new Date().toISOString() };
    try {
      await room.localParticipant.publishData(
        new TextEncoder().encode(JSON.stringify(payload)),
        { reliable: true },
      );
      appendMessage(participantName(room.localParticipant), payload.text, payload.timestamp);
      chatInput.value = '';
    } catch (sendError) {
      showError(sendError.message || 'The message could not be sent.');
    }
  });

  root.querySelector('[data-leave]').addEventListener('click', async (event) => {
    event.preventDefault();
    leaving = true;
    await room.disconnect();
    window.location.assign(event.currentTarget.href);
  });

  async function connect() {
    try {
      const response = await fetch(root.dataset.tokenUrl, {
        method: 'POST',
        headers: { 'X-CSRFToken': csrf, 'X-Requested-With': 'XMLHttpRequest' },
        credentials: 'same-origin',
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || 'Could not authorize classroom access.');
      await room.connect(body.url, body.token);
      state.textContent = 'Connected';
      setConnected(true);
      refreshParticipants();
      try {
        // LiveKit's setCameraEnabled creates and publishes one local camera
        // track. LocalTrackPublished attaches that exact track to the self tile.
        await room.localParticipant.setCameraEnabled(true);
        updateLocalControlState();
        refreshParticipants();
      } catch {
        updateLocalControlState();
        showError('Camera is unavailable. You can still listen and participate.');
      }
      try {
        await room.localParticipant.setMicrophoneEnabled(true);
        controls.microphone.dataset.enabled = 'true';
        controls.microphone.textContent = 'Disable microphone';
      } catch {
        showError('Microphone is unavailable. Check browser permissions.');
      }
      state.textContent = 'Connected';
    } catch (connectError) {
      state.textContent = 'Connection unavailable';
      showError(connectError.message || 'Unable to connect to the live classroom.');
    }
  }

  window.addEventListener('pagehide', () => {
    if (!leaving) room.disconnect();
  });

  connect();
})();
