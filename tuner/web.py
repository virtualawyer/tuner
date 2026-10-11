"""The inline web page.

One HTML string, one style block, one script. No CDN, no bundler, no
framework. Colors, radii, and shadows come from docs/design.md and appear
only inside the :root blocks; everything else reads a custom property.

The page fetches /api/state every 2 seconds and re-renders from that
payload alone. Actions POST and then re-fetch rather than patching the DOM
from a response body.
"""

STYLE = """
:root{
  --bg:#f4f6fb; --card:#ffffff; --border:#e3e8f0; --text:#1a2233;
  --muted:#6b7688; --accent:#2563eb; --track:#e9edf5;
  --green:#16a34a; --greenbg:#dcfce7; --red:#dc2626; --redbg:#fee2e2;
  --amber:#d97706; --amberbg:#fef3c7; --shadow:0 1px 3px rgba(20,30,60,.06);
  --on-accent:#ffffff;
}
@media (prefers-color-scheme:dark){
  :root{
    --bg:#0f141c; --card:#171e29; --border:#26303f; --text:#e7ecf3;
    --muted:#8a97ab; --accent:#4f86f7; --track:#232d3c;
    --green:#22c55e; --greenbg:#0f2f1c; --red:#ef4444; --redbg:#331515;
    --amber:#f59e0b; --amberbg:#33270c; --shadow:0 1px 3px rgba(0,0,0,.3);
    --on-accent:#ffffff;
  }
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Ubuntu,sans-serif;
  font-size:15px;line-height:1.45;-webkit-text-size-adjust:100%}
.wrap{max-width:960px;margin:0 auto;padding:14px 16px 60px}

header{display:flex;align-items:center;gap:12px;flex-wrap:wrap;
  padding:12px 0 6px;position:sticky;top:0;background:var(--bg);z-index:5}
.brand{display:flex;align-items:center;gap:9px;font-weight:700;font-size:17px}
.brand .dot{width:10px;height:10px;border-radius:50%;background:var(--green);
  flex:none}
.brand .dot.warn{background:var(--amber)}
.brand .dot.bad{background:var(--red)}
.chips{display:flex;gap:8px;flex-wrap:wrap;margin-left:auto;align-items:center}
.chip{background:var(--card);border:1px solid var(--border);border-radius:999px;
  padding:4px 11px;font-size:12.5px;color:var(--muted);white-space:nowrap}
.chip b{color:var(--text);font-weight:600}
.chip.bad{color:var(--red);border-color:var(--red)}
.chip.warn{color:var(--amber);border-color:var(--amber)}

h2{font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);
  margin:22px 4px 10px;font-weight:700;display:flex;align-items:center;gap:8px}
h2 .count{color:var(--text)}
.card{background:var(--card);border:1px solid var(--border);border-radius:14px;
  padding:15px 16px;box-shadow:var(--shadow)}
.card + .card{margin-top:12px}
.row{display:flex;align-items:center;gap:10px;min-width:0}
/* Not `.row.wrap`: that also matches .wrap, the page container, and
   every wrapped row silently inherited its 60px bottom padding. */
.row.wrapped{flex-wrap:wrap}
.row > *{min-width:0}
.spacer{margin-left:auto}
.title{font-weight:650;font-size:15px;word-break:break-word}
.sub{color:var(--muted);font-size:12.5px;margin-top:2px;word-break:break-word}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;
  overflow-wrap:anywhere}

.bar{height:8px;border-radius:999px;background:var(--track);overflow:hidden;
  margin-top:8px}
.bar span{display:block;height:100%;background:var(--accent);width:0;
  transition:width 200ms linear}
.bar.low span{background:var(--red)}

button{font:inherit}
.btn{background:var(--card);border:1px solid var(--border);border-radius:10px;
  color:var(--text);padding:7px 11px;font-size:13px;cursor:pointer;
  font-weight:600;min-height:34px}
.btn:active{transform:translateY(1px)}
.btn.primary{background:var(--accent);border-color:var(--accent);
  color:var(--on-accent)}
.btn.danger{color:var(--red);border-color:var(--red)}
.btn:disabled{opacity:.5;cursor:default}
input[type=text],input[type=number],input[type=password],textarea,select{
  font:inherit;background:var(--bg);color:var(--text);
  border:1px solid var(--border);border-radius:10px;padding:8px 11px;
  min-height:34px;width:100%}
input:focus,textarea:focus,select:focus{outline:2px solid var(--accent);
  outline-offset:-1px}
select.compact{width:auto;min-width:132px;padding:7px 9px}
textarea{min-height:120px;resize:vertical;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12.5px}
label.field{display:block;margin:10px 0 0}
label.field .lab{font-size:12.5px;color:var(--muted);display:block;margin-bottom:4px}
.inline{display:flex;align-items:center;gap:7px;font-size:13px;color:var(--muted)}
.inline input[type=checkbox]{width:18px;height:18px;min-height:0;accent-color:var(--accent)}

.pill{display:inline-block;border-radius:999px;padding:2px 9px;font-size:11.5px;
  font-weight:600;white-space:nowrap}
.pill.ok{background:var(--greenbg);color:var(--green)}
.pill.warnp{background:var(--amberbg);color:var(--amber)}
.pill.badp{background:var(--redbg);color:var(--red)}
.pill.mutedp{background:var(--track);color:var(--muted)}

.empty{color:var(--muted);text-align:center;padding:26px 12px}
.empty b{display:block;color:var(--text);font-size:15px;margin-bottom:4px}
.results{margin-top:10px;display:flex;flex-direction:column;gap:6px}
.result{display:flex;gap:8px;align-items:flex-start;font-size:13px}
.result .mark{flex:none;font-weight:700}
.result.ok .mark{color:var(--green)}
.result.no .mark{color:var(--red)}

.log{max-height:280px;overflow-y:auto}
.log div{padding:2px 0;border-bottom:1px solid var(--border)}
.log div:last-child{border-bottom:0}
.log .lvl-warn{color:var(--amber)}
.log .lvl-error{color:var(--red)}

/* --- the channel list -------------------------------------------------
   One panel, many rows, rather than one card per channel: a card grid is
   fine at four channels and useless at forty. Each row is two tight lines
   and opens in place for its controls. */
/* --- browsing a channel's lineup ------------------------------------ */
.picklist{margin-top:10px;border:1px solid var(--border);border-radius:12px;
  max-height:420px;overflow-y:auto;background:var(--bg)}
.pick{display:flex;align-items:center;gap:10px;padding:7px 11px;
  border-bottom:1px solid var(--border);cursor:pointer;min-height:40px}
.pick:last-child{border-bottom:0}
.pick:hover{background:var(--track)}
.pick input[type=checkbox]{width:17px;height:17px;flex:none;
  accent-color:var(--accent)}
.pick .pt{min-width:0;flex:1 1 0}
.pick .pt b{display:block;font-weight:600;font-size:13.5px;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis}
.pick .pt span{display:block;color:var(--muted);font-size:11.5px}
.pick.have{opacity:.55}
.pick.have:hover{background:none}

.policy{margin-top:10px;gap:14px}
.policy input[type=number]{width:74px;min-height:30px;padding:4px 8px}

.toolbar{display:flex;gap:8px;align-items:center;margin:0 4px 10px}
.toolbar .grow{flex:1 1 auto;min-width:0}
.toolbar input[type=text]{min-height:32px;padding:5px 10px;font-size:13px}
.toolbar select{min-height:32px;padding:5px 8px;font-size:13px;width:auto}
.sortdir{min-height:32px;padding:4px 9px;font-size:13px;line-height:1;
  font-variant-numeric:tabular-nums}

.list{background:var(--card);border:1px solid var(--border);
  border-radius:14px;box-shadow:var(--shadow);overflow:hidden}
.lrow{border-bottom:1px solid var(--border)}
.lrow:last-child{border-bottom:0}

.lhead{display:flex;align-items:center;gap:10px;padding:9px 14px;
  cursor:pointer;background:none;border:0;width:100%;text-align:left;
  color:inherit;font:inherit;min-height:44px}
.lhead:hover{background:var(--track)}
.lhead:focus-visible{outline:2px solid var(--accent);outline-offset:-2px}
.lrow.open .lhead{background:var(--track)}

.dot-s{width:8px;height:8px;border-radius:50%;flex:none;
  background:var(--green)}
.dot-s.off{background:var(--muted)}
.dot-s.bad{background:var(--red)}

/* flex-basis 0, not auto: with auto the name claims its full
   max-content width when the browser decides where to wrap, which
   shoves the caret onto its own line on a long channel name. */
.lname{min-width:0;flex:1 1 0;display:block}
.lname b{display:block;font-weight:650;font-size:14.5px;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.lmeta{display:block;color:var(--muted);font-size:12px;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis;margin-top:1px}
.lflags{display:flex;gap:5px;flex:none}
.caret{color:var(--muted);flex:none;font-size:13px;width:14px;
  text-align:center;transition:transform 160ms ease-out}
.lhead:hover .caret{color:var(--text)}
.lrow.open .caret{transform:rotate(90deg)}

.lbody{padding:2px 14px 13px;border-top:1px dashed var(--border)}
.lbody .sub.mono{margin-bottom:9px}

@media (max-width:560px){
  .toolbar{flex-wrap:wrap}
  .toolbar .grow{flex-basis:100%}
  .lhead{min-height:48px;flex-wrap:wrap;row-gap:6px}
  /* The name is how you identify a channel, so it keeps the first line to
     itself and the status pills drop below rather than squeezing it into
     an ellipsis. */
  .caret{order:2}
  .lflags{order:3;flex-basis:100%;padding-left:18px;flex-wrap:wrap;row-gap:5px}
  .lflags:empty{display:none}
}

.hidden{display:none !important}

@media (max-width:560px){
  .wrap{padding:10px 12px 48px}
  header{gap:8px}
  .chips{margin-left:0;width:100%}
  .btn{min-height:40px;padding:9px 13px}
  input[type=text],input[type=number],select{min-height:40px}
  .row.stack{flex-direction:column;align-items:stretch}
}
"""

SCRIPT = r"""
var STATE = null;

function fmtBytes(n){
  if(!n){return '0 B';}
  var units=['B','KB','MB','GB','TB'],i=0,v=n;
  while(v>=1024&&i<units.length-1){v/=1024;i++;}
  return (i===0? v.toFixed(0) : v.toFixed(1))+' '+units[i];
}
function el(id){return document.getElementById(id);}
function text(node,value){if(node){node.textContent=value;}}
function show(node,on){if(node){node.classList.toggle('hidden',!on);}}

function post(path,body){
  return fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(body||{})}).then(function(r){return r.json();});
}
function del(path){
  return fetch(path,{method:'DELETE'}).then(function(r){return r.json();});
}

function renderHeader(s){
  text(el('c-channels'),s.counts.channels);
  text(el('c-downloaded'),s.counts.downloaded);
  text(el('c-queued'),s.counts.queued);
  text(el('version'),s.version);

  var yt=el('c-ytdlp');
  if(s.ytdlp_version){
    text(yt,'yt-dlp '+s.ytdlp_version);
    yt.classList.remove('bad');
  }else{
    text(yt,'yt-dlp missing');
    yt.classList.add('bad');
  }

  var dot=el('dot');
  dot.className='dot';
  if(!s.disk.ok){dot.classList.add('bad');}
  else if(s.disk.low||s.counts.failed){dot.classList.add('warn');}

  var d=s.disk, freeChip=el('c-free'), bar=el('diskbar');
  if(d.ok){
    text(freeChip,fmtBytes(d.free)+' free');
    freeChip.parentElement.classList.toggle('warn',!!d.low);
    freeChip.parentElement.classList.remove('bad');
    bar.firstElementChild.style.width=d.percent+'%';
    bar.classList.toggle('low',!!d.low);
    text(el('disksub'),fmtBytes(d.used)+' of '+fmtBytes(d.total)+' used ('+
      d.percent+'%) · '+fmtBytes(s.library.bytes)+' in '+
      s.library.videos+' video'+(s.library.videos===1?'':'s')+
      (d.low? ' · below the '+d.min_free_gb+' GB floor, downloads paused':''));
  }else{
    text(freeChip,'drive not mounted');
    freeChip.parentElement.classList.add('bad');
    bar.firstElementChild.style.width='0%';
    text(el('disksub'),'The library path is not reachable. Check that the '+
      'media drive is mounted.');
  }
  text(el('libroot'),s.library.root);
}

function showResults(results){
  var box=el('add-results');
  box.textContent='';
  (results||[]).forEach(function(item){
    var row=document.createElement('div');
    row.className='result '+(item.ok?'ok':'no');
    var mark=document.createElement('span');
    mark.className='mark';
    mark.textContent=item.ok?'✓':'✕';
    var body=document.createElement('span');
    // textContent throughout: a channel title is not markup.
    body.textContent=item.message||item.url;
    row.appendChild(mark);
    row.appendChild(body);
    box.appendChild(row);
  });
}

function addChannels(urls,button,onDone){
  if(!urls.length){return;}
  button.disabled=true;
  var body={urls:urls};
  var seed=el('add-seed').value.trim(), keep=el('add-keep').value.trim();
  if(seed!==''){body.seed_n=seed;}
  if(keep!==''){body.keep_n=keep;}
  post('/api/channels',body).then(function(reply){
    button.disabled=false;
    if(reply.results){showResults(reply.results);}
    else{showResults([{ok:false,message:reply.error||'could not add that'}]);}
    onDone(reply);
    poll();
  }).catch(function(){
    button.disabled=false;
    showResults([{ok:false,message:'the server did not answer'}]);
  });
}

function wireAddForm(){
  var single=el('add-url'), addBtn=el('add-btn');
  var many=el('add-many'), manyWrap=el('many-wrap');

  function submitSingle(){
    var value=single.value.trim();
    if(!value){single.focus();return;}
    addChannels([value],addBtn,function(reply){
      // Clear only on success, so a typo can be corrected in place.
      if(reply.added){single.value='';}
    });
  }

  addBtn.addEventListener('click',submitSingle);
  single.addEventListener('keydown',function(event){
    if(event.key==='Enter'){submitSingle();}
  });

  el('many-toggle').addEventListener('click',function(){
    show(manyWrap,manyWrap.classList.contains('hidden'));
    if(!manyWrap.classList.contains('hidden')){many.focus();}
  });
  el('many-cancel').addEventListener('click',function(){show(manyWrap,false);});

  el('add-many-btn').addEventListener('click',function(){
    var lines=many.value.split('\n').map(function(line){return line.trim();})
      .filter(function(line){return line.length;});
    if(!lines.length){many.focus();return;}
    addChannels(lines,el('add-many-btn'),function(reply){
      if(reply.added===lines.length){many.value='';show(manyWrap,false);}
    });
  });
}

var BROWSE={url:'',entries:[],chosen:{}};

function browseSay(message,tone){
  var out=el('browse-status');
  out.textContent=message||'';
  out.style.color=tone==='bad'?'var(--red)'
    :(tone==='good'?'var(--green)':'');
}

function browseCount(){
  var n=Object.keys(BROWSE.chosen).length;
  text(el('browse-count'),n?n+' selected':'nothing selected yet');
  el('browse-download').disabled=!n;
  el('browse-download').textContent=n?('Download '+n):'Download';
}

function pickRow(entry){
  var have=entry.status==='downloaded'||entry.status==='downloading';
  var row=document.createElement('label');
  row.className='pick'+(have?' have':'');

  var box=document.createElement('input');
  box.type='checkbox';
  box.disabled=have;
  box.checked=!!BROWSE.chosen[entry.video_id];
  box.addEventListener('change',function(){
    if(box.checked){BROWSE.chosen[entry.video_id]=entry.title;}
    else{delete BROWSE.chosen[entry.video_id];}
    browseCount();
  });
  row.appendChild(box);

  var body=document.createElement('span');
  body.className='pt';
  var title=document.createElement('b');
  title.textContent=entry.title;
  body.appendChild(title);

  var bits=[];
  if(entry.duration){bits.push(fmtDuration(entry.duration));}
  if(entry.status==='downloaded'){bits.push('already downloaded');}
  else if(entry.status==='downloading'){bits.push('downloading now');}
  else if(entry.status==='queued'){bits.push('queued');}
  else if(entry.status==='skipped'){bits.push('skipped: '+(entry.skip_reason||''));}
  else if(entry.status==='failed'){bits.push('failed');}
  else if(entry.status==='deleted'){bits.push('deleted earlier');}
  var meta=document.createElement('span');
  meta.textContent=bits.join(' · ');
  body.appendChild(meta);
  row.appendChild(body);

  return row;
}

function renderPicks(){
  var host=el('browse-list');
  host.textContent='';
  BROWSE.entries.forEach(function(entry){host.appendChild(pickRow(entry));});
  show(host,BROWSE.entries.length>0);
  show(el('browse-actions'),BROWSE.entries.length>0);
  browseCount();
}

function wireBrowse(){
  el('browse-toggle').addEventListener('click',function(){
    var body=el('browse-body'), opening=body.classList.contains('hidden');
    show(body,opening);
    el('browse-toggle').textContent=opening?'Hide':'Show';
    if(opening){el('browse-url').focus();}
  });

  function fetchLineup(){
    var url=el('browse-url').value.trim();
    if(!url){el('browse-url').focus();return;}
    var button=el('browse-btn');
    button.disabled=true;
    BROWSE.entries=[];BROWSE.chosen={};
    renderPicks();
    browseSay('listing the channel, this can take a minute...');

    post('/api/browse',{url:url,limit:el('browse-limit').value})
      .then(function(reply){
        button.disabled=false;
        if(!reply.ok){browseSay(reply.message||'could not list that','bad');return;}
        BROWSE.url=url;
        BROWSE.entries=reply.entries||[];
        renderPicks();
        browseSay(reply.channel_title+': '+reply.message+
          (reply.subscribed?' (subscribed)':
            (reply.tracked?' (tracked, not subscribed)':'')),'good');
      }).catch(function(){
        button.disabled=false;
        browseSay('the server did not answer','bad');
      });
  }

  el('browse-btn').addEventListener('click',fetchLineup);
  el('browse-url').addEventListener('keydown',function(event){
    if(event.key==='Enter'){fetchLineup();}
  });

  el('browse-all').addEventListener('click',function(){
    BROWSE.entries.forEach(function(entry){
      if(entry.status!=='downloaded'&&entry.status!=='downloading'){
        BROWSE.chosen[entry.video_id]=entry.title;
      }
    });
    renderPicks();
  });
  el('browse-none').addEventListener('click',function(){
    BROWSE.chosen={};renderPicks();
  });

  el('browse-download').addEventListener('click',function(){
    var videos=Object.keys(BROWSE.chosen).map(function(id){
      return {video_id:id,title:BROWSE.chosen[id]};
    });
    if(!videos.length){return;}
    var button=el('browse-download');
    button.disabled=true;
    browseSay('queueing '+videos.length+'...');
    post('/api/pick',{url:BROWSE.url,videos:videos}).then(function(reply){
      button.disabled=false;
      browseSay(reply.message||'',reply.ok?'good':'bad');
      if(reply.ok){
        BROWSE.chosen={};
        BROWSE.entries.forEach(function(entry){
          if(reply.ok&&entry.status===null){entry.status='queued';}
        });
        renderPicks();
        poll();
      }
    }).catch(function(){
      button.disabled=false;
      browseSay('the server did not answer','bad');
    });
  });
}

function patchChannel(id,fields){
  return fetch('/api/channels/'+id,{method:'PATCH',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify(fields)}).then(function(r){return r.json();})
    .then(function(){poll();});
}

function triState(current,onChange,labels){
  // null means inherit. The select carries all three so the inherited
  // value is visible rather than implied.
  var select=document.createElement('select');
  select.className='compact';
  [['','Inherit ('+labels.inherited+')'],['1',labels.on],['0',labels.off]]
    .forEach(function(pair){
      var option=document.createElement('option');
      option.value=pair[0];
      option.textContent=pair[1];
      select.appendChild(option);
    });
  select.value=(current===null||current===undefined)?'':String(current);
  select.addEventListener('change',function(){
    onChange(select.value===''?null:Number(select.value));
  });
  return select;
}

function editing(host){
  // Only a form control holds uncommitted state. A button does not, and
  // treating a just-clicked button as "being edited" makes the panel it
  // sits in refuse to redraw itself.
  var node=document.activeElement;
  if(!node||!host.contains(node)){return false;}
  var tag=node.tagName;
  return tag==='INPUT'||tag==='SELECT'||tag==='TEXTAREA';
}

function ago(iso){
  if(!iso){return 'never checked';}
  var then=Date.parse(iso);
  if(isNaN(then)){return '';}
  var secs=Math.max(0,(Date.now()-then)/1000);
  if(secs<90){return 'just now';}
  if(secs<3600){return Math.round(secs/60)+'m ago';}
  if(secs<86400){return Math.round(secs/3600)+'h ago';}
  return Math.round(secs/86400)+'d ago';
}

// How loudly a channel is asking to be looked at. Errors first, then
// failures, then things merely waiting.
function attention(channel){
  var c=channel.counts||{};
  return (channel.last_error?1000:0)+(c.failed||0)*100+(c.missing||0)*50+
    (c.deferred||0)*10+(c.queued||0);
}

var CHANNEL_SORTS={
  name:function(a,b){
    return a.title.toLowerCase().localeCompare(b.title.toLowerCase());},
  checked:function(a,b){
    return (Date.parse(a.last_checked_at)||0)-(Date.parse(b.last_checked_at)||0);},
  videos:function(a,b){return (a.videos||0)-(b.videos||0);},
  size:function(a,b){return (a.bytes||0)-(b.bytes||0);},
  attention:function(a,b){return attention(a)-attention(b);}
};
// Name reads best A to Z; the rest are most-interesting-first.
var SORT_DEFAULT_DIR={name:1,checked:-1,videos:-1,size:-1,attention:-1};
// An arrow alone is ambiguous, so the button also says what it did.
var SORT_WORDS={
  name:['A to Z','Z to A'],
  checked:['Longest ago first','Just checked first'],
  videos:['Fewest first','Most first'],
  size:['Smallest first','Largest first'],
  attention:['Quietest first','Needs attention first']
};

function sortDirLabel(){
  var words=SORT_WORDS[CH.sort]||['Ascending','Descending'];
  return CH.dir===1?words[0]:words[1];
}
function paintSortDir(button){
  button.textContent=CH.dir===1?'\u2191':'\u2193';
  button.title=sortDirLabel();
  button.setAttribute('aria-label','Sort order: '+sortDirLabel());
}

var CH={sort:'name',dir:1,filter:''};
var EXPANDED={};

function loadPrefs(){
  try{
    var raw=window.localStorage.getItem('tuner.channels');
    if(raw){
      var saved=JSON.parse(raw);
      if(CHANNEL_SORTS[saved.sort]){CH.sort=saved.sort;}
      CH.dir=saved.dir===-1?-1:1;
    }
  }catch(e){}
}
function savePrefs(){
  try{
    window.localStorage.setItem('tuner.channels',
      JSON.stringify({sort:CH.sort,dir:CH.dir}));
  }catch(e){}
}

function channelFlags(channel){
  var wrap=document.createElement('span');
  wrap.className='lflags';
  var c=channel.counts||{};
  // Only what needs acting on. The healthy counts live in the meta line,
  // so a quiet channel shows no pills at all.
  [['failed','badp'],['missing','badp'],['deferred','warnp'],
   ['queued','warnp'],['downloading','warnp']].forEach(function(pair){
    var n=c[pair[0]];
    if(!n){return;}
    var pill=document.createElement('span');
    pill.className='pill '+pair[1];
    pill.textContent=n+' '+pair[0];
    wrap.appendChild(pill);
  });
  return wrap;
}

function channelBody(channel){
  var body=document.createElement('div');
  body.className='lbody';

  var folder=document.createElement('div');
  folder.className='sub mono';
  folder.textContent=channel.folder;
  body.appendChild(folder);

  if(channel.last_error){
    var problem=document.createElement('div');
    problem.className='sub';
    problem.style.color='var(--red)';
    problem.style.marginBottom='9px';
    problem.textContent=channel.last_error;
    body.appendChild(problem);
  }

  var controls=document.createElement('div');
  controls.className='row wrapped';

  var enabled=document.createElement('label');
  enabled.className='inline';
  var box=document.createElement('input');
  box.type='checkbox';
  box.checked=!!channel.enabled;
  box.addEventListener('change',function(){
    patchChannel(channel.id,{enabled:box.checked});
  });
  enabled.appendChild(box);
  enabled.appendChild(document.createTextNode('Enabled'));
  controls.appendChild(enabled);

  var seedWrap=document.createElement('label');
  seedWrap.className='inline';
  seedWrap.appendChild(document.createTextNode('Download last'));
  var seed=document.createElement('input');
  seed.type='number';
  seed.min='0';
  seed.style.width='72px';
  seed.value=(channel.seed_n===null||channel.seed_n===undefined)
    ? '' : channel.seed_n;
  seed.placeholder=String(channel.effective.seed_n);
  seed.title='How many recent episodes to hold. Raising this pulls back '+
    'videos skipped as pre-existing. Blank inherits the global setting ('+
    channel.effective.seed_n+').';
  seed.addEventListener('change',function(){
    patchChannel(channel.id,{seed_n:seed.value===''?null:seed.value});
  });
  seedWrap.appendChild(seed);
  controls.appendChild(seedWrap);

  var keepWrap=document.createElement('label');
  keepWrap.className='inline';
  keepWrap.appendChild(document.createTextNode('keep'));
  var keep=document.createElement('input');
  keep.type='number';
  keep.min='0';
  keep.style.width='78px';
  keep.value=(channel.keep_n===null||channel.keep_n===undefined)
    ? '' : channel.keep_n;
  keep.placeholder=String(channel.effective.keep_n);
  keep.title='Blank inherits the global setting ('+channel.effective.keep_n+
    '). 0 means keep everything.';
  keep.addEventListener('change',function(){
    patchChannel(channel.id,{keep_n:keep.value===''?null:keep.value});
  });
  keepWrap.appendChild(keep);
  controls.appendChild(keepWrap);

  controls.appendChild(triState(channel.skip_shorts,function(value){
    patchChannel(channel.id,{skip_shorts:value});
  },{inherited:channel.effective.skip_shorts?'skip':'keep',
     on:'Skip Shorts',off:'Keep Shorts'}));

  controls.appendChild(triState(channel.skip_live,function(value){
    patchChannel(channel.id,{skip_live:value});
  },{inherited:channel.effective.skip_live?'skip':'keep',
     on:'Skip live',off:'Keep live'}));

  var check=document.createElement('button');
  check.className='btn spacer';
  check.textContent='Check now';
  check.addEventListener('click',function(){
    check.disabled=true;
    post('/api/channels/'+channel.id+'/check').then(function(){
      check.disabled=false;poll();
    });
  });
  controls.appendChild(check);

  var remove=document.createElement('button');
  remove.className='btn danger';
  remove.textContent='Remove';
  remove.addEventListener('click',function(){
    var alsoFiles=window.confirm(
      'Remove '+channel.title+'?\n\nOK also deletes its '+channel.videos+
      ' downloaded file(s).\nCancel keeps the files and only stops tracking.');
    del('/api/channels/'+channel.id+(alsoFiles?'?delete_files=1':''))
      .then(function(){poll();});
  });
  controls.appendChild(remove);

  body.appendChild(controls);
  return body;
}

function channelRow(channel){
  var open=!!EXPANDED[channel.id];
  var row=document.createElement('div');
  row.className='lrow'+(open?' open':'');

  var head=document.createElement('button');
  head.className='lhead';
  head.type='button';
  head.setAttribute('aria-expanded',open?'true':'false');
  head.setAttribute('data-ch',channel.id);
  head.setAttribute('aria-label',channel.title);

  var dot=document.createElement('span');
  dot.className='dot-s'+(channel.last_error?' bad':(channel.enabled?'':' off'));
  head.appendChild(dot);

  var name=document.createElement('span');
  name.className='lname';
  var title=document.createElement('b');
  title.textContent=channel.title;
  name.appendChild(title);

  var bits=[];
  if(!channel.enabled){bits.push('paused');}
  bits.push(channel.videos
    ? channel.videos+' video'+(channel.videos===1?'':'s')
    : 'nothing kept');
  if(channel.bytes){bits.push(fmtBytes(channel.bytes));}
  bits.push(ago(channel.last_checked_at));
  var meta=document.createElement('span');
  meta.className='lmeta';
  meta.textContent=bits.join(' · ');
  name.appendChild(meta);
  head.appendChild(name);

  head.appendChild(channelFlags(channel));

  var caret=document.createElement('span');
  caret.className='caret';
  caret.textContent='▸';
  head.appendChild(caret);

  head.addEventListener('click',function(){
    if(EXPANDED[channel.id]){delete EXPANDED[channel.id];}
    else{EXPANDED[channel.id]=true;}
    if(STATE){renderChannels(STATE);}
    // The row was just replaced, so put focus back where it was.
    var again=el('channels').querySelector('[data-ch="'+channel.id+'"]');
    if(again){again.focus();}
  });
  row.appendChild(head);

  if(open){row.appendChild(channelBody(channel));}
  return row;
}

function wireChannelTools(){
  loadPrefs();
  var sort=el('channel-sort'), dir=el('channel-sortdir'),
      filter=el('channel-filter');

  sort.value=CH.sort;
  paintSortDir(dir);

  sort.addEventListener('change',function(){
    CH.sort=sort.value;
    CH.dir=SORT_DEFAULT_DIR[CH.sort]||1;
    paintSortDir(dir);
    savePrefs();
    if(STATE){renderChannels(STATE);}
  });
  dir.addEventListener('click',function(){
    CH.dir=-CH.dir;
    paintSortDir(dir);
    savePrefs();
    if(STATE){renderChannels(STATE);}
  });
  filter.addEventListener('input',function(){
    CH.filter=filter.value.trim().toLowerCase();
    if(STATE){renderChannels(STATE);}
  });
}

function renderChannels(s){
  var host=el('channels');
  // Never redraw under someone's fingers mid-edit.
  if(editing(host)){return;}

  var all=s.channels||[];
  var shown=CH.filter
    ? all.filter(function(c){
        return c.title.toLowerCase().indexOf(CH.filter)!==-1;})
    : all.slice();
  shown.sort(function(a,b){return CHANNEL_SORTS[CH.sort](a,b)*CH.dir;});

  host.textContent='';
  shown.forEach(function(channel){host.appendChild(channelRow(channel));});

  show(host,shown.length>0);
  show(el('channels-none'),all.length>0&&shown.length===0);
  show(el('channels-empty'),all.length===0);
  show(el('channels-toolbar'),all.length>1);
  text(el('channels-count'),all.length?all.length:'');
}

function fmtDuration(seconds){
  if(!seconds){return '';}
  var h=Math.floor(seconds/3600), m=Math.floor((seconds%3600)/60),
      sec=seconds%60;
  if(h){return h+'h '+m+'m';}
  if(m){return m+'m '+sec+'s';}
  return sec+'s';
}

function renderCurrent(s){
  var host=el('current'), job=s.activity.current;
  host.textContent='';
  show(el('current-empty'),!job);
  if(!job){return;}

  var card=document.createElement('div');
  card.className='card';

  var head=document.createElement('div');
  head.className='row wrapped';
  var title=document.createElement('span');
  title.className='title';
  title.textContent=job.title||job.video_id;
  head.appendChild(title);
  var pct=document.createElement('span');
  pct.className='sub spacer';
  pct.textContent=(job.percent||0).toFixed(1)+'%';
  head.appendChild(pct);
  card.appendChild(head);

  var bar=document.createElement('div');
  bar.className='bar';
  var fill=document.createElement('span');
  fill.style.width=(job.percent||0)+'%';
  bar.appendChild(fill);
  card.appendChild(bar);

  var bits=[];
  if(job.stage&&job.stage!=='downloading'){bits.push(job.stage);}
  if(job.size){bits.push(job.size);}
  if(job.rate){bits.push(job.rate);}
  if(job.eta){bits.push('ETA '+job.eta);}
  var sub=document.createElement('div');
  sub.className='sub';
  sub.textContent=bits.join(' · ')||'starting';
  card.appendChild(sub);

  host.appendChild(card);
}

function renderQueue(s){
  var host=el('queue'), items=s.activity.queue||[];
  host.textContent='';
  show(el('queue-card'),items.length>0);
  show(el('queue-empty'),items.length===0);
  text(el('queue-count'),items.length?items.length:'');

  items.forEach(function(video,index){
    var row=document.createElement('div');
    row.className='row wrapped';
    if(index){row.style.borderTop='1px solid var(--border)';
              row.style.paddingTop='8px';row.style.marginTop='8px';}

    var body=document.createElement('div');
    var title=document.createElement('div');
    title.className='title';
    title.textContent=video.title;
    var sub=document.createElement('div');
    sub.className='sub';
    sub.textContent=[video.channel,fmtDuration(video.duration_s),
      video.status==='deferred'?'waiting for disk space':'']
      .filter(Boolean).join(' · ');
    body.appendChild(title);
    body.appendChild(sub);
    row.appendChild(body);

    if(video.status==='deferred'){
      var pill=document.createElement('span');
      pill.className='pill warnp spacer';
      pill.textContent='deferred';
      row.appendChild(pill);
    }
    host.appendChild(row);
  });
}

var OUTCOME={downloaded:['ok','downloaded'],deleted:['mutedp','deleted'],
  skipped:['mutedp','skipped'],failed:['badp','failed'],
  missing:['badp','file missing'],queued:['warnp','queued'],
  deferred:['warnp','deferred'],downloading:['warnp','downloading'],
  'new':['mutedp','new'],probing:['mutedp','checking']};

function recentRow(video,index){
  var row=document.createElement('div');
  row.className='row wrapped';
  if(index){row.style.borderTop='1px solid var(--border)';
            row.style.paddingTop='8px';row.style.marginTop='8px';}

  var body=document.createElement('div');
  body.style.minWidth='0';
  var title=document.createElement('div');
  title.className='title';
  title.textContent=video.title;
  body.appendChild(title);

  var bits=[video.channel];
  if(video.file_size){bits.push(fmtBytes(video.file_size));}
  if(video.skip_reason){bits.push(video.skip_reason);}
  if(video.status==='failed'&&video.attempts){
    bits.push(video.attempts+' attempts');
  }
  if(video.picked){bits.push('picked');}
  if(video.watched_at){bits.push('watched');}
  var sub=document.createElement('div');
  sub.className='sub';
  sub.textContent=bits.filter(Boolean).join(' · ');
  body.appendChild(sub);

  if(video.error&&(video.status==='failed'||video.status==='missing')){
    var why=document.createElement('div');
    why.className='sub mono';
    why.textContent=video.error.split('\n').slice(-1)[0];
    body.appendChild(why);
  }
  row.appendChild(body);

  var pair=OUTCOME[video.status]||['mutedp',video.status];
  var pill=document.createElement('span');
  pill.className='pill spacer '+pair[0];
  pill.textContent=pair[1];
  row.appendChild(pill);

  if(video.status==='failed'||video.status==='missing'){
    var retry=document.createElement('button');
    retry.className='btn';
    retry.textContent='Retry';
    retry.addEventListener('click',function(){
      retry.disabled=true;
      post('/api/videos/'+video.id+'/retry').then(function(){poll();});
    });
    row.appendChild(retry);
  }
  if(video.status==='deleted'||video.status==='skipped'){
    // A deleted or skipped row is a tombstone: it stops the poller
    // re-downloading on its own. Asking by hand is a different thing, and
    // is the only way back for something pruned, skipped as pre-existing,
    // or filtered out.
    var again=document.createElement('button');
    again.className='btn';
    again.textContent=video.status==='deleted'?'Download again':'Download';
    again.addEventListener('click',function(){
      again.disabled=true;
      post('/api/videos/'+video.id+'/retry').then(function(){poll();});
    });
    row.appendChild(again);
  }
  if(video.status==='downloaded'){
    var drop=document.createElement('button');
    drop.className='btn danger';
    drop.textContent='Delete';
    drop.addEventListener('click',function(){
      if(!window.confirm('Delete the file for '+video.title+'?')){return;}
      drop.disabled=true;
      post('/api/videos/'+video.id+'/delete').then(function(){poll();});
    });
    row.appendChild(drop);
  }
  return row;
}

function renderRecent(s){
  var host=el('recent'), items=s.recent||[];
  if(editing(host)){return;}

  host.textContent='';
  show(el('recent-card'),items.length>0);
  show(el('recent-empty'),items.length===0);
  items.forEach(function(video,index){
    host.appendChild(recentRow(video,index));
  });
}

function renderLog(s){
  var host=el('log');
  host.textContent='';
  (s.events||[]).forEach(function(entry){
    var line=document.createElement('div');
    line.className='lvl-'+entry.level;
    line.textContent=entry.ts.replace('T',' ').replace('Z','')+'  '+
      entry.message;
    host.appendChild(line);
  });
}

var SETTINGS_NUMBERS=['poll_minutes','seed_n','keep_n','min_free_gb',
  'shorts_max_seconds','watched_grace_hours'];
var SETTINGS_FLAGS=['skip_shorts','skip_live','subtitles','thumbnails',
  'prefer_h264','watched_delete'];
var settingsOpen=false;

function fillUsers(users,selected){
  var select=el('set-jellyfin_user_id');
  var current=select.value||selected||'';
  select.textContent='';
  var blank=document.createElement('option');
  blank.value='';
  blank.textContent=users.length?'Pick a user':'Test the connection first';
  select.appendChild(blank);
  users.forEach(function(user){
    var option=document.createElement('option');
    option.value=user.id;
    option.textContent=user.name;
    select.appendChild(option);
  });
  select.value=current;
}

function renderSettings(s){
  show(el('jellyfin-hint'),!s.jellyfin.configured);
  // The add form's placeholders are the globals it would inherit.
  if(!editing(el('add-seed').parentElement.parentElement)){
    el('add-seed').placeholder=s.settings.seed_n;
    el('add-keep').placeholder=s.settings.keep_n;
  }
  if(!settingsOpen){return;}
  if(editing(el('settings-body'))){return;}

  SETTINGS_NUMBERS.forEach(function(key){
    var node=el('set-'+key);
    if(node&&key in s.settings){node.value=s.settings[key];}
  });
  SETTINGS_FLAGS.forEach(function(key){
    var node=el('set-'+key);
    if(node&&key in s.settings){node.checked=!!s.settings[key];}
  });
  el('set-jellyfin_url').value=s.jellyfin.url||'';
  el('set-jellyfin_api_key').value=s.jellyfin.api_key||'';
  if(!el('set-jellyfin_user_id').options.length){
    fillUsers([],s.jellyfin.user_id);
  }
}

function wireSettings(){
  el('settings-toggle').addEventListener('click',function(){
    settingsOpen=!settingsOpen;
    show(el('settings-body'),settingsOpen);
    el('settings-toggle').textContent=settingsOpen?'Hide':'Show';
    if(settingsOpen&&STATE){renderSettings(STATE);}
  });

  el('jellyfin-test').addEventListener('click',function(){
    var out=el('jellyfin-result');
    out.textContent='checking...';
    // Save first, so the key just typed is the one being tested.
    saveSettings(true).then(function(){
      return post('/api/jellyfin/test');
    }).then(function(reply){
      if(reply.ok){
        out.textContent='connected to '+reply.server_name+
          ' ('+reply.version+')';
        out.style.color='var(--green)';
        fillUsers(reply.users||[],STATE?STATE.jellyfin.user_id:'');
      }else{
        out.textContent=reply.error||'could not connect';
        out.style.color='var(--red)';
      }
    });
  });

  el('settings-save').addEventListener('click',function(){saveSettings(false);});

  el('ytdlp-update').addEventListener('click',function(){
    var button=el('ytdlp-update'), out=el('ytdlp-result');
    button.disabled=true;
    out.textContent='updating...';
    out.style.color='';
    post('/api/ytdlp/update').then(function(reply){
      button.disabled=false;
      out.textContent=reply.message||'no output';
      out.style.color=reply.ok?'var(--green)':'var(--red)';
      poll();
    }).catch(function(){
      button.disabled=false;
      out.textContent='the server did not answer';
      out.style.color='var(--red)';
    });
  });
}

function saveSettings(quiet){
  var payload={};
  SETTINGS_NUMBERS.forEach(function(key){
    var node=el('set-'+key);
    if(node&&node.value!==''){payload[key]=node.value;}
  });
  SETTINGS_FLAGS.forEach(function(key){
    var node=el('set-'+key);
    if(node){payload[key]=node.checked;}
  });
  payload.jellyfin_url=el('set-jellyfin_url').value;
  payload.jellyfin_api_key=el('set-jellyfin_api_key').value;
  payload.jellyfin_user_id=el('set-jellyfin_user_id').value;

  var out=el('settings-result');
  return post('/api/settings',payload).then(function(reply){
    if(!quiet){
      out.textContent=reply.ok
        ? 'saved '+(reply.changed.length||'no')+' setting'+
          (reply.changed.length===1?'':'s')
        : (reply.error||'could not save');
      out.style.color=reply.ok?'var(--green)':'var(--red)';
    }
    poll();
    return reply;
  });
}

function render(s){
  STATE=s;
  renderHeader(s);
  renderSettings(s);
  renderCurrent(s);
  renderQueue(s);
  renderChannels(s);
  renderRecent(s);
  renderLog(s);
}

function poll(){
  fetch('/api/state').then(function(r){return r.json();}).then(render)
    .catch(function(){});
}
wireAddForm();
wireBrowse();
wireChannelTools();
wireSettings();
poll();
setInterval(poll,2000);
"""

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__BRAND__</title>
<style>__STYLE__</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="brand"><span class="dot" id="dot"></span>__BRAND__</div>
    <div class="chips">
      <span class="chip"><b id="c-channels">0</b> channels</span>
      <span class="chip"><b id="c-downloaded">0</b> downloaded</span>
      <span class="chip"><b id="c-queued">0</b> queued</span>
      <span class="chip" id="c-ytdlp">yt-dlp</span>
      <span class="chip"><b id="c-free">--</b></span>
    </div>
  </header>

  <div class="card">
    <div class="row"><span class="title">Library</span></div>
    <div class="bar" id="diskbar"><span></span></div>
    <div class="sub" id="disksub">Reading disk usage...</div>
    <div class="sub mono" id="libroot">__LIBRARY__</div>
  </div>

__SECTIONS__

  <div class="sub" style="text-align:center;margin-top:26px">
    __BRAND__ <span id="version">0.0.0</span>
  </div>
</div>
<script>__SCRIPT__</script>
</body>
</html>
"""

SECTIONS = """
  <h2>Add a channel</h2>
  <div class="card">
    <div class="row stack">
      <input type="text" id="add-url" autocomplete="off" spellcheck="false"
             placeholder="youtube.com/@handle, /channel/UC..., or any video URL">
      <button class="btn primary" id="add-btn">Add</button>
      <button class="btn" id="many-toggle">Paste many</button>
    </div>

    <div class="row wrapped policy">
      <label class="inline">Download the last
        <input type="number" id="add-seed" min="0" max="1000"
               placeholder="3"> episodes</label>
      <label class="inline">and keep
        <input type="number" id="add-keep" min="0" max="1000"
               placeholder="5"> of them</label>
    </div>

    <div id="many-wrap" class="hidden">
      <label class="field"><span class="lab">One URL per line</span>
        <textarea id="add-many" spellcheck="false"
                  placeholder="https://www.youtube.com/@veritasium&#10;https://www.youtube.com/c/Computerphile&#10;UCabcdefghijklmnopqrstuv"></textarea>
      </label>
      <div class="row" style="margin-top:8px">
        <button class="btn primary" id="add-many-btn">Add all</button>
        <button class="btn" id="many-cancel">Cancel</button>
      </div>
    </div>

    <div class="results" id="add-results"></div>
  </div>

  <h2>Browse a channel</h2>
  <div class="card">
    <div class="row">
      <span class="title">Pick individual videos</span>
      <button class="btn spacer" id="browse-toggle">Show</button>
    </div>
    <div class="sub">Fetch a channel's lineup and choose exactly what to
      download. Picked videos ignore the Shorts and livestream filters, and
      keep-N never prunes them.</div>

    <div id="browse-body" class="hidden">
      <div class="row stack" style="margin-top:12px">
        <input type="text" id="browse-url" autocomplete="off"
               placeholder="youtube.com/@handle, or any channel URL">
        <select id="browse-limit" class="compact" aria-label="How many to list">
          <option value="50">newest 50</option>
          <option value="100" selected>newest 100</option>
          <option value="200">newest 200</option>
          <option value="500">newest 500</option>
        </select>
        <button class="btn primary" id="browse-btn">Fetch</button>
      </div>
      <div class="sub" id="browse-status"></div>

      <div class="row wrapped hidden" id="browse-actions" style="margin-top:10px">
        <button class="btn" id="browse-all">Select all</button>
        <button class="btn" id="browse-none">Clear</button>
        <span class="sub" id="browse-count"></span>
        <button class="btn primary spacer" id="browse-download">Download</button>
      </div>
      <div class="picklist hidden" id="browse-list"></div>
    </div>
  </div>

  <h2>Now downloading</h2>
  <div id="current"></div>
  <div class="card empty" id="current-empty">
    <b>Nothing downloading</b>
    New videos appear here as they are picked up.
  </div>

  <h2>Queue <span class="count" id="queue-count"></span></h2>
  <div class="card" id="queue-card">
    <div id="queue"></div>
  </div>
  <div class="card empty" id="queue-empty">
    <b>Queue empty</b>
    Everything discovered so far has been handled.
  </div>

  <h2>Channels <span class="count" id="channels-count"></span></h2>
  <div class="toolbar" id="channels-toolbar">
    <span class="grow">
      <input type="text" id="channel-filter" autocomplete="off"
             placeholder="Filter channels">
    </span>
    <select id="channel-sort" aria-label="Sort channels by">
      <option value="name">Name</option>
      <option value="checked">Last checked</option>
      <option value="videos">Videos</option>
      <option value="size">Size on disk</option>
      <option value="attention">Needs attention</option>
    </select>
    <button class="btn sortdir" id="channel-sortdir"
            aria-label="Reverse sort order">↓</button>
  </div>
  <div class="list hidden" id="channels"></div>
  <div class="card empty hidden" id="channels-none">
    <b>No match</b>
    No channel name contains that.
  </div>
  <div class="card empty" id="channels-empty">
    <b>No channels yet</b>
    Paste a YouTube channel URL above to start tracking it.
  </div>

  <h2>Recent</h2>
  <div class="card" id="recent-card">
    <div id="recent"></div>
  </div>
  <div class="card empty" id="recent-empty">
    <b>Nothing yet</b>
    Downloads, skips, and failures will be listed here.
  </div>

  <h2>Log</h2>
  <div class="card">
    <div class="log mono" id="log"></div>
  </div>

  <h2>Settings</h2>
  <div class="card">
    <div class="row">
      <span class="title">Settings</span>
      <button class="btn spacer" id="settings-toggle">Show</button>
    </div>

    <div class="sub" id="jellyfin-hint">
      Jellyfin is not connected, so watched videos are never deleted. Open
      settings and add the server URL and an API key.
    </div>

    <div id="settings-body" class="hidden">
      <label class="field"><span class="lab">Poll every (minutes, 5 or more)</span>
        <input type="number" id="set-poll_minutes" min="5" max="1440"></label>
      <label class="field"><span class="lab">Videos to grab when a channel is added</span>
        <input type="number" id="set-seed_n" min="0" max="50"></label>
      <label class="field"><span class="lab">Keep newest per channel (0 keeps everything)</span>
        <input type="number" id="set-keep_n" min="0" max="10000"></label>
      <label class="field"><span class="lab">Stop downloading below (GB free)</span>
        <input type="number" id="set-min_free_gb" min="0" max="10000"></label>
      <label class="field"><span class="lab">Treat videos up to this long as Shorts (seconds)</span>
        <input type="number" id="set-shorts_max_seconds" min="0" max="3600"></label>

      <div class="row wrapped" style="margin-top:12px">
        <label class="inline"><input type="checkbox" id="set-skip_shorts">
          Skip Shorts</label>
        <label class="inline"><input type="checkbox" id="set-skip_live">
          Skip livestreams and premieres</label>
        <label class="inline"><input type="checkbox" id="set-subtitles">
          Download subtitles</label>
        <label class="inline"><input type="checkbox" id="set-thumbnails">
          Save thumbnails for Jellyfin</label>
        <label class="inline"><input type="checkbox" id="set-prefer_h264">
          Prefer H.264 over AV1</label>
      </div>

      <div class="sub" style="margin-top:16px"><b>Jellyfin</b></div>
      <label class="field"><span class="lab">Server URL</span>
        <input type="text" id="set-jellyfin_url" autocomplete="off"
               placeholder="http://127.0.0.1:8096"></label>
      <label class="field"><span class="lab">API key</span>
        <input type="text" id="set-jellyfin_api_key" autocomplete="off"
               spellcheck="false"></label>
      <label class="field"><span class="lab">User</span>
        <select id="set-jellyfin_user_id"></select></label>
      <div class="row wrapped" style="margin-top:10px">
        <button class="btn" id="jellyfin-test">Test connection</button>
        <span class="sub" id="jellyfin-result"></span>
      </div>
      <div class="row wrapped" style="margin-top:12px">
        <label class="inline"><input type="checkbox" id="set-watched_delete">
          Delete videos once watched in Jellyfin</label>
      </div>
      <label class="field"><span class="lab">Wait this long after watching (hours)</span>
        <input type="number" id="set-watched_grace_hours" min="0" max="8760"></label>

      <div class="sub" style="margin-top:16px"><b>Maintenance</b></div>
      <div class="row wrapped" style="margin-top:8px">
        <button class="btn" id="ytdlp-update">Update yt-dlp</button>
        <span class="sub" id="ytdlp-result">YouTube breaks yt-dlp
          regularly. Update it when downloads start failing.</span>
      </div>

      <div class="row wrapped" style="margin-top:16px">
        <button class="btn primary" id="settings-save">Save</button>
        <span class="sub" id="settings-result"></span>
      </div>
    </div>
  </div>
"""


def render(cfg):
    """Build the page for this config. No placeholder may survive."""
    brand = str(cfg.get("title") or "Tuner")
    library = str(cfg.get("library_root") or "")
    return (PAGE
            .replace("__STYLE__", STYLE)
            .replace("__SCRIPT__", SCRIPT)
            .replace("__SECTIONS__", SECTIONS)
            .replace("__LIBRARY__", library)
            .replace("__BRAND__", brand))
