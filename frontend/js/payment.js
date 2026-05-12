import { api, getToken, getUser, setToken, setUser } from './api.js';

// ── State ─────────────────────────────────────────────────────────────────────
let _publicKey   = '';
let _cycle       = 'weekly';
let _selectedPlan = null;   // 'basic' | 'max'

const _PRICES = {
  basic: {
    weekly:  { kobo:  50000, original_kobo: 100000, discount_pct: 50, label: '₦500',   original_label: '₦1,000' },
    monthly: { kobo: 150000, original_kobo: 300000, discount_pct: 50, label: '₦1,500', original_label: '₦3,000' },
  },
  max: {
    weekly:  { kobo: 100000, original_kobo: 150000, discount_pct: 33, label: '₦1,000', original_label: '₦1,500' },
    monthly: { kobo: 300000, original_kobo: 400000, discount_pct: 25, label: '₦3,000', original_label: '₦4,000' },
  },
};

// ── Cycle toggle ──────────────────────────────────────────────────────────────
window.setCycle = function (c) {
  _cycle = c;
  document.getElementById('btn-weekly') .classList.toggle('active', c === 'weekly');
  document.getElementById('btn-monthly').classList.toggle('active', c === 'monthly');
  _updatePriceLabels();
};

function _priceHtml(p, cycle) {
  const period = cycle === 'weekly' ? '/week' : '/month';
  return `
    <span class="price-original">${p.original_label}</span>
    <span class="price-discount-badge">${p.discount_pct}% OFF</span>
    <br>
    ${p.label}<span class="plan-period">${period}</span>`;
}

function _updatePriceLabels() {
  const basicEl = document.getElementById('basic-price');
  if (basicEl) basicEl.innerHTML = _priceHtml(_PRICES.basic[_cycle], _cycle);
  const maxEl = document.getElementById('pro-price') || document.getElementById('max-price');
  if (maxEl) maxEl.innerHTML = _priceHtml(_PRICES.max[_cycle], _cycle);
}

// ── Select plan and show payment card ─────────────────────────────────────────
window.startPayment = function (plan) {
  if (plan === 'pro') plan = 'max';  // 'pro' is a UI alias for 'max'
  if (!getToken()) { window.location.href = 'index.html'; return; }

  // Block if user already has an active subscription
  const user = getUser();
  if (user?.subscription_expiry) {
    const expiry = new Date(user.subscription_expiry);
    if (expiry > new Date()) {
      const expiryStr = expiry.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
      _showPayAlert(`You already have an active subscription until ${expiryStr}. You can subscribe again after it expires.`, 'error');
      return;
    }
  }

  _selectedPlan = plan;
  const price     = _PRICES[plan]?.[_cycle] || _PRICES.basic[_cycle];
  const planLabel  = plan === 'max' ? 'Max' : 'Basic';
  const cycleLabel = _cycle === 'weekly' ? 'Weekly' : 'Monthly';

  document.getElementById('pay-plan-label').textContent = `${planLabel} — ${cycleLabel}`;
  document.getElementById('pay-amount-label').innerHTML =
    `<span class="price-original" style="font-size:.9rem">${price.original_label}</span>
     <span class="price-discount-badge" style="font-size:.8rem">${price.discount_pct}% OFF</span>
     <br><strong>${price.label}</strong>`;

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
  const price = _PRICES[plan]?.[_cycle] || _PRICES.basic[_cycle];

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
      label:    `Pritis ${plan === 'max' ? 'Max' : 'Basic'} — ${_cycle}`,
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
  const planLabel = plan === 'max' || plan === 'pro' ? 'Max' : 'Basic';
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

  // Highlight active plan card
  const map = { free: 'card-free', basic: 'card-basic', pro: 'card-pro', max: 'card-pro' };
  const card = document.getElementById(map[plan]);
  if (card) card.style.outline = '3px solid #22c55e';

  // Disable button for current plan if subscription is still active
  const isActive = user.subscription_expiry && new Date(user.subscription_expiry) > new Date();
  if (isActive) {
    ['btn-basic', 'btn-pro', 'btn-max'].forEach(id => {
      const btn = document.getElementById(id);
      if (btn) { btn.textContent = plan === 'basic' && id === 'btn-basic' ? 'Current Plan ✓' :
                                   (plan === 'pro' || plan === 'max') && (id === 'btn-pro' || id === 'btn-max') ? 'Current Plan ✓' :
                                   btn.textContent;
                  if (id === `btn-${plan}` || (plan === 'max' && (id === 'btn-pro' || id === 'btn-max'))) {
                    btn.textContent = 'Current Plan ✓'; btn.disabled = true; btn.style.opacity = '.6';
                  }
               }
    });
  }

  const badge = document.getElementById('hero-badge');
  if (badge) {
    const displayPlan = plan === 'max' || plan === 'pro' ? 'MAX' : plan.toUpperCase();
    if (plan === 'free') badge.textContent = 'CHOOSE A PLAN';
    else badge.textContent = `CURRENT PLAN: ${displayPlan} — ACTIVE`;
  }
}

// ── Init: also update config prices for "max" plan ────────────────────────────

// ── Boot ──────────────────────────────────────────────────────────────────────
async function _init() {
  try {
    const cfg = await api.get('/payment/config');
    _publicKey = cfg.public_key || '';
    if (cfg.plans) {
      ['basic', 'max'].forEach(plan => {
        if (!cfg.plans[plan]) return;
        ['weekly', 'monthly'].forEach(cycle => {
          const src = cfg.plans[plan][cycle];
          if (!src) return;
          const dst = _PRICES[plan][cycle];
          if (src.kobo)          dst.kobo          = src.kobo;
          if (src.original_kobo) dst.original_kobo = src.original_kobo;
          if (src.discount_pct)  dst.discount_pct  = src.discount_pct;
          if (src.label)         dst.label         = src.label.replace(' / week','').replace(' / month','');
          // Derive original_label from original_kobo
          dst.original_label = '₦' + (dst.original_kobo / 100).toLocaleString('en-NG');
        });
      });
    }
  } catch {
    ['btn-basic', 'btn-pro', 'btn-max'].forEach(id => {
      const btn = document.getElementById(id);
      if (btn) btn.disabled = true;
    });
  }
  _highlightCurrentPlan();
  _updatePriceLabels();
}

_init();
