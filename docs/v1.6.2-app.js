(function(){
  'use strict';

  const core=document.createElement('script');
  core.src='v1.6.2-jellyfish-core.js?v=1.6.2';

  core.onload=()=>{
    const next=document.createElement('script');
    next.src='v1.6.2-next-launch.js?v=1.6.2';
    document.body.appendChild(next);
  };

  document.body.appendChild(core);
})();
