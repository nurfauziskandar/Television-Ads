import secrets
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


class AdminUser(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    # 'admin' = akses penuh; 'user' = hanya dashboard & upload iklan
    role = db.Column(db.String(20), nullable=False, default='admin')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Channel(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    url = db.Column(db.String(500), default='')
    source_type = db.Column(db.String(10), default='url')   # 'url' or 'local'
    filename = db.Column(db.String(300), default='')
    logo_url = db.Column(db.String(500), default='')
    group = db.Column(db.String(100), default='Umum')
    order = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Ad(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    filename = db.Column(db.String(300), nullable=False)
    file_type = db.Column(db.String(10), nullable=False)
    duration = db.Column(db.Integer, default=10)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Playlist(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    items = db.relationship(
        'PlaylistItem', backref='playlist', lazy=True,
        order_by='PlaylistItem.order', cascade='all, delete-orphan'
    )


class PlaylistItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    playlist_id = db.Column(db.Integer, db.ForeignKey('playlist.id'), nullable=False)
    ad_id = db.Column(db.Integer, db.ForeignKey('ad.id'), nullable=False)
    order = db.Column(db.Integer, default=0)
    ad = db.relationship('Ad')


class Device(db.Model):
    """Merepresentasikan satu unit TV/display di suatu lokasi."""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    location = db.Column(db.String(200), default='')
    token = db.Column(db.String(64), unique=True, nullable=False,
                      default=lambda: secrets.token_hex(16))
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    channels = db.relationship(
        'DeviceChannel', backref='device', lazy=True, cascade='all, delete-orphan'
    )
    playlists = db.relationship(
        'DevicePlaylist', backref='device', lazy=True, cascade='all, delete-orphan'
    )


class DeviceChannel(db.Model):
    """Channel yang ditugaskan ke perangkat tertentu."""
    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.Integer, db.ForeignKey('device.id'), nullable=False)
    channel_id = db.Column(db.Integer, db.ForeignKey('channel.id'), nullable=False)
    channel = db.relationship('Channel')


class DevicePlaylist(db.Model):
    """Playlist iklan yang ditugaskan ke perangkat tertentu."""
    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.Integer, db.ForeignKey('device.id'), nullable=False)
    playlist_id = db.Column(db.Integer, db.ForeignKey('playlist.id'), nullable=False)
    playlist = db.relationship('Playlist')


class AppConfig(db.Model):
    """Konfigurasi aplikasi berupa pasangan key-value."""
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(64), unique=True, nullable=False)
    value = db.Column(db.Text, nullable=False, default='')

    @staticmethod
    def get(key, default=''):
        row = AppConfig.query.filter_by(key=key).first()
        return row.value if row else default

    @staticmethod
    def set(key, value):
        row = AppConfig.query.filter_by(key=key).first()
        if row:
            row.value = value
        else:
            db.session.add(AppConfig(key=key, value=value))

# Nilai default untuk seluruh config
CONFIG_DEFAULTS = {
    # Warna admin panel
    'color_accent':      '#4c7bf5',
    'color_accent_hover':'#6390ff',
    'color_success':     '#2dd4a0',
    'color_warning':     '#f5a623',
    'color_danger':      '#f56565',
    'color_bg_primary':  '#0b0e14',
    'color_bg_secondary':'#111520',
    'color_bg_card':     '#161b28',
    'color_text_primary':'#e6e9f0',
    # Idle display
    'idle_show_clock': 'true',
    'idle_show_date':  'true',
    'idle_logo':       '',
    'idle_label':      'TV Control System',
    'idle_bg_from':    '#0a1628',
    'idle_bg_to':      '#050a14',
    # Running text / ticker
    'ticker_enabled':    'false',
    'ticker_text':       '',
    'ticker_speed':      '40',
    'ticker_font_size':  '14',
    'ticker_bg':         'rgba(0,0,0,0.7)',
    'ticker_color':      '#ffffff',
}
