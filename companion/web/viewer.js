// HID-Fi screen viewer: the browser half of companion/DESIGN.md "Viewer tickets".
// Written to drop into the dashboard (web_ui.h) unchanged: no modules, no
// WebCrypto (the dashboard is plain http, not a secure context), no emoji.
//
// watchScreen(t, video, on) where t = the board's screen_ticket reply
// ({ip, port, fp, tn, vn, ticket}) and `on` gets state changes:
//   'connecting' | 'live' | 'reconnect' | 'ended:<why>' | 'refused:<why>'
//   'stats:<fps>' | 'displays:<json {displays, display}>' | 'display:<json {id, fallback}>'
//   'audio:<json {system, mic, why}>' (what the computer shares) | 'audiotrack' (its sound arrived)
// opts.audio: ask for the computer's sound too (docs/AUDIO.md) - one viewer per
// computer on a page, or it is heard twice. It plays through `video`.
// Returns {stop(), display(id)}: display() switches to another of the
// computer's displays. Keyframes are asked for by WebRTC itself when a picture breaks.
function watchScreen(t, video, on, opts){
  let pc=null, ws=null, done=false, live=false;
  const say=s=>{ if(!done||s.indexOf('ended')===0||s.indexOf('refused')===0) on(s); };
  const finish=s=>{ if(done)return; done=true; say(s);
    try{ws&&ws.readyState===1&&ws.send(JSON.stringify({t:'bye'}));}catch(e){}
    try{ws&&ws.close();}catch(e){} try{pc&&pc.close();}catch(e){} };
  // The answer must carry the certificate the paired app registered with the
  // board. Anything else is someone in the middle: refuse it.
  const fpOf=sdp=>{ const m=/a=fingerprint:sha-256 ([0-9A-Fa-f:]+)/.exec(sdp);
    return m?m[1].replace(/:/g,'').toLowerCase():''; };
  try{ ws=new WebSocket('ws://'+t.ip+':'+t.port+'/'); }
  catch(e){ finish('refused:unreachable'); return {stop(){}}; }
  say('connecting');
  const opened=setTimeout(()=>{ if(!live)finish('refused:unreachable'); },12000);
  ws.onopen=()=>ws.send(JSON.stringify({t:'hello',tn:t.tn,vn:t.vn,ticket:t.ticket}));
  ws.onerror=()=>{ if(!live)finish('refused:unreachable'); };
  ws.onclose=()=>{ clearTimeout(opened); finish(live?'ended:closed':'refused:unreachable'); };
  ws.onmessage=async ev=>{
    let m; try{m=JSON.parse(ev.data);}catch(e){return;}
    if(m.t==='error'){ return finish('refused:'+(m.reason||'error')); }
    if(m.t==='end'){ return finish('ended:'+(m.reason||'')); }
    if(m.t==='stats'){ on('stats:'+(m.fps|0)); return; }
    if(m.t==='displays'){ on('displays:'+JSON.stringify({displays:m.displays||[],display:m.display})); return; }
    if(m.t==='display'){ on('display:'+JSON.stringify({id:m.id,fallback:!!m.fallback})); return; }
    if(m.t==='audio'){ on('audio:'+JSON.stringify({system:m.system,mic:m.mic,why:m.why||{}})); return; }
    if(m.t==='ready'){
      on('displays:'+JSON.stringify({displays:m.displays||[],display:m.display}));
      if(m.audio) on('audio:'+JSON.stringify({system:m.audio.system,mic:m.audio.mic,why:m.audio.why||{}}));
      pc=new RTCPeerConnection({iceServers:[]});
      const tr=pc.addTransceiver('video',{direction:'recvonly'});
      // The sound, when asked for and the computer can share it (an app from
      // before sound declines the audio section; the picture is unaffected).
      if(opts&&opts.audio&&m.audio) pc.addTransceiver('audio',{direction:'recvonly'});
      try{ // H.264 first: the PC encodes it in hardware
        const caps=RTCRtpReceiver.getCapabilities&&RTCRtpReceiver.getCapabilities('video');
        if(caps&&tr.setCodecPreferences){
          const h=caps.codecs.filter(c=>/h264/i.test(c.mimeType));
          if(h.length) tr.setCodecPreferences(h.concat(caps.codecs.filter(c=>!/h264/i.test(c.mimeType))));
        }
      }catch(e){}
      // Picture and sound in one stream on the one element: the browser keeps
      // them in step. The sound's jitter buffer is asked to stay as small as the
      // network allows (Chrome: jitterBufferTarget; older: playoutDelayHint).
      const ms=new MediaStream();
      pc.ontrack=e=>{
        ms.addTrack(e.track);
        if(e.track.kind==='audio'){
          try{ if('jitterBufferTarget' in e.receiver) e.receiver.jitterBufferTarget=0;
               else e.receiver.playoutDelayHint=0; }catch(x){}
          on('audiotrack');
        }
        if(video.srcObject!==ms) video.srcObject=ms;
      };
      pc.onconnectionstatechange=()=>{
        const s=pc.connectionState;
        if(s==='connected'){ live=true; clearTimeout(opened); say('live'); }
        else if(s==='disconnected'){ say('reconnect'); }
        else if(s==='failed'){ finish(live?'ended:network':'refused:network'); }
      };
      const offer=await pc.createOffer();
      await pc.setLocalDescription(offer);
      ws.send(JSON.stringify({t:'offer',sdp:pc.localDescription.sdp}));
      return;
    }
    if(m.t==='answer'){
      if(!t.fp||fpOf(m.sdp)!==String(t.fp).toLowerCase()) return finish('refused:fingerprint');
      try{ await pc.setRemoteDescription({type:'answer',sdp:m.sdp}); }
      catch(e){ finish('refused:answer'); }
    }
  };
  return {
    stop(){ finish('ended:stopped'); },
    display(id){ try{ ws&&ws.readyState===1&&ws.send(JSON.stringify({t:'display',id:id})); }catch(e){} }
  };
}
