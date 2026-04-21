import { api, getToken, getUser, setToken, setUser } from './api.js';

// ── State ─────────────────────────────────────────────────────────────────────
let _publicKey   = '';
let _cycle       = 'weekly';
let _selectedPlan = null;   // 'basic' | 'pro'

const _PRICES = {
  basic:  { weekly: { kobo: 100000, label: '₦1,000' }, monthly: { kobo: 300000, label: '₦3,000' } },
  pro:    { weekly: { kobo: 150000, label: '₦1,500' }, monthly: { kobo: 400000, label: '₦4,000' } },
};

// ── Cycle toggle ──────────────────────────────────────────────────────────────
window.setCycle = function (c) {
  _cycle = c;
  document.getElementById('btn-weekly') .classList.toggle('active', c === 'weekly');
  document.getElementById('btn-monthly').classList.toggle('active', c === 'monthly');
  _updatePriceLabels();
};

function _updatePriceLabels() {
  const period = _cycle === 'weekly' ? '/week' : '/month';
  document.getElementById('basic-price').innerHTML =
    `${_PRICES.basic[_cycle].label}<span class="plan-period">${period}</span>`;
  document.getElementById('pro-price').innerHTML =
    `${_PRICES.pro[_cycle].label}<span class="plan-period">${period}</span>`;
}

// ── Select plan and show payment card ─────────────────────────────────────────
window.startPayment = function (plan) {
  if (!getToken()) { window.location.href = 'index.html'; return; }
  _selectedPlan = plan;

  const price = _PRICES[plan][_cycle];
  const planLabel = plan === 'basic' ? 'Basic' : 'Pro';
  const cycleLabel = _cycle === 'weekly' ? 'Weekly' : 'Monthly';

  document.getElementById('pay-plan-label').textContent = `${planLabel} — ${cycleLabel}`;
  document.getElementById('pay-amount-label').textContent = price.label;

  // Scroll to & show payment card
  const payCard = document.getElementById('pay-card');
  payCard.classList.remove('hidden');
  payCard.scrollIntoView({ behavior: 'smooth', block: 'center' });
};

window.cancelPayCard = function () {
  document.getElementById('pay-card').classList.add('hidden');
  _selectedPlan = null;
};

// ── Confirm and open Paystack popup ──────────────────────────────────────────
window.confirmPayment = function () {
  if (!_publicKey) {
    _showPayAlert('Payment gateway is not configured. Please contact support.', 'error');
    return;
  }
  if (typeof PaystackPop === 'undefined') {
    _showPayAlert('Paystack did not load. Check your internet connection and try again.', 'error');
    return;
  }

  const user  = getUser();
  if (!user) { window.location.href = 'index.html'; return; }

  const plan  = _selectedPlan || 'basic';
  const price = _PRICES[plan][_cycle];

  const btn = document.getElementById('pay-btn');
  btn.disabled    = true;
  btn.textContent = 'Opening payment…';

  const safetyTimer = setTimeout(() => {
    btn.disabled    = false;
    btn.textContent = 'Pay with Paystack';
  }, 15000);

  let handler;
  try {
    handler = PaystackPop.setup({
      key:      _publicKey,
      email:    user.email,
      amount:   price.kobo,
      currency: 'NGN',
      ref:      `pritis_${plan}_${_cycle}_${Date.now()}`,
      label:    `Pritis ${plan === 'basic' ? 'Basic' : 'Pro'} — ${_cycle}`,
      channels: ['card', 'bank', 'ussd', 'bank_transfer'],
      metadata: { user_id: String(user.id), plan, cycle: _cycle },

      callback: function (response) {
        clearTimeout(safetyTimer);
        _hideWaiting();
        btn.textContent = 'Verifying payment…';
        _verifyPayment(response.reference, plan, _cycle);
      },

      onClose: function () {
        clearTimeout(safetyTimer);
        const ref = (handler && (handler.transactionRef || handler.trans)) || null;
        if (ref) {
          _showWaiting(ref, plan, _cycle);
        } else {
          btn.disabled    = false;
          btn.textContent = 'Pay with Paystack';
        }
      },
    });

    handler.openIframe();
  } catch (err) {
    clearTimeout(safetyTimer);
    btn.disabled    = false;
    btn.textContent = 'Pay with Paystack';
    _showPayAlert('Could not open payment popup: ' + (err.message || err), 'error');
  }
};

// ── Bank transfer waiting + polling ──────────────────────────────────────────
function _showWaiting(reference, plan, cycle) {
  document.getElementById('pay-main').classList.add('hidden');
  document.getElementById('pay-waiting').classList.remove('hidden');

  const interval = setInterval(async () => {
    try {
      const result = await api.get(`/payment/status/${reference}?plan=${plan}&cycle=${cycle}`);
      if (result.confirmed) {
        clearInterval(interval);
        window._activePayPoll = null;
        if (result.token) {
          setToken(result.token);
          try { const u = await api.get('/auth/me'); if (u) setUser(u); } catch {}
        }
        _showPaySuccess(plan);
      } else {
        const el = document.getElementById('pay-poll-status');
        if (el) el.textContent = 'Still waiting… checking again in 5s';
      }
    } catch { /* keep polling */ }
  }, 5000);

  window._activePayPoll = interval;

  window.cancelWaiting = function () {
    clearInterval(interval);
    window._activePayPoll = null;
    document.getElementById('pay-waiting').classList.add('hidden');
    document.getElementById('pay-main').classList.remove('hidden');
  };
}

function _hideWaiting() {
  if (window._activePayPoll) {
    clearInterval(window._activePayPoll);
    window._activePayPoll = null;
  }
}

// ── Verify card/instant payments ─────────────────────────────────────────────
async function _verifyPayment(reference, plan, cycle) {
  const btn = document.getElementById('pay-btn');
  try {
    const result = await api.post('/payment/verify', { reference, plan, cycle });
    if (result.token) {
      setToken(result.token);
      try { const u = await api.get('/auth/me'); if (u) setUser(u); } catch {}
    }
    _showPaySuccess(plan);
  } catch (e) {
    _showPayAlert(e.message || 'Payment verification failed. Please contact support.', 'error');
    if (btn) { btn.disabled = false; btn.textContent = 'Pay with Paystack'; }
  }
}

function _showPaySuccess(plan) {
  document.getElementById('pay-main').classList.add('hidden');
  document.getElementById('pay-waiting').classList.add('hidden');
  document.getElementById('pay-success').classList.remove('hidden');
  const planLabel = plan === 'pro' ? 'Pro' : 'Basic';
  document.getElementById('success-title').textContent = `${planLabel} Plan Activated! 🎉`;
  document.getElementById('success-msg').textContent =
    `You now have full ${planLabel} access. Enjoy your new features!`;
  setTimeout(() => { window.location.href = 'dashboard.html'; }, 3000);
}

function _showPayAlert(msg, type = 'error') {
  const el = document.getElementById('pay-alert');
  if (!el) return;
  el.textContent = msg;
  el.className   = `alert alert-${type}`;
  el.classList.remove('hidden');
  setTimeout(() => el.classList.add('hidden'), 8000);
}

// ── Highlight current plan card on load ───────────────────────────────────────
function _highlightCurrentPlan() {
  const user = getUser();
  if (!user) return;
  const plan = user.subscription_plan || 'free';
  const map  = { free: 'card-free', basic: 'card-basic', pro: 'card-pro' };
  const cardId = map[plan];
  if (cardId) {
    const card = document.getElementById(cardId);
    if (card) card.style.outline = '3px solid #22c55e';
  }
  // Update button label for current plan
  if (plan === 'basic') {
    const btn = document.getElementById('btn-basic');
    if (btn) { btn.textContent = 'Current Plan ✓'; btn.disabled = true; btn.style.opacity = '.6'; }
  }
  if (plan === 'pro') {
    const btn = document.getElementById('btn-pro');
    if (btn) { btn.textContent = 'Current Plan ✓'; btn.disabled = true; btn.style.opacity = '.6'; }
  }
  // Show hero badge based on current plan
  const badge = document.getElementById('hero-badge');
  if (badge) {
    if (plan === 'free') badge.textContent = 'CHOOSE A PLAN';
    else badge.textContent = `CURRENT PLAN: ${plan.toUpperCase()} — MANAGE SUBSCRIPTION`;
  }
}

// ── Boot ──────────────────────────────────────────────────────────────────────
async function _init() {
  try {
    const cfg = await api.get('/payment/config');
    _publicKey = cfg.public_key || '';
    // Override prices from backend if different
    if (cfg.plans) {
      if (cfg.plans.basic) {
        _PRICES.basic.weekly.kobo  = cfg.plans.basic.weekly?.kobo  || _PRICES.basic.weekly.kobo;
        _PRICES.basic.monthly.kobo = cfg.plans.basic.monthly?.kobo || _PRICES.basic.monthly.kobo;
      }
      if (cfg.plans.pro) {
        _PRICES.pro.weekly.kobo  = cfg.plans.pro.weekly?.kobo  || _PRICES.pro.weekly.kobo;
        _PRICES.pro.monthly.kobo = cfg.plans.pro.monthly?.kobo || _PRICES.pro.monthly.kobo;
      }
    }
  } catch {
    document.getElementById('btn-basic').disabled = true;
    document.getElementById('btn-pro').disabled   = true;
  }
  _highlightCurrentPlan();
}

_init();
