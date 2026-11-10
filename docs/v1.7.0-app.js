(function(){
  'use strict';

  const previous=document.createElement('script');
  previous.src='v1.6.2-app.js?v=1.6.2';
  document.body.appendChild(previous);

  const config=document.createElement('script');
  config.src='push-config.js?v=1.7.0';

  config.onload=()=>{
    const appId=String(
      window.WAL_PUSH_CONFIG?.oneSignalAppId || ''
    ).trim();

    const loadPreferences=()=>{
      const preferences=document.createElement('script');
      preferences.src='v1.7.0-push.js?v=1.7.0';
      document.body.appendChild(preferences);
    };

    if(!appId){
      loadPreferences();
      return;
    }

    window.OneSignalDeferred=window.OneSignalDeferred||[];

    const sdk=document.createElement('script');
    sdk.src='https://cdn.onesignal.com/sdks/web/v16/OneSignalSDK.page.js';
    sdk.defer=true;
    sdk.onload=loadPreferences;
    sdk.onerror=loadPreferences;
    document.head.appendChild(sdk);
  };

  config.onerror=()=>{
    const preferences=document.createElement('script');
    preferences.src='v1.7.0-push.js?v=1.7.0';
    document.body.appendChild(preferences);
  };

  document.body.appendChild(config);
})();
