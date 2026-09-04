(function(){
'use strict';

const JELLYFISH_SITE={
  CA:{name:'Vandenberg Space Force Base',lat:34.742,lon:-120.572,tz:'America/Los_Angeles'},
  FL:{name:'Cape Canaveral / Kennedy Space Center',lat:28.4889,lon:-80.5778,tz:'America/New_York'}
};
const JELLYFISH_MIN_SUN=-12.5;
const JELLYFISH_MAX_SUN=-5.0;
const EARTH_RADIUS_KM=6371;

const main=document.querySelector('main');
if(!main)return;

main.innerHTML=`<div class="upcoming-columns">
  <section class="upcoming-column" aria-labelledby="upcomingLaunchesTitle">
    <div class="section-title" id="upcomingLaunchesTitle">Upcoming Launches</div>
    <div class="upcoming-range" id="upcomingRangeLaunches"></div>
    <div id="list"></div>
  </section>
  <div class="upcoming-divider" aria-hidden="true"></div>
  <section class="upcoming-column" aria-labelledby="upcomingJellyfishTitle">
    <div class="section-title" id="upcomingJellyfishTitle">Upcoming Space Jellyfish</div>
    <div class="upcoming-range" id="upcomingRangeJellyfish"></div>
    <div id="jellyfishList"></div>
    <p class="jellyfish-note">Predicted from exact liftoff time and solar geometry. A favorable prediction means the sky should be dark enough while the high-altitude plume can still be sunlit; clouds, haze, trajectory changes, or launch delays can still affect visibility.</p>
  </section>
</div>`;

const version=document.getElementById('versionBadge');
if(version)version.textContent='v1.5.0';

const prefsHelp=document.querySelector('.prefs-help');
if(prefsHelp&&!/space jellyfish/i.test(prefsHelp.textContent)){
  prefsHelp.textContent+=' When strong twilight lighting is predicted, the launch notification will also say that a space jellyfish is expected.';
}

function julianDay(date){return date.getTime()/86400000+2440587.5;}
function solarElevation(date,latDeg,lonDeg){
  const jd=julianDay(date);const n=jd-2451545.0;
  const meanLongitude=((280.460+0.9856474*n)%360+360)%360;
  const meanAnomaly=((357.528+0.9856003*n)%360+360)%360*Math.PI/180;
  const lambda=((meanLongitude+1.915*Math.sin(meanAnomaly)+0.020*Math.sin(2*meanAnomaly))%360+360)%360*Math.PI/180;
  const obliquity=(23.439-0.0000004*n)*Math.PI/180;
  const ra=((Math.atan2(Math.cos(obliquity)*Math.sin(lambda),Math.cos(lambda))*180/Math.PI)%360+360)%360;
  const dec=Math.asin(Math.sin(obliquity)*Math.sin(lambda));
  const gmst=((280.46061837+360.98564736629*(jd-2451545.0))%360+360)%360;
  const ha=(((gmst+lonDeg-ra+180)%360+360)%360-180)*Math.PI/180;
  const lat=latDeg*Math.PI/180;
  return Math.asin(Math.sin(lat)*Math.sin(dec)+Math.cos(lat)*Math.cos(dec)*Math.cos(ha))*180/Math.PI;
}
function shadowHeightKm(solarAlt){
  if(solarAlt>=0)return 0;
  const depression=Math.abs(solarAlt)*Math.PI/180;
  return EARTH_RADIUS_KM*(1/Math.max(.01,Math.cos(depression))-1);
}
function findNearestCrossing(center,site,target,rising){
  const start=new Date(center.getTime()-18*3600000);const end=new Date(center.getTime()+18*3600000);const step=5*60000;
  let previous=start;let previousDelta=solarElevation(previous,site.lat,site.lon)-target;const found=[];
  for(let ms=start.getTime()+step;ms<=end.getTime();ms+=step){
    const current=new Date(ms);const currentDelta=solarElevation(current,site.lat,site.lon)-target;
    const crossed=rising?(previousDelta<0&&currentDelta>=0):(previousDelta>0&&currentDelta<=0);
    if(crossed){
      let low=previous.getTime(),high=current.getTime();
      for(let i=0;i<28;i+=1){
        const mid=(low+high)/2;const delta=solarElevation(new Date(mid),site.lat,site.lon)-target;
        if(rising){if(delta<0)low=mid;else high=mid;}else{if(delta>0)low=mid;else high=mid;}
      }
      found.push(new Date(high));
    }
    previous=current;previousDelta=currentDelta;
  }
  if(!found.length)return null;
  found.sort((a,b)=>Math.abs(a-center)-Math.abs(b-center));
  return found[0];
}
function predictJellyfishClient(launch){
  const site=JELLYFISH_SITE[launch.location_code];const launchDate=parseDate(launch.launch_time_utc);
  if(!site||!launchDate)return{likely:false,reason:'Exact liftoff time required.'};
  const alt=solarElevation(launchDate,site.lat,site.lon);
  const altLater=solarElevation(new Date(launchDate.getTime()+5*60000),site.lat,site.lon);
  const phase=altLater<alt?'evening':'morning';
  const likely=alt>=JELLYFISH_MIN_SUN&&alt<=JELLYFISH_MAX_SUN;
  const sunset=findNearestCrossing(launchDate,site,-.833,false);
  const sunrise=findNearestCrossing(launchDate,site,-.833,true);
  return{
    likely,
    confidence:likely?'strong':'outside-window',
    phase,
    reference_location:site.name,
    solar_elevation_deg:Number(alt.toFixed(2)),
    required_sunlit_altitude_km:Number(shadowHeightKm(alt).toFixed(1)),
    sunset_utc:sunset?sunset.toISOString():null,
    sunrise_utc:sunrise?sunrise.toISOString():null,
    minutes_after_sunset:phase==='evening'&&sunset?Number(((launchDate-sunset)/60000).toFixed(1)):null,
    minutes_before_sunrise:phase==='morning'&&sunrise?Number(((sunrise-launchDate)/60000).toFixed(1)):null,
    model:'solar-geometry-v1-client'
  };
}
function jellyfishPrediction(launch){
  if(launch&&launch.jellyfish&&typeof launch.jellyfish==='object')return launch.jellyfish;
  return predictJellyfishClient(launch);
}
function formatSolarTime(value,locationCode){
  const date=parseDate(value);if(!date)return'Unavailable';
  return new Intl.DateTimeFormat('en-US',{timeZone:locationTimeZone(locationCode),hour:'numeric',minute:'2-digit',timeZoneName:'short'}).format(date);
}
function jellyfishMeta(launch,prediction){
  if(!prediction||!prediction.likely)return'';
  if(prediction.phase==='morning'){
    const minutes=prediction.minutes_before_sunrise;
    const minuteText=Number.isFinite(Number(minutes))?`${Math.round(Number(minutes))} min before sunrise`:'pre-sunrise twilight';
    return`<div class="jellyfish-meta">Sunrise: ${escapeHtml(formatSolarTime(prediction.sunrise_utc,launch.location_code))} &middot; Launch: ${escapeHtml(minuteText)} &middot; Sun: ${escapeHtml(String(prediction.solar_elevation_deg))}&deg;</div>`;
  }
  const minutes=prediction.minutes_after_sunset;
  const minuteText=Number.isFinite(Number(minutes))?`${Math.round(Number(minutes))} min after sunset`:'post-sunset twilight';
  return`<div class="jellyfish-meta">Sunset: ${escapeHtml(formatSolarTime(prediction.sunset_utc,launch.location_code))} &middot; Launch: ${escapeHtml(minuteText)} &middot; Sun: ${escapeHtml(String(prediction.solar_elevation_deg))}&deg;</div>`;
}
function upcomingWindow(){
  const now=new Date();const currentKey=currentMonthKey();
  const currentUpcoming=launchFeed.filter(launch=>launchIsInMonth(launch,currentKey)&&launchIsStillUpcoming(launch)).sort(sortLaunches);
  const shouldIncludeNextMonth=isInLastFiveDaysOfMonth(now)&&currentUpcoming.length===0;
  if(shouldIncludeNextMonth){
    const nextDate=nextMonthDate(now);const nextKey=nextMonthKey(now);
    return{
      launches:launchFeed.filter(launch=>launchIsInMonth(launch,nextKey)&&launchIsStillUpcoming(launch)).sort(sortLaunches),
      range:`${monthName(now)} and ${monthName(nextDate)}`,
      empty:`No tracked launches scheduled for the rest of ${monthName(now)} or ${monthName(nextDate)} yet.`
    };
  }
  return{launches:currentUpcoming,range:monthName(now),empty:`No tracked launches scheduled for the rest of ${monthName(now)} yet.`};
}
function launchCard(launch,showJellyfish){
  let tMinus='&mdash;';const launchDate=parseDate(launch.launch_time_utc);
  if(launchDate){const delta=fmtDelta(launchDate-new Date());tMinus=delta.launched?'Now':(delta.days>0?`T&minus;${delta.days}d ${delta.hours}h`:`T&minus;${delta.hours}h ${delta.mins}m`);}
  const prediction=showJellyfish?jellyfishPrediction(launch):null;
  return`<div class="card"><div class="t-minus"><b>${tMinus}</b>${escapeHtml(launch.date_text||'')}</div><div class="card-main">${statusBadge(launch)}${locationBadge(launch)}${showJellyfish?'<span class="badge jellyfish">Jellyfish</span>':''}<div class="card-title"><span class="card-rocket">${escapeHtml(launch.rocket)}</span> &middot; ${escapeHtml(launch.mission)}</div><div class="card-detail">${escapeHtml(launch.site||'')}${launch.time_description?' — '+escapeHtml(launch.time_description):''}</div>${showJellyfish?jellyfishMeta(launch,prediction):''}</div></div>`;
}
function renderInto(targetId,launches,emptyMessage,showJellyfish){
  const target=document.getElementById(targetId);if(!target)return;
  if(!launches.length){target.innerHTML=`<div class="empty">${escapeHtml(emptyMessage)}</div>`;return;}
  target.innerHTML=launches.map(launch=>launchCard(launch,showJellyfish)).join('');
}

renderUpcoming=function(){
  const windowData=upcomingWindow();
  const jellyfishLaunches=windowData.launches.filter(launch=>jellyfishPrediction(launch).likely);
  const rangeLaunches=document.getElementById('upcomingRangeLaunches');const rangeJellyfish=document.getElementById('upcomingRangeJellyfish');
  if(rangeLaunches)rangeLaunches.textContent=windowData.range;
  if(rangeJellyfish)rangeJellyfish.textContent=windowData.range;
  renderInto('list',windowData.launches,windowData.empty,false);
  renderInto('jellyfishList',jellyfishLaunches,'No strong space jellyfish predictions in this upcoming window yet.',true);
};

renderUpcoming();
})();
