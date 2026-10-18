import asyncio
import csv
import io
import os
import time
from datetime import datetime, timedelta, timezone

from aiogram import Dispatcher, F
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from bson import ObjectId
from pymongo import AsyncMongoClient, ReturnDocument

# Anime bot moduli. Asosiy bot.py shu faylni import qiladi.
_mongo = AsyncMongoClient(os.environ["MONGO_URI"])
_db = _mongo["bot_yaratuvchi"]
bots = _db["bots"]
bot_users = _db["bot_users"]
anime = _db["anime"]            # animelar
anime_cats = _db["anime_cats"]  # kategoriyalar
anime_pay = _db["anime_pay"]    # to'lov so'rovlari
anime_buy = _db["anime_buy"]    # sotib olingan animelar


def fmt(n):
    return f"{int(n):,}".replace(",", " ")


async def track(bot_id, user):
    await bot_users.update_one(
        {"bot_id": bot_id, "user_id": user.id},
        {"$set": {"first_name": user.first_name}},
        upsert=True,
    )


async def delete_anime_data(bot_id):
    await anime.delete_many({"bot_id": bot_id})
    await anime_cats.delete_many({"bot_id": bot_id})
    await anime_pay.delete_many({"bot_id": bot_id})
    await anime_buy.delete_many({"bot_id": bot_id})


async def count_anime(bot_id):
    return await anime.count_documents({"bot_id": bot_id})


# =====================================================
#  ANIME BOT (admin panel, VIP, pullik, kategoriyalar)
# =====================================================
U_SEARCH = "🔍 Kod bo'yicha qidirish"
U_TOP = "🏆 TOP reyting"
U_CATS = "🗂 Kategoriyalar"
U_RECS = "⭐ Tavsiyalar"
U_VIP = "💎 VIP obuna"
U_ME = "👤 Profilim"
U_ADMIN = "🛠 Admin panel"

AD = {
    "add_anime": "🎬 Anime qo'shish",
    "add_series": "📺 Serial qo'shish",
    "add_ep": "➕ Seriallarga qism qo'shish",
    "list": "📋 Animelar ro'yxati",
    "desc": "✏️ Tavsifni tahrirlash",
    "del": "🗑 Anime o'chirish",
    "vip_add": "💎 VIP anime qo'shish",
    "vip_mark": "💎 VIP qilib belgilash",
    "paid_add": "💰 Pullik anime qo'shish",
    "paid_mark": "💰 Pullik qilib belgilash",
    "date": "📅 Chiqish sanasini belgilash",
    "cats": "🗂 Kategoriyalar boshqaruvi",
    "recs": "⭐ Tavsiyalar boshqaruvi",
    "export": "📤 Eksport",
    "back": "⬅️ Orqaga",
}


def today_tk():
    return datetime.now(timezone(timedelta(hours=5))).date()


def parse_date(s):
    for f in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(s.strip(), f).date()
        except ValueError:
            pass
    return None


def anime_info(it):
    lines = [f"🎌 {it['title']}", f"🆔 Kod: {it['code']}"]
    lines.append("📺 Serial" if it["kind"] == "series" else "🎞 Yakka anime")
    if it.get("cat"):
        lines.append(f"🗂 Kategoriya: {it['cat']}")
    if it.get("date"):
        lines.append(f"📅 Chiqish sanasi: {it['date']}")
    if it.get("vip"):
        lines.append("💎 VIP")
    if it.get("price", 0) > 0:
        lines.append(f"💰 Narxi: {fmt(it['price'])} so'm")
    if it["kind"] == "series":
        lines.append(f"🎞 Qismlar: {len(it.get('eps', []))}")
    lines.append(f"👁 Ko'rishlar: {it.get('views', 0)}")
    if it.get("desc"):
        lines += ["", it["desc"]]
    return "\n".join(lines)[:1000]


async def send_media(bot, chat_id, kind, file_id, caption=None):
    if kind == "video":
        await bot.send_video(chat_id, file_id, caption=caption)
    elif kind == "animation":
        await bot.send_animation(chat_id, file_id, caption=caption)
    else:
        await bot.send_document(chat_id, file_id, caption=caption)


def text_blocks(lines, limit=3500):
    block = ""
    for ln in lines:
        if len(block) + len(ln) + 1 > limit:
            yield block
            block = ""
        block += ln + "\n"
    if block:
        yield block


def item_line(it):
    mark = "📺" if it["kind"] == "series" else "🎞"
    if it.get("vip"):
        mark += "💎"
    if it.get("price", 0) > 0:
        mark += "💰"
    return f"{it['code']} — {it['title']} {mark}"


class A(StatesGroup):
    title = State()
    price = State()
    media = State()
    eps = State()
    code = State()
    value = State()
    receipt = State()


def make_anime_dispatcher(bot_id: str, owner_id: int) -> Dispatcher:
    adp = Dispatcher()
    OWN = F.from_user.id == owner_id

    # ---------- yordamchilar ----------
    async def bot_doc():
        return await bots.find_one({"_id": ObjectId(bot_id)})

    def user_kb(is_owner):
        kb = ReplyKeyboardBuilder()
        for t in (U_SEARCH, U_TOP, U_CATS, U_RECS, U_VIP, U_ME):
            kb.button(text=t)
        if is_owner:
            kb.button(text=U_ADMIN)
            kb.adjust(2, 2, 2, 1)
        else:
            kb.adjust(2, 2, 2)
        return kb.as_markup(resize_keyboard=True)

    def panel_kb():
        kb = ReplyKeyboardBuilder()
        for t in (
            AD["add_anime"], AD["add_series"], AD["add_ep"], AD["list"], U_SEARCH, AD["desc"],
            AD["del"], AD["vip_add"], AD["vip_mark"], AD["paid_add"], AD["paid_mark"], AD["date"],
            AD["cats"], AD["recs"], U_TOP, AD["export"], AD["back"],
        ):
            kb.button(text=t)
        kb.adjust(2)
        return kb.as_markup(resize_keyboard=True)

    async def is_vip(uid):
        u = await bot_users.find_one({"bot_id": bot_id, "user_id": uid})
        return bool(u and u.get("vip_until", 0) > time.time())

    async def purchased(uid, code):
        return await anime_buy.find_one({"bot_id": bot_id, "user_id": uid, "code": code}) is not None

    async def next_code():
        last = await anime.find_one({"bot_id": bot_id}, sort=[("code", -1)])
        return (last["code"] + 1) if last else 1

    async def gate(uid, it):
        """Ruxsat tekshiruvi: (ok, matn, tugmalar)"""
        if uid == owner_id:
            return True, None, None
        if it.get("date"):
            d = parse_date(it["date"])
            if d and d > today_tk():
                return False, f"⏳ Bu anime hali chiqmagan.\n📅 Chiqish sanasi: {it['date']}", None
        if it.get("vip") and not await is_vip(uid):
            kb = InlineKeyboardBuilder()
            kb.button(text="💎 VIP obuna", callback_data="vipinfo")
            return False, "💎 Bu VIP anime. Ko'rish uchun VIP obuna oling.", kb.as_markup()
        if it.get("price", 0) > 0 and not await purchased(uid, it["code"]):
            kb = InlineKeyboardBuilder()
            kb.button(text="💳 To'lash", callback_data=f"pay:{it['code']}")
            return False, f"💰 Bu pullik anime.\n{it['title']}\nNarxi: {fmt(it['price'])} so'm", kb.as_markup()
        return True, None, None

    async def deliver(bot, chat_id, it, uid):
        ok, text, markup = await gate(uid, it)
        if not ok:
            await bot.send_message(chat_id, text, reply_markup=markup)
            return
        await anime.update_one({"_id": it["_id"]}, {"$inc": {"views": 1}})
        if it["kind"] == "movie":
            m = it["media"]
            await send_media(bot, chat_id, m["kind"], m["file_id"], anime_info(it))
            return
        eps = it.get("eps", [])
        if not eps:
            await bot.send_message(chat_id, anime_info(it) + "\n\nQismlar hali qo'shilmagan.")
            return
        kb = InlineKeyboardBuilder()
        for i in range(min(len(eps), 100)):
            kb.button(text=str(i + 1), callback_data=f"ep:{it['code']}:{i + 1}")
        kb.adjust(5)
        await bot.send_message(chat_id, anime_info(it) + "\n\nQismni tanlang 👇", reply_markup=kb.as_markup())

    async def lookup(message: Message, code: int):
        it = await anime.find_one({"bot_id": bot_id, "code": code})
        if not it:
            await message.answer("❌ Bunday kod topilmadi.")
            return
        await deliver(message.bot, message.chat.id, it, message.from_user.id)

    async def top_lines():
        items = await anime.find({"bot_id": bot_id}).sort("views", -1).limit(10).to_list()
        return [f"{i + 1}. {item_line(it)} — 👁 {it.get('views', 0)}" for i, it in enumerate(items)]

    async def begin_payment(bot, chat_id, uid, state: FSMContext, pk, code, amount):
        d = await bot_doc()
        card = d.get("card") or "To'lov rekvizitlari hali kiritilmagan. Admin bilan bog'laning."
        await state.clear()
        await state.set_state(A.receipt)
        await state.update_data(pk=pk, code=code, amount=amount)
        await bot.send_message(
            chat_id,
            f"💳 To'lov\n\n💰 Summa: {fmt(amount)} so'm\n\n{card}\n\n"
            "To'lovni amalga oshirib, chek (skrinshot) rasmini shu yerga yuboring 📸\nBekor qilish: /bekor",
        )

    # ---------- buyruqlar ----------
    @adp.message(CommandStart())
    async def a_start(message: Message, state: FSMContext):
        await state.clear()
        doc = await bot_doc()
        if not doc:
            return
        await track(bot_id, message.from_user)
        await message.answer(doc["welcome"], reply_markup=user_kb(message.from_user.id == owner_id))

    @adp.message(Command("bekor"))
    async def a_cancel(message: Message, state: FSMContext):
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=user_kb(message.from_user.id == owner_id))

    @adp.message(Command("tugadi"))
    async def a_done(message: Message, state: FSMContext):
        await state.clear()
        kb = panel_kb() if message.from_user.id == owner_id else user_kb(False)
        await message.answer("✅ Tayyor.", reply_markup=kb)

    @adp.message(Command("stat"), OWN)
    async def a_stat(message: Message):
        n = await bot_users.count_documents({"bot_id": bot_id})
        c = await anime.count_documents({"bot_id": bot_id})
        await message.answer(f"📊 Foydalanuvchilar: {n}\n🎌 Animelar: {c}")

    @adp.message(Command("xabar"), OWN)
    async def a_broadcast(message: Message, command: CommandObject):
        if not command.args:
            await message.answer("Foydalanish: /xabar matn")
            return
        ok = bad = 0
        for u in await bot_users.find({"bot_id": bot_id}).to_list():
            try:
                await message.bot.send_message(u["user_id"], command.args)
                ok += 1
            except Exception:
                bad += 1
            await asyncio.sleep(0.05)
        await message.answer(f"📢 Yuborildi: {ok}, yetib bormadi: {bad}")

    @adp.message(Command("karta"), OWN)
    async def a_card(message: Message, command: CommandObject):
        if not command.args:
            await message.answer("Foydalanish: /karta 8600 **** **** **** Ism Familiya")
            return
        await bots.update_one({"_id": ObjectId(bot_id)}, {"$set": {"card": command.args}})
        await message.answer("✅ Karta ma'lumoti saqlandi.")

    @adp.message(Command("vip"), OWN)
    async def a_vipset(message: Message, command: CommandObject):
        parts = (command.args or "").split()
        if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
            await message.answer("Foydalanish: /vip NARX KUN\nMasalan: /vip 30000 30")
            return
        await bots.update_one(
            {"_id": ObjectId(bot_id)}, {"$set": {"vip_price": int(parts[0]), "vip_days": int(parts[1])}}
        )
        await message.answer(f"✅ VIP obuna: {fmt(int(parts[0]))} so'm / {parts[1]} kun")

    # ---------- admin panel ----------
    @adp.message(Command("admin"), OWN)
    @adp.message(F.text == U_ADMIN, OWN)
    async def a_panel(message: Message, state: FSMContext):
        await state.clear()
        await message.answer(
            "🛠 Admin panel\n\n"
            "Qo'shimcha buyruqlar:\n"
            "/karta matn — to'lov kartasi\n"
            "/vip NARX KUN — VIP obuna narxi (masalan /vip 30000 30)\n"
            "/xabar matn — barchaga xabar\n"
            "/stat — statistika\n"
            "/bekor — amalni bekor qilish",
            reply_markup=panel_kb(),
        )

    @adp.message(F.text == AD["back"], OWN)
    async def a_back(message: Message, state: FSMContext):
        await state.clear()
        await message.answer("Asosiy menyu", reply_markup=user_kb(True))

    def reg_add(key, kind, vip, paid):
        @adp.message(F.text == AD[key], OWN)
        async def _h(message: Message, state: FSMContext):
            await state.clear()
            await state.set_state(A.title)
            await state.update_data(kind=kind, vip=vip, paid=paid)
            await message.answer("📝 Nomini yozing:\n(Bekor qilish: /bekor)")

    reg_add("add_anime", "movie", False, False)
    reg_add("add_series", "series", False, False)
    reg_add("vip_add", "movie", True, False)
    reg_add("paid_add", "movie", False, True)

    def reg_code(key, act, prompt):
        @adp.message(F.text == AD[key], OWN)
        async def _h(message: Message, state: FSMContext):
            await state.clear()
            await state.set_state(A.code)
            await state.update_data(act=act)
            await message.answer(prompt + "\n(Bekor qilish: /bekor)")

    reg_code("add_ep", "add_ep", "📺 Serial kodini yozing:")
    reg_code("desc", "desc", "✏️ Qaysi animening tavsifi o'zgaradi? Kodni yozing:")
    reg_code("del", "del", "🗑 O'chiriladigan anime kodini yozing:")
    reg_code("vip_mark", "vip", "💎 VIP qilinadigan (yoki VIP dan chiqariladigan) anime kodini yozing:")
    reg_code("paid_mark", "price", "💰 Narx belgilanadigan anime kodini yozing:")
    reg_code("date", "date", "📅 Chiqish sanasi belgilanadigan anime kodini yozing:")

    @adp.message(F.text == AD["list"], OWN)
    async def a_list(message: Message, state: FSMContext):
        await state.clear()
        items = await anime.find({"bot_id": bot_id}).sort("code", 1).to_list()
        if not items:
            await message.answer("Hali anime yo'q.")
            return
        for block in text_blocks([item_line(it) for it in items]):
            await message.answer(block)

    @adp.message(F.text == AD["export"], OWN)
    async def a_export(message: Message, state: FSMContext):
        await state.clear()
        items = await anime.find({"bot_id": bot_id}).sort("code", 1).to_list()
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["kod", "nom", "turi", "kategoriya", "vip", "narx", "sana", "korishlar", "qismlar"])
        for it in items:
            w.writerow(
                [it["code"], it["title"], it["kind"], it.get("cat") or "", int(bool(it.get("vip"))),
                 it.get("price", 0), it.get("date") or "", it.get("views", 0), len(it.get("eps", []))]
            )
        await message.answer_document(
            BufferedInputFile(buf.getvalue().encode("utf-8-sig"), filename="animelar.csv")
        )
        buf2 = io.StringIO()
        w2 = csv.writer(buf2)
        w2.writerow(["user_id", "ism", "vip_gacha"])
        for u in await bot_users.find({"bot_id": bot_id}).to_list():
            w2.writerow([u["user_id"], u.get("first_name", ""), int(u.get("vip_until", 0))])
        await message.answer_document(
            BufferedInputFile(buf2.getvalue().encode("utf-8-sig"), filename="foydalanuvchilar.csv")
        )

    @adp.message(F.text == AD["cats"], OWN)
    async def a_cats(message: Message, state: FSMContext):
        await state.clear()
        kb = InlineKeyboardBuilder()
        kb.button(text="➕ Qo'shish", callback_data="cm:add")
        kb.button(text="🗑 O'chirish", callback_data="cm:del")
        kb.button(text="🏷 Animega biriktirish", callback_data="cm:assign")
        kb.button(text="📋 Ro'yxat", callback_data="cm:list")
        kb.adjust(2)
        await message.answer("🗂 Kategoriyalar boshqaruvi:", reply_markup=kb.as_markup())

    @adp.message(F.text == AD["recs"], OWN)
    async def a_recs(message: Message, state: FSMContext):
        await state.clear()
        kb = InlineKeyboardBuilder()
        kb.button(text="➕ Qo'shish", callback_data="rm:add")
        kb.button(text="➖ Olib tashlash", callback_data="rm:del")
        kb.button(text="📋 Ro'yxat", callback_data="rm:list")
        kb.adjust(2)
        await message.answer("⭐ Tavsiyalar boshqaruvi:", reply_markup=kb.as_markup())

    # ---------- foydalanuvchi tugmalari ----------
    @adp.message(F.text == U_SEARCH)
    async def u_search(message: Message, state: FSMContext):
        await state.clear()
        await state.set_state(A.code)
        await state.update_data(act="search")
        await message.answer("🔍 Anime kodini (raqam) yozing:")

    @adp.message(F.text == U_TOP)
    async def u_top(message: Message, state: FSMContext):
        await state.clear()
        lines = await top_lines()
        await message.answer("🏆 TOP reyting\n\n" + ("\n".join(lines) if lines else "Hali anime yo'q."))

    @adp.message(F.text == U_CATS)
    async def u_cats(message: Message, state: FSMContext):
        await state.clear()
        cats = await anime_cats.find({"bot_id": bot_id}).sort("name", 1).to_list()
        if not cats:
            await message.answer("Kategoriyalar hali yo'q.")
            return
        kb = InlineKeyboardBuilder()
        for c in cats:
            kb.button(text=c["name"], callback_data=f"uc:{c['_id']}")
        kb.adjust(2)
        await message.answer("🗂 Kategoriyani tanlang:", reply_markup=kb.as_markup())

    @adp.message(F.text == U_RECS)
    async def u_recs(message: Message, state: FSMContext):
        await state.clear()
        items = await anime.find({"bot_id": bot_id, "rec": True}).sort("code", -1).limit(30).to_list()
        if not items:
            await message.answer("Tavsiyalar hali yo'q.")
            return
        await message.answer("⭐ Tavsiyalar\n\n" + "\n".join(item_line(it) for it in items) + "\n\nKodni yozing 👇")

    async def vip_info(bot, chat_id, uid):
        d = await bot_doc()
        price = d.get("vip_price", 0)
        days = d.get("vip_days", 30)
        if not price:
            await bot.send_message(chat_id, "💎 VIP obuna hozircha mavjud emas.")
            return
        u = await bot_users.find_one({"bot_id": bot_id, "user_id": uid})
        until = (u or {}).get("vip_until", 0)
        status = ""
        if until > time.time():
            left = int((until - time.time()) // 86400) + 1
            status = f"\n✅ VIP faol, taxminan {left} kun qoldi."
        kb = InlineKeyboardBuilder()
        kb.button(text="💳 To'lash", callback_data="payvip")
        await bot.send_message(
            chat_id,
            f"💎 VIP obuna\n\n💰 Narxi: {fmt(price)} so'm\n📅 Muddati: {days} kun\n"
            f"VIP animelarni ko'rish imkonini beradi.{status}",
            reply_markup=kb.as_markup(),
        )

    @adp.message(F.text == U_VIP)
    async def u_vip(message: Message, state: FSMContext):
        await state.clear()
        await vip_info(message.bot, message.chat.id, message.from_user.id)

    @adp.message(F.text == U_ME)
    async def u_me(message: Message, state: FSMContext):
        await state.clear()
        uid = message.from_user.id
        u = await bot_users.find_one({"bot_id": bot_id, "user_id": uid})
        until = (u or {}).get("vip_until", 0)
        vip = "faol ✅" if until > time.time() else "yo'q"
        n = await anime_buy.count_documents({"bot_id": bot_id, "user_id": uid})
        await message.answer(f"👤 Profilim\n\n🆔 ID: {uid}\n💎 VIP: {vip}\n💰 Sotib olingan animelar: {n}")

    # ---------- HOLATLAR (admin jarayonlari) ----------
    @adp.message(A.title, OWN, F.text)
    async def st_title(message: Message, state: FSMContext):
        d = await state.get_data()
        await state.update_data(title=message.text.strip()[:100])
        if d.get("paid"):
            await state.set_state(A.price)
            await message.answer("💰 Narxini yozing (so'm, faqat raqam):")
        else:
            await state.set_state(A.media)
            what = "1-qism videosini" if d["kind"] == "series" else "videoni"
            await message.answer(f"🎞 Endi {what} yuboring:")

    @adp.message(A.price, OWN, F.text)
    async def st_price(message: Message, state: FSMContext):
        t = message.text.replace(" ", "")
        if not t.isdigit():
            await message.answer("Faqat raqam yozing yoki /bekor.")
            return
        await state.update_data(price=int(t))
        await state.set_state(A.media)
        await message.answer("🎞 Endi videoni yuboring:")

    @adp.message(A.media, OWN, F.video | F.document | F.animation)
    async def st_media(message: Message, state: FSMContext):
        d = await state.get_data()
        media = message.video or message.animation or message.document
        kind = "video" if message.video else ("animation" if message.animation else "document")
        code = await next_code()
        item = {
            "bot_id": bot_id,
            "code": code,
            "title": d["title"],
            "kind": d["kind"],
            "desc": "",
            "cat": None,
            "vip": bool(d.get("vip")),
            "price": int(d.get("price", 0)),
            "date": None,
            "views": 0,
            "rec": False,
            "created": time.time(),
        }
        media_doc = {"kind": kind, "file_id": media.file_id}
        if d["kind"] == "series":
            item["eps"] = [media_doc]
        else:
            item["media"] = media_doc
        await anime.insert_one(item)
        await state.clear()
        extra = "\nQism qo'shish: «➕ Seriallarga qism qo'shish»." if d["kind"] == "series" else ""
        await message.answer(
            f"✅ Saqlandi. Kod: {code}\nTavsif, kategoriya, sana: admin paneldagi tugmalar orqali.{extra}",
            reply_markup=panel_kb(),
        )

    @adp.message(A.media, OWN)
    async def st_media_bad(message: Message):
        await message.answer("Video (yoki fayl) yuboring, yoki /bekor.")

    @adp.message(A.eps, OWN, F.video | F.document | F.animation)
    async def st_eps(message: Message, state: FSMContext):
        d = await state.get_data()
        media = message.video or message.animation or message.document
        kind = "video" if message.video else ("animation" if message.animation else "document")
        r = await anime.find_one_and_update(
            {"bot_id": bot_id, "code": d["code"]},
            {"$push": {"eps": {"kind": kind, "file_id": media.file_id}}},
            return_document=ReturnDocument.AFTER,
        )
        n = len(r["eps"]) if r else 0
        await message.answer(f"✅ {n}-qism qo'shildi. Yana yuboring yoki /tugadi.")

    @adp.message(A.eps, OWN)
    async def st_eps_bad(message: Message):
        await message.answer("Qism videosini yuboring, tugatish uchun /tugadi.")

    @adp.message(A.code, F.text)
    async def st_code(message: Message, state: FSMContext):
        d = await state.get_data()
        act = d.get("act")
        uid = message.from_user.id
        if act != "search" and uid != owner_id:
            await state.clear()
            return
        t = message.text.strip()
        if not t.isdigit():
            await message.answer("Faqat raqam (kod) yozing yoki /bekor.")
            return
        it = await anime.find_one({"bot_id": bot_id, "code": int(t)})
        if not it:
            await message.answer("❌ Bunday kod topilmadi. Qayta yozing yoki /bekor.")
            return
        code = it["code"]
        if act == "search":
            await state.clear()
            await deliver(message.bot, message.chat.id, it, uid)
        elif act == "del":
            await anime.delete_one({"_id": it["_id"]})
            await anime_buy.delete_many({"bot_id": bot_id, "code": code})
            await state.clear()
            await message.answer(f"🗑 {code} — {it['title']} o'chirildi.", reply_markup=panel_kb())
        elif act == "vip":
            new = not it.get("vip")
            await anime.update_one({"_id": it["_id"]}, {"$set": {"vip": new}})
            await state.clear()
            await message.answer(("💎 VIP qilindi." if new else "VIP dan chiqarildi."), reply_markup=panel_kb())
        elif act in ("rec_add", "rec_del"):
            await anime.update_one({"_id": it["_id"]}, {"$set": {"rec": act == "rec_add"}})
            await state.clear()
            await message.answer("✅ Bajarildi.", reply_markup=panel_kb())
        elif act == "cat_assign":
            cats = await anime_cats.find({"bot_id": bot_id}).sort("name", 1).to_list()
            await state.clear()
            if not cats:
                await message.answer("Avval kategoriya qo'shing.", reply_markup=panel_kb())
                return
            kb = InlineKeyboardBuilder()
            for c in cats:
                kb.button(text=c["name"], callback_data=f"ca:{code}:{c['_id']}")
            kb.button(text="🚫 Kategoriyasiz", callback_data=f"ca:{code}:none")
            kb.adjust(2)
            await message.answer(f"{it['title']} uchun kategoriyani tanlang:", reply_markup=kb.as_markup())
        elif act == "add_ep":
            if it["kind"] != "series":
                await message.answer("Bu serial emas. Boshqa kod yozing yoki /bekor.")
                return
            await state.update_data(code=code)
            await state.set_state(A.eps)
            await message.answer(
                f"📺 {it['title']} (hozir {len(it.get('eps', []))} ta qism).\n"
                "Qism videolarini ketma-ket yuboring. Tugatish: /tugadi"
            )
        elif act in ("desc", "price", "date"):
            await state.update_data(code=code)
            await state.set_state(A.value)
            prompts = {
                "desc": "✏️ Yangi tavsifni yozing:",
                "price": "💰 Narxni yozing (so'm). 0 = bepul:",
                "date": "📅 Sanani yozing (masalan 2026-12-31 yoki 31.12.2026). Olib tashlash: -",
            }
            await message.answer(prompts[act])

    @adp.message(A.value, OWN, F.text)
    async def st_value(message: Message, state: FSMContext):
        d = await state.get_data()
        act = d.get("act")
        t = message.text.strip()
        if act == "cat_add":
            name = t[:30]
            if await anime_cats.find_one({"bot_id": bot_id, "name": name}):
                await message.answer("Bunday kategoriya bor.")
            else:
                await anime_cats.insert_one({"bot_id": bot_id, "name": name})
                await message.answer(f"✅ «{name}» qo'shildi.", reply_markup=panel_kb())
            await state.clear()
            return
        code = d.get("code")
        if act == "desc":
            await anime.update_one({"bot_id": bot_id, "code": code}, {"$set": {"desc": t[:600]}})
        elif act == "price":
            t2 = t.replace(" ", "")
            if not t2.isdigit():
                await message.answer("Faqat raqam yozing yoki /bekor.")
                return
            await anime.update_one({"bot_id": bot_id, "code": code}, {"$set": {"price": int(t2)}})
        elif act == "date":
            if t == "-":
                await anime.update_one({"bot_id": bot_id, "code": code}, {"$set": {"date": None}})
            else:
                dt = parse_date(t)
                if not dt:
                    await message.answer("Sana noto'g'ri. Masalan: 2026-12-31. Yoki /bekor.")
                    return
                await anime.update_one({"bot_id": bot_id, "code": code}, {"$set": {"date": dt.isoformat()}})
        await state.clear()
        await message.answer("✅ Saqlandi.", reply_markup=panel_kb())

    @adp.message(A.receipt, F.photo | F.document)
    async def st_receipt(message: Message, state: FSMContext):
        d = await state.get_data()
        u = message.from_user
        r = await anime_pay.insert_one(
            {
                "bot_id": bot_id,
                "user_id": u.id,
                "kind": d["pk"],
                "code": d.get("code"),
                "amount": d["amount"],
                "status": "pending",
                "ts": time.time(),
            }
        )
        pid = str(r.inserted_id)
        kb = InlineKeyboardBuilder()
        kb.button(text="✅ Tasdiqlash", callback_data=f"aok:{pid}")
        kb.button(text="❌ Rad etish", callback_data=f"ano:{pid}")
        kb.adjust(2)
        what = "VIP obuna" if d["pk"] == "vip" else f"Anime kodi {d.get('code')}"
        try:
            await message.bot.send_message(
                owner_id, f"💳 Yangi to'lov\n👤 {u.full_name} (ID: {u.id})\n📦 {what}\n💰 {fmt(d['amount'])} so'm"
            )
            await message.copy_to(owner_id, reply_markup=kb.as_markup())
        except Exception:
            await message.answer("Adminga yuborib bo'lmadi. Keyinroq urinib ko'ring.")
            await state.clear()
            return
        await state.clear()
        await message.answer("✅ Chek adminga yuborildi. Tasdiqlangach xabar keladi.")

    @adp.message(A.receipt)
    async def st_receipt_bad(message: Message):
        await message.answer("Chek rasmini yuboring yoki /bekor.")

    # ---------- INLINE TUGMALAR ----------
    @adp.callback_query(F.data.startswith("ep:"))
    async def cb_ep(cb: CallbackQuery):
        _, code, n = cb.data.split(":")
        it = await anime.find_one({"bot_id": bot_id, "code": int(code)})
        if not it:
            await cb.answer("Topilmadi", show_alert=True)
            return
        ok, text, markup = await gate(cb.from_user.id, it)
        if not ok:
            await cb.message.answer(text, reply_markup=markup)
            await cb.answer()
            return
        eps = it.get("eps", [])
        i = int(n) - 1
        if not (0 <= i < len(eps)):
            await cb.answer("Qism topilmadi", show_alert=True)
            return
        await send_media(cb.bot, cb.message.chat.id, eps[i]["kind"], eps[i]["file_id"], f"{it['title']} — {n}-qism")
        await cb.answer()

    @adp.callback_query(F.data.startswith("uc:"))
    async def cb_user_cat(cb: CallbackQuery):
        try:
            cat = await anime_cats.find_one({"_id": ObjectId(cb.data.split(":")[1]), "bot_id": bot_id})
        except Exception:
            cat = None
        if not cat:
            await cb.answer("Topilmadi", show_alert=True)
            return
        items = await anime.find({"bot_id": bot_id, "cat": cat["name"]}).sort("code", 1).to_list()
        if not items:
            await cb.message.answer(f"«{cat['name']}» da hali anime yo'q.")
        else:
            lines = [f"🗂 {cat['name']}", ""] + [item_line(it) for it in items] + ["", "Kodni yozing 👇"]
            for block in text_blocks(lines):
                await cb.message.answer(block)
        await cb.answer()

    @adp.callback_query(F.data == "vipinfo")
    async def cb_vipinfo(cb: CallbackQuery):
        await vip_info(cb.bot, cb.message.chat.id, cb.from_user.id)
        await cb.answer()

    @adp.callback_query(F.data == "payvip")
    async def cb_payvip(cb: CallbackQuery, state: FSMContext):
        d = await bot_doc()
        price = d.get("vip_price", 0)
        if not price:
            await cb.answer("VIP obuna mavjud emas", show_alert=True)
            return
        await begin_payment(cb.bot, cb.message.chat.id, cb.from_user.id, state, "vip", None, price)
        await cb.answer()

    @adp.callback_query(F.data.startswith("pay:"))
    async def cb_pay(cb: CallbackQuery, state: FSMContext):
        code = int(cb.data.split(":")[1])
        it = await anime.find_one({"bot_id": bot_id, "code": code})
        if not it or it.get("price", 0) <= 0:
            await cb.answer("Topilmadi", show_alert=True)
            return
        if await purchased(cb.from_user.id, code):
            await cb.answer("Bu animeni allaqachon sotib olgansiz", show_alert=True)
            return
        await begin_payment(cb.bot, cb.message.chat.id, cb.from_user.id, state, "item", code, it["price"])
        await cb.answer()

    @adp.callback_query(F.data.startswith("aok:") | F.data.startswith("ano:"), OWN)
    async def cb_decide(cb: CallbackQuery):
        action, pid = cb.data.split(":")
        ok = action == "aok"
        p = await anime_pay.find_one_and_update(
            {"_id": ObjectId(pid), "bot_id": bot_id, "status": "pending"},
            {"$set": {"status": "ok" if ok else "no"}},
        )
        if not p:
            await cb.answer("Bu so'rov allaqachon ko'rib chiqilgan", show_alert=True)
            return
        if ok and p["kind"] == "vip":
            d = await bot_doc()
            days = d.get("vip_days", 30)
            u = await bot_users.find_one({"bot_id": bot_id, "user_id": p["user_id"]})
            base = max(time.time(), (u or {}).get("vip_until", 0))
            await bot_users.update_one(
                {"bot_id": bot_id, "user_id": p["user_id"]},
                {"$set": {"vip_until": base + days * 86400}},
                upsert=True,
            )
            note = f"✅ To'lov tasdiqlandi. VIP obuna {days} kunga faollashtirildi!"
        elif ok:
            await anime_buy.update_one(
                {"bot_id": bot_id, "user_id": p["user_id"], "code": p["code"]},
                {"$set": {"ts": time.time()}},
                upsert=True,
            )
            note = f"✅ To'lov tasdiqlandi. Endi {p['code']} kodini yozing va tomosha qiling!"
        else:
            note = "❌ To'lov rad etildi. Savol bo'lsa, admin bilan bog'laning."
        try:
            await cb.bot.send_message(p["user_id"], note)
        except Exception:
            pass
        try:
            await cb.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await cb.answer("Bajarildi")

    # kategoriyalarni boshqarish
    @adp.callback_query(F.data.startswith("cm:"), OWN)
    async def cb_cm(cb: CallbackQuery, state: FSMContext):
        action = cb.data.split(":")[1]
        cats = await anime_cats.find({"bot_id": bot_id}).sort("name", 1).to_list()
        if action == "add":
            await state.clear()
            await state.set_state(A.value)
            await state.update_data(act="cat_add")
            await cb.message.answer("Yangi kategoriya nomini yozing:\n(Bekor qilish: /bekor)")
        elif action == "del":
            if not cats:
                await cb.answer("Kategoriyalar yo'q", show_alert=True)
                return
            kb = InlineKeyboardBuilder()
            for c in cats:
                kb.button(text=f"🗑 {c['name']}", callback_data=f"cd:{c['_id']}")
            kb.adjust(2)
            await cb.message.answer("Qaysi kategoriyani o'chiramiz?", reply_markup=kb.as_markup())
        elif action == "assign":
            await state.clear()
            await state.set_state(A.code)
            await state.update_data(act="cat_assign")
            await cb.message.answer("Qaysi animega? Kodini yozing:\n(Bekor qilish: /bekor)")
        elif action == "list":
            if not cats:
                await cb.message.answer("Kategoriyalar yo'q.")
            else:
                await cb.message.answer("🗂 Kategoriyalar:\n" + "\n".join(f"• {c['name']}" for c in cats))
        await cb.answer()

    @adp.callback_query(F.data.startswith("cd:"), OWN)
    async def cb_cat_del(cb: CallbackQuery):
        try:
            cat = await anime_cats.find_one({"_id": ObjectId(cb.data.split(":")[1]), "bot_id": bot_id})
        except Exception:
            cat = None
        if cat:
            await anime.update_many({"bot_id": bot_id, "cat": cat["name"]}, {"$set": {"cat": None}})
            await anime_cats.delete_one({"_id": cat["_id"]})
            await cb.message.answer(f"🗑 «{cat['name']}» o'chirildi.")
        await cb.answer()

    @adp.callback_query(F.data.startswith("ca:"), OWN)
    async def cb_cat_assign(cb: CallbackQuery):
        _, code, cid = cb.data.split(":")
        name = None
        if cid != "none":
            try:
                cat = await anime_cats.find_one({"_id": ObjectId(cid), "bot_id": bot_id})
            except Exception:
                cat = None
            if not cat:
                await cb.answer("Kategoriya topilmadi", show_alert=True)
                return
            name = cat["name"]
        await anime.update_one({"bot_id": bot_id, "code": int(code)}, {"$set": {"cat": name}})
        await cb.message.answer("✅ Saqlandi.")
        await cb.answer()

    # tavsiyalarni boshqarish
    @adp.callback_query(F.data.startswith("rm:"), OWN)
    async def cb_rm(cb: CallbackQuery, state: FSMContext):
        action = cb.data.split(":")[1]
        if action in ("add", "del"):
            await state.clear()
            await state.set_state(A.code)
            await state.update_data(act="rec_add" if action == "add" else "rec_del")
            await cb.message.answer("Anime kodini yozing:\n(Bekor qilish: /bekor)")
        else:
            items = await anime.find({"bot_id": bot_id, "rec": True}).sort("code", 1).to_list()
            await cb.message.answer(
                "⭐ Tavsiyalar:\n" + ("\n".join(item_line(it) for it in items) if items else "yo'q")
            )
        await cb.answer()

    # ---------- kod yozish va boshqa xabarlar ----------
    @adp.message(F.text.regexp(r"^\d+$"))
    async def a_code_text(message: Message):
        await track(bot_id, message.from_user)
        await lookup(message, int(message.text))

    @adp.message()
    async def a_fallback(message: Message):
        await message.answer(
            "Anime kodini (raqam) yozing yoki menyudan tanlang 👇",
            reply_markup=user_kb(message.from_user.id == owner_id),
        )

    return adp
