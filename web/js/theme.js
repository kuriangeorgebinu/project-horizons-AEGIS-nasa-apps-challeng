// Theme toggle
function toggleTheme() {
  const html = document.documentElement;
  const btn = document.querySelector('.theme-toggle');
  if (html.getAttribute('data-theme') === 'light') {
    html.removeAttribute('data-theme');
    btn.textContent = '🌙';
    announce('Dark mode enabled');
  } else {
    html.setAttribute('data-theme', 'light');
    btn.textContent = '☀️';
    announce('Light mode enabled');
  }
}


