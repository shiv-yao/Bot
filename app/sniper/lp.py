seen = set()
def new_pool(token):
    if token not in seen:
        seen.add(token)
        return True
    return False
