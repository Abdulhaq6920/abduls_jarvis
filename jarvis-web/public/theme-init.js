// Runs before first paint (loaded as an external script because the CSP forbids inline script).
// Only the theme preference is stored; never any credential.
(function () {
  try {
    var saved = localStorage.getItem('jarvis-theme');
    var light = saved ? saved === 'light' : window.matchMedia('(prefers-color-scheme: light)').matches;
    document.documentElement.setAttribute('data-theme', light ? 'light' : 'dark');
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute('content', light ? '#f4f7fc' : '#070b14');
  } catch (e) { /* storage blocked: keep the default dark theme */ }
})();
