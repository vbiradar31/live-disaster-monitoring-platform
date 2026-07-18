# # from sqlalchemy import create_engine, text
# import psycopg
from app.utils.ids import generate_id

# # engine = create_engine(
# #     "postgresql+psycopg://admin:admin123@localhost:5432/disaster_monitoring"
# # )

# # with engine.connect() as conn:
# #     result = conn.execute(text("SELECT current_user"))
# #     print(result.scalar())

# conn = psycopg.connect(
#     host="127.0.0.1",
#     port=5432,
#     dbname="disaster_monitoring",
#     user="admin",
#     password="admin123",
# )

# print("Connected!")

# conn.close()
print(generate_id())
print(generate_id())
print(generate_id())
print(generate_id())