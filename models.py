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
