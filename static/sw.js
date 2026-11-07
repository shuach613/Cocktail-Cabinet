const VERSION='cabinet-assets-v2';
const STATIC=['/static/style.css','/static/refinement.css','/static/app.js','/static/icon.svg','/static/app-icon.png'];
self.addEventListener('install',event=>{event.waitUntil(caches.open(VERSION).then(c=>c.addAll(STATIC)));self.skipWaiting();});
self.addEventListener('activate',event=>{event.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(k=>k!==VERSION).map(k=>caches.delete(k)))));self.clients.claim();});
self.addEventListener('fetch',event=>{
 const url=new URL(event.request.url);
 if(event.request.method!=='GET'||url.origin!==self.location.origin||!url.pathname.startsWith('/static/'))return;
 event.respondWith(caches.match(event.request).then(hit=>hit||fetch(event.request).then(response=>{if(response.ok){const clone=response.clone();caches.open(VERSION).then(c=>c.put(event.request,clone));}return response;})));
});
