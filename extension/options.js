const apiUrlEl = document.getElementById('apiUrl');
const apiKeyEl = document.getElementById('apiKey');
const savedEl = document.getElementById('saved');

chrome.storage.sync.get(['apiUrl', 'apiKey'], (cfg) => {
  apiUrlEl.value = cfg.apiUrl || '';
  apiKeyEl.value = cfg.apiKey || '';
});

document.getElementById('save').addEventListener('click', () => {
  chrome.storage.sync.set(
    { apiUrl: apiUrlEl.value.trim(), apiKey: apiKeyEl.value.trim() },
    () => {
      savedEl.style.display = 'block';
      setTimeout(() => (savedEl.style.display = 'none'), 1500);
    }
  );
});
