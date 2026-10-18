// Adds a Show/Hide button to every password field on the page. Import it once per page.

function addToggle(input) {
  if (input.dataset.toggle === 'ready') return;
  input.dataset.toggle = 'ready';

  const wrapper = document.createElement('span');
  wrapper.className = 'password-field';
  input.replaceWith(wrapper);
  wrapper.append(input);

  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'password-toggle';
  button.textContent = 'Show';
  button.setAttribute('aria-label', 'Show password');
  button.setAttribute('aria-pressed', 'false');
  button.addEventListener('click', () => {
    const show = input.type === 'password';
    input.type = show ? 'text' : 'password';
    button.textContent = show ? 'Hide' : 'Show';
    button.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
    button.setAttribute('aria-pressed', String(show));
    input.focus();
  });
  wrapper.append(button);
}

document.querySelectorAll('input[type="password"]').forEach(addToggle);
