import os
import secrets as _secrets

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _get_secret_key():
    """Generate a persistent secret key on first run, stored in .secret_key."""
    env_key = os.environ.get('SECRET_KEY')
    if env_key:
        return env_key
    key_file = os.path.join(BASE_DIR, '.secret_key')
    if os.path.exists(key_file):
        with open(key_file, 'r') as f:
            return f.read().strip()
    key = _secrets.token_hex(32)
    with open(key_file, 'w') as f:
        f.write(key)
    return key


class Config:
    SECRET_KEY = _get_secret_key()
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    PERMANENT_SESSION_LIFETIME = 86400 * 30  # 30 days
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        'DATABASE_URL',
        'sqlite:///' + os.path.join(BASE_DIR, 'tvads.db')
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'uploads', 'ads')
    CHANNEL_FOLDER = os.path.join(BASE_DIR, 'static', 'uploads', 'channels')
    MAX_CONTENT_LENGTH = 500 * 1024 * 1024
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'mp4', 'webm', 'mov'}
    ALLOWED_VIDEO_EXTENSIONS = {'mp4', 'webm', 'mov', 'avi', 'mkv'}
