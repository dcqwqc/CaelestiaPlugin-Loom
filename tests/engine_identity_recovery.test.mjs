import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const code=readFileSync(new URL('../bridge/zen/.hey-tabby.uc.js',import.meta.url),'utf8');
const begin=code.indexOf('    function isNativeEngineWindow(');
const end=code.indexOf('    function safeChatUrl(',begin);
assert.ok(begin>0 && end>begin);
let windows=[];
const store={getCustomWindowValue(w){return w.persisted?'1':'';},
  deleteCustomWindowValue(w){w.persisted=false;},
  deleteCustomTabValue(t){t.stored=false;}};
const ctx={sessionStore:()=>store,browserWindows:()=>windows,
  engineWindow:null,log(){},bareEngineChrome(){},Services:{},};
vm.createContext(ctx);
vm.runInContext(code.slice(begin,end),ctx);
function tab(uri,marked=false) {
 const attrs=new Map(marked?[['qwqc-tabby-engine','true']]:[]);
 return {linkedBrowser:{currentURI:{spec:uri}},getAttribute:k=>attrs.get(k)??null,
   setAttribute:(k,v)=>attrs.set(k,v),removeAttribute:k=>attrs.delete(k)};
}
function browser(tabs,persisted=true) {
 return {closed:false,persisted,_qwqcTabbyEngine:false,
   gBrowser:{tabs,selectedTab:tabs[0],updateTitlebar(){}} ,
   document:{documentElement:{setAttribute(){}}}};
}
test('stale session engine window with unrelated tabs is rejected without navigation',()=>{
 const unrelated=[tab('https://web.whatsapp.com/'),tab('about:blank'),tab('https://github.com/example')];
 const stale=browser(unrelated);
 windows=[stale];
 assert.equal(ctx.findEngineWindow(),null);
 assert.equal(stale.persisted,false);
 assert.equal(stale.gBrowser.selectedTab,unrelated[0]);
 assert.equal(stale.gBrowser.tabs.length,3);
 assert.equal(unrelated.some(t=>t.getAttribute('qwqc-tabby-engine')),false);
});
test('explicit tagged ChatGPT engine tab is preserved on session restore',()=>{
 const normal=tab('https://web.whatsapp.com/');
 const loom=tab('https://chatgpt.com/c/existing',true);
 const w=browser([normal,loom]); windows=[w];
 assert.equal(ctx.findEngineWindow(),w);
 assert.equal(w.gBrowser.selectedTab,loom);
 assert.equal(w.persisted,true);
});
test('brand-new engine with temporary blank tab may load its marker',()=>{
 const w=browser([tab('about:blank')]);
 w._qwqcTabbyEngine=true; w._qwqcLoomCreatedHere=true;windows=[w];
 ctx.engineWindow=w;
 assert.equal(ctx.findEngineWindow(),w);
});
