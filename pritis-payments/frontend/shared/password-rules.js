// Rules for new passwords. Must match PASSWORD_RULES in backend/app/schemas/auth.py.
export const PASSWORD_RULES = [
  { test: (p) => p.length >= 8, label: 'At least 8 characters' },
  { test: (p) => /[A-Z]/.test(p), label: 'An uppercase letter' },
  { test: (p) => /[a-z]/.test(p), label: 'A lowercase letter' },
  { test: (p) => /\d/.test(p), label: 'A number' },
  { test: (p) => /[^A-Za-z0-9\s]/.test(p), label: 'A symbol such as ! @ # $ or -' },
];

/** True when the password meets every rule. */
export function passwordIsStrong(password) {
  return PASSWORD_RULES.every((rule) => rule.test(password));
}

/** Fills `list` with the rules and ticks each one off live as `input` changes. */
export function attachPasswordChecklist(input, list) {
  const items = PASSWORD_RULES.map((rule) => {
    const li = document.createElement('li');
    li.textContent = rule.label;
    return li;
  });
  list.replaceChildren(...items);

  const update = () => {
    PASSWORD_RULES.forEach((rule, i) => {
      items[i].classList.toggle('met', rule.test(input.value));
    });
  };
  input.addEventListener('input', update);
  update();
}
