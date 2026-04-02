const { chromium } = require('playwright');
const path = require('path');
(async () => {
  const ext_path = path.resolve('extension');
  const browser = await chromium.launchPersistentContext('/tmp/pw_ext_test4', {
    executablePath: '/usr/bin/google-chrome',
    headless: false,
    args: [
      `--disable-extensions-except=${ext_path}`,
      `--load-extension=${ext_path}`
    ]
  });
  console.log("Browser launched. Waiting for service worker...");
  
  let [background] = browser.serviceWorkers();
  if (!background)
    background = await browser.waitForEvent('serviceworker');
  console.log("Service worker found:", background.url());
  
  background.on('console', msg => console.log('SW Console:', msg.text()));
  background.on('pageerror', err => console.error('SW Error:', err));
  
  await new Promise(r => setTimeout(r, 2000));
  await browser.close();
})();
