const $ = id => document.getElementById(id);
const NS = 'http://www.w3.org/2000/svg';
const LABELS = {seats:'Seats likely', standing:'Standing room', crowded:'Crowded', unknown:'Unknown crowding'};
const COLORS = {seats:'#3f9d67', standing:'#d4a34a', crowded:'#c96e5f', unknown:'#839289'};
let stops = [], network = [], selected = null, bounds, zoom = 1, nextLocation = 'from';
let requestNumber = 0, insightRequest = 0;
function node(tag, cls, text) {const e = document.createElement(tag); if(cls)e.className=cls; if(text!==undefined)e.textContent=text; return e;}
function svg(tag, attrs, text) {const e=document.createElementNS(NS,tag);for(const [k,v] of Object.entries(attrs))e.setAttribute(k,v);if(text)e.textContent=text;return e;}
async function api(path) {const r=await fetch(path); const data=await r.json();if(!r.ok)throw new Error(typeof data.detail==='string'?data.detail:'Please check your inputs and try again.');return data;}
function project(lat, lon) {return [140+(lon-bounds.minLon)/bounds.dx*590, 610-(lat-bounds.minLat)/bounds.dy*350];}
function line(points, color, width, opacity=1) {return svg('polyline',{points:points.map(p=>project(...p).join(',')).join(' '),fill:'none',stroke:color,'stroke-width':width,'stroke-linecap':'round','stroke-linejoin':'round',opacity});}
function drawMap() {
  if(!bounds)return;
  const content=$('map-content'); content.replaceChildren();
  content.append(svg('text',{x:65,y:375,class:'area-label',transform:'rotate(-90 65 375)'},'NEW DELHI'));
  network.forEach(r=>content.append(line(r.geometry,'#8bab99',3,.45)));
  if(selected){
    content.append(line(selected.geometry,'#fff',11));
    for(let i=0;i<selected.geometry.length-1;i++)content.append(line(selected.geometry.slice(i,i+2),COLORS[selected.crowding_by_stretch[i]||'unknown'],6));
  }
  stops.forEach(s=>{
    const [x,y]=project(s.stop_lat,s.stop_lon);
    const group=svg('g',{class:'map-stop',tabindex:0,role:'button','aria-label':`Set ${nextLocation} to ${s.stop_name}`});
    group.append(svg('circle',{cx:x,cy:y,r:6,fill:'#fff',stroke:'#547f65','stroke-width':2}));
    group.append(svg('text',{x:x+13,y:y+4,class:'stop-label'},s.stop_name));
    const pick=()=>{$(nextLocation).value=s.stop_name;nextLocation=nextLocation==='from'?'to':'from';$('map-description').textContent=`${s.stop_name} selected. Pick ${nextLocation==='from'?'an origin':'a destination'} next.`;drawMap();};
    group.addEventListener('click',pick);group.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();pick();}});
    content.append(group);
  });
  if(selected){
    [selected.board,selected.alight].forEach((s,i)=>{const [x,y]=project(s.lat,s.lon);content.append(svg('circle',{cx:x,cy:y,r:13,fill:i?'#173d2e':'#fff',stroke:'#173d2e','stroke-width':3}));content.append(svg('text',{x,y:y+4,'text-anchor':'middle','font-size':11,'font-weight':700,fill:i?'#fff':'#173d2e'},i?'B':'A'));});
  }
}
function choose(option, card) {
  selected=option;document.querySelectorAll('.journey').forEach(c=>{c.classList.remove('selected');c.setAttribute('aria-pressed','false');});
  card.classList.add('selected');card.setAttribute('aria-pressed','true');
  $('map-headline').textContent=`Bus ${option.route} · ${option.total_min} minutes`;
  $('map-description').textContent=`${option.board.name} to ${option.alight.name}. ${LABELS[option.crowding]}.`;
  drawMap();
}
function renderJourneys(options) {
  const host=$('results');host.replaceChildren();$('result-count').textContent=`${options.length} ${options.length===1?'option':'options'}`;
  if(!options.length){selected=null;drawMap();$('map-headline').textContent='No direct route found';$('map-description').textContent='Try a different stop pair. Transfers are not supported yet.';host.append(node('p','empty','No direct bus connects these locations within 800 m of each end. Try nearby stops.'));return;}
  options.forEach((o,i)=>{
    const card=node('button','journey');card.type='button';card.setAttribute('aria-pressed','false');
    const top=node('div','journey-top'),duration=node('span','duration',o.total_min+' ');duration.append(node('small','','min'));
    top.append(duration,node('span','badge '+o.crowding,LABELS[o.crowding]));
    card.append(top,node('span','route-tag','BUS '+o.route+' · '+o.stops+' stops'),node('p','journey-stops',o.board.name+' to '+o.alight.name),node('div','journey-detail',`Walk ${o.walk_min} min · Wait ~${o.wait_min} min · Ride ${o.ride_min} min`),node('div','journey-detail',`${o.confidence[0].toUpperCase()+o.confidence.slice(1)} sample confidence · Quiet-hour baseline ${o.usual_offpeak_min} min`));
    card.onclick=()=>{choose(o,card);$('map-nav').click();};host.append(card);if(i===0)choose(o,card);
  });
}
$('journey-form').addEventListener('submit',async e=>{
  e.preventDefault();const id=++requestNumber;$('search').disabled=true;$('search').textContent='Finding your journey…';$('result-count').textContent='Searching';$('results').replaceChildren(node('p','empty','Comparing travel time and crowding…'));
  try {const q=new URLSearchParams({from:$('from').value.trim(),to:$('to').value.trim(),depart:$('depart').value,mode:document.querySelector('input[name=mode]:checked').value});const data=await api('/journeys?'+q);if(id===requestNumber)renderJourneys(data.options);}
  catch(e){if(id===requestNumber){$('results').replaceChildren(node('p','error',e.message));$('result-count').textContent='Search unavailable';selected=null;drawMap();$('map-headline').textContent='Journey unavailable';$('map-description').textContent='Check your search inputs and try again.';}}
  finally{if(id===requestNumber){$('search').disabled=false;$('search').textContent='Find my journey';}}
});
$('swap').onclick=()=>{const value=$('from').value;$('from').value=$('to').value;$('to').value=value;};
function applyZoom(){const w=900/zoom,h=760/zoom;$('map').setAttribute('viewBox',`${(900-w)/2} ${(760-h)/2} ${w} ${h}`);}
$('zoom-in').onclick=()=>{zoom=Math.min(2.5,zoom+.25);applyZoom();};$('zoom-out').onclick=()=>{zoom=Math.max(.75,zoom-.25);applyZoom();};$('fit').onclick=()=>{zoom=1;applyZoom();};
function hh(bin){return String(Math.floor(bin/2)).padStart(2,'0')+':'+(bin%2?'30':'00');}
async function showInsights(){const id=++insightRequest;const host=$('insight-results');host.replaceChildren(node('p','empty','Loading estimates…'));try{const data=await api('/crowding/top?'+new URLSearchParams({day_type:$('insight-day').value,limit:12,min_confidence:'low'}));if(id!==insightRequest)return;host.replaceChildren();if(!data.items.length)host.append(node('p','empty','No estimates for this day type.'));data.items.forEach(item=>{const row=node('div','insight-row'),left=node('div'),right=node('div');left.append(node('strong','','Bus '+item.route_short+' · '+hh(item.half_hour)),node('p','',item.from_stop+' · '+(item.direction_id?'reverse':'outbound')));right.append(node('span','badge '+item.level,LABELS[item.level]),node('p','',item.load_est+' estimated riders'));row.append(left,right);host.append(row);});}catch(e){if(id===insightRequest)host.replaceChildren(node('p','error',e.message));}}
function setTab(insights){document.querySelector(".app").classList.remove("map-mode");$("map-nav").classList.remove("active");$('planner-panel').hidden=insights;$('insights-panel').hidden=!insights;$('planner-nav').classList.toggle('active',!insights);$('network-nav').classList.toggle('active',insights);if(insights)showInsights();}
$('planner-nav').onclick=()=>setTab(false);$('network-nav').onclick=()=>setTab(true);$('insight-day').onchange=showInsights;
async function init(){
  try{
    const health=await api('/health');$('dataset-pill').textContent=health.synthetic?'Synthetic demo':'Local dataset';$('data-notice').textContent=health.synthetic?'DEMO DATA · Illustrative routes and synthetic ridership. No live feeds connected.':'LOCAL DATA · Baseline model estimates. No live feed connected.';
    if(!health.ready)throw new Error('Transit data is not ready. Run the demo setup or real-data pipeline and restart the API.');
    const [stopData,net]=await Promise.all([api('/stops?limit=1000'),api('/network')]);
    stops=stopData.stops;network=net.routes;if(!stops.length)throw new Error('No stops are loaded.');
    $('route-count').textContent=network.length;$('stop-count').textContent=net.stop_count;
    stops.forEach(s=>{const o=node('option');o.value=s.stop_name;$('stop-list').append(o);});
    const lats=stops.map(s=>s.stop_lat),lons=stops.map(s=>s.stop_lon);bounds={minLat:Math.min(...lats),minLon:Math.min(...lons)};bounds.dx=Math.max(.01,Math.max(...lons)-bounds.minLon);bounds.dy=Math.max(.01,Math.max(...lats)-bounds.minLat);drawMap();
    if(health.synthetic){$('depart').value='2026-11-20T08:00';$('journey-form').requestSubmit();}
    else{$('from').value='';$('to').value='';}
  }catch(e){$('dataset-pill').textContent='Data unavailable';$('results').replaceChildren(node('p','error',e.message));$('map-headline').textContent='Waiting for transit data';$('map-description').textContent='The network will appear when data is ready.';}
}
const ist=new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Kolkata',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(new Date());$('depart').value=ist.replace(' ','T');init();

$('map-nav').onclick=()=>{document.querySelector('.app').classList.add('map-mode');$('map-nav').classList.add('active');$('planner-nav').classList.remove('active');$('network-nav').classList.remove('active');};

function distanceMeters(a,b,c,d){const rad=Math.PI/180;const x=Math.sin((c-a)*rad/2)**2+Math.cos(a*rad)*Math.cos(c*rad)*Math.sin((d-b)*rad/2)**2;return 6371000*2*Math.asin(Math.sqrt(x));}
function nearby(lat,lon,isDemo){
  const host=$('nearby-stands');host.replaceChildren();
  $('nearby-location').textContent=isDemo?'Demo location: Connaught Place. Illustrative stops only.':'Nearby loaded stops · straight-line distances';
  const ranked=stops.map(s=>({...s,distance:distanceMeters(lat,lon,s.stop_lat,s.stop_lon)})).sort((a,b)=>a.distance-b.distance).filter(s=>s.distance<=2000).slice(0,4);
  if(!ranked.length){host.append(node('p','empty','No loaded bus stands within 2 km. The current dataset may not cover your location.'));return;}
  ranked.forEach(s=>{
    const row=node('button','stand-card');row.type='button';
    const routes=network.filter(r=>r.geometry.some(p=>Math.abs(p[0]-s.stop_lat)<.00001&&Math.abs(p[1]-s.stop_lon)<.00001));
    row.append(node('strong','',s.stop_name),node('span','stand-distance',Math.round(s.distance)+' m away'),node('p','', 'Routes: '+[...new Set(routes.map(r=>r.name))].join(', ')),node('small','','Arrival / next bus: unavailable until feed connection'));
    row.onclick=()=>{$('from').value=s.stop_name;setTab(false);$('to').focus();};host.append(row);
  });
}
$('demo-location').onclick=()=>{if(!stops.length){$('nearby-location').textContent='Stop data is still loading. Please try again.';return;}nearby(28.6315,77.2167,true);};
$('locate-me').onclick=()=>{
  if(!navigator.geolocation){$('nearby-location').textContent='Location is unavailable in this browser.';return;}
  $('nearby-location').textContent='Finding your location…';
  navigator.geolocation.getCurrentPosition(p=>nearby(p.coords.latitude,p.coords.longitude,false),()=>{$('nearby-location').textContent='Location could not be obtained. Allow location access or explore the demo area.';},{enableHighAccuracy:true,timeout:12000,maximumAge:30000});
};
$('map-nav').click();
