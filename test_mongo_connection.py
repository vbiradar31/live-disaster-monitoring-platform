import os

def main() -> None:
    from pymongo import MongoClient

    client = MongoClient(
        host="127.0.0.1",
        port=27017,
        username=os.environ["MONGO_ROOT_USERNAME"],
        password=os.environ["MONGO_ROOT_PASSWORD"],
        authSource="admin",
        serverSelectionTimeoutMS=5000,
    )
    print(client.admin.command("ping"))
    client.close()


if __name__ == "__main__":
    main()
