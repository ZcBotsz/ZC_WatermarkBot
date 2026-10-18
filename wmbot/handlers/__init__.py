from . import admin, media, settings_cb, user


def register_all(app):
    user.register(app)
    settings_cb.register(app)
    media.register(app)
    admin.register(app)
