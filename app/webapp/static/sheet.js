/* Photo OCR — the one sheet every secondary surface opens in (photo, model
 * and style pickers, a History take, a confirm).
 *
 * Built on the vendored modal shell (_vendored/modal/: `.detail-dialog`,
 * `.detail-card`, `.detail-header`, the unpainted × close), so its iOS
 * anchoring and scroll lock come for free and nothing here restyles the
 * shell. Each open builds its own native <dialog> and removes it on close,
 * so a confirm can stack over the sheet that asked for it. A backdrop tap,
 * Escape and the × all dismiss; focus goes back to the opener. No sticky
 * footer: the actions end the sheet in the flow. */

'use strict';

import { icon } from './_vendored/icons/icons.js';

let sheetSeq = 0;

/**
 * Open a sheet.
 *
 * @param {object} o
 * @param {string} o.title                 the heading (also the dialog's name)
 * @param {Node} [o.body]                  content between the header and the actions
 * @param {Array<object>} [o.actions]      full-width rows, in order:
 *   { label, icon?, tone?: 'primary' | 'danger', current?, hidden?, onSelect?() }.
 *   A `danger` action sits last, after a divider, in danger text. `current`
 *   marks a picker's chosen row (a trailing check, aria-current); `hidden`
 *   starts it hidden (the caller reveals it from the returned `actions`).
 *   Choosing an action closes the sheet first, then runs `onSelect`.
 * @param {string} [o.className]           an extra class on the dialog (per use)
 * @param {(reason: string) => void} [o.onClose]  'action' | 'dismiss'
 * @returns {{dialog: HTMLDialogElement, body: HTMLElement, close: () => void,
 *            actions: HTMLButtonElement[]}}  `actions` in the order given
 */
export function openSheet(o) {
  const opener = document.activeElement;
  const titleId = 'sheetTitle' + (++sheetSeq);
  let reason = 'dismiss';

  const dialog = document.createElement('dialog');
  dialog.className = 'detail-dialog sheet' + (o.className ? ' ' + o.className : '');
  dialog.setAttribute('aria-labelledby', titleId);

  const card = document.createElement('div');
  card.className = 'detail-card sheet-card';

  const head = document.createElement('div');
  head.className = 'detail-header';
  const h = document.createElement('h2');
  h.id = titleId;
  h.textContent = o.title;
  const closeBtn = document.createElement('button');
  closeBtn.type = 'button';
  closeBtn.className = 'icon-button detail-close';
  closeBtn.setAttribute('aria-label', 'Close');
  closeBtn.innerHTML = icon('x');
  closeBtn.addEventListener('click', function () { dialog.close(); });
  head.append(h, closeBtn);
  card.appendChild(head);

  const body = document.createElement('div');
  body.className = 'sheet-body';
  if (o.body) body.appendChild(o.body);
  card.appendChild(body);

  const actions = o.actions || [];
  const buttons = [];
  if (actions.length) {
    const list = document.createElement('div');
    list.className = 'sheet-actions';
    actions.forEach(function (a) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = a.tone === 'primary' ? 'button-primary sheet-action' : 'sheet-action';
      if (a.tone === 'danger') btn.dataset.tone = 'danger';
      if (a.icon) btn.innerHTML = icon(a.icon);
      const label = document.createElement('span');
      label.className = 'sheet-action-label';
      label.textContent = a.label;
      btn.appendChild(label);
      btn.hidden = !!a.hidden;
      if (a.current) {
        btn.setAttribute('aria-current', 'true');
        btn.insertAdjacentHTML('beforeend', icon('check', 'sheet-action-check'));
      }
      btn.addEventListener('click', function () {
        reason = 'action';
        dialog.close();
        if (a.onSelect) a.onSelect();
      });
      list.appendChild(btn);
      buttons.push(btn);
    });
    card.appendChild(list);
  }

  dialog.appendChild(card);
  // A tap on the backdrop (the dialog element itself, outside its card).
  dialog.addEventListener('click', function (e) { if (e.target === dialog) dialog.close(); });
  // Escape fires `cancel` then `close`; every way out lands here once.
  dialog.addEventListener('close', function () {
    dialog.remove();
    if (opener && opener.isConnected && typeof opener.focus === 'function') opener.focus();
    if (o.onClose) o.onClose(reason);
  });

  document.body.appendChild(dialog);
  dialog.showModal();
  return {
    dialog: dialog,
    body: body,
    close: function () { dialog.close(); },
    actions: buttons,
  };
}

/**
 * Ask one yes/no question — the app's only confirm (never the browser's own
 * confirm dialog, which ignores the theme, the type scale and the button
 * tiers).
 * Resolves true only on the confirm action; Cancel, the ×, a backdrop tap
 * and Escape all resolve false.
 *
 * @param {object} o
 * @param {string} o.title          the question ("Delete this take?")
 * @param {string} [o.message]      one sentence saying what happens
 * @param {string} o.confirmLabel   the verb ("Delete")
 * @param {boolean} [o.danger]      draw the verb in the danger tint
 * @returns {Promise<boolean>}
 */
export function confirmSheet(o) {
  return new Promise(function (resolve) {
    let yes = false;
    const content = document.createElement('div');
    if (o.message) {
      const p = document.createElement('p');
      p.className = 'sheet-message';
      p.textContent = o.message;
      content.appendChild(p);
    }
    const row = document.createElement('div');
    row.className = 'sheet-confirm';
    const cancel = document.createElement('button');
    cancel.type = 'button';
    cancel.className = 'button-ghost';
    cancel.textContent = 'Cancel';
    const go = document.createElement('button');
    go.type = 'button';
    // A destructive verb restates the tint recipe on danger (design.md
    // "Components"), never a solid red; a neutral one is the primary.
    go.className = o.danger ? 'button-tint danger' : 'button-primary';
    go.dataset.confirm = 'yes';
    go.textContent = o.confirmLabel;
    row.append(cancel, go);
    content.appendChild(row);

    const sheet = openSheet({
      title: o.title,
      body: content,
      className: 'confirm-sheet',
      onClose: function () { resolve(yes); },
    });
    cancel.addEventListener('click', function () { sheet.close(); });
    go.addEventListener('click', function () { yes = true; sheet.close(); });
    // Land on Cancel: Enter on an unread question must not delete.
    cancel.focus();
  });
}
