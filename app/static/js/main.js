/* ============================================================
   Smart 99¢ Plus — Main JavaScript
   ============================================================ */

'use strict';

// ── Cart Drawer ──────────────────────────────────────────────

const cartDrawer  = document.getElementById('cartDrawer');
const cartOverlay = document.getElementById('cartOverlay');
const cartCloseBtn = document.getElementById('cartCloseBtn');

function openCartDrawer() {
  if (!cartDrawer) return;
  cartDrawer.classList.add('open');
  cartOverlay.classList.add('open');
  document.body.style.overflow = 'hidden';
  refreshCartDrawer();
}

function closeCartDrawer() {
  if (!cartDrawer) return;
  cartDrawer.classList.remove('open');
  cartOverlay.classList.remove('open');
  document.body.style.overflow = '';
}

if (cartCloseBtn)  cartCloseBtn.addEventListener('click', closeCartDrawer);
if (cartOverlay)   cartOverlay.addEventListener('click', closeCartDrawer);

// Open drawer on all cart-toggle buttons
document.querySelectorAll('.cart-toggle-btn').forEach(btn => {
  btn.addEventListener('click', openCartDrawer);
});

// Close on Escape
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') closeCartDrawer();
});

// ── Cart Count Badge ─────────────────────────────────────────

function updateCartBadges(count) {
  document.querySelectorAll('#cartBadge, #cartBadgeDesktop').forEach(badge => {
    badge.textContent = count;
    badge.style.display = count > 0 ? 'flex' : 'none';
  });
}

// ── Add to Cart ──────────────────────────────────────────────

async function addToCart(productId, quantity = 1, variantId = 0) {
  try {
    const resp = await fetch('/cart/add', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': getCsrfToken(),
      },
      body: JSON.stringify({
        product_id: productId,
        variant_id: variantId || 0,
        quantity,
      }),
    });

    const data = await resp.json();
    if (data.success) {
      updateCartBadges(data.cart_count);
      openCartDrawer();
      showToast(data.message || 'Added to cart!', 'success');
    } else {
      showToast(data.message || 'Could not add item.', 'error');
    }
  } catch (err) {
    showToast('Network error. Please try again.', 'error');
  }
}

// Attach to all "Add to Cart" buttons
document.addEventListener('click', function (e) {
  const btn = e.target.closest('.add-to-cart-btn');
  if (!btn) return;
  if (btn.disabled) return;

  const productId = btn.dataset.productId;
  const qtyInputId = btn.dataset.qtyInput;
  const quantity = qtyInputId
    ? parseInt(document.getElementById(qtyInputId)?.value || 1, 10)
    : 1;

  // variant_id can come from a sibling hidden input or a data attribute that
  // the swatch JS updates on selection
  let variantId = parseInt(btn.dataset.variantId || '0', 10) || 0;
  const variantInput = document.getElementById('selectedVariantId');
  if (variantInput && variantInput.value) {
    variantId = parseInt(variantInput.value, 10) || 0;
  }

  // Visual feedback
  const original = btn.innerHTML;
  btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Adding…';
  btn.disabled = true;

  addToCart(productId, quantity, variantId).finally(() => {
    btn.innerHTML = original;
    btn.disabled = false;
  });
});

// ── Cart Drawer: Refresh Contents ────────────────────────────

async function refreshCartDrawer() {
  const body   = document.getElementById('cartDrawerBody');
  const footer = document.getElementById('cartDrawerFooter');
  const emptyMsg = document.getElementById('cartEmptyMsg');
  if (!body) return;

  try {
    const resp = await fetch('/cart/drawer');
    if (!resp.ok) return;
    const data = await resp.json();

    if (!data.items || data.items.length === 0) {
      body.innerHTML = `
        <div class="text-center py-5 text-muted" id="cartEmptyMsg">
          <i class="bi bi-bag-x" style="font-size:3rem;"></i>
          <p class="mt-3">Your cart is empty</p>
          <a href="/shop" class="btn btn-primary btn-sm">Start Shopping</a>
        </div>`;
      footer?.classList.add('d-none');
      return;
    }

    body.innerHTML = data.items.map(item => {
      const keyAttr = String(item.line_key).replace(/:/g, '_');
      const colorChip = item.color
        ? `<span class="drawer-item-color"><span class="drawer-item-color-dot" style="background:${escAttr(item.color_hex || '#ccc')};"></span>${escHtml(item.color)}</span>`
        : '';
      return `
      <div class="drawer-cart-item" id="drawerItem_${keyAttr}">
        ${item.image_url
          ? `<img src="${escAttr(item.image_url)}" alt="${escHtml(item.name)}" class="drawer-item-img" />`
          : `<div class="drawer-item-img" style="display:flex;align-items:center;justify-content:center;"><i class="bi bi-image text-muted"></i></div>`
        }
        <div class="drawer-item-info">
          <div class="drawer-item-name">${escHtml(item.name)}</div>
          ${colorChip}
          <div class="drawer-item-price">$${item.unit_price.toFixed(2)}</div>
          <div class="d-flex align-items-center gap-2 mt-1">
            <div class="quantity-selector" style="border-radius:7px;">
              <button class="qty-btn drawer-qty-minus" data-line-key="${escAttr(item.line_key)}" style="padding:.25rem .5rem;font-size:.85rem;">
                <i class="bi bi-dash"></i>
              </button>
              <span style="padding:0 .4rem;font-size:.85rem;font-weight:700;">${item.quantity}</span>
              <button class="qty-btn drawer-qty-plus" data-line-key="${escAttr(item.line_key)}" style="padding:.25rem .5rem;font-size:.85rem;">
                <i class="bi bi-plus"></i>
              </button>
            </div>
            <span class="text-muted small">$${(item.unit_price * item.quantity).toFixed(2)}</span>
          </div>
        </div>
        <button class="drawer-item-remove" data-line-key="${escAttr(item.line_key)}" title="Remove">
          <i class="bi bi-x-lg"></i>
        </button>
      </div>
    `;}).join('');

    if (footer) {
      footer.classList.remove('d-none');
      const subtotalEl = document.getElementById('drawerSubtotal');
      if (subtotalEl) subtotalEl.textContent = `$${data.subtotal.toFixed(2)}`;
    }

  } catch (err) {
    // silently fail — drawer content just won't update
  }
}

// Drawer qty/remove buttons (delegated)
document.addEventListener('click', async function (e) {
  const minusBtn = e.target.closest('.drawer-qty-minus');
  const plusBtn  = e.target.closest('.drawer-qty-plus');
  const removeBtn = e.target.closest('.drawer-item-remove');

  if (minusBtn || plusBtn) {
    const lineKey = (minusBtn || plusBtn).dataset.lineKey;
    const keyAttr = String(lineKey).replace(/:/g, '_');
    const row = document.getElementById(`drawerItem_${keyAttr}`);
    const qtyEl = row?.querySelector('span[style]');
    if (!qtyEl) return;
    const currentQty = parseInt(qtyEl.textContent, 10);
    const newQty = minusBtn ? currentQty - 1 : currentQty + 1;
    await updateCartQty(lineKey, newQty);
    return;
  }

  if (removeBtn) {
    await updateCartQty(removeBtn.dataset.lineKey, 0);
  }
});

async function updateCartQty(lineKey, quantity) {
  const endpoint = quantity <= 0 ? '/cart/remove' : '/cart/update';
  const body = quantity <= 0
    ? { line_key: lineKey }
    : { line_key: lineKey, quantity };

  const resp = await fetch(endpoint, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify(body),
  });
  const data = await resp.json();
  if (data.success) {
    updateCartBadges(data.cart_count);
    refreshCartDrawer();
    // Also update cart page if open
    updateCartPageTotals(data, lineKey);
  }
}

// ── Cart Page: Live Update ────────────────────────────────────

// Cart page qty inputs
document.querySelectorAll('.cart-qty-input').forEach(input => {
  input.addEventListener('change', function () {
    updateCartQty(this.dataset.lineKey, parseInt(this.value, 10));
  });
});

// Cart page +/- buttons
document.addEventListener('click', function (e) {
  const minus = e.target.closest('.cart-table .qty-minus');
  const plus  = e.target.closest('.cart-table .qty-plus');
  if (!minus && !plus) return;

  const lineKey = (minus || plus).dataset.lineKey;
  const input = document.querySelector(`.cart-qty-input[data-line-key="${lineKey}"]`);
  if (!input) return;

  const currentVal = parseInt(input.value, 10);
  if (minus && currentVal > 1) {
    input.value = currentVal - 1;
    updateCartQty(lineKey, currentVal - 1);
  } else if (plus) {
    const max = parseInt(input.max, 10) || 99;
    if (currentVal < max) {
      input.value = currentVal + 1;
      updateCartQty(lineKey, currentVal + 1);
    }
  }
});

// Remove from cart page
document.querySelectorAll('.remove-from-cart-btn').forEach(btn => {
  btn.addEventListener('click', async function () {
    const lineKey = this.dataset.lineKey;
    const keyAttr = String(lineKey).replace(/:/g, '_');
    const row = document.getElementById(`cartRow_${keyAttr}`);
    row?.classList.add('opacity-50');
    const resp = await fetch('/cart/remove', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
      body: JSON.stringify({ line_key: lineKey }),
    });
    const data = await resp.json();
    if (data.success) {
      row?.remove();
      updateCartBadges(data.cart_count);
      updateCartPageTotals(data, lineKey);
    }
  });
});

function updateCartPageTotals(data, lineKey) {
  const subtotalEl = document.getElementById('cartSubtotal');
  const totalEl    = document.getElementById('cartTotal');
  if (subtotalEl && data.subtotal !== undefined) {
    subtotalEl.textContent = `$${parseFloat(data.subtotal).toFixed(2)}`;
  }
  if (totalEl && data.subtotal !== undefined) {
    totalEl.textContent = `$${parseFloat(data.subtotal).toFixed(2)}`;
  }
  if (data.item_total !== undefined && lineKey) {
    const keyAttr = String(lineKey).replace(/:/g, '_');
    const itemTotalEl = document.getElementById(`itemTotal_${keyAttr}`);
    if (itemTotalEl) itemTotalEl.textContent = `$${parseFloat(data.item_total).toFixed(2)}`;
  }
}

function escAttr(s) {
  return String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
}

// ── Drawer Cart JSON endpoint ─────────────────────────────────
// We need a route that returns JSON cart data for the drawer.
// This is handled via a fetch to /cart/json (add to cart.py)

// ── Toast Notifications ───────────────────────────────────────

function showToast(message, type = 'success') {
  const container = getOrCreateToastContainer();
  const id = `toast-${Date.now()}`;
  const iconMap = { success: 'bi-check-circle-fill', error: 'bi-x-circle-fill', warning: 'bi-exclamation-triangle-fill', info: 'bi-info-circle-fill' };
  const colorMap = { success: 'var(--teal)', error: 'var(--primary)', warning: 'var(--accent)', info: '#0dcaf0' };

  const el = document.createElement('div');
  el.id = id;
  el.className = 'toast align-items-center show';
  el.style.cssText = `--bs-toast-border-color:${colorMap[type] || colorMap.success};min-width:260px;`;
  el.setAttribute('role', 'alert');
  el.innerHTML = `
    <div class="d-flex align-items-center gap-2 p-3">
      <i class="bi ${iconMap[type] || iconMap.success}" style="color:${colorMap[type]};font-size:1.1rem;flex-shrink:0;"></i>
      <span class="flex-grow-1" style="font-size:.9rem;font-weight:600;">${escHtml(message)}</span>
      <button type="button" class="btn-close btn-close-sm ms-1" onclick="this.closest('.toast').remove()"></button>
    </div>`;
  container.appendChild(el);
  setTimeout(() => el.remove(), 4000);
}

function getOrCreateToastContainer() {
  let c = document.getElementById('toastContainer');
  if (!c) {
    c = document.createElement('div');
    c.id = 'toastContainer';
    c.style.cssText = 'position:fixed;bottom:1.5rem;right:1.5rem;z-index:9999;display:flex;flex-direction:column;gap:.5rem;';
    document.body.appendChild(c);
  }
  return c;
}

// ── Utilities ──────────────────────────────────────────────────

function getCsrfToken() {
  return document.querySelector('meta[name="csrf-token"]')?.content
    || document.querySelector('[name="csrf_token"]')?.value
    || '';
}

function escHtml(str) {
  const d = document.createElement('div');
  d.appendChild(document.createTextNode(str));
  return d.innerHTML;
}

// ── Navbar Scroll Shadow ───────────────────────────────────────

const mainNav = document.getElementById('mainNav');
if (mainNav) {
  window.addEventListener('scroll', () => {
    if (window.scrollY > 10) {
      mainNav.style.boxShadow = '0 2px 20px rgba(0,0,0,.12)';
    } else {
      mainNav.style.boxShadow = '0 1px 12px rgba(0,0,0,.07)';
    }
  }, { passive: true });
}

// ── Auto-dismiss Flash Alerts ──────────────────────────────────

setTimeout(() => {
  document.querySelectorAll('.alert.fade.show').forEach(alert => {
    alert.classList.remove('show');
    setTimeout(() => alert.remove(), 300);
  });
}, 5000);
