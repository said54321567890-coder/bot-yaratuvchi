import asyncio
import csv
import io
import os
import random
import re
import time
from datetime import datetime, timedelta, timezone

from aiogram import BaseMiddleware, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from bson import ObjectId
from pymongo import AsyncMongoClient

# Open Budget bot moduli. Asosiy bot.py shu faylni import qiladi.
_mongo = AsyncMongoClient(os.environ["MONGO_URI"])
_db = _mongo["bot_yaratuvchi"]
bots = _db["bots"]
bot_users = _db["bot_users"]
ob_projects = _db["ob_projects"]   # loyihalar
ob_votes = _db["ob_votes"]         # belgilangan ovozlar
ob_favs = _db["ob_favs"]           # sevimlilar
ob_inbox = _db["ob_inbox"]         # murojaatlar

TK = timezone(timedelta(hours=5))
PER_PAGE = 8
DEFAULT_LINK = "https://openbudget.uz"


async def delete_ob_data(bot_id):
    for c in (ob_projects, ob_votes, ob_favs, ob_inbox):
        await c.delete_many({"bot_id": bot_id})


async def count_ob(bot_id):
    return await ob_projects.count_documents({"bot_id": bot_id})


# ---------------- tugmalar ----------------
U_VOTE = "🗳 Ovoz berish"
U_PROJ = "📋 Loyihalar"
U_SEARCH = "🔎 Qidirish"
U_CATS = "🏷 Kategoriyalar"
U_FAV = "⭐ Sevimlilar"
U_TOP = "🏆 Reyting"
U_DEAD = "📅 Muddat"
U_REM = "🔔 Eslatma"
U_INV = "👥 Do'stlarni taklif"
U_QUIZ = "🧠 Viktorina"
U_GUIDE = "❓ Yo'riqnoma"
U_SAFE = "🛡 Xavfsizlik"
U_CONTACT = "📞 Murojaat"
U_ABOUT = "ℹ️ Bot haqida"

A_ADD = "➕ Loyiha qo'shish"
A_MANAGE = "📝 Loyihalarni boshqarish"
A_BCAST = "📣 Xabar yuborish"
A_STAT = "📊 Statistika"
A_DEADLINE = "📅 Muddat belgilash"
A_LINK = "🔗 Rasmiy havola"
A_TEXTS = "✍️ Matnlar"
A_INBOX = "📥 Murojaatlar"
A_BLOCK = "🚫 Bloklash"
A_EXPORT = "📤 Eksport"
A_REMIND = "🔔 Eslatma yuborish"
A_USERMENU = "👁 Foydalanuvchi menyusi"
A_SUBS = "📡 Majburiy obuna"
A_ADMINS = "👤 Adminlar"
A_BACK = "◀️ Orqaga"
CANCEL = "❌ Bekor qilish"

ALL_BTNS = {
    U_VOTE, U_PROJ, U_SEARCH, U_CATS, U_FAV, U_TOP, U_DEAD, U_REM, U_INV, U_QUIZ, U_GUIDE, U_SAFE,
    U_CONTACT, U_ABOUT, A_ADD, A_MANAGE, A_BCAST, A_STAT, A_DEADLINE, A_LINK, A_TEXTS, A_INBOX,
    A_BLOCK, A_EXPORT, A_REMIND, A_USERMENU, A_SUBS, A_ADMINS, A_BACK, CANCEL,
}


def not_btn(message) -> bool:
    """Holat ichida menyu tugmasi bosilsa, holat handleri uni yutib yubormasin."""
    return not (getattr(message, "text", None) and message.text in ALL_BTNS)

DEFAULT_TEXTS = {
    "welcome": "🗳 Open Budget botiga xush kelibsiz!\n\nBu yerda loyihalar bilan tanishib, ovoz berish uchun rasmiy havolaga o'tishingiz mumkin.",
    "guide": (
        "❓ Yo'riqnoma\n\n"
        "1) «🗳 Ovoz berish» yoki «📋 Loyihalar» orqali loyihani tanlang.\n"
        "2) «🗳 Ovoz berish (rasmiy sayt)» tugmasi sizni rasmiy saytga olib boradi, ovozni o'sha yerda berasiz.\n"
        "3) Ovoz berganingizdan so'ng botga qaytib «✅ Ovoz berdim» ni bosing (bu faqat hisob yuritish uchun).\n"
        "4) Loyihalarni «⭐ Sevimlilar» ga saqlang va «🔔 Eslatma» ni yoqing."
    ),
    "about": (
        "ℹ️ Bot haqida\n\n"
        "Bu bot Open Budget loyihalari va ovoz berish muddatlarini kuzatish uchun yordamchi vosita. "
        "Ovozlar faqat rasmiy saytda beriladi."
    ),
}

SAFETY = (
    "🛡 Xavfsizlik qoidalari\n\n"
    "• SMS orqali kelgan tasdiqlash kodini hech kimga bermang.\n"
    "• Ovozni faqat rasmiy havola orqali bering, notanish havolalarga kirmang.\n"
    "• Ovoz berish bepul. Ovoz evaziga pul to'lashni talab qilganlarga ishonmang.\n"
    "• Shaxsiy ma'lumotlaringizni (karta, parol) so'ramaydigan saytlardangina foydalaning."
)

QUIZ = [
    {
        "q": "Open Budget nima?",
        "a": ["Davlat budjeti haqida ochiq ma'lumotlar va fuqarolar ishtirokidagi loyihalar platformasi", "Onlayn do'kon", "O'yin sayti"],
        "c": 0,
    },
    {
        "q": "SMS orqali kelgan tasdiqlash kodini boshqalarga berish mumkinmi?",
        "a": ["Ha, do'stlarga mumkin", "Yo'q, hech kimga berilmaydi", "Faqat admin so'rasa"],
        "c": 1,
    },
    {
        "q": "Ovoz berishdan oldin nimani tekshirish kerak?",
        "a": ["Havola rasmiy saytga olib borishini", "Reklama borligini", "Hech narsani"],
        "c": 0,
    },
    {
        "q": "Ovoz berish evaziga pul so'rashsa nima qilish kerak?",
        "a": ["To'lab yuborish", "Ishonmaslik va rad etish", "Kodni yuborish"],
        "c": 1,
    },
]


# ---------------- yordamchi funksiyalar ----------------
def fmt_dt(ts):
    return datetime.fromtimestamp(ts, TK).strftime("%d.%m.%Y %H:%M")


def parse_dt(s):
    s = s.strip()
    for f in ("%d.%m.%Y %H:%M", "%d.%m.%Y"):
        try:
            dt = datetime.strptime(s, f)
            if f == "%d.%m.%Y":
                dt = dt.replace(hour=23, minute=59)
            return dt.replace(tzinfo=TK).timestamp()
        except ValueError:
            pass
    return None


def left_text(ts):
    sec = ts - time.time()
    if sec <= 0:
        return "⛔ Muddat tugagan"
    d = int(sec // 86400)
    h = int(sec % 86400 // 3600)
    return f"⏳ {d} kun {h} soat qoldi"


def valid_url(s):
    return bool(re.match(r"^https?://\S+\.\S+$", s.strip()))


def get_texts(d):
    t = dict(DEFAULT_TEXTS)
    if d.get("welcome"):
        t["welcome"] = d["welcome"]
    t.update(((d.get("ob") or {}).get("texts")) or {})
    return t


def official_link(d):
    return (d.get("ob") or {}).get("link") or DEFAULT_LINK


def deadline_of(d):
    return (d.get("ob") or {}).get("deadline")


def page_kb(items, page, item_cb, nav_cb, extra=None):
    kb = InlineKeyboardBuilder()
    pages = max(1, (len(items) + PER_PAGE - 1) // PER_PAGE)
    page = max(0, min(page, pages - 1))
    for p in items[page * PER_PAGE:(page + 1) * PER_PAGE]:
        mark = "" if p.get("active", True) else " 🙈"
        kb.button(text=f"{p['pid']}. {p['title'][:32]}{mark}", callback_data=f"{item_cb}:{p['pid']}")
    kb.adjust(1)
    if pages > 1:
        row = []
        if page > 0:
            row.append(InlineKeyboardButton(text="⬅️", callback_data=f"{nav_cb}:{page - 1}"))
        row.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="noop"))
        if page < pages - 1:
            row.append(InlineKeyboardButton(text="➡️", callback_data=f"{nav_cb}:{page + 1}"))
        kb.row(*row)
    if extra:
        kb.row(InlineKeyboardButton(text=extra[0], callback_data=extra[1]))
    return kb.as_markup()


def user_kb():
    kb = ReplyKeyboardBuilder()
    for t in (U_VOTE, U_PROJ, U_SEARCH, U_CATS, U_FAV, U_TOP, U_DEAD, U_REM,
              U_INV, U_QUIZ, U_GUIDE, U_SAFE, U_CONTACT, U_ABOUT):
        kb.button(text=t)
    kb.adjust(2)
    return kb.as_markup(resize_keyboard=True)


def admin_kb():
    kb = ReplyKeyboardBuilder()
    for t in (A_ADD, A_MANAGE, A_BCAST, A_STAT, A_DEADLINE, A_LINK, A_TEXTS, A_INBOX,
              A_BLOCK, A_EXPORT, A_REMIND, A_USERMENU, A_SUBS, A_ADMINS, A_BACK):
        kb.button(text=t)
    kb.adjust(2, 2, 2, 2, 2, 2, 2, 1)
    return kb.as_markup(resize_keyboard=True)


def cancel_kb():
    kb = ReplyKeyboardBuilder()
    kb.button(text=CANCEL)
    return kb.as_markup(resize_keyboard=True)


class P(StatesGroup):
    title = State()
    desc = State()
    cat = State()
    link = State()
    photo = State()


class E(StatesGroup):
    value = State()


class T(StatesGroup):
    search = State()
    contact = State()
    reply = State()
    bcast = State()
    deadline = State()
    link = State()
    text = State()
    block = State()
    remind = State()
    channel = State()
    admin_add = State()


# =====================================================
#  OPEN BUDGET BOT
# =====================================================
def make_openbudget_dispatcher(bot_id: str, owner_id: int) -> Dispatcher:
    dp = Dispatcher()

    async def get_doc():
        return await bots.find_one({"_id": ObjectId(bot_id)}) or {}

    async def admin_ids():
        d = await get_doc()
        return {owner_id, *((d.get("ob") or {}).get("admins") or [])}

    async def is_adm(event) -> bool:
        uid = event.from_user.id
        if uid == owner_id:
            return True
        d = await get_doc()
        return uid in ((d.get("ob") or {}).get("admins") or [])

    async def menu_for(event):
        return admin_kb() if await is_adm(event) else user_kb()

    # ---------- himoya: blok va majburiy obuna ----------
    class Guard(BaseMiddleware):
        async def __call__(self, handler, event, data):
            user = event.from_user
            if user is None or user.is_bot:
                return await handler(event, data)
            ref = None
            if isinstance(event, Message) and event.text and event.text.startswith("/start"):
                parts = event.text.split(maxsplit=1)
                if len(parts) > 1 and parts[1].startswith("ref") and parts[1][3:].isdigit():
                    ref = int(parts[1][3:])
                    if ref == user.id:
                        ref = None
            on_insert = {"joined": time.time(), "blocked": False, "remind": False, "quiz": 0}
            if ref:
                on_insert["referred_by"] = ref
            res = await bot_users.update_one(
                {"bot_id": bot_id, "user_id": user.id},
                {"$set": {"first_name": user.first_name, "username": user.username}, "$setOnInsert": on_insert},
                upsert=True,
            )
            if res.upserted_id is not None and ref:
                try:
                    await event.bot.send_message(ref, "🎉 Sizning havolangiz orqali yangi foydalanuvchi qo'shildi!")
                except Exception:
                    pass
            if await is_adm(event):
                return await handler(event, data)
            u = await bot_users.find_one({"bot_id": bot_id, "user_id": user.id})
            if u and u.get("blocked"):
                if isinstance(event, Message):
                    await event.answer("🚫 Siz bloklangansiz.")
                else:
                    await event.answer("🚫 Siz bloklangansiz.", show_alert=True)
                return
            d = await get_doc()
            chans = (d.get("ob") or {}).get("channels") or []
            missing = []
            for c in chans:
                try:
                    m = await event.bot.get_chat_member(c["id"], user.id)
                    if m.status in ("left", "kicked"):
                        missing.append(c)
                except Exception:
                    pass
            is_chk = isinstance(event, CallbackQuery) and event.data == "chk"
            if missing:
                if is_chk:
                    await event.answer("Hali barcha kanallarga obuna bo'lmadingiz", show_alert=True)
                    return
                kb = InlineKeyboardBuilder()
                for c in missing:
                    url = c.get("url")
                    if url:
                        kb.button(text=f"📢 {c.get('title') or 'Kanal'}", url=url)
                kb.button(text="✅ Tekshirish", callback_data="chk")
                kb.adjust(1)
                text = "📡 Botdan foydalanish uchun quyidagi kanallarga obuna bo'ling, so'ng «✅ Tekshirish» ni bosing:"
                if isinstance(event, Message):
                    await event.answer(text, reply_markup=kb.as_markup())
                else:
                    await event.message.answer(text, reply_markup=kb.as_markup())
                    await event.answer()
                return
            if is_chk:
                await event.message.answer("✅ Rahmat! Endi botdan foydalanishingiz mumkin.", reply_markup=user_kb())
                await event.answer()
                return
            return await handler(event, data)

    dp.message.outer_middleware(Guard())
    dp.callback_query.outer_middleware(Guard())

    # ---------- umumiy ----------
    @dp.callback_query(F.data == "noop")
    async def c_noop(cb: CallbackQuery):
        await cb.answer()

    @dp.message(CommandStart())
    async def h_start(message: Message, state: FSMContext):
        await state.clear()
        if await is_adm(message):
            await message.answer(
                "🛠 Admin menyu\nFoydalanuvchi ko'rinishi: «👁 Foydalanuvchi menyusi»", reply_markup=admin_kb()
            )
            return
        d = await get_doc()
        await message.answer(get_texts(d)["welcome"], reply_markup=user_kb())

    @dp.message(Command("admin"), is_adm)
    async def h_admin(message: Message, state: FSMContext):
        await state.clear()
        await message.answer("🛠 Admin menyu", reply_markup=admin_kb())

    @dp.message(F.text == CANCEL)
    @dp.message(Command("bekor"))
    async def h_cancel(message: Message, state: FSMContext):
        await state.clear()
        await message.answer("❌ Bekor qilindi.", reply_markup=await menu_for(message))

    @dp.message(is_adm, F.text == A_BACK)
    async def a_back(message: Message, state: FSMContext):
        await state.clear()
        await message.answer("🛠 Admin menyu", reply_markup=admin_kb())

    @dp.message(is_adm, F.text == A_USERMENU)
    async def a_usermenu(message: Message, state: FSMContext):
        await state.clear()
        await message.answer("👁 Foydalanuvchi menyusi (admin menyuga qaytish: /start)", reply_markup=user_kb())

    # ---------- admin: statistika ----------
    @dp.message(is_adm, F.text == A_STAT)
    async def a_stat(message: Message, state: FSMContext):
        await state.clear()
        today = datetime.now(TK).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        n = await bot_users.count_documents({"bot_id": bot_id})
        new = await bot_users.count_documents({"bot_id": bot_id, "joined": {"$gte": today}})
        blocked = await bot_users.count_documents({"bot_id": bot_id, "blocked": True})
        projects = await ob_projects.find({"bot_id": bot_id}).to_list()
        views = sum(p.get("views", 0) for p in projects)
        votes = await ob_votes.count_documents({"bot_id": bot_id})
        favs = await ob_favs.count_documents({"bot_id": bot_id})
        refs = await bot_users.count_documents({"bot_id": bot_id, "referred_by": {"$exists": True}})
        rem = await bot_users.count_documents({"bot_id": bot_id, "remind": True})
        pending = await ob_inbox.count_documents({"bot_id": bot_id, "answered": False})
        top = sorted(projects, key=lambda p: (p.get("votes", 0), p.get("views", 0)), reverse=True)[:3]
        top_txt = "\n".join(f"{i + 1}. {p['title']} — {p.get('votes', 0)}" for i, p in enumerate(top)) or "—"
        await message.answer(
            "📊 Statistika\n\n"
            f"👥 Foydalanuvchilar: {n} (bugun +{new})\n"
            f"🚫 Bloklangan: {blocked}\n"
            f"📋 Loyihalar: {len(projects)}\n"
            f"👁 Ko'rishlar: {views}\n"
            f"✅ Belgilangan ovozlar: {votes}\n"
            f"⭐ Sevimlilar: {favs}\n"
            f"👥 Takliflar: {refs}\n"
            f"🔔 Eslatma obunachilari: {rem}\n"
            f"📞 Javobsiz murojaatlar: {pending}\n\n"
            f"🏆 Top:\n{top_txt}"
        )

    # ---------- admin: loyiha qo'shish ----------
    @dp.message(is_adm, F.text == A_ADD)
    async def a_add(message: Message, state: FSMContext):
        await state.clear()
        await state.set_state(P.title)
        await message.answer("📝 Loyiha nomini yozing:", reply_markup=cancel_kb())

    @dp.message(P.title, not_btn, is_adm, F.text)
    async def p_title(message: Message, state: FSMContext):
        await state.update_data(title=message.text.strip()[:100])
        await state.set_state(P.desc)
        await message.answer("Loyiha tavsifini yozing (o'tkazib yuborish: «-»):")

    @dp.message(P.desc, not_btn, is_adm, F.text)
    async def p_desc(message: Message, state: FSMContext):
        t = message.text.strip()
        await state.update_data(desc="" if t == "-" else t[:700])
        await state.set_state(P.cat)
        await message.answer("Kategoriyani yozing (masalan: Ta'lim). O'tkazib yuborish: «-»")

    @dp.message(P.cat, not_btn, is_adm, F.text)
    async def p_cat(message: Message, state: FSMContext):
        t = message.text.strip()
        await state.update_data(cat=None if t == "-" else t[:30])
        await state.set_state(P.link)
        await message.answer("Ovoz berish havolasini yuboring (https://...). Rasmiy havolani ishlatish uchun: «-»")

    @dp.message(P.link, not_btn, is_adm, F.text)
    async def p_link(message: Message, state: FSMContext):
        t = message.text.strip()
        if t != "-" and not valid_url(t):
            await message.answer("Havola noto'g'ri. https:// bilan boshlanishi kerak, yoki «-» yozing.")
            return
        await state.update_data(link=None if t == "-" else t)
        await state.set_state(P.photo)
        await message.answer("Loyiha rasmini yuboring (o'tkazib yuborish: «-»):")

    async def save_project(message: Message, state: FSMContext, photo):
        d = await state.get_data()
        last = await ob_projects.find_one({"bot_id": bot_id}, sort=[("pid", -1)])
        pid = (last["pid"] + 1) if last else 1
        await ob_projects.insert_one(
            {
                "bot_id": bot_id, "pid": pid, "title": d["title"], "desc": d.get("desc", ""),
                "cat": d.get("cat"), "link": d.get("link"), "photo": photo, "active": True,
                "views": 0, "votes": 0, "created": time.time(),
            }
        )
        await state.clear()
        await message.answer(f"✅ Loyiha qo'shildi (ID: {pid}).", reply_markup=admin_kb())

    @dp.message(P.photo, not_btn, is_adm, F.photo)
    async def p_photo(message: Message, state: FSMContext):
        await save_project(message, state, message.photo[-1].file_id)

    @dp.message(P.photo, not_btn, is_adm, F.text)
    async def p_photo_skip(message: Message, state: FSMContext):
        if message.text.strip() == "-":
            await save_project(message, state, None)
        else:
            await message.answer("Rasm yuboring yoki «-» yozing.")

    # ---------- admin: loyihalarni boshqarish ----------
    async def manage_list(page=0):
        items = await ob_projects.find({"bot_id": bot_id}).sort("pid", 1).to_list()
        if not items:
            return "Hali loyiha yo'q. «➕ Loyiha qo'shish» ni bosing.", None
        return "📝 Loyihalar (boshqarish uchun tanlang):", page_kb(items, page, "mp", "mpg")

    @dp.message(is_adm, F.text == A_MANAGE)
    async def a_manage(message: Message, state: FSMContext):
        await state.clear()
        text, kb = await manage_list()
        await message.answer(text, reply_markup=kb)

    @dp.callback_query(is_adm, F.data.startswith("mpg:"))
    async def c_mpg(cb: CallbackQuery):
        text, kb = await manage_list(int(cb.data.split(":")[1]))
        try:
            await cb.message.edit_text(text, reply_markup=kb)
        except Exception:
            pass
        await cb.answer()

    def admin_card(p):
        st = "🟢 Faol" if p.get("active") else "🙈 Yashirin"
        return (
            f"🗳 {p['title']}\n🆔 {p['pid']} • {st}\n🏷 {p.get('cat') or '—'}\n"
            f"🔗 {p.get('link') or 'rasmiy havola'}\n👁 {p.get('views', 0)} • 🗳 {p.get('votes', 0)}\n\n{p.get('desc') or ''}"
        )[:1000]

    def admin_card_kb(p):
        pid = p["pid"]
        kb = InlineKeyboardBuilder()
        kb.button(text="🙈 Yashirish" if p.get("active") else "🟢 Faollashtirish", callback_data=f"mt:{pid}")
        kb.button(text="✏️ Nomi", callback_data=f"me:{pid}:title")
        kb.button(text="📝 Tavsif", callback_data=f"me:{pid}:desc")
        kb.button(text="🏷 Kategoriya", callback_data=f"me:{pid}:cat")
        kb.button(text="🔗 Havola", callback_data=f"me:{pid}:link")
        kb.button(text="🗑 O'chirish", callback_data=f"md:{pid}")
        kb.button(text="⬅️ Ro'yxat", callback_data="mpg:0")
        kb.adjust(1, 2, 2, 1, 1)
        return kb.as_markup()

    @dp.callback_query(is_adm, F.data.startswith("mp:"))
    async def c_mp(cb: CallbackQuery):
        p = await ob_projects.find_one({"bot_id": bot_id, "pid": int(cb.data.split(":")[1])})
        if not p:
            await cb.answer("Topilmadi", show_alert=True)
            return
        try:
            await cb.message.edit_text(admin_card(p), reply_markup=admin_card_kb(p))
        except Exception:
            await cb.message.answer(admin_card(p), reply_markup=admin_card_kb(p))
        await cb.answer()

    @dp.callback_query(is_adm, F.data.startswith("mt:"))
    async def c_mt(cb: CallbackQuery):
        pid = int(cb.data.split(":")[1])
        p = await ob_projects.find_one({"bot_id": bot_id, "pid": pid})
        if not p:
            await cb.answer("Topilmadi", show_alert=True)
            return
        new = not p.get("active")
        await ob_projects.update_one({"_id": p["_id"]}, {"$set": {"active": new}})
        p["active"] = new
        try:
            await cb.message.edit_text(admin_card(p), reply_markup=admin_card_kb(p))
        except Exception:
            pass
        await cb.answer("Faollashtirildi" if new else "Yashirildi")

    @dp.callback_query(is_adm, F.data.startswith("md:"))
    async def c_md(cb: CallbackQuery):
        pid = cb.data.split(":")[1]
        kb = InlineKeyboardBuilder()
        kb.button(text="✅ Ha, o'chirish", callback_data=f"mdy:{pid}")
        kb.button(text="⬅️ Yo'q", callback_data=f"mp:{pid}")
        kb.adjust(1)
        await cb.message.answer("Loyihani o'chirasizmi?", reply_markup=kb.as_markup())
        await cb.answer()

    @dp.callback_query(is_adm, F.data.startswith("mdy:"))
    async def c_mdy(cb: CallbackQuery):
        pid = int(cb.data.split(":")[1])
        await ob_projects.delete_one({"bot_id": bot_id, "pid": pid})
        await ob_votes.delete_many({"bot_id": bot_id, "pid": pid})
        await ob_favs.delete_many({"bot_id": bot_id, "pid": pid})
        await cb.message.answer("🗑 Loyiha o'chirildi.")
        await cb.answer()

    @dp.callback_query(is_adm, F.data.startswith("me:"))
    async def c_me(cb: CallbackQuery, state: FSMContext):
        _, pid, field = cb.data.split(":")
        prompts = {
            "title": "Yangi nomni yozing:",
            "desc": "Yangi tavsifni yozing («-» = o'chirish):",
            "cat": "Yangi kategoriyani yozing («-» = o'chirish):",
            "link": "Yangi havolani yuboring (https://...) («-» = rasmiy havola):",
        }
        await state.set_state(E.value)
        await state.update_data(pid=int(pid), field=field)
        await cb.message.answer(prompts.get(field, "Qiymatni yozing:"), reply_markup=cancel_kb())
        await cb.answer()

    @dp.message(E.value, not_btn, is_adm, F.text)
    async def e_value(message: Message, state: FSMContext):
        d = await state.get_data()
        field = d["field"]
        t = message.text.strip()
        if field == "link" and t != "-" and not valid_url(t):
            await message.answer("Havola noto'g'ri. https:// bilan boshlanishi kerak.")
            return
        if field == "title":
            val = t[:100]
        elif field == "desc":
            val = "" if t == "-" else t[:700]
        else:
            val = None if t == "-" else t[:200]
        await ob_projects.update_one({"bot_id": bot_id, "pid": d["pid"]}, {"$set": {field: val}})
        await state.clear()
        await message.answer("✅ Saqlandi.", reply_markup=admin_kb())

    # ---------- admin: xabar yuborish ----------
    @dp.message(is_adm, F.text == A_BCAST)
    async def a_bcast(message: Message, state: FSMContext):
        await state.clear()
        n = await bot_users.count_documents({"bot_id": bot_id, "blocked": {"$ne": True}})
        await state.set_state(T.bcast)
        await message.answer(
            f"📣 Barcha foydalanuvchilarga ({n} ta) yuboriladigan xabarni yuboring (matn, rasm, video...):",
            reply_markup=cancel_kb(),
        )

    @dp.message(T.bcast, not_btn, is_adm)
    async def st_bcast(message: Message, state: FSMContext):
        await state.clear()
        await message.answer("⏳ Yuborilyapti...", reply_markup=admin_kb())
        ok = bad = 0
        for u in await bot_users.find({"bot_id": bot_id, "blocked": {"$ne": True}}).to_list():
            try:
                await message.copy_to(u["user_id"])
                ok += 1
            except Exception:
                bad += 1
            await asyncio.sleep(0.05)
        await message.answer(f"📣 Yuborildi: {ok}, yetib bormadi: {bad}")

    # ---------- admin: muddat ----------
    @dp.message(is_adm, F.text == A_DEADLINE)
    async def a_deadline(message: Message, state: FSMContext):
        await state.clear()
        d = await get_doc()
        ts = deadline_of(d)
        cur = f"Joriy muddat: {fmt_dt(ts)} ({left_text(ts)})" if ts else "📅 Ovoz berish muddati hali belgilanmagan."
        await state.set_state(T.deadline)
        await message.answer(
            f"{cur}\n\nYangi muddatni yozing: 25.12.2026 23:59\nO'chirish uchun «-» yozing.", reply_markup=cancel_kb()
        )

    @dp.message(T.deadline, not_btn, is_adm, F.text)
    async def st_deadline(message: Message, state: FSMContext):
        t = message.text.strip()
        if t == "-":
            await bots.update_one({"_id": ObjectId(bot_id)}, {"$set": {"ob.deadline": None}})
            msg = "✅ Muddat o'chirildi."
        else:
            ts = parse_dt(t)
            if not ts:
                await message.answer("Format noto'g'ri. Masalan: 25.12.2026 23:59")
                return
            await bots.update_one({"_id": ObjectId(bot_id)}, {"$set": {"ob.deadline": ts}})
            msg = f"✅ Muddat belgilandi: {fmt_dt(ts)}"
        await state.clear()
        await message.answer(msg, reply_markup=admin_kb())

    # ---------- admin: rasmiy havola ----------
    @dp.message(is_adm, F.text == A_LINK)
    async def a_link(message: Message, state: FSMContext):
        await state.clear()
        d = await get_doc()
        await state.set_state(T.link)
        await message.answer(
            f"Joriy havola: {official_link(d)}\n\nYangi rasmiy havolani yuboring (https://...):",
            reply_markup=cancel_kb(),
        )

    @dp.message(T.link, not_btn, is_adm, F.text)
    async def st_link(message: Message, state: FSMContext):
        t = message.text.strip()
        if not valid_url(t):
            await message.answer("Havola noto'g'ri. https:// bilan boshlanishi kerak.")
            return
        await bots.update_one({"_id": ObjectId(bot_id)}, {"$set": {"ob.link": t}})
        await state.clear()
        await message.answer("✅ Rasmiy havola saqlandi.", reply_markup=admin_kb())

    # ---------- admin: matnlar ----------
    @dp.message(is_adm, F.text == A_TEXTS)
    async def a_texts(message: Message, state: FSMContext):
        await state.clear()
        kb = InlineKeyboardBuilder()
        kb.button(text="👋 Salomlashuv matni", callback_data="tx:welcome")
        kb.button(text="❓ Yo'riqnoma matni", callback_data="tx:guide")
        kb.button(text="ℹ️ Bot haqida matni", callback_data="tx:about")
        kb.adjust(1)
        await message.answer("Qaysi matnni o'zgartiramiz?", reply_markup=kb.as_markup())

    @dp.callback_query(is_adm, F.data.startswith("tx:"))
    async def c_tx(cb: CallbackQuery, state: FSMContext):
        key = cb.data.split(":")[1]
        d = await get_doc()
        await state.set_state(T.text)
        await state.update_data(key=key)
        await cb.message.answer(f"Joriy matn:\n\n{get_texts(d).get(key, '')}\n\nYangi matnni yuboring:", reply_markup=cancel_kb())
        await cb.answer()

    @dp.message(T.text, not_btn, is_adm, F.text)
    async def st_text(message: Message, state: FSMContext):
        d = await state.get_data()
        await bots.update_one({"_id": ObjectId(bot_id)}, {"$set": {f"ob.texts.{d['key']}": message.text[:1500]}})
        await state.clear()
        await message.answer("✅ Matn saqlandi.", reply_markup=admin_kb())

    # ---------- admin: murojaatlar ----------
    @dp.message(is_adm, F.text == A_INBOX)
    async def a_inbox(message: Message, state: FSMContext):
        await state.clear()
        items = await ob_inbox.find({"bot_id": bot_id, "answered": False}).sort("ts", 1).limit(10).to_list()
        if not items:
            await message.answer("📭 Javob kutayotgan murojaat yo'q.")
            return
        for it in items:
            kb = InlineKeyboardBuilder()
            kb.button(text="↩️ Javob berish", callback_data=f"ir:{it['_id']}")
            await message.answer(f"👤 {it['name']} (ID: {it['user_id']})\n\n{it['text']}", reply_markup=kb.as_markup())

    @dp.callback_query(is_adm, F.data.startswith("ir:"))
    async def c_ir(cb: CallbackQuery, state: FSMContext):
        await state.set_state(T.reply)
        await state.update_data(inbox=cb.data.split(":")[1])
        await cb.message.answer("Javobingizni yuboring:", reply_markup=cancel_kb())
        await cb.answer()

    @dp.message(T.reply, not_btn, is_adm)
    async def st_reply(message: Message, state: FSMContext):
        d = await state.get_data()
        try:
            it = await ob_inbox.find_one({"_id": ObjectId(d["inbox"])})
        except Exception:
            it = None
        await state.clear()
        if not it:
            await message.answer("Murojaat topilmadi.", reply_markup=admin_kb())
            return
        try:
            await message.copy_to(it["user_id"])
            await ob_inbox.update_one({"_id": it["_id"]}, {"$set": {"answered": True}})
            await message.answer("✅ Javob yuborildi.", reply_markup=admin_kb())
        except Exception:
            await message.answer("Yuborib bo'lmadi.", reply_markup=admin_kb())

    # ---------- admin: bloklash ----------
    @dp.message(is_adm, F.text == A_BLOCK)
    async def a_block(message: Message, state: FSMContext):
        await state.clear()
        await state.set_state(T.block)
        await message.answer("Foydalanuvchi ID sini yuboring (bloklash/blokdan chiqarish):", reply_markup=cancel_kb())

    @dp.message(T.block, not_btn, is_adm, F.text)
    async def st_block(message: Message, state: FSMContext):
        t = message.text.strip()
        if not t.isdigit():
            await message.answer("Faqat raqam (ID) yuboring.")
            return
        uid = int(t)
        if uid in await admin_ids():
            await message.answer("Adminni bloklab bo'lmaydi.")
            return
        u = await bot_users.find_one({"bot_id": bot_id, "user_id": uid})
        if not u:
            await message.answer("Bunday foydalanuvchi topilmadi.")
            return
        new = not u.get("blocked")
        await bot_users.update_one({"_id": u["_id"]}, {"$set": {"blocked": new}})
        await state.clear()
        await message.answer("🚫 Bloklandi." if new else "✅ Blokdan chiqarildi.", reply_markup=admin_kb())

    # ---------- admin: eksport ----------
    @dp.message(is_adm, F.text == A_EXPORT)
    async def a_export(message: Message, state: FSMContext):
        await state.clear()
        users = await bot_users.find({"bot_id": bot_id}).to_list()
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["user_id", "ism", "username", "qoshilgan", "bloklangan", "eslatma", "taklif_qilgan"])
        for u in users:
            w.writerow([u["user_id"], u.get("first_name", ""), u.get("username") or "", fmt_dt(u.get("joined", 0)),
                        int(bool(u.get("blocked"))), int(bool(u.get("remind"))), u.get("referred_by", "")])
        await message.answer_document(
            BufferedInputFile(buf.getvalue().encode("utf-8-sig"), filename="openbudget_users.csv"),
            caption=f"📤 {len(users)} ta foydalanuvchi",
        )

    # ---------- admin: eslatma yuborish ----------
    @dp.message(is_adm, F.text == A_REMIND)
    async def a_remind(message: Message, state: FSMContext):
        await state.clear()
        n = await bot_users.count_documents({"bot_id": bot_id, "remind": True, "blocked": {"$ne": True}})
        if not n:
            await message.answer("Eslatmaga obuna bo'lgan foydalanuvchi yo'q.")
            return
        await state.set_state(T.remind)
        await message.answer(
            f"🔔 Eslatma matnini yuboring. Standart matn uchun «-» yozing.\nEslatma {n} ta obunachiga yuboriladi.",
            reply_markup=cancel_kb(),
        )

    @dp.message(T.remind, not_btn, is_adm, F.text)
    async def st_remind(message: Message, state: FSMContext):
        await state.clear()
        d = await get_doc()
        t = message.text.strip()
        if t == "-":
            ts = deadline_of(d)
            t = "🔔 Eslatma! Ovoz berish davom etmoqda."
            if ts:
                t += f"\n📅 Muddat: {fmt_dt(ts)} ({left_text(ts)})"
            t += f"\n🔗 {official_link(d)}"
        ok = bad = 0
        for u in await bot_users.find({"bot_id": bot_id, "remind": True, "blocked": {"$ne": True}}).to_list():
            try:
                await message.bot.send_message(u["user_id"], t)
                ok += 1
            except Exception:
                bad += 1
            await asyncio.sleep(0.05)
        await message.answer(f"🔔 Yuborildi: {ok}, yetib bormadi: {bad}", reply_markup=admin_kb())

    # ---------- admin: majburiy obuna ----------
    @dp.message(is_adm, F.text == A_SUBS)
    async def a_subs(message: Message, state: FSMContext):
        await state.clear()
        kb = InlineKeyboardBuilder()
        kb.button(text="➕ Kanal qo'shish", callback_data="sc:add")
        kb.button(text="📋 Kanallar ro'yxati", callback_data="sc:list")
        kb.button(text="➖ Kanal o'chirish", callback_data="sc:del")
        kb.adjust(1)
        await message.answer("📡 Majburiy obuna boshqaruvi:", reply_markup=kb.as_markup())

    @dp.callback_query(is_adm, F.data.startswith("sc:"))
    async def c_sc(cb: CallbackQuery, state: FSMContext):
        action = cb.data.split(":")[1]
        d = await get_doc()
        chans = (d.get("ob") or {}).get("channels") or []
        if action == "add":
            await state.set_state(T.channel)
            await cb.message.answer(
                "Kanal @username yoki ID sini yuboring.\n⚠️ Bot shu kanalda admin bo'lishi kerak.", reply_markup=cancel_kb()
            )
        elif action == "list":
            txt = "\n".join(f"• {c.get('title') or c['id']}" for c in chans) or "Kanallar yo'q."
            await cb.message.answer("📋 Kanallar:\n" + txt)
        else:
            if not chans:
                await cb.answer("Kanallar yo'q", show_alert=True)
                return
            kb = InlineKeyboardBuilder()
            for i, c in enumerate(chans):
                kb.button(text=f"🗑 {c.get('title') or c['id']}", callback_data=f"scd:{i}")
            kb.adjust(1)
            await cb.message.answer("Qaysi kanalni o'chiramiz?", reply_markup=kb.as_markup())
        await cb.answer()

    @dp.message(T.channel, not_btn, is_adm, F.text)
    async def st_channel(message: Message, state: FSMContext):
        t = message.text.strip()
        try:
            chat = await message.bot.get_chat(int(t) if t.lstrip("-").isdigit() else t)
        except Exception:
            await message.answer("Kanal topilmadi. @username yoki ID ni tekshiring.")
            return
        url = f"https://t.me/{chat.username}" if chat.username else None
        if not url:
            try:
                url = await message.bot.export_chat_invite_link(chat.id)
            except Exception:
                url = None
        await bots.update_one(
            {"_id": ObjectId(bot_id)},
            {"$push": {"ob.channels": {"id": chat.id, "title": chat.title, "username": chat.username, "url": url}}},
        )
        await state.clear()
        await message.answer(f"✅ Kanal qo'shildi: {chat.title}", reply_markup=admin_kb())

    @dp.callback_query(is_adm, F.data.startswith("scd:"))
    async def c_scd(cb: CallbackQuery):
        d = await get_doc()
        chans = list((d.get("ob") or {}).get("channels") or [])
        i = int(cb.data.split(":")[1])
        if 0 <= i < len(chans):
            chans.pop(i)
            await bots.update_one({"_id": ObjectId(bot_id)}, {"$set": {"ob.channels": chans}})
            await cb.message.answer("🗑 Kanal o'chirildi.")
        await cb.answer()

    # ---------- admin: adminlar ----------
    @dp.message(is_adm, F.text == A_ADMINS)
    async def a_admins(message: Message, state: FSMContext):
        await state.clear()
        kb = InlineKeyboardBuilder()
        kb.button(text="➕ Admin qo'shish", callback_data="ad:add")
        kb.button(text="📋 Adminlar ro'yxati", callback_data="ad:list")
        kb.button(text="➖ Adminni o'chirish", callback_data="ad:del")
        kb.adjust(1)
        await message.answer("👤 Adminlar boshqaruvi:", reply_markup=kb.as_markup())

    @dp.callback_query(is_adm, F.data.startswith("ad:"))
    async def c_ad(cb: CallbackQuery, state: FSMContext):
        action = cb.data.split(":")[1]
        d = await get_doc()
        extra = (d.get("ob") or {}).get("admins") or []
        if action == "add":
            await state.set_state(T.admin_add)
            await cb.message.answer("Yangi adminning Telegram ID sini yuboring:", reply_markup=cancel_kb())
        elif action == "list":
            lines = [f"👑 {owner_id} (egasi)"] + [f"👤 {a}" for a in extra]
            await cb.message.answer("👤 Adminlar:\n" + "\n".join(lines))
        else:
            if not extra:
                await cb.answer("Qo'shimcha adminlar yo'q", show_alert=True)
                return
            kb = InlineKeyboardBuilder()
            for a in extra:
                kb.button(text=f"🗑 {a}", callback_data=f"add:{a}")
            kb.adjust(1)
            await cb.message.answer("Qaysi adminni o'chiramiz?", reply_markup=kb.as_markup())
        await cb.answer()

    @dp.message(T.admin_add, not_btn, is_adm, F.text)
    async def st_admin_add(message: Message, state: FSMContext):
        t = message.text.strip()
        if not t.isdigit():
            await message.answer("Faqat raqam (ID) yuboring.")
            return
        await bots.update_one({"_id": ObjectId(bot_id)}, {"$addToSet": {"ob.admins": int(t)}})
        await state.clear()
        await message.answer("✅ Admin qo'shildi.", reply_markup=admin_kb())

    @dp.callback_query(is_adm, F.data.startswith("add:"))
    async def c_add_del(cb: CallbackQuery):
        await bots.update_one({"_id": ObjectId(bot_id)}, {"$pull": {"ob.admins": int(cb.data.split(":")[1])}})
        await cb.message.answer("🗑 Admin o'chirildi.")
        await cb.answer()

    # =====================================================
    #  FOYDALANUVCHI QISMI
    # =====================================================
    async def categories():
        cats = await ob_projects.distinct("cat", {"bot_id": bot_id, "active": True, "cat": {"$ne": None}})
        return sorted(c for c in cats if c)

    async def mode_items(mode, uid):
        base = {"bot_id": bot_id, "active": True}
        if mode == "f":
            pids = [f["pid"] for f in await ob_favs.find({"bot_id": bot_id, "user_id": uid}).to_list()]
            base["pid"] = {"$in": pids}
        elif mode.startswith("c"):
            cats = await categories()
            i = int(mode[1:])
            base["cat"] = cats[i] if 0 <= i < len(cats) else None
        return await ob_projects.find(base).sort("pid", 1).to_list()

    async def show_list(target: Message, uid, mode, page, title, edit=False):
        items = await mode_items(mode, uid)
        if not items:
            empty = "⭐ Sevimlilar bo'sh." if mode == "f" else "Hali loyiha yo'q."
            if edit:
                await target.edit_text(empty)
            else:
                await target.answer(empty)
            return
        kb = page_kb(items, page, "pj", f"pp:{mode}")
        if edit:
            try:
                await target.edit_text(title, reply_markup=kb)
            except Exception:
                pass
        else:
            await target.answer(title, reply_markup=kb)

    def card_text(p, d):
        lines = [f"🗳 {p['title']}", f"🆔 ID: {p['pid']}"]
        if p.get("cat"):
            lines.append(f"🏷 Kategoriya: {p['cat']}")
        if p.get("desc"):
            lines += ["", p["desc"]]
        lines += ["", f"✅ Belgilangan ovozlar: {p.get('votes', 0)}  •  👁 {p.get('views', 0)}"]
        ts = deadline_of(d)
        if ts:
            lines.append(f"📅 Muddat: {fmt_dt(ts)} ({left_text(ts)})")
        lines.append("ℹ️ Ovoz rasmiy saytda beriladi, bu yerda faqat belgilab borasiz.")
        return "\n".join(lines)

    def card_kb(p, voted, fav, link):
        kb = InlineKeyboardBuilder()
        kb.button(text="🗳 Ovoz berish (rasmiy sayt)", url=link)
        kb.button(text="✅ Ovozim belgilangan" if voted else "✅ Ovoz berdim", callback_data=f"vt:{p['pid']}")
        kb.button(text="💔 Sevimlidan olib tashlash" if fav else "⭐ Sevimlilarga qo'shish", callback_data=f"fv:{p['pid']}")
        kb.adjust(1)
        return kb.as_markup()

    async def send_card(target: Message, uid, p):
        d = await get_doc()
        await ob_projects.update_one({"_id": p["_id"]}, {"$inc": {"views": 1}})
        p["views"] = p.get("views", 0) + 1
        voted = await ob_votes.find_one({"bot_id": bot_id, "pid": p["pid"], "user_id": uid}) is not None
        fav = await ob_favs.find_one({"bot_id": bot_id, "pid": p["pid"], "user_id": uid}) is not None
        link = p.get("link") or official_link(d)
        text = card_text(p, d)
        kb = card_kb(p, voted, fav, link)
        if p.get("photo"):
            await target.answer_photo(p["photo"], caption=text[:1000], reply_markup=kb)
        else:
            await target.answer(text[:4000], reply_markup=kb)

    @dp.message(F.text == U_VOTE)
    async def u_vote(message: Message, state: FSMContext):
        await state.clear()
        await show_list(message, message.from_user.id, "a", 0, "🗳 Ovoz berish uchun loyihani tanlang:")

    @dp.message(F.text == U_PROJ)
    async def u_proj(message: Message, state: FSMContext):
        await state.clear()
        await show_list(message, message.from_user.id, "a", 0, "📋 Loyihalar ro'yxati:")

    @dp.message(F.text == U_FAV)
    async def u_fav(message: Message, state: FSMContext):
        await state.clear()
        await show_list(message, message.from_user.id, "f", 0, "⭐ Sevimli loyihalaringiz:")

    @dp.callback_query(F.data.startswith("pp:"))
    async def c_pp(cb: CallbackQuery):
        _, mode, page = cb.data.split(":")
        await show_list(cb.message, cb.from_user.id, mode, int(page), "📋 Loyihalar:", edit=True)
        await cb.answer()

    @dp.callback_query(F.data.startswith("pj:"))
    async def c_pj(cb: CallbackQuery):
        p = await ob_projects.find_one({"bot_id": bot_id, "pid": int(cb.data.split(":")[1]), "active": True})
        if not p:
            await cb.answer("Loyiha topilmadi", show_alert=True)
            return
        await send_card(cb.message, cb.from_user.id, p)
        await cb.answer()

    @dp.callback_query(F.data.startswith("vt:"))
    async def c_vt(cb: CallbackQuery):
        pid = int(cb.data.split(":")[1])
        d = await get_doc()
        ts = deadline_of(d)
        if ts and ts < time.time():
            await cb.answer("⛔ Ovoz berish muddati tugagan", show_alert=True)
            return
        p = await ob_projects.find_one({"bot_id": bot_id, "pid": pid, "active": True})
        if not p:
            await cb.answer("Loyiha topilmadi", show_alert=True)
            return
        res = await ob_votes.update_one(
            {"bot_id": bot_id, "pid": pid, "user_id": cb.from_user.id}, {"$setOnInsert": {"ts": time.time()}}, upsert=True
        )
        if res.upserted_id is None:
            await cb.answer("Siz bu loyihani allaqachon belgilagansiz", show_alert=True)
            return
        await ob_projects.update_one({"_id": p["_id"]}, {"$inc": {"votes": 1}})
        fav = await ob_favs.find_one({"bot_id": bot_id, "pid": pid, "user_id": cb.from_user.id}) is not None
        try:
            await cb.message.edit_reply_markup(reply_markup=card_kb(p, True, fav, p.get("link") or official_link(d)))
        except Exception:
            pass
        await cb.answer("✅ Rahmat! Ovozingiz belgilandi", show_alert=True)

    @dp.callback_query(F.data.startswith("fv:"))
    async def c_fv(cb: CallbackQuery):
        pid = int(cb.data.split(":")[1])
        uid = cb.from_user.id
        p = await ob_projects.find_one({"bot_id": bot_id, "pid": pid})
        if not p:
            await cb.answer("Loyiha topilmadi", show_alert=True)
            return
        key = {"bot_id": bot_id, "pid": pid, "user_id": uid}
        ex = await ob_favs.find_one(key)
        if ex:
            await ob_favs.delete_one({"_id": ex["_id"]})
            fav = False
        else:
            await ob_favs.insert_one(key)
            fav = True
        d = await get_doc()
        voted = await ob_votes.find_one(key) is not None
        try:
            await cb.message.edit_reply_markup(reply_markup=card_kb(p, voted, fav, p.get("link") or official_link(d)))
        except Exception:
            pass
        await cb.answer("⭐ Sevimlilarga qo'shildi" if fav else "Sevimlilardan olib tashlandi")

    @dp.message(F.text == U_SEARCH)
    async def u_search(message: Message, state: FSMContext):
        await state.clear()
        await state.set_state(T.search)
        await message.answer("🔎 Loyiha nomi yoki ID sini yozing:", reply_markup=cancel_kb())

    @dp.message(T.search, not_btn, F.text)
    async def st_search(message: Message, state: FSMContext):
        q = message.text.strip()
        await state.clear()
        base = {"bot_id": bot_id, "active": True}
        if q.isdigit():
            base["pid"] = int(q)
        else:
            base["title"] = {"$regex": re.escape(q), "$options": "i"}
        items = await ob_projects.find(base).sort("pid", 1).limit(20).to_list()
        if not items:
            await message.answer("❌ Hech narsa topilmadi.", reply_markup=await menu_for(message))
            return
        await message.answer("🔎 Topilgan loyihalar:", reply_markup=await menu_for(message))
        await message.answer("Tanlang 👇", reply_markup=page_kb(items, 0, "pj", "pp:a"))

    @dp.message(F.text == U_CATS)
    async def u_cats(message: Message, state: FSMContext):
        await state.clear()
        cats = await categories()
        if not cats:
            await message.answer("Kategoriyalar hali yo'q.")
            return
        kb = InlineKeyboardBuilder()
        for i, c in enumerate(cats):
            kb.button(text=c, callback_data=f"pc:{i}")
        kb.adjust(2)
        await message.answer("🏷 Kategoriyani tanlang:", reply_markup=kb.as_markup())

    @dp.callback_query(F.data.startswith("pc:"))
    async def c_pc(cb: CallbackQuery):
        await show_list(cb.message, cb.from_user.id, f"c{cb.data.split(':')[1]}", 0, "📋 Loyihalar:")
        await cb.answer()

    @dp.message(F.text == U_TOP)
    async def u_top(message: Message, state: FSMContext):
        await state.clear()
        items = await ob_projects.find({"bot_id": bot_id, "active": True}).to_list()
        items.sort(key=lambda p: (p.get("votes", 0), p.get("views", 0)), reverse=True)
        items = items[:10]
        if not items:
            await message.answer("Hali loyiha yo'q.")
            return
        lines = [f"{i + 1}. {p['title']} — ✅ {p.get('votes', 0)} • 👁 {p.get('views', 0)}" for i, p in enumerate(items)]
        kb = InlineKeyboardBuilder()
        for p in items:
            kb.button(text=f"{p['pid']}. {p['title'][:32]}", callback_data=f"pj:{p['pid']}")
        kb.adjust(1)
        await message.answer("🏆 Reyting (belgilangan ovozlar bo'yicha)\n\n" + "\n".join(lines), reply_markup=kb.as_markup())

    @dp.message(F.text == U_DEAD)
    async def u_dead(message: Message, state: FSMContext):
        await state.clear()
        d = await get_doc()
        ts = deadline_of(d)
        text = f"📅 Ovoz berish muddati: {fmt_dt(ts)}\n{left_text(ts)}" if ts else "📅 Ovoz berish muddati hali belgilanmagan."
        await message.answer(f"{text}\n\n🔗 Rasmiy havola: {official_link(d)}")

    @dp.message(F.text == U_REM)
    async def u_rem(message: Message, state: FSMContext):
        await state.clear()
        u = await bot_users.find_one({"bot_id": bot_id, "user_id": message.from_user.id})
        new = not (u or {}).get("remind")
        await bot_users.update_one({"bot_id": bot_id, "user_id": message.from_user.id}, {"$set": {"remind": new}})
        await message.answer("🔔 Eslatma yoqildi. Muddat yaqinlashganda xabar olasiz." if new else "🔕 Eslatma o'chirildi.")

    @dp.message(F.text == U_INV)
    async def u_inv(message: Message, state: FSMContext):
        await state.clear()
        me = await message.bot.get_me()
        uid = message.from_user.id
        n = await bot_users.count_documents({"bot_id": bot_id, "referred_by": uid})
        await message.answer(f"👥 Do'stlarni taklif\n\nHavolangiz:\nhttps://t.me/{me.username}?start=ref{uid}\n\nTaklif qilinganlar: {n}")

    @dp.message(F.text == U_QUIZ)
    async def u_quiz(message: Message, state: FSMContext):
        await state.clear()
        await send_question(message)

    async def send_question(target: Message):
        qi = random.randrange(len(QUIZ))
        q = QUIZ[qi]
        kb = InlineKeyboardBuilder()
        for ai, a in enumerate(q["a"]):
            kb.button(text=a, callback_data=f"qz:{qi}:{ai}")
        kb.adjust(1)
        await target.answer(f"🧠 Viktorina\n\n{q['q']}", reply_markup=kb.as_markup())

    @dp.callback_query(F.data.startswith("qz:"))
    async def c_qz(cb: CallbackQuery):
        _, qi, ai = cb.data.split(":")
        q = QUIZ[int(qi)]
        kb = InlineKeyboardBuilder()
        kb.button(text="🧠 Keyingi savol", callback_data="qn")
        if int(ai) == q["c"]:
            await bot_users.update_one({"bot_id": bot_id, "user_id": cb.from_user.id}, {"$inc": {"quiz": 1}})
            res = "✅ To'g'ri!"
        else:
            res = f"❌ Noto'g'ri. To'g'ri javob: {q['a'][q['c']]}"
        u = await bot_users.find_one({"bot_id": bot_id, "user_id": cb.from_user.id})
        try:
            await cb.message.edit_text(f"🧠 {q['q']}\n\n{res}\n\n🏅 Ballaringiz: {(u or {}).get('quiz', 0)}", reply_markup=kb.as_markup())
        except Exception:
            pass
        await cb.answer()

    @dp.callback_query(F.data == "qn")
    async def c_qn(cb: CallbackQuery):
        await send_question(cb.message)
        await cb.answer()

    @dp.message(F.text == U_GUIDE)
    async def u_guide(message: Message, state: FSMContext):
        await state.clear()
        await message.answer(get_texts(await get_doc())["guide"])

    @dp.message(F.text == U_SAFE)
    async def u_safe(message: Message, state: FSMContext):
        await state.clear()
        await message.answer(SAFETY)

    @dp.message(F.text == U_ABOUT)
    async def u_about(message: Message, state: FSMContext):
        await state.clear()
        await message.answer(get_texts(await get_doc())["about"])

    @dp.message(F.text == U_CONTACT)
    async def u_contact(message: Message, state: FSMContext):
        await state.clear()
        await state.set_state(T.contact)
        await message.answer("📞 Murojaatingizni yozing (matn, rasm...):", reply_markup=cancel_kb())

    @dp.message(T.contact, not_btn)
    async def st_contact(message: Message, state: FSMContext):
        await state.clear()
        u = message.from_user
        preview = (message.text or message.caption or "[fayl]")[:300]
        res = await ob_inbox.insert_one(
            {"bot_id": bot_id, "user_id": u.id, "name": u.full_name, "text": preview,
             "answered": False, "ts": time.time(), "admin_msgs": []}
        )
        admin_msgs = []
        for a in await admin_ids():
            try:
                head = await message.bot.send_message(
                    a, f"📞 Yangi murojaat\n👤 {u.full_name} (ID: {u.id})\nJavob berish uchun shu xabarga reply qiling."
                )
                cp = await message.copy_to(a)
                admin_msgs += [{"chat": a, "msg": head.message_id}, {"chat": a, "msg": cp.message_id}]
            except Exception:
                pass
        await ob_inbox.update_one({"_id": res.inserted_id}, {"$set": {"admin_msgs": admin_msgs}})
        await message.answer("✅ Murojaatingiz yuborildi. Tez orada javob beramiz.", reply_markup=user_kb())

    # admin murojaatga reply orqali javob beradi
    @dp.message(is_adm, F.reply_to_message)
    async def a_reply_to(message: Message):
        rid = message.reply_to_message.message_id
        it = await ob_inbox.find_one(
            {"bot_id": bot_id, "admin_msgs": {"$elemMatch": {"chat": message.chat.id, "msg": rid}}}
        )
        if not it:
            return
        try:
            await message.copy_to(it["user_id"])
            await ob_inbox.update_one({"_id": it["_id"]}, {"$set": {"answered": True}})
            await message.answer("✅ Javob yuborildi.")
        except Exception:
            await message.answer("Yuborib bo'lmadi.")

    @dp.message()
    async def fallback(message: Message):
        await message.answer("Menyudan tanlang 👇", reply_markup=await menu_for(message))

    return dp
