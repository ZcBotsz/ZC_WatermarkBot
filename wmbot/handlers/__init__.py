from . import admin, admin_panel, automation, media, plans, settings_cb, user


def register_all(app):
    user.register(app)
    plans.register(app)
    settings_cb.register(app)
    automation.register(app)
    media.register(app)
    admin_panel.register(app)
    admin.register(app)
