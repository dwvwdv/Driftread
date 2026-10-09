const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

// Execute the actual service worker/message listener in its own JavaScript realm.
function background(storage) {
  const listeners = [];
  const runtime = { id: 'extension-id', getURL: page => `chrome-extension://extension-id/${page}`,
    onMessage: { addListener: listener => listeners.push(listener) } };
  const context = vm.createContext({ chrome: { storage, runtime, action: {
    setBadgeText() {}, setBadgeBackgroundColor() {},
  } } });
  context.importScripts = (...files) => {
    for (const file of files) vm.runInContext(fs.readFileSync(path.join(__dirname, '../..', file), 'utf8'), context);
  };
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../../background.js'), 'utf8'), context);
  function send(message, sender) {
    return new Promise((resolve, reject) => {
      let retained = false, responded = false;
      for (const listener of listeners) {
        retained = listener(structuredClone(message), sender, result => {
          responded = true; resolve(structuredClone(result));
        }) === true || retained;
      }
      if (!retained && !responded) reject(new Error('Message channel was not retained'));
    });
  }
  return { context, send, runtime(page = 'popup.html') {
    return { id: runtime.id, getURL: runtime.getURL,
      sendMessage: message => send(message, { id: runtime.id, url: runtime.getURL(page) }) };
  } };
}
function installBackground(chrome) {
  const worker = background(chrome.storage);
  chrome.runtime = { ...chrome.runtime, ...worker.runtime() };
  return worker;
}
module.exports = { background, installBackground };
