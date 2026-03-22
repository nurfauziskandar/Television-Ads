/* ===================================================================
   TV Control — Admin Shared JS
   =================================================================== */

// ---- Toast Notifications ----

function showToast(message, type) {
    type = type || 'info';
    var container = document.getElementById('toast-container');
    var toast = document.createElement('div');
    toast.className = 'toast ' + type;

    var icons = {
        success: 'fa-solid fa-circle-check',
        error: 'fa-solid fa-circle-xmark',
        warning: 'fa-solid fa-triangle-exclamation',
        info: 'fa-solid fa-circle-info'
    };

    toast.innerHTML = '<i class="' + (icons[type] || icons.info) + '"></i><span>' + message + '</span>';
    container.appendChild(toast);

    setTimeout(function() {
        toast.style.opacity = '0';
        toast.style.transform = 'translateX(100%)';
        toast.style.transition = 'all 0.3s ease';
        setTimeout(function() { toast.remove(); }, 300);
    }, 3500);
}

// ---- Modal ----

function openModal(id) {
    var overlay = document.getElementById(id);
    if (overlay) overlay.classList.add('active');
}

function closeModal(id) {
    var overlay = document.getElementById(id);
    if (overlay) overlay.classList.remove('active');
}

// Close modal on overlay click
document.addEventListener('click', function(e) {
    if (e.target.classList.contains('modal-overlay')) {
        e.target.classList.remove('active');
    }
});

// Close modal on Escape
document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
        var modals = document.querySelectorAll('.modal-overlay.active');
        modals.forEach(function(m) { m.classList.remove('active'); });
    }
});

// ---- API Helper ----

async function api(url, options) {
    options = options || {};
    var headers = options.headers || {};
    if (options.body && typeof options.body === 'string') {
        headers['Content-Type'] = 'application/json';
    }
    options.headers = headers;

    var resp = await fetch(url, options);
    var data = await resp.json();

    if (!resp.ok) {
        throw new Error(data.error || 'Request gagal');
    }
    return data;
}

// ---- Sidebar Toggle ----

function toggleSidebar() {
    var sidebar = document.querySelector('.sidebar');
    var overlay = document.querySelector('.sidebar-overlay');
    sidebar.classList.toggle('open');
    overlay.classList.toggle('active');
}

// ---- Sidebar Status ----

function updateSidebarStatus(count) {
    var dot = document.getElementById('sidebar-status-dot');
    var text = document.getElementById('sidebar-display-count');
    if (dot && text) {
        if (count > 0) {
            dot.classList.add('online');
            text.textContent = count + ' display terhubung';
        } else {
            dot.classList.remove('online');
            text.textContent = '0 display terhubung';
        }
    }
}

// ---- Admin Socket (for sidebar status) ----

document.addEventListener('DOMContentLoaded', function() {
    if (typeof io !== 'undefined') {
        var sidebarSocket = io('/admin');
        sidebarSocket.on('display_count', function(data) {
            updateSidebarStatus(data.count);
        });
    }
});
