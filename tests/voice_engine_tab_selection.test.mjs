import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../bridge/zen/.hey-tabby.uc.js', import.meta.url), 'utf8');
const begin = source.indexOf('    function chooseEngineTab(');
const end = source.indexOf('    function findEngineWindow(', begin);
assert.ok(begin > 0 && end > begin, 'loads actual engine selection implementation');
const calls = [];
const context = {
  bareEngineChrome() {},
  sessionStore: () => ({
    setCustomWindowValue() {},
    setCustomTabValue(tab) { calls.push(['mark', tab.href]); },
    deleteCustomTabValue(tab) { calls.push(['unmark', tab.href]); },
  }),
};
vm.createContext(context);
vm.runInContext(source.slice(begin, end), context);

function tab(href, marked = false) {
  const attrs = new Map(marked ? [['qwqc-tabby-engine','true']] : []);
  return {
    href, linkedBrowser: {currentURI:{spec:href}},
    getAttribute: k => attrs.get(k) || null,
    setAttribute(k,v) { attrs.set(k,v); },
    removeAttribute(k) { attrs.delete(k); },
  };
}
function win(tabs, selectedTab) {
  return {gBrowser:{tabs,selectedTab,updateTitlebar() {}},
    document:{documentElement:{setAttribute() {}}},closed:false};
}
const loomUrl = 'https://chatgpt.com/g/g-p-'+'a'.repeat(32)+'-loom/c/l1';
const workingUrl = 'https://chatgpt.com/g/g-p-'+'b'.repeat(32)+'-working/c/w1';
const markerUrl = 'https://chatgpt.com/?tabby=1';

test('restored engine does not hijack unrelated Working chat', () => {
  calls.length = 0;
  const unrelated = tab(workingUrl, true);
  const marker = tab(markerUrl);
  const w = win([unrelated, marker], unrelated);
  context.styleEngineWindow(w);
  assert.equal(w.gBrowser.selectedTab, marker);
  assert.equal(marker.getAttribute('qwqc-tabby-engine'), 'true');
  assert.equal(unrelated.getAttribute('qwqc-tabby-engine'), null);
  assert.ok(calls.some(([action,href]) => action==='unmark' && href===workingUrl));
});

test('established Loom conversation wins over spare marker tab', () => {
  const conversation = tab(loomUrl, true);
  const marker = tab(markerUrl);
  const w = win([conversation, marker], conversation);
  context.styleEngineWindow(w);
  assert.equal(w.gBrowser.selectedTab, conversation);
});

test('Loom conversation wins when unrelated chat was previously marked', () => {
  const unrelated = tab(workingUrl, true);
  const loom = tab(loomUrl);
  const w = win([unrelated, loom], unrelated);
  context.styleEngineWindow(w);
  assert.equal(w.gBrowser.selectedTab, loom);
  assert.equal(unrelated.getAttribute('qwqc-tabby-engine'), null);
});

test('ambiguous normal tabs must not become the voice engine', () => {
  const a = tab('https://chatgpt.com/c/a');
  const b = tab('https://chatgpt.com/c/b');
  assert.equal(context.chooseEngineTab([a,b], a), null);
});
