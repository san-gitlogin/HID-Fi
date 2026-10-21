// HID-Fi screen viewer: the browser half of companion/DESIGN.md "Viewer tickets".
// Written to drop into the dashboard (web_ui.h) unchanged: no modules, no
// WebCrypto (the dashboard is plain http, not a secure context), no emoji.
//
// watchScreen(t, video, on) where t = the board's screen_ticket reply
// ({ip, port, fp, tn, vn, ticket}) and `on` gets state changes:
//   'connecting' | 'live' | 'reconnect' | 'ended:<why>' | 'refused:<why>'
// Returns {stop()}. Keyframes are asked for by WebRTC itself when a picture breaks.
function watchScreen(t, video, on){
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
    if(m.t==='ready'){
      pc=new RTCPeerConnection({iceServers:[]});
      const tr=pc.addTransceiver('video',{direction:'recvonly'});
      try{ // H.264 first: the PC encodes it in hardware
        const caps=RTCRtpReceiver.getCapabilities&&RTCRtpReceiver.getCapabilities('video');
        if(caps&&tr.setCodecPreferences){
          const h=caps.codecs.filter(c=>/h264/i.test(c.mimeType));
          if(h.length) tr.setCodecPreferences(h.concat(caps.codecs.filter(c=>!/h264/i.test(c.mimeType))));
        }
      }catch(e){}
      pc.ontrack=e=>{ video.srcObject=e.streams[0]||new MediaStream([e.track]); };
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
    stop(){ finish('ended:stopped'); }
  };
}
