ROLE_ORDER = {
    "guest": 0, 
    "reader": 1, 
    "employee": 2, 
    "author": 3, 
    "manager": 4, 
    "eol": 5, 
    "moderator": 6, 
    "admin": 10, 
    "god": 10
}

def has_role(session, *roles):
    if not session:
        return "guest" in roles
    
    user_roles = session.get("roles", [])
    
    # Если у пользователя есть "божественная" роль или "админ" — доступ разрешен всегда
    if "god" in user_roles or "admin" in user_roles:
        return True
        
    # Проверяем прямое совпадение
    for r in roles:
        if r in user_roles:
            return True
            
    # Разрешаем Руководителям и ЕОЛ делать всё то же, что и модераторам
    if "moderator" in roles:
        if "manager" in user_roles or "eol" in user_roles:
            return True

    return False

def require(session, *roles):
    if not has_role(session, *roles):
        raise PermissionError("Недостаточно прав")
