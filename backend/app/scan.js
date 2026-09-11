const $ = id => document.getElementById(id);
let mode='clip', clips=[], active=false, finishing=false, socket, audio, capture, source, stream, meter, raf, stopTimer, finalSeen=false, latest;
let flushed;
const pct = v => v == null ? 'Unavailable' : (v*100).toFixed(1)+'%';
const GAUGE_CIRCUMFERENCE = 326.73;
function updateGauge(prefix, value, theme, labelText, customNum){
  const fill=$(prefix+'Fill'), num=$(prefix+'Num'), badge=$(prefix+'Badge');
  if(!fill||!num||!badge)return;
  fill.classList.remove('stroke-safe','stroke-caution','stroke-danger','stroke-neutral');
  badge.classList.remove('badge-safe','badge-caution','badge-danger','badge-neutral');
  if(value==null){
    fill.style.strokeDashoffset=GAUGE_CIRCUMFERENCE;
    fill.classList.add('stroke-neutral');
    num.textContent=customNum||'—';
    badge.textContent=labelText||'Waiting';
    badge.classList.add('badge-neutral');
    return;
  }
  const clamped=Math.max(0,Math.min(100,Math.round(value)));
  fill.style.strokeDashoffset=GAUGE_CIRCUMFERENCE*(1-clamped/100);
  num.textContent=customNum||(clamped+'%');
  fill.classList.add('stroke-'+theme);
  badge.classList.add('badge-'+theme);
  badge.textContent=labelText;
}
function status(text, error=false){$('status').textContent=text; $('status').className=error?'error':'';}
function lock(value){active=value; for(const id of ['start','clipMode','micMode','clip','upload','sector','semantic']) $(id).disabled=value; $('stop').disabled=!value; $('player').controls=!value;}
function setMode(value){mode=value; $('library').hidden=value==='mic'; $('player').hidden=value==='mic'; $('micNote').hidden=value!=='mic'; $('clipMode').setAttribute('aria-pressed',value==='clip'); $('micMode').setAttribute('aria-pressed',value==='mic'); $('start').textContent=value==='mic'?'Start microphone':'Play & analyze'; $('player').pause();}
$('clipMode').onclick=()=>setMode('clip'); $('micMode').onclick=()=>setMode('mic');
async function loadClips(selected){const response=await fetch('/api/clips'); if(!response.ok)throw Error('Could not load clips.'); clips=await response.json(); $('clip').replaceChildren(...clips.map(c=>new Option(c.label,c.filename))); if(selected)$('clip').value=selected; updateClip();}
function updateClip(){const clip=clips.find(c=>c.filename===$('clip').value); if(clip)$('player').src=clip.url;}
$('clip').onchange=updateClip;
$('upload').onchange=async e=>{const file=e.target.files[0]; if(!file)return; const body=new FormData();body.append('file',file);try{const response=await fetch('/api/upload',{method:'POST',body}), data=await response.json();if(data.error)throw Error(data.error);await loadClips(data.filename);status('Clip added.');}catch(e){status(e.message,true);}finally{$('upload').value='';}};
function render(d){
  latest=d;
  $('transcript').textContent=d.transcript||'Waiting for intelligible speech…';
  $('transcriptionNote').textContent=d.transcription_error||'';
  $('voiceLabel').textContent=d.voice.label;

  // Noise-aware clone risk score adjustment
  const qualityScore = d.channel && d.channel.channel_quality_score != null ? d.channel.channel_quality_score : 1.0;
  const confidenceMultiplier = Math.min(1.0, Math.max(0.30, qualityScore / 0.85));
  const rawSynthetic = d.detector.synthetic_probability;
  const adjustedSynthetic = rawSynthetic != null ? rawSynthetic * confidenceMultiplier : null;

  $('cloneScore').textContent='Synthetic score '+pct(adjustedSynthetic);

  // Voice explanation with interference warning
  if(adjustedSynthetic != null && qualityScore < 0.80 && d.detector.provider && d.detector.provider !== 'none'){
    const qualityPct = Math.round(qualityScore * 100);
    const originalPct = Math.round(rawSynthetic * 100);
    const adjPct = Math.round(adjustedSynthetic * 100);
    $('voiceExplanation').textContent = `${d.voice.explanation} (⚠ Audio interference detected — channel quality ${qualityPct}%. Risk score adjusted from ${originalPct}% to ${adjPct}% due to reduced model confidence.)`;
  } else {
    $('voiceExplanation').textContent=d.voice.explanation;
  }

  const pName = d.detector.provider === 'reality-defender' ? 'Reality Defender' : (d.detector.provider || 'Reality Defender');
  $('provider').textContent='Detector: '+pName+(mode==='clip'?' · whole selected file':' · recorded microphone analysis');
  $('providerError').textContent=d.detector.error||'';
  $('fraudLabel').textContent=d.context.assessment;
  $('fraudScore').textContent=d.context.score==null?'Risk not assessed':`${d.context.level} · ${d.context.score}/100 triage score`;
  $('fraudAction').textContent=d.context.action;
  $('method').textContent=d.context.method+(d.context.notice?' · '+d.context.notice:'');
  $('findings').replaceChildren(...d.context.findings.map(f=>{const li=document.createElement('li');li.textContent=f.category+': “'+f.evidence+'”'+(f.reason?' — '+f.reason:'');return li;}));
  $('rating').textContent=d.rating; $('final').dataset.state=d.rating;
  $('ratingHeading').textContent=d.final?'CYPHER / final assessment':'CYPHER / provisional assessment';
  $('explanation').textContent=d.decision.explanation; $('action').textContent='Recommended action: '+d.decision.action;
  $('quality').textContent=pct(d.channel.channel_quality_score); $('reliability').textContent=d.detector.synthetic_probability==null?'Unavailable':d.decision.evidence_reliability.toFixed(1)+'%';
  $('duration').textContent=d.duration_seconds+'s';
  const snr=d.channel.snr_db==null?'unavailable':d.channel.snr_db.toFixed(1)+' dB';
  $('channelDetails').textContent=`${d.channel.degradation_label} · Noise separation estimate ${snr} · occupied bandwidth ${(d.channel.bandwidth_hz/1000).toFixed(1)} kHz · clipping ${(d.channel.clipping_ratio*100).toFixed(2)}%. ${(d.channel.notes||[]).join(' ')}`;

  // Update live circular score gauges
  if(adjustedSynthetic!=null){
    const clonePct=Math.round(adjustedSynthetic*100);
    let vTheme='safe', vBadge='Human Voice';
    if(clonePct>=65||d.voice.label==='Likely synthetic'){vTheme='danger';vBadge='Cloned Voice';}
    else if(clonePct>=35||d.voice.label==='Inconclusive'){vTheme='caution';vBadge='Uncertain';}
    updateGauge('voiceGauge',clonePct,vTheme,vBadge);
  }else if(d.voice.label==='Recording voice...'){
    updateGauge('voiceGauge',null,'neutral','Recording...','—');
  }else if(d.voice.label==='Analyzing voice...'){
    updateGauge('voiceGauge',null,'caution','Analyzing...','...');
  }else if(d.detector.error){
    updateGauge('voiceGauge',null,'neutral','Unavailable','N/A');
  }else{
    updateGauge('voiceGauge',null,'neutral','Awaiting Audio');
  }

  if(d.context.score!=null){
    const fraudPct=Math.round(d.context.score);
    let fTheme='safe', fBadge='Low Risk';
    if(fraudPct>=65||d.context.level==='CRITICAL'||d.context.level==='HIGH'){fTheme='danger';fBadge='High Fraud Risk';}
    else if(fraudPct>=30||d.context.level==='MEDIUM'){fTheme='caution';fBadge='Review Needed';}
    updateGauge('fraudGauge',fraudPct,fTheme,fBadge);
  }else{
    updateGauge('fraudGauge',null,'neutral','Not Assessed');
  }

  let oScore=null, oTheme='neutral', oBadge='Waiting';
  if(d.rating==='Suspicious'){
    oScore=Math.max(d.context.score||75,Math.round((adjustedSynthetic||0.75)*100));
    oTheme='danger'; oBadge='Threat Detected';
  }else if(d.rating==='No fraud indicators detected'){
    if(d.voice.label==='Likely synthetic'){
      oScore=Math.round((adjustedSynthetic||0.7)*100);
      oTheme='caution'; oBadge='Clone · No Fraud';
    }else{
      oScore=5; oTheme='safe'; oBadge='No Fraud Found';
    }
  }else if(d.rating==='No current warning'||d.rating==='Trusted'){
    oScore=Math.max(d.context.score||0,Math.round((adjustedSynthetic||0.05)*100));
    oTheme='safe'; oBadge='Safe / Trusted';
  }else if(d.rating&&d.rating!=='Waiting for evidence'){
    oScore=45; oTheme='caution'; oBadge='Review Needed';
  }
  updateGauge('overallGauge',oScore,oTheme,oBadge);

  if(d.final){finalSeen=true;status('Scan complete. Review both assessments and the recommended action.');cleanup();}
}
function draw(){
  const canvas=$('meter'),ctx=canvas.getContext('2d');canvas.width=canvas.clientWidth*devicePixelRatio;canvas.height=58*devicePixelRatio;ctx.scale(devicePixelRatio,devicePixelRatio);ctx.clearRect(0,0,canvas.width,canvas.height);
  const values=new Uint8Array(meter.frequencyBinCount);meter.getByteFrequencyData(values);ctx.fillStyle='#197454';
  const width=canvas.clientWidth/48;
  for(let i=0;i<48;i++){const h=3+values[Math.floor(i*values.length/48)]/255*50;ctx.fillRect(i*width,29-h/2,Math.max(2,width-3),h);}
  raf=requestAnimationFrame(draw);
}
async function cleanup(){
  clearTimeout(stopTimer);cancelAnimationFrame(raf);
  if(capture){capture.port.onmessage=null;capture.disconnect();} capture=null;
  if(source){source.disconnect();source=null;}
  if(stream){stream.getTracks().forEach(t=>t.stop());stream=null;}
  if(audio){const previous=audio;audio=null;await previous.close().catch(()=>{});}
  $('player').pause();if(socket){socket.onclose=null;socket.close();socket=null;}
  lock(false);finishing=false;
}
function resetResult(){
  $('transcript').textContent='Waiting for intelligible speech…';$('transcriptionNote').textContent='';
  for(const id of ['voiceLabel','fraudLabel','rating'])$(id).textContent='Waiting for evidence';
  for(const id of ['cloneScore','fraudScore','quality','reliability'])$(id).textContent='—';
  for(const id of ['voiceExplanation','provider','providerError','method','channelDetails','action','fraudAction'])$(id).textContent='';
  $('findings').replaceChildren();$('duration').textContent='0s';$('ratingHeading').textContent='CYPHER / provisional assessment';$('explanation').textContent='Collecting audio and context.';$('final').dataset.state='';
  updateGauge('voiceGauge',null,'neutral','Awaiting Audio');
  updateGauge('fraudGauge',null,'neutral','Not Assessed');
  updateGauge('overallGauge',null,'neutral','Waiting');
}
async function start(){
  if(active)return;lock(true);$('stop').disabled=true;finishing=false;finalSeen=false;latest=null;resetResult();status('Preparing audio and connecting…');
  try{
    audio=new AudioContext({sampleRate:16000});await audio.resume();
    if(audio.sampleRate!==16000)throw Error('This browser could not create 16 kHz audio. Try current Chrome or Edge.');
    await audio.audioWorklet.addModule('/capture.js');
    if(mode==='mic')stream=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:false,noiseSuppression:false,autoGainControl:false}});
    else {
      if(!$('clip').value)throw Error('Add a clip first.');
      const player=$('player');player.currentTime=0;
      if(player.readyState<1)await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('Audio metadata timed out.')),10000);player.onloadedmetadata=()=>{clearTimeout(timer);resolve();};player.onerror=()=>{clearTimeout(timer);reject(Error('Browser cannot play this audio format. Convert it to WAV or MP3.'));};});
      if(!Number.isFinite(player.duration)||player.duration>120)throw Error('Choose a clip of 120 seconds or less for this demo.');
    }
    capture=new AudioWorkletNode(audio,'pcm-capture');meter=audio.createAnalyser();meter.fftSize=256;
    // Each session gets a fresh media element so it can join a fresh AudioContext.
    if(mode==='clip'){
      const old=$('player'), player=old.cloneNode(true);old.replaceWith(player);player.currentTime=0;
      source=audio.createMediaElementSource(player);
      player.onended=()=>finish();player.onerror=()=>{status('Clip playback failed.',true);cleanup();};
    }else source=audio.createMediaStreamSource(stream);
    socket=new WebSocket(`${location.protocol==='https:'?'wss':'ws'}://${location.host}/ws/scan`);
    await new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>reject(Error('Connection timed out.')),15000);
      socket.onopen=()=>socket.send(JSON.stringify({source:mode,filename:$('clip').value,semantic:$('semantic').checked,sector:$('sector').value}));
      socket.onerror=()=>{clearTimeout(timer);reject(Error('Could not connect to the scan server.'));};
      socket.onclose=()=>{clearTimeout(timer);reject(Error('Scan connection closed before it was ready.'));};
      socket.onmessage=e=>{const d=JSON.parse(e.data);if(d.type==='ready'){clearTimeout(timer);resolve();}else if(d.type==='error'){clearTimeout(timer);reject(Error(d.message));}else if(d.type==='update')render(d);};
    });
    socket.onmessage=e=>{const d=JSON.parse(e.data);if(d.type==='update')render(d);else if(d.type==='error'){status(d.message,true);cleanup();}};
    socket.onclose=()=>{if(!finalSeen){status('Connection interrupted. No final assessment was received.',true);$('rating').textContent='Incomplete scan';cleanup();}};
    capture.port.onmessage=e=>{
      if(e.data.done){flushed?.();return;}
      if(socket?.readyState===WebSocket.OPEN){
        if(socket.bufferedAmount>320000){status('Upload cannot keep up. Scan stopped; retry on a stable connection.',true);cleanup();return;}
        socket.send(e.data.pcm);
      }
    };
    source.connect(capture);capture.connect(meter);
    const gain=audio.createGain();gain.gain.value=mode==='mic'?0:1;meter.connect(gain);gain.connect(audio.destination);
    if(mode==='clip')await $('player').play();
    $('stop').disabled=false;
    draw();status(mode==='mic'?'Listening. Voice and fraud checks update as evidence arrives.':'Playing. Transcribing the audio while Reality Defender scans the file.');
    stopTimer=setTimeout(()=>finish(),119000);
  }catch(e){status(e.message,true);await cleanup();}
}
async function finish(){
  if(!active||finishing)return;finishing=true;$('stop').disabled=true;
  status(mode==='mic'?'Uploading and analyzing audio with Reality Defender…':'Finishing transcription and waiting for the detector’s final result…');
  const playbackComplete=mode==='clip' && $('player').ended;
  $('player').pause();stream?.getTracks().forEach(t=>t.stop());clearTimeout(stopTimer);
  if(capture){await new Promise(resolve=>{const timer=setTimeout(resolve,1000);flushed=()=>{clearTimeout(timer);resolve();};capture.port.postMessage('stop');});}
  source?.disconnect();cancelAnimationFrame(raf);
  if(socket?.readyState===WebSocket.OPEN)socket.send(JSON.stringify({type:'end',playback_complete:playbackComplete}));
  stopTimer=setTimeout(()=>{status('Final processing timed out. No complete assessment is available.',true);$('rating').textContent='Incomplete scan';cleanup();},220000);
}
$('start').onclick=start;$('stop').onclick=finish;
window.addEventListener('pagehide',()=>{stream?.getTracks().forEach(t=>t.stop());socket?.close();audio?.close();});
loadClips().catch(e=>status(e.message,true));
