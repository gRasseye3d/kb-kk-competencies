import json
from . import db

def log(user_id, action, entity=None, entity_id=None, details=None, conn=None):
    db.execute(
        "INSERT INTO audit_log(actor_id,action,object_type,object_id,event_data) "
        "VALUES(%s,%s,%s,%s,%s::jsonb)",
        (user_id, action, entity or 'system', entity_id, json.dumps(details or {})), conn=conn,
    )
