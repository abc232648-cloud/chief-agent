'use strict';

async function loadUsers() {
  if (!chiefContext?.user_management?.roles.length) throw new Error('User management is not available to your role.');
  const data = await api('/api/auth/users');
  const target = document.getElementById('usersList');
  target.replaceChildren();
  for (const user of data.users) {
    const row = document.createElement('div'); row.className = 'item';
    const title = document.createElement('h3'); title.textContent = user.username + (user.is_current_user ? ' (you)' : '');
    const detail = document.createElement('p');
    detail.textContent = user.role + ' · ' + (user.enabled ? 'Enabled' : 'Disabled') + ' · Domains: ' + user.domains.map(id => id === '*' ? 'All domains and installation controls' : (chiefContext.domains.find(d => d.id === id)?.label || id)).join(', ');
    row.append(title, detail);
    if (user.enabled) {
      const button = document.createElement('button'); button.type = 'button'; button.className = 'action danger';
      button.textContent = 'Disable access'; button.dataset.action = 'user-disable';
      button.dataset.id = user.id; button.dataset.username = user.username;
      // Backend independently prevents disabling the last active Owner.
      if (user.role === 'Owner' && (data.users.filter(u => u.enabled && u.role === 'Owner').length === 1 ||
          (user.domains.includes('*') && data.users.filter(u => u.enabled && u.role === 'Owner' && u.domains.includes('*')).length === 1))) {
        button.disabled = true; button.title = 'The last installation-wide Owner must remain enabled.';
      }
      row.append(button);
    }
    target.append(row);
  }
  const scopes = document.getElementById('newUserDomains');
  if (!scopes.children.length) {
    for (const domain of chiefContext.domains.filter(d=>data.management.domains.includes('*') || data.management.domains.includes(d.id))) {
      const label = document.createElement('label'), input = document.createElement('input');
      input.type = 'checkbox'; input.value = domain.id; input.name = 'user-domain';
      label.append(input, document.createTextNode(' ' + domain.label)); scopes.append(label);
    }
  }
  const roleSelect = document.getElementById('newUserRole'), prior = roleSelect.value;
  roleSelect.replaceChildren();
  for (const role of [...data.management.roles].reverse()) {
    const option = document.createElement('option'); option.value = role; option.textContent = role; roleSelect.append(option);
  }
  if (data.management.roles.includes(prior)) roleSelect.value = prior;
  document.getElementById('usersScopeNotice').textContent = chiefContext.role === 'Owner' ? 'You can manage every user. The last active Owner cannot be disabled.' : chiefContext.role === 'Administrator' ? 'You can manage Managers and Workers entirely within your assigned domains.' : 'You can create and disable Workers entirely within your assigned domains. Accounts with any additional domain access are excluded.';
  updateUserRole();
}

function updateUserRole() {
  const privileged = chiefContext.user_management.domains.includes('*') && ['Owner', 'Administrator'].includes(document.getElementById('newUserRole').value);
  document.getElementById('userGlobalLabel').hidden = !privileged;
  if (!privileged) document.getElementById('newUserGlobal').checked = false;
  const global = document.getElementById('newUserGlobal').checked;
  document.querySelectorAll('#newUserDomains input').forEach(input => {input.disabled = global;});
}

async function createChiefUser() {
  const form = document.getElementById('newUserForm');
  if (!form.reportValidity()) return;
  const password = document.getElementById('newUserPassword');
  const repeat = document.getElementById('newUserPasswordRepeat');
  const message = document.getElementById('usersResult');
  try {
    if (password.value !== repeat.value) throw new Error('The passwords do not match.');
    const domains = document.getElementById('newUserGlobal').checked ? ['*'] : [...document.querySelectorAll('#newUserDomains input:checked')].map(input => input.value);
    if (!domains.length) throw new Error('Choose at least one domain.');
    await postJson('/api/auth/users', {username: document.getElementById('newUserName').value.trim(), password: password.value, role: document.getElementById('newUserRole').value, domains});
    form.reset(); message.textContent = 'User created. Share their sign-in details privately. No agent permissions or approvals were granted.';
    await loadUsers();
  } finally {
    // Never retain the new password after success, rejection, or reauthentication.
    password.value = ''; repeat.value = '';
  }
}

async function disableChiefUser(button) {
  if (!confirm('Disable ' + button.dataset.username + '? Their active sessions will be revoked. Their existing records will be kept.')) return;
  await postJson('/api/auth/users/' + encodeURIComponent(button.dataset.id) + '/disable', {});
  await loadUsers();
  document.getElementById('usersResult').textContent = 'Access disabled and active sessions revoked. Historical records were preserved.';
}

function userActions(button) {
  return {'user-create': createChiefUser, 'user-disable': () => disableChiefUser(button), 'users-refresh': loadUsers};
}

document.addEventListener('change', event => {
  if (['newUserRole', 'newUserGlobal'].includes(event.target.id)) updateUserRole();
});
