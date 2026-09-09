(function(){
'use strict';

const style=document.createElement('style');
style.textContent=`
.hero-card.hero-tab{display:block;color:inherit;text-decoration:none;cursor:pointer;transition:box-shadow .2s ease,border-color .2s ease,transform .2s ease}.hero-card.hero-tab:hover,.hero-card.hero-tab:focus-visible{border-color:#c9a7ff;box-shadow:0 0 18px rgba(201,167,255,.5);outline:none}.hero-card.hero-tab:active{transform:translateY(1px)}.hero-open-hint{position:relative;z-index:2;margin-top:.9rem;color:#c9a7ff;font-family:'JetBrains Mono',monospace;font-size:.67rem;letter-spacing:.04em;text-transform:uppercase}
`;
document.head.appendChild(style);

const version=document.getElementById('versionBadge');
if(version)version.textContent='v1.6.1';

renderHero=function(next){
  const hero=document.getElementById('hero');
  if(heroTimer){clearInterval(heroTimer);heroTimer=null;}
  if(!next){currentHeroUid=null;hero.innerHTML='';return;}
  currentHeroUid=next.uid||`${next.rocket}-${next.mission}-${next.launch_time_utc}`;
  hero.innerHTML=`<a class="hero-card hero-tab" href="next-launch.html" aria-label="Open details for the next tracked launch"><div class="hero-label">Next tracked launch</div><div class="hero-mission">${escapeHtml(next.rocket)} &middot; ${escapeHtml(next.mission)}</div><div class="clock" id="hero-clock"></div><div class="hero-open-hint">View launch details and map &#8594;</div></a>`;
  const clockElement=document.getElementById('hero-clock');
  function tick(){
    const launchDate=parseDate(next.launch_time_utc);
    if(!launchDate){clockElement.innerHTML='<span class="unit">EXACT TIME TBD</span>';return;}
    const delta=fmtDelta(launchDate-new Date());
    if(delta.launched){clockElement.innerHTML='<span class="unit">LIFTOFF WINDOW OPEN</span>';return;}
    clockElement.innerHTML=`<span><span class="unit">${delta.days}</span><small>days</small></span><span><span class="unit">${pad(delta.hours)}</span><small>hrs</small></span><span><span class="unit">${pad(delta.mins)}</span><small>min</small></span><span><span class="unit">${pad(delta.secs)}</span><small>sec</small></span>`;
  }
  tick();
  heroTimer=setInterval(tick,1000);
};

if(Array.isArray(launchFeed)&&launchFeed.length){
  renderHero(getNextUpcomingLaunch());
}
})();
