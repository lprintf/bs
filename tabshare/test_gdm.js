const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launchPersistentContext('/tmp/pw_ext_test_8', {
    executablePath: '/usr/bin/google-chrome',
    headless: false,
    args: [
      '--auto-select-desktop-capture-source=Entire screen',
       '--enable-usermedia-screen-capturing',
      '--allow-http-screen-capture',
      '--use-fake-ui-for-media-stream'
    ]
  });
  const page = await browser.newPage();
  await page.goto("https://www.google.com");
  try {
      const res = await page.evaluate(async () => {
          try {
              const stream = await navigator.mediaDevices.getDisplayMedia({ video: true });
              return "GDM Success: " + stream.id;
          } catch(e) {
              return "GDM Error: " + e.message;
          }
      });
      console.log(res);
  } catch(e) {
      console.log("Evaluate error:", e);
  }
  await browser.close();
})();
