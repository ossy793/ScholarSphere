// Landing page: if the visitor is already signed in, point the buttons at their dashboard.
// A plain fetch (not api.js), so a signed-out visitor isn't sent to the login page.
try {
  const resp = await fetch('/api/user/me', { credentials: 'same-origin' });
  if (resp.ok) {
    const login = document.getElementById('login-btn');
    const start = document.getElementById('start-btn');
    login.hidden = true;
    start.textContent = 'Go to your dashboard';
    start.href = '/dashboard.html';
  }
} catch {
  // offline or the API is down: the page still works as it is
}
