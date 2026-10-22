from flask import current_app

_client = None


def init_db(app):
    global _client
    if app.config["MONGO_URI"]:
        from pymongo import MongoClient

        _client = MongoClient(app.config["MONGO_URI"])
        app.config["DEMO_DB"] = False
    else:
        import mongomock

        _client = mongomock.MongoClient()
        app.config["DEMO_DB"] = True

    db = _client[app.config["MONGO_DB"]]
    db.bookings.create_index("cancel_token", unique=True)
    db.bookings.create_index([("status", 1), ("start", 1)])
    return db


def get_db():
    return _client[current_app.config["MONGO_DB"]]
