(function(){
'use strict';

const UPDATE_PREF_KEY='watchALaunchUpdateNotifications';
const config=window.WAL_PUSH_CONFIG||{};
const appId=String(config.oneSignalAppId||'').trim();
const prefsCard=document.querySelector('.prefs-card');
const locationSelect=document.getElementById('locationSelect');

if(!prefsCard||!locationSelect)return;

const style=document.createElement('style');
style.textContent=`
.update-notify-panel{margin-top:1rem;padding:1rem;border:1px solid var(--hairline);border-radius:10px;background:rgba(0,0,0,.18)}
.update-notify-title{color:var(--text);font-family:'Work Sans',sans-serif;font-size:.95rem;font-weight:600;line-height:1.4}
.update-notify-sub{margin-top:.25rem;color:var(--muted);font-family:'Lora',serif;font-size:.78rem;line-height:1.45}
.update-choice-row{display:flex;justify-content:center;gap:.65rem;margin-top:.8rem}
.update-choice,.update-enable{appearance:none;border:1px solid var(--hairline);border-radius:8px;color:var(--text);background:rgba(21,27,84,.72);font-family:'JetBrains Mono',monospace;cursor:pointer;transition:box-shadow .2s ease,border-color .2s ease,background .2s ease,transform .2s ease}
.update-choice{min-width:82px;padding:.55rem .8rem;font-size:.75rem;font-weight:700}
.update-choice:hover,.update-choice:focus-visible,.update-enable:hover:not(:disabled),.update-enable:focus-visible:not(:disabled){border-color:#c9a7ff;box-shadow:0 0 15px rgba(201,167,255,.48);outline:none}
.update-choice.active{border-color:#c9a7ff;background:rgba(90,54,130,.55);box-shadow:0 0 10px rgba(201,167,255,.25)}
.update-choice:active,.update-enable:active:not(:disabled){transform:translateY(1px)}
.update-push-row{display:flex;align-items:center;justify-content:space-between;gap:.8rem;margin-top:1rem;padding-top:.85rem;border-top:1px solid rgba(201,195,224,.18)}
.update-status{color:var(--muted);font-family:'JetBrains Mono',monospace;font-size:.68rem;line-height:1.45}
.update-status strong{color:var(--text)}
.update-enable{flex:0 0 auto;padding:.58rem .75rem;font-size:.68rem;font-weight:700}
.update-enable:disabled{opacity:.45;cursor:not-allowed}
@media(max-width:520px){.update-push-row{align-items:stretch;flex-direction:column}.update-enable{width:100%}}
`;
document.head.appendChild(style);

const panel=document.createElement('div');
panel.className='update-notify-panel';
panel.innerHTML=`
  <div class="update-notify-title">Do you want to also receive notifications for updates?</div>
  <div class="update-notify-sub">Updates include launch times changing or being updated.</div>
  <div class="update-choice-row" role="group" aria-label="Receive launch update notifications">
    <button class="update-choice" id="updateNotificationsYes" type="button">YES</button>
    <button class="update-choice" id="updateNotificationsNo" type="button">NO</button>
  </div>
  <div class="update-push-row">
    <div class="update-status" id="updatePushStatus"></div>
    <button class="update-enable" id="enableUpdatePush" type="button">Enable Update Notifications</button>
  </div>
`;

prefsCard.appendChild(panel);

const yesButton=document.getElementById('updateNotificationsYes');
const noButton=document.getElementById('updateNotificationsNo');
const enableButton=document.getElementById('enableUpdatePush');
const status=document.getElementById('updatePushStatus');

let updatePreference=localStorage.getItem(UPDATE_PREF_KEY);
if(updatePreference!=='yes'&&updatePreference!=='no'){
  updatePreference='yes';
  localStorage.setItem(UPDATE_PREF_KEY,'yes');
}

let oneSignalReady=false;
let OneSignalRef=null;

function isIos(){
  return /iPad|iPhone|iPod/.test(navigator.userAgent)
    || (navigator.platform==='MacIntel'&&navigator.maxTouchPoints>1);
}

function isStandalone(){
  return window.matchMedia('(display-mode: standalone)').matches
    || window.navigator.standalone===true;
}

function renderChoice(){
  const yes=updatePreference==='yes';
  yesButton.classList.toggle('active',yes);
  noButton.classList.toggle('active',!yes);
  yesButton.setAttribute('aria-pressed',String(yes));
  noButton.setAttribute('aria-pressed',String(!yes));
}

function tagsForCurrentPreferences(){
  const location=locationSelect.value;
  return{
    wal_ca:(location==='CA'||location==='BOTH')?'1':'0',
    wal_fl:(location==='FL'||location==='BOTH')?'1':'0',
    wal_updates:updatePreference==='yes'?'1':'0'
  };
}

function syncTags(){
  if(!oneSignalReady||!OneSignalRef)return;

  try{
    OneSignalRef.User.addTags(tagsForCurrentPreferences());
  }catch(error){
    console.warn('Could not sync Watch-A-Launch push preferences.',error);
  }
}

function renderStatus(){
  if(!appId){
    status.innerHTML='<strong>Saved locally.</strong> Web-push delivery is not connected yet.';
    enableButton.disabled=true;
    return;
  }

  if(isIos()&&!isStandalone()){
    status.innerHTML='<strong>iPhone:</strong> Add Watch-A-Launch to your Home Screen first, then reopen it here.';
    enableButton.disabled=true;
    return;
  }

  if(!oneSignalReady||!OneSignalRef){
    status.textContent='Loading update-notification controls…';
    enableButton.disabled=true;
    return;
  }

  if(!OneSignalRef.Notifications.isPushSupported()){
    status.textContent='This browser does not support app update notifications.';
    enableButton.disabled=true;
    return;
  }

  const optedIn=Boolean(OneSignalRef.User.PushSubscription.optedIn);
  const permission=OneSignalRef.Notifications.permission;

  if(optedIn&&permission){
    status.innerHTML='<strong>Enabled.</strong> Your Yes/No choice is remembered on this device.';
    enableButton.textContent='Notifications Enabled';
    enableButton.disabled=true;
  }else{
    status.innerHTML='<strong>Not enabled yet.</strong> Tap the button to allow Watch-A-Launch update notifications.';
    enableButton.textContent='Enable Update Notifications';
    enableButton.disabled=false;
  }
}

function setPreference(value){
  updatePreference=value;
  localStorage.setItem(UPDATE_PREF_KEY,value);
  renderChoice();
  syncTags();
  renderStatus();
}

yesButton.addEventListener('click',()=>setPreference('yes'));
noButton.addEventListener('click',()=>setPreference('no'));
locationSelect.addEventListener('change',()=>syncTags());

enableButton.addEventListener('click',async()=>{
  if(!oneSignalReady||!OneSignalRef)return;

  enableButton.disabled=true;
  status.textContent='Requesting notification permission…';

  try{
    await OneSignalRef.Notifications.requestPermission();

    if(OneSignalRef.Notifications.permission){
      await OneSignalRef.User.PushSubscription.optIn();
      syncTags();
    }
  }catch(error){
    console.warn('Could not enable Watch-A-Launch update notifications.',error);
  }

  renderStatus();
});

renderChoice();
renderStatus();

if(appId){
  window.OneSignalDeferred=window.OneSignalDeferred||[];

  window.OneSignalDeferred.push(async function(OneSignal){
    try{
      await OneSignal.init({
        appId,
        serviceWorkerPath:'sw.js',
        serviceWorkerParam:{
          scope:'./'
        }
      });

      OneSignalRef=OneSignal;
      oneSignalReady=true;

      OneSignal.User.PushSubscription.addEventListener(
        'change',
        function(){
          syncTags();
          renderStatus();
        }
      );

      syncTags();
      renderStatus();

    }catch(error){
      console.warn('OneSignal initialization failed.',error);
      status.textContent='Update notifications could not be initialized on this device.';
      enableButton.disabled=true;
    }
  });
}
})();
