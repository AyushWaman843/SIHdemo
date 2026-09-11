/* Context monitoring is independent of the file-based voice detector. */
(() => {
  const section = document.createElement('section');
  section.className = 'card';
  section.innerHTML = `
    <h2>Conversation safety</h2>
    <p>Check what the caller asks you to do. Voice authenticity alone cannot establish a safe request.</p>
    <label for="risk-sector">Protection context</label>
    <select id="risk-sector"><option value="individual">Individual</option><option value="financial">Financial institution</option><option value="enterprise">Enterprise</option><option value="government">Government</option></select>
    <p><button id="risk-start">Start microphone context</button> <button id="risk-stop" disabled>Stop</button></p>
    <small>Live context uses your browser’s speech recognition service, which may send microphone audio to its provider. English only for this prototype. This mode does not run live voice-clone detection.</small>
    <p id="risk-live" role="status">Microphone off.</p>
    <label for="risk-transcript">Transcript / manual demo input</label>
    <label><input id="risk-semantic" type="checkbox"> Use AI context analysis (sends transcript to Groq; requires server GROQ_API_KEY)</label>
    <textarea id="risk-transcript" rows="5" maxlength="12000" style="width:100%;padding:12px;font:inherit" placeholder="Type a conversation, or start the microphone…"></textarea>
    <p><button id="risk-check">Check conversation</button> <button id="risk-export" disabled>Download incident summary</button></p>
    <label for="risk-example">Demo conversation (text only)</label>
    <select id="risk-example"><option value="">Choose an example…</option><option value="otp">Bank impersonation + OTP</option><option value="ceo">Executive payment request</option><option value="remote">IT support remote access</option><option value="safe">Benign security advice</option></select>
    <div id="risk-result" class="decision uncertain" role="status" aria-live="polite">Context not assessed.</div>
    <h3>Alert timeline</h3><ol id="risk-events"></ol>
    <small>Rules-based triage; scores are not probabilities. Alerts recommend human action and do not block calls or transactions. Transcripts are kept in this page’s memory unless you download a summary.</small>`;
  document.querySelector('main').appendChild(section);
  const get = id => document.getElementById('risk-' + id);
  let recognition, running = false, transcript = '', lastResult, lastSource = 'Manual transcript', generation = 0;
  const events = [];
  let busy=false, queued=null, timer;
  async function check(source) {
    if (busy) {queued=source;return;}
    busy=true;
    try {await executeCheck(source);} finally {busy=false;if(queued){const next=queued;queued=null;check(next).catch(fail);}}
  }
  async function executeCheck(source) {
    const current = ++generation, text = get('transcript').value;
    const response = await fetch('/api/context', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text,sector:get('sector').value,semantic:get('semantic').checked && !source.includes('provisional')})});
    if (!response.ok) throw Error('Context check failed. Check server connection.');
    const data = await response.json();
    if (current !== generation || text !== get('transcript').value) return;
    lastResult = data; lastSource = source;
    const box = get('result'); box.replaceChildren();
    box.className = 'decision ' + (['HIGH','CRITICAL'].includes(data.level)?'suspicious':'uncertain');
    const title = document.createElement('strong'); title.textContent = `${data.level} — ${data.assessment}`; box.append(title);
    const note = document.createElement('p'); note.textContent = `${source}. Triage score: ${data.score ?? 'unavailable'}. ${data.action}`; box.append(note);
    const method = document.createElement('p'); method.textContent = data.method + (data.notice ? ' — '+data.notice : ''); box.append(method);
    const list = document.createElement('ul');
    for (const finding of data.findings) { const item = document.createElement('li'); item.textContent = `${finding.category}: “${finding.evidence}” ${finding.reason || ''}`; list.append(item); }
    box.append(list); get('export').disabled = false;
    const signature = JSON.stringify([data.level,data.findings.map(f=>f.category)]);
    if (!events.length || events[events.length-1].signature !== signature) {
      events.push({time:new Date().toISOString(),level:data.level,categories:data.findings.map(f=>f.category),source,signature});
      if(events.length > 30) events.shift();
      get('events').replaceChildren(...events.map(e=>{const li=document.createElement('li');li.textContent=`${new Date(e.time).toLocaleTimeString()} · ${e.level} · ${e.categories.join(', ') || 'No listed warning pattern'} (${e.source})`;return li;}));
    }
  }
  const fail = e => {get('live').textContent=e.message;};
  get('check').onclick=()=>check('Manual transcript').catch(fail);
  get('sector').onchange=()=>check(lastSource).catch(fail);
  get('semantic').onchange=()=>check(lastSource).catch(fail);
  const examples = {
    otp:'I am calling from your bank. Your account will be blocked. Share your OTP immediately.',
    ceo:'This is your CEO. Transfer money to the new account right now. Keep this confidential and bypass approval.',
    remote:'I am IT support. Install AnyDesk and give me your password.',
    safe:'Never share your OTP or password. Our bank will not ask for them. Check the official app for your balance.'
  };
  get('example').onchange=()=>{if(examples[get('example').value]){get('transcript').value=examples[get('example').value];check('Demo text (no audio scan)').catch(fail);}};
  const Speech = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Speech) {get('start').disabled=true;get('live').textContent='Browser speech recognition unavailable. Use manual text or analyze a clip for local Whisper context.';}
  get('start').onclick=()=>{
    transcript='';get('transcript').value='';events.length=0;get('events').replaceChildren();get('result').textContent='Listening; context not assessed yet.';lastResult=null;get('export').disabled=true;++generation;
    recognition=new Speech();recognition.lang='en-IN';recognition.continuous=true;recognition.interimResults=true;
    recognition.onresult=e=>{let partial='';for(let i=e.resultIndex;i<e.results.length;i++){if(e.results[i].isFinal)transcript += e.results[i][0].transcript+' ';else partial+=e.results[i][0].transcript;}
      transcript=transcript.slice(-10000);get('transcript').value=(transcript+partial).slice(-12000);clearTimeout(timer);timer=setTimeout(()=>check(partial?'Live microphone (provisional transcript)':'Live microphone').catch(fail),600);};
    recognition.onerror=e=>{get('live').textContent='Microphone recognition error: '+e.error;running=false;};
    recognition.onend=()=>{running=false;get('start').disabled=false;get('stop').disabled=true;get('transcript').readOnly=false;get('example').disabled=false;if(!get('live').textContent.includes('error'))get('live').textContent='Microphone stopped. Alerts remain available for review.';};
    try {recognition.start();running=true;get('start').disabled=true;get('stop').disabled=false;get('transcript').readOnly=true;get('example').disabled=true;get('live').textContent='Listening — context updates as speech is recognized.';} catch(e){fail(e);}
  };
  get('stop').onclick=()=>{if(recognition)recognition.stop();};
  get('export').onclick=()=>{if(!lastResult)return;const blob=new Blob([JSON.stringify({created:new Date().toISOString(),source:lastSource,assessment:lastResult,timeline:events,notice:'Prototype assessment; no automatic enforcement. Full transcript omitted.'},null,2)],{type:'application/json'});const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download='cypher-incident.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
  // Use the actual Whisper transcript when a file analysis completes.
  const previous = window.showResult;
  window.showResult = function(data) {previous(data);if(!data.error && !running){get('transcript').value=data.transcript || '';check('Clip transcription: '+data.filename).catch(fail);}};
})();
