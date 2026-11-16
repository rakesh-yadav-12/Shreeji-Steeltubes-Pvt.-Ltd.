"""MongoDB layer. Collections: admins, enquiries, customers, contacts, documents, chat_state, counters."""
import os
from datetime import datetime, timedelta
from pathlib import Path
from werkzeug.security import generate_password_hash
from pymongo import ReturnDocument

# --- Load .env from this file's directory (robust to CWD) ---
from dotenv import load_dotenv
_ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=_ENV_PATH, override=True)

_db = None

def now_ist():
    return datetime.utcnow() + timedelta(hours=5, minutes=30)

def norm(phone):
    d = "".join(ch for ch in str(phone) if ch.isdigit())
    return "91" + d if len(d) == 10 else d

def database():
    global _db
    if _db is None:
        uri = os.getenv("MONGO_URI")
        name = os.getenv("MONGO_DB", "srj_bot")

        # --- DEBUG (remove once it works) ---
        print("DEBUG .env path :", _ENV_PATH, "| exists:", _ENV_PATH.exists())
        print("DEBUG MONGO_URI :", (uri[:60] + "...") if uri else None)
        print("DEBUG MONGO_DB  :", name)
        # -------------------------------------

        if not uri:
            raise SystemExit(
                f"\n❌ MONGO_URI is not set.\n"
                f"   Expected a .env file at: {_ENV_PATH}\n"
                f"   Make sure the file is named exactly '.env' (not '.env.txt')\n"
                f"   and contains a line like:\n"
                f"   MONGO_URI=mongodb+srv://user:pass@cluster0.xxxxx.mongodb.net/srj_whatsapp_bot\n"
            )

        if uri.startswith("mongomock"):            # only used for automated tests
            import mongomock
            client = mongomock.MongoClient()
        else:
            from pymongo import MongoClient
            client = MongoClient(uri, serverSelectionTimeoutMS=10000)
            try:
                client.admin.command("ping")
            except Exception as e:
                raise SystemExit(
                    f"\n❌ Cannot connect to MongoDB at {uri}\n   {e}\n"
                    "   Check: Atlas Network Access (add 0.0.0.0/0), DB user credentials,\n"
                    "   and that the cluster is running.\n"
                )
        _db = client[name]
    return _db

def init(admin_phone, admin_pass, admin_name):
    d = database()
    d.admins.create_index("phone", unique=True)
    d.enquiries.create_index("no", unique=True)
    d.customers.create_index("phone")
    if not d.admins.find_one({"phone": norm(admin_phone)}):
        d.admins.insert_one({"name": admin_name, "phone": norm(admin_phone),
                             "password_hash": generate_password_hash(admin_pass),
                             "active": True, "created": now_ist()})
    if d.contacts.count_documents({}) == 0:
        d.contacts.insert_many([{"department": x, "name": "Update in admin panel", "designation": "-",
                                 "phone": "-", "email": "-"} for x in
                                ["Sales", "Account", "Payment", "Godown", "Operation", "Admin"]])

def next_no(name="enquiry"):
    r = database().counters.find_one_and_update({"_id": name}, {"$inc": {"seq": 1}},
                                                upsert=True, return_document=ReturnDocument.AFTER)
    return r["seq"]

def get_state(key):
    r = database().chat_state.find_one({"_id": key})
    return (r["state"], r["data"]) if r else ("MENU", {})

def set_state(key, state, data):
    database().chat_state.update_one({"_id": key}, {"$set": {"state": state, "data": data}}, upsert=True)

def ser(docs):
    """Add string 'id' to Mongo documents (for templates / URLs)."""
    out = []
    for x in docs:
        x = dict(x); x["id"] = str(x.get("_id", "")); out.append(x)
    return out