(function(){
  'use strict';
  const version=document.getElementById('versionBadge');
  if(version)version.textContent='v1.6.1';
  const script=document.createElement('script');
  script.src='v1.6.1-next-launch.js';
  script.defer=true;
  document.body.appendChild(script);
})();
