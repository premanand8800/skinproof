(function(){
 'use strict';
 var CLS='spx-',DATA=null,LOADING=false;
 var css='.spx-panel{background:var(--surface);border:1px solid var(--rule);border-radius:var(--r);padding:20px;margin:16px 0}'
 +'.spx-panel h3{margin:0 0 4px}.spx-sub{color:var(--ink2);font-size:13.5px;margin:0 0 16px}'
 +'.spx-pair{display:grid;grid-template-columns:1fr 1fr;gap:12px}'
 +'.spx-fig{margin:0;position:relative}.spx-fig img{display:block;width:100%;height:auto;border-radius:var(--r-s);border:1px solid var(--rule)}'
 +'.spx-fig img{aspect-ratio:3/4;object-fit:cover;object-position:50% 35%}'
 +'.spx-fig figcaption{font:500 12px/1 var(--mono);color:var(--ink2);margin-top:8px;text-transform:uppercase;letter-spacing:.08em}'
 +'.spx-chip{display:inline-block;font:500 11px/1 var(--mono);color:var(--brand);background:var(--brand-soft);border-radius:999px;padding:6px 10px;margin:14px 0 10px}'
 +'.spx-sim{display:grid;grid-template-columns:minmax(0,260px) 1fr;gap:16px;align-items:start}'
 +'.spx-simbox{position:relative;border:2px dashed var(--likely);border-radius:var(--r-s);padding:6px;background:var(--likely-bg)}'
 +'.spx-simbox img{display:block;width:100%;height:auto;border-radius:6px}'
 +'.spx-lbl{display:block;font:600 13px/1.2 var(--sans);color:var(--likely);text-align:center;padding:8px 4px 4px}'
 +'.spx-ctx{display:flex;flex-wrap:wrap;gap:10px;margin-top:6px}'
 +'.spx-row{display:flex;align-items:center;gap:10px;border:1px solid var(--rule);border-radius:var(--r-s);padding:8px 12px;font-size:13.5px;color:var(--ink2)}'
 +'.spx-sw{width:22px;height:22px;border-radius:50%;border:1px solid var(--rule2);flex:none}'
 +'.spx-row b{color:var(--ink);font-family:var(--mono);font-weight:500}'
 +'.spx-note{font-size:12.5px;color:var(--ink2);margin:14px 0 0}'
 +'.spx-mini .spx-pair{grid-template-columns:1fr 1fr}.spx-mini .spx-fig img{max-height:300px;object-fit:cover;object-position:50% 40%}'
 +'@media(max-width:640px){.spx-sim{grid-template-columns:1fr}.spx-simbox{max-width:300px}.spx-panel{padding:16px}}';
 function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]})}
 function safeUrl(u){return typeof u==='string'&&u.charAt(0)==='/'&&u.charAt(1)!=='/'?u:''}
 function style(){if(document.getElementById('spx-style'))return;var s=document.createElement('style');s.id='spx-style';s.textContent=css;document.head.appendChild(s)}
 function html(d,mini){
  var m=d.masks||{},s=d.simulation||{},t=d.skin_tone||{},f=d.fitzpatrick||{};
  var h='<span class="eyebrow">What YouCam saw</span><h3>More of YouCam, on the same photo</h3>'
  +'<p class="spx-sub">Made after the recorded run from the volunteer\'s before photo ('+esc(d.units_spent)+' YouCam units). The recorded agent events are unchanged.</p>';
  h+='<span class="spx-chip">YouCam &middot; Skin Analysis masks</span><div class="spx-pair">'
  +'<figure class="spx-fig"><img loading="lazy" src="'+safeUrl(m.before)+'" alt="Skin Analysis mask overlay, before photo"><figcaption>Before'+(m.concerns?' &middot; '+esc(m.concerns.join(', ')):'')+'</figcaption></figure>'
  +'<figure class="spx-fig"><img loading="lazy" src="'+safeUrl(m.after)+'" alt="Skin Analysis mask overlay, after photo"><figcaption>After, 10 s later</figcaption></figure></div>';
  if(!mini){
   var tg=s.targets?Object.keys(s.targets).map(function(k){return esc(k)+' '+Math.round(s.targets[k]*100)+'%'}).join(', '):'';
   h+='<span class="spx-chip">YouCam &middot; Skin Simulation</span><div class="spx-sim"><div class="spx-simbox"><img loading="lazy" src="'+safeUrl(s.image)+'" alt="Simulation of the product\'s claimed effect, not the volunteer\'s result"><span class="spx-lbl">'+esc(s.label||'Simulation, not your result')+'</span></div>'
   +'<div><p style="margin:0 0 8px"><b>What the claim would look like.</b></p><p class="spx-sub" style="margin:0">A simulation of the product\'s claimed concerns'+(tg?' ('+tg+')':'')+' applied to the before photo. It is not a measurement and is never used in the decision.</p></div></div>';
   h+='<span class="spx-chip">YouCam &middot; Skin Tone and Fitzpatrick</span><div class="spx-ctx">'
   +'<div class="spx-row"><span class="spx-sw" style="background:'+esc(/^#[0-9a-fA-F]{3,8}$/.test(t.hex||'')?t.hex:'transparent')+'"></span>Skin tone <b>'+esc(t.hex)+'</b></div>'
   +'<div class="spx-row">Fitzpatrick type <b>'+esc(f.type)+'</b></div></div>'
   +'<p class="spx-note">Context only, never a verdict. '+esc(d.consent||'')+'</p>';
  }
  return h;
 }
 function place(){
  var hash=location.hash||'';
  var out=document.getElementById('out'),story=document.getElementById('story');
  var replay=/^#run=live-volunteer$/.test(hash)&&out&&out.querySelector('.decision');
  var targets=[];
  if(replay)targets.push({el:out,mini:false});
  if(story&&!replay)targets.push({el:story,mini:true});
  if(!targets.length)return;
  if(!DATA){
   if(LOADING)return;LOADING=true;
   fetch('/api/demo/volunteer/extras').then(function(r){return r.ok?r.json():null}).then(function(j){DATA=j||false;place()}).catch(function(){DATA=false}).finally(function(){LOADING=false});
   return;
  }
  if(!DATA)return;
  targets.forEach(function(t){
   if(t.el.querySelector(':scope > .spx-panel'))return;
   style();
   var p=document.createElement('section');p.className='spx-panel'+(t.mini?' spx-mini':'');p.setAttribute('aria-label','What YouCam saw');
   p.innerHTML=html(DATA,t.mini);t.el.appendChild(p);
  });
 }
 function safe(){try{place()}catch(e){}}
 try{
  new MutationObserver(safe).observe(document.body||document.documentElement,{childList:true,subtree:true});
  window.addEventListener('hashchange',safe);setInterval(safe,1500);safe();
 }catch(e){}
})();
