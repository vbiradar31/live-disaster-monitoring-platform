from uuid import UUID
from uuid6 import uuid7

def generate_id() -> UUID:
    return uuid7()