import os
import re
import secrets
import requests as http_requests
from datetime import datetime
from urllib.parse import quote
from flask import Flask, render_template, request, jsonify, Response, redirect, url_for
from flask_socketio import SocketIO, emit, join_room
from werkzeug.utils import secure_filename
from functools import wraps
from flask import session
from models import db, AdminUser, Channel, Ad, Playlist, PlaylistItem, Device, DeviceChannel, DevicePlaylist, AppConfig, CONFIG_DEFAULTS
from config import Config

# Patch on socketserver.BaseServer.handle_error to silently ignore ENOTCONN (errno 57).
# On macOS, port 5000 is shared with AirPlay Receiver via SO_REUSEPORT. When both
# are active, some accepted connections have their data consumed by AirPlay before
# Flask can read them, resulting in ENOTCONN. Using port 8000 avoids the conflict;
# this patch suppresses any residual log noise.
import socketserver as _socketserver
_orig_handle_error = _socketserver.BaseServer.handle_error

def _patched_handle_error(self, request, client_address):
    import sys
    exc = sys.exc_info()[1]
    if isinstance(exc, OSError) and exc.errno == 57:
        return
    _orig_handle_error(self, request, client_address)

_socketserver.BaseServer.handle_error = _patched_handle_error

# Patch simple-websocket to handle ENOTCONN (errno 57) gracefully on macOS.
# This error is raised when the OS reports a socket as "not connected" mid-handshake,
# which happens when connecting via IP address with Werkzeug threading mode.
try:
    import simple_websocket
    _orig_receive = simple_websocket.Server.receive

    def _patched_receive(self):
        try:
            return _orig_receive(self)
        except OSError as e:
            if e.errno == 57:
                raise simple_websocket.ConnectionClosed(message='')
            raise

    simple_websocket.Server.receive = _patched_receive
except (ImportError, AttributeError):
    pass

app = Flask(__name__)
app.config.from_object(Config)
db.init_app(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# token -> display info dict
connected_displays = {}
# socket sid -> token (reverse lookup)
sid_to_token = {}

LOGO_FOLDER = os.path.join('static', 'uploads', 'logo')

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['CHANNEL_FOLDER'], exist_ok=True)
os.makedirs(LOGO_FOLDER, exist_ok=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def allowed_file(filename):
    return '.' in filename and \
        filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']


def allowed_video(filename):
    return '.' in filename and \
        filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_VIDEO_EXTENSIONS']


def get_file_type(filename):
    ext = filename.rsplit('.', 1)[1].lower()
    return 'video' if ext in {'mp4', 'webm', 'mov', 'avi'} else 'image'


def channel_to_dict(ch):
    url = ch.url
    if ch.source_type == 'local' and ch.filename:
        url = f'/static/uploads/channels/{ch.filename}'
    return {
        'id': ch.id, 'name': ch.name, 'url': url,
        'source_type': ch.source_type,
        'logo_url': ch.logo_url, 'group': ch.group,
        'order': ch.order, 'is_active': ch.is_active,
    }


def ad_to_dict(a):
    return {
        'id': a.id, 'name': a.name, 'filename': a.filename,
        'file_type': a.file_type, 'duration': a.duration,
        'is_active': a.is_active,
        'url': f'/static/uploads/ads/{a.filename}',
    }


def playlist_to_dict(pl, include_items=True):
    data = {
        'id': pl.id, 'name': pl.name, 'is_active': pl.is_active,
        'item_count': len(pl.items),
    }
    if include_items:
        data['items'] = [
            {'id': it.id, 'order': it.order, 'ad': ad_to_dict(it.ad)}
            for it in pl.items if it.ad
        ]
    return data


def device_to_dict(dev, include_assignments=False):
    is_online = dev.token in connected_displays
    info = connected_displays.get(dev.token, {})
    data = {
        'id': dev.id,
        'name': dev.name,
        'location': dev.location,
        'token': dev.token,
        'is_active': dev.is_active,
        'is_online': is_online,
        'mode': info.get('mode', 'idle'),
        'playing': info.get('playing', '-'),
        'channel_count': len(dev.channels),
        'playlist_count': len(dev.playlists),
    }
    if include_assignments:
        data['channels'] = [
            channel_to_dict(dc.channel)
            for dc in dev.channels
            if dc.channel and dc.channel.is_active
        ]
        data['playlists'] = [
            playlist_to_dict(dp.playlist, include_items=False)
            for dp in dev.playlists
            if dp.playlist and dp.playlist.is_active
        ]
    return data


def get_displays_summary():
    return {
        'count': len(connected_displays),
        'displays': [
            {k: v for k, v in info.items() if k != 'sid'}
            for info in connected_displays.values()
        ],
    }


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if AdminUser.query.count() == 0:
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Setup belum selesai'}), 403
            return redirect(url_for('setup'))
        if not session.get('admin_logged_in'):
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Unauthorized'}), 401
            return redirect(url_for('login', next=request.path))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    """Hanya role 'admin' yang diizinkan. Harus digunakan setelah login_required."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if AdminUser.query.count() == 0:
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Setup belum selesai'}), 403
            return redirect(url_for('setup'))
        if not session.get('admin_logged_in'):
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Unauthorized'}), 401
            return redirect(url_for('login', next=request.path))
        # Session lama (sebelum fitur role) tidak punya admin_role;
        # perlakukan sebagai admin agar tidak mengunci pengguna yang sudah login.
        role = session.get('admin_role')
        if role is None:
            # Sinkronkan role dari database
            user = AdminUser.query.filter_by(
                username=session.get('admin_username')
            ).first()
            if user:
                session['admin_role'] = user.role
                role = user.role
        if role != 'admin':
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Forbidden'}), 403
            return redirect(url_for('admin_dashboard'))
        return f(*args, **kwargs)
    return decorated


@app.route('/setup', methods=['GET', 'POST'])
def setup():
    if AdminUser.query.count() > 0:
        return redirect(url_for('login'))
    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        confirm = request.form.get('confirm', '')
        if not username or not password:
            error = 'Username dan password wajib diisi'
        elif len(username) < 3:
            error = 'Username minimal 3 karakter'
        elif len(password) < 8:
            error = 'Password minimal 8 karakter'
        elif password != confirm:
            error = 'Konfirmasi password tidak cocok'
        else:
            user = AdminUser(username=username, role='admin')
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            session.permanent = True
            session['admin_logged_in'] = True
            session['admin_username'] = username
            session['admin_role'] = 'admin'
            return redirect(url_for('admin_dashboard'))
    return render_template('setup.html', error=error)


@app.route('/login', methods=['GET', 'POST'])
def login():
    if AdminUser.query.count() == 0:
        return redirect(url_for('setup'))
    if session.get('admin_logged_in'):
        return redirect(url_for('admin_dashboard'))
    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        user = AdminUser.query.filter_by(username=username).first()
        if user and user.check_password(password):
            session.permanent = True
            session['admin_logged_in'] = True
            session['admin_username'] = username
            session['admin_role'] = user.role
            next_url = request.form.get('next') or url_for('admin_dashboard')
            return redirect(next_url)
        error = 'Username atau password salah'
    return render_template('login.html', error=error,
                           next=request.args.get('next', ''))


@app.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return redirect(url_for('login'))


def parse_m3u(content):
    channels = []
    lines = content.strip().split('\n')
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith('#EXTINF:'):
            info = line[8:]
            name = info.split(',', 1)[-1].strip() if ',' in info else 'Unknown'
            logo = re.search(r'tvg-logo="([^"]*)"', info)
            group = re.search(r'group-title="([^"]*)"', info)
            logo_url = logo.group(1) if logo else ''
            group_name = group.group(1) if group else 'Umum'
            i += 1
            while i < len(lines) and lines[i].strip().startswith('#'):
                i += 1
            if i < len(lines):
                url = lines[i].strip()
                if url and not url.startswith('#'):
                    channels.append({
                        'name': name, 'url': url,
                        'logo_url': logo_url, 'group': group_name or 'Umum',
                    })
        i += 1
    return channels


# ---------------------------------------------------------------------------
# Admin Pages
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/')
@login_required
def admin_dashboard():
    stats = {
        'channels': Channel.query.count(),
        'active_channels': Channel.query.filter_by(is_active=True).count(),
        'ads': Ad.query.count(),
        'active_ads': Ad.query.filter_by(is_active=True).count(),
        'playlists': Playlist.query.count(),
        'active_playlists': Playlist.query.filter_by(is_active=True).count(),
        'devices': Device.query.count(),
        'displays': len(connected_displays),
    }
    return render_template('dashboard.html', page='dashboard', stats=stats)


@app.route('/admin/devices')
@admin_required
def admin_devices():
    devices = Device.query.order_by(Device.created_at).all()
    channels = Channel.query.filter_by(is_active=True).order_by(Channel.group, Channel.name).all()
    playlists = Playlist.query.filter_by(is_active=True).all()
    return render_template('devices.html', page='devices',
                           devices=devices, channels=channels, playlists=playlists)


@app.route('/admin/channels')
@admin_required
def admin_channels():
    channels = Channel.query.order_by(Channel.order, Channel.id).all()
    groups = sorted({ch.group for ch in channels if ch.group})
    return render_template('channels.html', page='channels', channels=channels, groups=groups)


@app.route('/admin/ads')
@login_required
def admin_ads():
    ads = Ad.query.order_by(Ad.created_at.desc()).all()
    return render_template('ads.html', page='ads', ads=ads)


@app.route('/admin/playlists')
@admin_required
def admin_playlists():
    playlists = Playlist.query.order_by(Playlist.created_at.desc()).all()
    ads = Ad.query.filter_by(is_active=True).order_by(Ad.name).all()
    return render_template('playlists.html', page='playlists', playlists=playlists, ads=ads)


@app.route('/admin/control')
@admin_required
def admin_control():
    devices = Device.query.filter_by(is_active=True).order_by(Device.name).all()
    return render_template('control.html', page='control', devices=devices)


# ---------------------------------------------------------------------------
# Admin — User Management
# ---------------------------------------------------------------------------

@app.route('/admin/users')
@admin_required
def admin_users():
    users = AdminUser.query.order_by(AdminUser.created_at).all()
    return render_template('users.html', page='users', users=users)


@app.route('/api/users', methods=['GET'])
@admin_required
def api_get_users():
    users = AdminUser.query.order_by(AdminUser.created_at).all()
    return jsonify([
        {'id': u.id, 'username': u.username, 'role': u.role,
         'created_at': u.created_at.isoformat()}
        for u in users
    ])


@app.route('/api/users', methods=['POST'])
@admin_required
def api_create_user():
    data = request.json or {}
    username = (data.get('username') or '').strip()
    password = data.get('password', '')
    role = data.get('role', 'user')

    if not username or len(username) < 3:
        return jsonify({'error': 'Username minimal 3 karakter'}), 400
    if len(password) < 8:
        return jsonify({'error': 'Password minimal 8 karakter'}), 400
    if role not in ('admin', 'user'):
        return jsonify({'error': 'Role tidak valid'}), 400
    if AdminUser.query.filter_by(username=username).first():
        return jsonify({'error': 'Username sudah digunakan'}), 409

    user = AdminUser(username=username, role=role)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    return jsonify({'id': user.id, 'username': user.username, 'role': user.role}), 201


@app.route('/api/users/<int:uid>', methods=['PUT'])
@admin_required
def api_update_user(uid):
    user = AdminUser.query.get_or_404(uid)
    data = request.json or {}

    # Cegah admin menghapus role admin dari dirinya sendiri
    if str(uid) == str(session.get('admin_id')) and data.get('role') == 'user':
        return jsonify({'error': 'Tidak dapat mengubah role akun sendiri'}), 400

    new_username = (data.get('username') or '').strip()
    if new_username and new_username != user.username:
        if len(new_username) < 3:
            return jsonify({'error': 'Username minimal 3 karakter'}), 400
        if AdminUser.query.filter(
            AdminUser.username == new_username, AdminUser.id != uid
        ).first():
            return jsonify({'error': 'Username sudah digunakan'}), 409
        user.username = new_username

    new_password = data.get('password', '')
    if new_password:
        if len(new_password) < 8:
            return jsonify({'error': 'Password minimal 8 karakter'}), 400
        user.set_password(new_password)

    if 'role' in data:
        if data['role'] not in ('admin', 'user'):
            return jsonify({'error': 'Role tidak valid'}), 400
        user.role = data['role']

    db.session.commit()
    return jsonify({'id': user.id, 'username': user.username, 'role': user.role})


@app.route('/api/users/<int:uid>', methods=['DELETE'])
@admin_required
def api_delete_user(uid):
    user = AdminUser.query.get_or_404(uid)
    # Cegah admin menghapus dirinya sendiri
    if user.username == session.get('admin_username'):
        return jsonify({'error': 'Tidak dapat menghapus akun yang sedang login'}), 400
    # Pastikan selalu ada minimal satu admin
    if user.role == 'admin' and AdminUser.query.filter_by(role='admin').count() <= 1:
        return jsonify({'error': 'Harus ada minimal satu akun admin'}), 400
    db.session.delete(user)
    db.session.commit()
    return jsonify({'status': 'deleted'})


# ---------------------------------------------------------------------------
# Admin — Settings
# ---------------------------------------------------------------------------

# (magic_bytes, offset, mime) — validasi tipe file dari konten, bukan extension
_ALLOWED_LOGO_MAGIC = [
    (b'\x89PNG\r\n\x1a\n', 0, 'png'),
    (b'\xff\xd8\xff',       0, 'jpg'),
    (b'RIFF',               0, 'webp'),   # WEBP: RIFF....WEBP
]
_LOGO_MAX_BYTES = 2 * 1024 * 1024  # 2 MB


def _detect_logo_ext(stream):
    """Baca 12 byte pertama dan kembalikan ekstensi jika tipe diizinkan, else None."""
    header = stream.read(12)
    stream.seek(0)
    for magic, offset, ext in _ALLOWED_LOGO_MAGIC:
        if header[offset:offset + len(magic)] == magic:
            if ext == 'webp' and header[8:12] != b'WEBP':
                continue
            return ext
    return None


def _hex_to_rgba(hex_color, alpha):
    """Konversi hex color (#rrggbb) ke string rgba() dengan alpha tertentu."""
    h = hex_color.lstrip('#')
    if len(h) == 3:
        h = ''.join(c * 2 for c in h)
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return f'rgba({r}, {g}, {b}, {alpha})'
    except Exception:
        return hex_color


@app.route('/admin/settings')
@admin_required
def admin_settings():
    config = {k: AppConfig.get(k, v) for k, v in CONFIG_DEFAULTS.items()}
    return render_template('settings.html', page='settings', config=config)


@app.route('/api/config', methods=['GET'])
@admin_required
def api_get_config():
    return jsonify({k: AppConfig.get(k, v) for k, v in CONFIG_DEFAULTS.items()})


@app.route('/api/config', methods=['POST'])
@admin_required
def api_update_config():
    data = request.json or {}
    for key, value in data.items():
        if key in CONFIG_DEFAULTS:
            AppConfig.set(key, str(value))
    db.session.commit()
    return jsonify({'status': 'ok'})


@app.route('/api/config/css')
def api_config_css():
    """Endpoint CSS yang meng-override variabel warna admin panel."""
    accent       = AppConfig.get('color_accent',       CONFIG_DEFAULTS['color_accent'])
    accent_hover = AppConfig.get('color_accent_hover',  CONFIG_DEFAULTS['color_accent_hover'])
    success      = AppConfig.get('color_success',       CONFIG_DEFAULTS['color_success'])
    warning      = AppConfig.get('color_warning',       CONFIG_DEFAULTS['color_warning'])
    danger       = AppConfig.get('color_danger',        CONFIG_DEFAULTS['color_danger'])
    bg_primary   = AppConfig.get('color_bg_primary',    CONFIG_DEFAULTS['color_bg_primary'])
    bg_secondary = AppConfig.get('color_bg_secondary',  CONFIG_DEFAULTS['color_bg_secondary'])
    bg_card      = AppConfig.get('color_bg_card',       CONFIG_DEFAULTS['color_bg_card'])
    text_primary = AppConfig.get('color_text_primary',  CONFIG_DEFAULTS['color_text_primary'])

    css = f""":root {{
  --accent:          {accent};
  --accent-hover:    {accent_hover};
  --accent-subtle:   {_hex_to_rgba(accent, 0.12)};
  --success:         {success};
  --success-subtle:  {_hex_to_rgba(success, 0.12)};
  --warning:         {warning};
  --warning-subtle:  {_hex_to_rgba(warning, 0.12)};
  --danger:          {danger};
  --danger-subtle:   {_hex_to_rgba(danger, 0.12)};
  --bg-primary:      {bg_primary};
  --bg-secondary:    {bg_secondary};
  --bg-card:         {bg_card};
  --text-primary:    {text_primary};
}}"""
    return Response(css, content_type='text/css',
                    headers={'Cache-Control': 'no-cache'})


@app.route('/api/config/logo', methods=['POST'])
@admin_required
def api_upload_logo():
    if 'file' not in request.files:
        return jsonify({'error': 'File tidak ditemukan'}), 400
    f = request.files['file']
    if not f or f.filename == '':
        return jsonify({'error': 'File kosong'}), 400

    # Validasi ukuran
    f.stream.seek(0, 2)
    size = f.stream.tell()
    f.stream.seek(0)
    if size > _LOGO_MAX_BYTES:
        return jsonify({'error': 'Ukuran file melebihi 2 MB'}), 400

    # Validasi magic bytes — tolak jika bukan PNG/JPEG/WEBP
    ext = _detect_logo_ext(f.stream)
    if not ext:
        return jsonify({'error': 'Tipe file tidak diizinkan. Gunakan PNG, JPEG, atau WEBP'}), 400

    # Hapus logo lama jika ada
    old_logo = AppConfig.get('idle_logo', '')
    if old_logo:
        old_path = os.path.join(LOGO_FOLDER, old_logo)
        if os.path.exists(old_path):
            os.remove(old_path)

    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f'idle_logo_{ts}.{ext}'
    f.save(os.path.join(LOGO_FOLDER, filename))

    AppConfig.set('idle_logo', filename)
    db.session.commit()
    return jsonify({'filename': filename, 'url': f'/static/uploads/logo/{filename}'}), 201


@app.route('/api/config/logo', methods=['DELETE'])
@admin_required
def api_delete_logo():
    filename = AppConfig.get('idle_logo', '')
    if filename:
        path = os.path.join(LOGO_FOLDER, filename)
        if os.path.exists(path):
            os.remove(path)
        AppConfig.set('idle_logo', '')
        db.session.commit()
    return jsonify({'status': 'deleted'})


@app.route('/api/config/display')
def api_config_display():
    """Konfigurasi idle display — tidak memerlukan auth (diakses display client)."""
    return jsonify({
        k: AppConfig.get(k, CONFIG_DEFAULTS[k])
        for k in ('idle_style', 'idle_show_clock', 'idle_show_date', 'idle_logo',
                  'idle_label', 'idle_bg_from', 'idle_bg_to',
                  'ticker_enabled', 'ticker_text', 'ticker_speed',
                  'ticker_font_size', 'ticker_bg', 'ticker_color')
    })


# ---------------------------------------------------------------------------
# Display Page
# ---------------------------------------------------------------------------

@app.route('/display')
def display():
    return render_template('display.html')


# ---------------------------------------------------------------------------
# API — Channels
# ---------------------------------------------------------------------------

@app.route('/api/channels', methods=['GET'])
@admin_required
def api_get_channels():
    channels = Channel.query.order_by(Channel.order, Channel.id).all()
    return jsonify([channel_to_dict(c) for c in channels])


@app.route('/api/channels', methods=['POST'])
@admin_required
def api_create_channel():
    data = request.json
    ch = Channel(
        name=data['name'], url=data.get('url', ''),
        logo_url=data.get('logo_url', ''),
        group=data.get('group', 'Umum'),
        order=data.get('order', 0),
    )
    db.session.add(ch)
    db.session.commit()
    return jsonify(channel_to_dict(ch)), 201


@app.route('/api/channels/<int:cid>', methods=['PUT'])
@admin_required
def api_update_channel(cid):
    ch = Channel.query.get_or_404(cid)
    data = request.json
    ch.name = data.get('name', ch.name)
    ch.url = data.get('url', ch.url)
    ch.logo_url = data.get('logo_url', ch.logo_url)
    ch.group = data.get('group', ch.group)
    ch.order = data.get('order', ch.order)
    if 'is_active' in data:
        ch.is_active = data['is_active']
    db.session.commit()
    return jsonify(channel_to_dict(ch))


@app.route('/api/channels/<int:cid>', methods=['DELETE'])
@admin_required
def api_delete_channel(cid):
    ch = Channel.query.get_or_404(cid)
    if ch.source_type == 'local' and ch.filename:
        filepath = os.path.join(app.config['CHANNEL_FOLDER'], ch.filename)
        if os.path.exists(filepath):
            os.remove(filepath)
    db.session.delete(ch)
    db.session.commit()
    return jsonify({'status': 'deleted'})


@app.route('/api/channels/upload', methods=['POST'])
@admin_required
def api_upload_channel():
    if 'file' not in request.files:
        return jsonify({'error': 'File tidak ditemukan'}), 400
    f = request.files['file']
    if f.filename == '' or not allowed_video(f.filename):
        return jsonify({'error': 'Tipe file tidak diizinkan'}), 400

    original = secure_filename(f.filename)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"{ts}_{original}"
    f.save(os.path.join(app.config['CHANNEL_FOLDER'], filename))

    ch = Channel(
        name=request.form.get('name', original.rsplit('.', 1)[0]),
        url='', source_type='local', filename=filename,
        logo_url='', group=request.form.get('group', 'Lokal'),
    )
    db.session.add(ch)
    db.session.commit()
    return jsonify(channel_to_dict(ch)), 201


@app.route('/api/channels/import', methods=['POST'])
@admin_required
def api_import_channels():
    data = request.json
    m3u_url = data.get('url', '')
    m3u_text = data.get('text', '')

    if m3u_url:
        try:
            resp = http_requests.get(m3u_url, timeout=30,
                                     headers={'User-Agent': 'Mozilla/5.0'})
            m3u_text = resp.text
        except Exception as e:
            return jsonify({'error': str(e)}), 400

    if not m3u_text:
        return jsonify({'error': 'No M3U data provided'}), 400

    parsed = parse_m3u(m3u_text)
    added = 0
    for item in parsed:
        if not Channel.query.filter_by(url=item['url']).first():
            db.session.add(Channel(**item))
            added += 1
    db.session.commit()
    return jsonify({'added': added, 'total_parsed': len(parsed)})


# ---------------------------------------------------------------------------
# API — Ads
# ---------------------------------------------------------------------------

@app.route('/api/ads', methods=['GET'])
@login_required
def api_get_ads():
    return jsonify([ad_to_dict(a) for a in Ad.query.order_by(Ad.created_at.desc()).all()])


@app.route('/api/ads', methods=['POST'])
@login_required
def api_create_ad():
    if 'file' not in request.files:
        return jsonify({'error': 'File tidak ditemukan'}), 400
    f = request.files['file']
    if f.filename == '' or not allowed_file(f.filename):
        return jsonify({'error': 'Tipe file tidak diizinkan'}), 400

    original = secure_filename(f.filename)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"{ts}_{original}"
    f.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))

    ad = Ad(
        name=request.form.get('name', original),
        filename=filename, file_type=get_file_type(filename),
        duration=int(request.form.get('duration', 10)),
    )
    db.session.add(ad)
    db.session.commit()
    return jsonify(ad_to_dict(ad)), 201


@app.route('/api/ads/<int:aid>', methods=['PUT'])
@admin_required
def api_update_ad(aid):
    ad = Ad.query.get_or_404(aid)
    data = request.json
    ad.name = data.get('name', ad.name)
    ad.duration = data.get('duration', ad.duration)
    if 'is_active' in data:
        ad.is_active = data['is_active']
    db.session.commit()
    return jsonify(ad_to_dict(ad))


@app.route('/api/ads/<int:aid>', methods=['DELETE'])
@admin_required
def api_delete_ad(aid):
    ad = Ad.query.get_or_404(aid)
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], ad.filename)
    if os.path.exists(filepath):
        os.remove(filepath)
    db.session.delete(ad)
    db.session.commit()
    return jsonify({'status': 'deleted'})


# ---------------------------------------------------------------------------
# API — Playlists
# ---------------------------------------------------------------------------

@app.route('/api/playlists', methods=['GET'])
@admin_required
def api_get_playlists():
    return jsonify([playlist_to_dict(p) for p in Playlist.query.order_by(Playlist.created_at.desc()).all()])


@app.route('/api/playlists', methods=['POST'])
@admin_required
def api_create_playlist():
    pl = Playlist(name=request.json['name'])
    db.session.add(pl)
    db.session.commit()
    return jsonify(playlist_to_dict(pl)), 201


@app.route('/api/playlists/<int:pid>', methods=['PUT'])
@admin_required
def api_update_playlist(pid):
    pl = Playlist.query.get_or_404(pid)
    data = request.json
    pl.name = data.get('name', pl.name)
    if 'is_active' in data:
        pl.is_active = data['is_active']
    db.session.commit()
    return jsonify(playlist_to_dict(pl))


@app.route('/api/playlists/<int:pid>', methods=['DELETE'])
@admin_required
def api_delete_playlist(pid):
    db.session.delete(Playlist.query.get_or_404(pid))
    db.session.commit()
    return jsonify({'status': 'deleted'})


@app.route('/api/playlists/<int:pid>/items', methods=['POST'])
@admin_required
def api_add_playlist_item(pid):
    pl = Playlist.query.get_or_404(pid)
    max_order = db.session.query(db.func.max(PlaylistItem.order)).filter_by(playlist_id=pid).scalar() or 0
    item = PlaylistItem(playlist_id=pid, ad_id=request.json['ad_id'], order=max_order + 1)
    db.session.add(item)
    db.session.commit()
    return jsonify(playlist_to_dict(pl)), 201


@app.route('/api/playlists/<int:pid>/items/<int:iid>', methods=['DELETE'])
@admin_required
def api_remove_playlist_item(pid, iid):
    db.session.delete(PlaylistItem.query.filter_by(id=iid, playlist_id=pid).first_or_404())
    db.session.commit()
    return jsonify(playlist_to_dict(Playlist.query.get(pid)))


@app.route('/api/playlists/<int:pid>/items/reorder', methods=['PUT'])
@admin_required
def api_reorder_playlist_items(pid):
    for idx, item_id in enumerate(request.json.get('items', [])):
        item = PlaylistItem.query.filter_by(id=item_id, playlist_id=pid).first()
        if item:
            item.order = idx
    db.session.commit()
    return jsonify(playlist_to_dict(Playlist.query.get(pid)))


# ---------------------------------------------------------------------------
# API — Devices
# ---------------------------------------------------------------------------

@app.route('/api/devices', methods=['GET'])
@admin_required
def api_get_devices():
    return jsonify([device_to_dict(d) for d in Device.query.order_by(Device.name).all()])


@app.route('/api/devices/<int:did>', methods=['GET'])
@admin_required
def api_get_device(did):
    return jsonify(device_to_dict(Device.query.get_or_404(did), include_assignments=True))


@app.route('/api/devices', methods=['POST'])
@admin_required
def api_create_device():
    data = request.json
    device = Device(
        name=data['name'],
        location=data.get('location', ''),
        token=secrets.token_hex(16),
    )
    db.session.add(device)
    db.session.commit()
    return jsonify(device_to_dict(device)), 201


@app.route('/api/devices/<int:did>', methods=['PUT'])
@admin_required
def api_update_device(did):
    device = Device.query.get_or_404(did)
    data = request.json
    device.name = data.get('name', device.name)
    device.location = data.get('location', device.location)
    if 'is_active' in data:
        device.is_active = data['is_active']
    db.session.commit()
    return jsonify(device_to_dict(device))


@app.route('/api/devices/<int:did>', methods=['DELETE'])
@admin_required
def api_delete_device(did):
    device = Device.query.get_or_404(did)
    if device.token in connected_displays:
        sid = connected_displays[device.token].get('sid')
        connected_displays.pop(device.token, None)
        if sid:
            sid_to_token.pop(sid, None)
    db.session.delete(device)
    db.session.commit()
    return jsonify({'status': 'deleted'})


@app.route('/api/devices/<int:did>/token', methods=['POST'])
@admin_required
def api_regenerate_token(did):
    device = Device.query.get_or_404(did)
    data = request.json or {}
    custom_token = data.get('token', '').strip()

    if custom_token:
        if len(custom_token) < 4:
            return jsonify({'error': 'Token minimal 4 karakter'}), 400
        if len(custom_token) > 64:
            return jsonify({'error': 'Token maksimal 64 karakter'}), 400
        if not re.match(r'^[a-zA-Z0-9_\-]+$', custom_token):
            return jsonify({'error': 'Token hanya boleh mengandung huruf, angka, - dan _'}), 400
        conflict = Device.query.filter(Device.token == custom_token, Device.id != did).first()
        if conflict:
            return jsonify({'error': 'Token sudah digunakan perangkat lain'}), 409
        new_token = custom_token
    else:
        new_token = secrets.token_hex(16)

    connected_displays.pop(device.token, None)
    device.token = new_token
    db.session.commit()
    return jsonify(device_to_dict(device))


@app.route('/api/devices/<int:did>/channels', methods=['PUT'])
@admin_required
def api_batch_assign_channels(did):
    device = Device.query.get_or_404(did)
    channel_ids = set(request.json.get('channel_ids', []))
    DeviceChannel.query.filter_by(device_id=did).delete()
    for ch_id in channel_ids:
        if Channel.query.get(ch_id):
            db.session.add(DeviceChannel(device_id=did, channel_id=ch_id))
    db.session.commit()
    return jsonify(device_to_dict(device, include_assignments=True))


@app.route('/api/devices/<int:did>/playlists', methods=['PUT'])
@admin_required
def api_batch_assign_playlists(did):
    device = Device.query.get_or_404(did)
    playlist_ids = set(request.json.get('playlist_ids', []))
    DevicePlaylist.query.filter_by(device_id=did).delete()
    for pl_id in playlist_ids:
        if Playlist.query.get(pl_id):
            db.session.add(DevicePlaylist(device_id=did, playlist_id=pl_id))
    db.session.commit()
    return jsonify(device_to_dict(device, include_assignments=True))


# ---------------------------------------------------------------------------
# API — Display Command (per-device)
# ---------------------------------------------------------------------------

@app.route('/api/display/command', methods=['POST'])
@admin_required
def api_display_command():
    data = request.json
    device_id = data.get('device_id')
    action = data.get('action')

    device = Device.query.get_or_404(device_id)
    room = 'device_' + device.token

    if action == 'play_channel':
        ch = Channel.query.get(data.get('channel_id'))
        if not ch:
            return jsonify({'error': 'Channel tidak ditemukan'}), 404
        socketio.emit('command', {
            'action': 'play_channel',
            'channel': channel_to_dict(ch),
        }, room=room, namespace='/display')
        return jsonify({'status': 'ok', 'action': action, 'channel': ch.name})

    elif action == 'play_ads':
        pl = Playlist.query.get(data.get('playlist_id'))
        if not pl:
            return jsonify({'error': 'Playlist tidak ditemukan'}), 404
        ads = [ad_to_dict(i.ad) for i in pl.items if i.ad and i.ad.is_active]
        socketio.emit('command', {
            'action': 'play_ads',
            'playlist': {'id': pl.id, 'name': pl.name, 'ads': ads},
        }, room=room, namespace='/display')
        return jsonify({'status': 'ok', 'action': action, 'playlist': pl.name})

    elif action == 'idle':
        socketio.emit('command', {'action': 'idle'}, room=room, namespace='/display')
        return jsonify({'status': 'ok', 'action': 'idle'})

    elif action == 'set_volume':
        vol = data.get('volume', 80)
        socketio.emit('command', {'action': 'set_volume', 'volume': vol},
                      room=room, namespace='/display')
        return jsonify({'status': 'ok', 'volume': vol})

    elif action == 'set_ticker':
        text = data.get('text', '')[:50]
        ticker = {
            'enabled':   data.get('enabled', False),
            'text':      text,
            'speed':     data.get('speed', 40),
            'font_size': max(10, min(48, int(data.get('font_size', 14)))),
        }
        AppConfig.set('ticker_enabled',   'true' if ticker['enabled'] else 'false')
        AppConfig.set('ticker_text',      ticker['text'])
        AppConfig.set('ticker_speed',     str(ticker['speed']))
        AppConfig.set('ticker_font_size', str(ticker['font_size']))
        db.session.commit()
        socketio.emit('command', {'action': 'set_ticker', **ticker},
                      room=room, namespace='/display')
        return jsonify({'status': 'ok', **ticker})

    return jsonify({'error': 'Aksi tidak dikenal'}), 400


@app.route('/api/display/status')
@admin_required
def api_display_status():
    return jsonify(get_displays_summary())


@app.route('/api/report/devices')
@login_required
def api_report_devices():
    devices = Device.query.order_by(Device.created_at).all()
    rows = []
    for dev in devices:
        info = connected_displays.get(dev.token)
        rows.append({
            'id':       dev.id,
            'name':     dev.name,
            'location': dev.location or '-',
            'status':   'online' if info else 'offline',
            'mode':     info.get('mode', '-') if info else '-',
            'playing':  info.get('playing', '-') if info else '-',
        })
    total   = len(rows)
    online  = sum(1 for r in rows if r['status'] == 'online')
    return jsonify({
        'total':   total,
        'online':  online,
        'offline': total - online,
        'devices': rows,
    })


# ---------------------------------------------------------------------------
# Stream Proxy (CORS bypass for IPTV)
# ---------------------------------------------------------------------------

@app.route('/api/proxy/stream')
def proxy_stream():
    url = request.args.get('url')
    if not url:
        return jsonify({'error': 'URL required'}), 400

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                      'AppleWebKit/537.36 (KHTML, like Gecko) '
                      'Chrome/120.0.0.0 Safari/537.36'
    }

    try:
        is_manifest = any(x in url.lower() for x in ['.m3u8', '.m3u'])

        if is_manifest:
            resp = http_requests.get(url, timeout=15, headers=headers)
            text = resp.text
            base_url = url.rsplit('/', 1)[0]
            new_lines = []
            for raw_line in text.split('\n'):
                line = raw_line.strip()
                if line and not line.startswith('#'):
                    if not line.startswith('http'):
                        line = base_url + '/' + line
                    line = '/api/proxy/stream?url=' + quote(line, safe='')
                elif 'URI="' in line:
                    def _rewrite(m):
                        u = m.group(1)
                        if not u.startswith('http'):
                            u = base_url + '/' + u
                        return 'URI="/api/proxy/stream?url=' + quote(u, safe='') + '"'
                    line = re.sub(r'URI="([^"]*)"', _rewrite, line)
                new_lines.append(line)
            return Response(
                '\n'.join(new_lines),
                content_type='application/vnd.apple.mpegurl',
                headers={'Access-Control-Allow-Origin': '*', 'Cache-Control': 'no-cache'},
            )
        else:
            resp = http_requests.get(url, stream=True, timeout=30, headers=headers)

            def generate():
                try:
                    for chunk in resp.iter_content(chunk_size=65536):
                        yield chunk
                except (OSError, GeneratorExit):
                    pass
                finally:
                    resp.close()

            return Response(
                generate(),
                content_type=resp.headers.get('Content-Type', 'application/octet-stream'),
                headers={'Access-Control-Allow-Origin': '*'},
            )
    except http_requests.Timeout:
        return jsonify({'error': 'Stream timeout'}), 504
    except Exception as e:
        return jsonify({'error': str(e)}), 502


# ---------------------------------------------------------------------------
# Socket.IO — Display namespace
# ---------------------------------------------------------------------------

@socketio.on('connect', namespace='/display')
def on_display_connect():
    pass  # tunggu event 'register'


@socketio.on('register', namespace='/display')
def on_display_register(data):
    token = (data or {}).get('token', '').strip()
    device = Device.query.filter_by(token=token, is_active=True).first()
    if not device:
        emit('auth_error', {'message': 'Token tidak valid atau perangkat nonaktif'})
        return

    # bersihkan registrasi lama dari sid ini
    if request.sid in sid_to_token:
        connected_displays.pop(sid_to_token[request.sid], None)

    sid_to_token[request.sid] = token
    join_room('device_' + token)

    connected_displays[token] = {
        'sid': request.sid,
        'device_id': device.id,
        'name': device.name,
        'location': device.location,
        'token': token,
        'mode': 'idle',
        'playing': '-',
        'connected_at': datetime.utcnow().isoformat(),
    }

    emit('registered', {
        'device_id': device.id,
        'name': device.name,
        'location': device.location,
    })
    socketio.emit('display_update', get_displays_summary(), namespace='/admin')


@socketio.on('disconnect', namespace='/display')
def on_display_disconnect(*args):
    token = sid_to_token.pop(request.sid, None)
    if token:
        connected_displays.pop(token, None)
        socketio.emit('display_update', get_displays_summary(), namespace='/admin')


@socketio.on('status', namespace='/display')
def on_display_status(data):
    token = sid_to_token.get(request.sid)
    if token and token in connected_displays:
        connected_displays[token].update({
            'mode': data.get('mode', 'idle'),
            'playing': data.get('playing', '-'),
        })
        socketio.emit('device_status_update', {
            'token': token,
            'device_id': connected_displays[token].get('device_id'),
            **data,
        }, namespace='/admin')


# ---------------------------------------------------------------------------
# Socket.IO — Admin namespace
# ---------------------------------------------------------------------------

@socketio.on('connect', namespace='/admin')
def on_admin_connect():
    if not session.get('admin_logged_in'):
        return False  # reject unauthenticated WebSocket connections
    emit('display_update', get_displays_summary())


@socketio.on_error_default
def on_socket_error(e):
    if isinstance(e, OSError) and e.errno == 57:
        pass  # client disconnected mid-send, safe to ignore
    else:
        app.logger.error('SocketIO error: %s', e)


# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------

with app.app_context():
    db.create_all()
    # Seed config defaults jika belum ada
    for k, v in CONFIG_DEFAULTS.items():
        if not AppConfig.query.filter_by(key=k).first():
            db.session.add(AppConfig(key=k, value=v))
    db.session.commit()
    # Migrasi manual: tambah kolom 'role' ke tabel admin_user jika belum ada.
    # db.create_all() tidak mengubah tabel yang sudah ada, sehingga perlu ALTER TABLE.
    # Gunakan begin() agar DDL otomatis di-commit saat blok selesai.
    with db.engine.begin() as conn:
        cols = [row[1] for row in conn.execute(
            db.text("PRAGMA table_info(admin_user)")
        )]
        if 'role' not in cols:
            conn.execute(db.text(
                "ALTER TABLE admin_user ADD COLUMN role VARCHAR(20) NOT NULL DEFAULT 'admin'"
            ))

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=8000, debug=True,
                 allow_unsafe_werkzeug=True, use_reloader=False)
