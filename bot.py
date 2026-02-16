import asyncio
import os
import logging
import time
from collections import defaultdict
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.types import FSInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder
import yt_dlp
from asyncio import to_thread
from aiohttp import web

# Настройки (из переменных окружения)
TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "@flawlessvideohub")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "-1001234567890"))

os.makedirs("downloads", exist_ok=True)

# Логирование без личных данных
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler("bot.log"), logging.StreamHandler()]
)

bot = Bot(token=TOKEN)
dp = Dispatcher()

ALLOWED_DOMAINS = [
    "tiktok.com", "vm.tiktok.com", "vt.tiktok.com",
    "instagram.com", "www.instagram.com",
    "youtube.com", "youtu.be", "m.youtube.com"
]

class ThrottlingMiddleware:
    def __init__(self):
        self.user_limits = defaultdict(list)
        self.download_times = defaultdict(float)

    async def __call__(self, handler, event: types.Message, data):
        user_id = event.from_user.id
        now = time.time()
        # Антифлуд: не более 5 сообщений в минуту
        self.user_limits[user_id] = [t for t in self.user_limits[user_id] if now - t < 60]
        if len(self.user_limits[user_id]) >= 5:
            await event.answer("Слишком много запросов! Подожди минуту ⏳")
            logging.warning("Обнаружен флуд")
            return
        self.user_limits[user_id].append(now)

        # Ограничение на скачивание: раз в 20 секунд
        if hasattr(event, "text") and "http" in event.text.lower():
            last_download = self.download_times[user_id]
            if now - last_download < 20:
                await event.answer("Подожди 20 секунд перед следующим скачиванием ⏳")
                return
            self.download_times[user_id] = now

        return await handler(event, data)

dp.message.middleware(ThrottlingMiddleware())

async def health(request):
    return web.Response(text="Bot is alive! OK")

async def web_server():
    app = web.Application()
    app.router.add_get('/', health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', os.getenv('PORT', 10000))
    await site.start()

async def check_subscription(user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(CHANNEL_ID, user_id)
        return member.status in ["member", "administrator", "creator"]
    except Exception as e:
        logging.error(f"Ошибка проверки подписки: {type(e).__name__}")
        return False

def subscribe_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="Подписаться на канал", url=f"https://t.me/{CHANNEL_USERNAME.lstrip('@')}")
    builder.button(text="Проверить подписку", callback_data="check_sub")
    return builder.as_markup()

@dp.message(CommandStart())
async def start(message: types.Message):
    welcome_text = (
        "🎥 *Привет! Я Flawless Video Bot* 🎥\n\n"
        "Я скачиваю видео *без водяных знаков* из:\n"
        "• TikTok\n"
        "• Instagram Reels\n"
        "• YouTube Shorts\n\n"
        "Просто отправь мне ссылку — и видео придёт чистым и в лучшем качестве! 🚀"
    )

    if await check_subscription(message.from_user.id):
        await message.answer(welcome_text + "\n\nМожешь отправлять ссылку прямо сейчас 👇", parse_mode="Markdown")
    else:
        await message.answer(
            welcome_text + "\n\n❗ Чтобы пользоваться ботом, подпишись на наш канал:",
            reply_markup=subscribe_keyboard(),
            parse_mode="Markdown"
        )

@dp.callback_query(F.data == "check_sub")
async def check_callback(callback: types.CallbackQuery):
    if await check_subscription(callback.from_user.id):
        await callback.message.edit_text(
            "🎉 Спасибо за подписку!\n\n"
            "Теперь можешь отправлять ссылки на видео из TikTok, Instagram Reels или YouTube Shorts.\n"
            "Видео придёт без водяных знаков и в отличном качестве! 🔥",
            parse_mode="Markdown"
        )
        await callback.answer()
    else:
        await callback.answer("Ты ещё не подписался! Подпишись и нажми «Проверить подписку» снова.", show_alert=True)

@dp.message(F.text.contains("http"))
async def handle_link(message: types.Message):
    if not await check_subscription(message.from_user.id):
        await message.answer("Подпишись на канал для использования бота!", reply_markup=subscribe_keyboard())
        return

    url = message.text.strip()
    logging.info("Получена ссылка на видео")

    if not any(domain in url for domain in ALLOWED_DOMAINS):
        await message.answer("Поддерживаются только ссылки из TikTok, Instagram Reels или YouTube Shorts 😔")
        return

    await message.answer("Скачиваю видео... Подожди ⏳")

    try:
        filename = await download_video(url)
        file_size = os.path.getsize(filename)
        if file_size < 50 * 1024 * 1024:
            await message.answer_video(FSInputFile(filename), caption="Готово! Без водяных знаков 👍")
        else:
            await message.answer_document(FSInputFile(filename), caption="Видео большое, отправляю как файл 👍")
        os.remove(filename)
        logging.info("Видео успешно отправлено")
    except Exception as e:
        logging.error(f"Ошибка скачивания видео: {type(e).__name__}")
        await message.answer("Не удалось скачать видео 😔 Проверь ссылку и попробуй снова.")

async def download_video(url: str):
    ydl_opts = {
        'format': 'best[height<=720][ext=mp4]/best[ext=mp4]/best',
        'outtmpl': 'downloads/%(id)s.%(ext)s',
        'noplaylist': True,
        'quiet': True,
        'no_warnings': True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = await to_thread(ydl.extract_info, url, download=True)
        filename = ydl.prepare_filename(info)
        return filename

async def main():
    await asyncio.gather(
        web_server(),
        dp.start_polling(bot)
    )

if __name__ == "__main__":
    asyncio.run(main())
