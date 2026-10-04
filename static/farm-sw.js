"use strict";
const CACHE='farm-public-v2';
const ASSETS=['/static/farm-offline.html','/static/farm-outbox.js','/static/farm-offline.js','/static/dashboard.css','/static/work.css'];
self.addEventListener('install',event=>event.waitUntil(caches.open(CACHE).then(cache=>cache.addAll(ASSETS)).then(()=>self.skipWaiting())));
self.addEventListener('activate',event=>event.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(key=>key.startsWith('farm-public-')&&key!==CACHE).map(key=>caches.delete(key)))).then(()=>self.clients.claim())));
self.addEventListener('fetch',event=>{
  const request=event.request,url=new URL(request.url);
  if(request.method!=='GET'||url.origin!==self.location.origin)return;
  // Never cache an authenticated response, API request, photograph or mutation.
  if(request.mode==='navigate'&&url.pathname==='/work'){
    if(url.searchParams.get('offline')==='1'){event.respondWith(caches.match('/static/farm-offline.html'));return;}
    event.respondWith(fetch(request).catch(()=>caches.match('/static/farm-offline.html')));return;
  }
  if(ASSETS.includes(url.pathname))event.respondWith(caches.match(request).then(cached=>cached||fetch(request)));
});
