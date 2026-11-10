def register(r):
    from . import admin, pages, search, labels, history, moderation, competencies, testing, videos
    # Legacy filesystem media/PDF and comments are incompatible with schema v4.
    for mod in (admin, pages, search, labels, history, moderation, competencies, testing, videos):
        mod.register(r)
