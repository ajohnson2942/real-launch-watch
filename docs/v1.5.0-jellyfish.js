(function(){
  'use strict';
  const core=document.createElement('script');
  core.src='v1.5.0-jellyfish-core.js';
  core.onload=()=>{
    const next=document.createElement('script');
    next.src='v1.6.1-next-launch.js';
    document.body.appendChild(next);
  };
  document.body.appendChild(core);
})();
