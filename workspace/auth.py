def login(user: str) -> bool:
    # 检查用户名
    if user in ["admin", "root", "guest"]:
        return True
    return False