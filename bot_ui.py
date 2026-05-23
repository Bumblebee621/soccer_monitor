import logging
from typing import Optional, Any, Callable, Awaitable

from aiogram import Bot, Dispatcher, F, Router, BaseMiddleware
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, TelegramObject
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramBadRequest

from config_manager import RuntimeConfig, SUPPORTED_LEAGUES

logger = logging.getLogger("BotUI")

router = Router()
config: RuntimeConfig = None
allowed_chat_id: Optional[int] = None

# Price thresholds on Polymarket are between 0 and 1
THRESHOLD_MIN = 0.01
THRESHOLD_MAX = 1.0


class AuthMiddleware(BaseMiddleware):
    """Silently ignores all events from unauthorized chat IDs."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        chat_id = None
        if isinstance(event, Message):
            chat_id = event.chat.id
        elif isinstance(event, CallbackQuery):
            chat_id = event.message.chat.id if event.message else None

        if allowed_chat_id and chat_id != allowed_chat_id:
            return  # silently ignore unauthorized users

        return await handler(event, data)


class BotStates(StatesGroup):
    waiting_for_global_threshold = State()
    waiting_for_league_threshold = State()

def get_main_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Status", callback_data="menu_status")],
        [InlineKeyboardButton(text="⚙️ Edit Thresholds", callback_data="menu_thresholds")],
        [InlineKeyboardButton(text="🏆 Manage Leagues", callback_data="menu_leagues")],
        [InlineKeyboardButton(text="❌ Close", callback_data="menu_close")]
    ])

def get_thresholds_menu_kb():
    buttons = []
    
    # Global
    buttons.append([InlineKeyboardButton(text=f"🌍 Global Threshold: {config.global_threshold:.3f}", callback_data="edit_global_threshold")])
    
    # Leagues
    for league in config.active_leagues:
        t = config.league_thresholds.get(league)
        text = f"{league}: {t:.3f}" if t is not None else f"{league}: Default"
        buttons.append([
            InlineKeyboardButton(text=f"{text}", callback_data=f"edit_thresh_{league}"),
            InlineKeyboardButton(text="🗑 Reset", callback_data=f"reset_thresh_{league}")
        ])
        
    buttons.append([InlineKeyboardButton(text="⬅️ Back to Main Menu", callback_data="menu_main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_leagues_menu_kb():
    buttons = []
    for league in SUPPORTED_LEAGUES.keys():
        is_active = league in config.active_leagues
        status = "✅" if is_active else "❌"
        buttons.append([InlineKeyboardButton(text=f"{status} {league}", callback_data=f"toggle_league_{league}")])
        
    buttons.append([InlineKeyboardButton(text="⬅️ Back to Main Menu", callback_data="menu_main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def validate_threshold(value: float) -> str:
    """Returns an error message if the threshold is invalid, or empty string if valid."""
    if value < THRESHOLD_MIN or value > THRESHOLD_MAX:
        return f"Threshold must be between {THRESHOLD_MIN} and {THRESHOLD_MAX}. Please try again, or /cancel."
    return ""


@router.message(Command("menu", "start"))
async def cmd_menu(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("🛠 *Soccer Monitor Control Panel*", reply_markup=get_main_menu_kb(), parse_mode="Markdown")

@router.callback_query(F.data == "menu_main")
async def cb_menu_main(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("🛠 *Soccer Monitor Control Panel*", reply_markup=get_main_menu_kb(), parse_mode="Markdown")
    await callback.answer()

@router.callback_query(F.data == "menu_close")
async def cb_menu_close(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.delete()
    await callback.answer()

@router.callback_query(F.data == "menu_status")
async def cb_menu_status(callback: CallbackQuery):
    active_cnt = len(config.active_leagues)
    text = (
        f"📊 *Status*\n\n"
        f"Active Leagues: {active_cnt}\n"
        f"Global Threshold: {config.global_threshold:.3f}\n"
        f"Custom Thresholds: {len(config.league_thresholds)}"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Back", callback_data="menu_main")]])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")
    await callback.answer()

@router.callback_query(F.data == "menu_thresholds")
async def cb_menu_thresholds(callback: CallbackQuery):
    await callback.message.edit_text("⚙️ *Edit Thresholds*\nSelect a threshold to modify:", reply_markup=get_thresholds_menu_kb(), parse_mode="Markdown")
    await callback.answer()

@router.callback_query(F.data == "menu_leagues")
async def cb_menu_leagues(callback: CallbackQuery):
    await callback.message.edit_text("🏆 *Manage Leagues*\nTap to toggle monitoring:", reply_markup=get_leagues_menu_kb(), parse_mode="Markdown")
    await callback.answer()

@router.callback_query(F.data.startswith("toggle_league_"))
async def cb_toggle_league(callback: CallbackQuery):
    league = callback.data.replace("toggle_league_", "")
    config.toggle_league(league)
    await callback.message.edit_reply_markup(reply_markup=get_leagues_menu_kb())
    await callback.answer(f"Toggled {league}")

@router.callback_query(F.data == "edit_global_threshold")
async def cb_edit_global_threshold(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        f"Enter the new global threshold ({THRESHOLD_MIN}–{THRESHOLD_MAX}).\nType /cancel to abort.",
        reply_markup=None
    )
    await state.set_state(BotStates.waiting_for_global_threshold)
    await callback.answer()

@router.callback_query(F.data.startswith("edit_thresh_"))
async def cb_edit_league_threshold(callback: CallbackQuery, state: FSMContext):
    league = callback.data.replace("edit_thresh_", "")
    await state.update_data(target_league=league)
    await callback.message.edit_text(
        f"Enter the new threshold for {league} ({THRESHOLD_MIN}–{THRESHOLD_MAX}).\nType /cancel to abort.",
        reply_markup=None
    )
    await state.set_state(BotStates.waiting_for_league_threshold)
    await callback.answer()

@router.callback_query(F.data.startswith("reset_thresh_"))
async def cb_reset_league_threshold(callback: CallbackQuery):
    league = callback.data.replace("reset_thresh_", "")
    config.remove_league_threshold(league)
    try:
        await callback.message.edit_reply_markup(reply_markup=get_thresholds_menu_kb())
    except TelegramBadRequest:
        pass
    await callback.answer(f"Reset {league} threshold to default.")

@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is None:
        return
    await state.clear()
    await message.answer("Cancelled. Type /menu to open panel again.")

@router.message(BotStates.waiting_for_global_threshold)
async def process_global_threshold(message: Message, state: FSMContext):
    logger.info(f"process_global_threshold triggered, text={message.text!r}")
    if not message.text:
        await message.answer("Please send a number, or /cancel.")
        return
    try:
        val = float(message.text.strip())
        error = validate_threshold(val)
        if error:
            await message.answer(error)
            return
        config.set_global_threshold(val)
        await state.clear()
        await message.answer("✅ Global threshold updated.", reply_markup=get_thresholds_menu_kb())
    except ValueError:
        await message.answer("Invalid number. Please try again, or /cancel.")
    except Exception as e:
        logger.error(f"Error in process_global_threshold: {e}", exc_info=True)
        await message.answer(f"Unexpected error: {e}")

@router.message(BotStates.waiting_for_league_threshold)
async def process_league_threshold(message: Message, state: FSMContext):
    logger.info(f"process_league_threshold triggered, text={message.text!r}")
    if not message.text:
        await message.answer("Please send a number, or /cancel.")
        return
    try:
        val = float(message.text.strip())
        error = validate_threshold(val)
        if error:
            await message.answer(error)
            return
        data = await state.get_data()
        league = data.get("target_league")
        logger.info(f"Setting threshold for league={league!r} to {val}")
        if league:
            config.set_league_threshold(league, val)
            await state.clear()
            await message.answer(f"✅ {league} threshold updated to {val:.3f}.", reply_markup=get_thresholds_menu_kb())
        else:
            await state.clear()
            await message.answer("Error: League not found. Type /menu to restart.")
    except ValueError:
        await message.answer("Invalid number. Please try again, or /cancel.")
    except Exception as e:
        logger.error(f"Error in process_league_threshold: {e}", exc_info=True)
        await message.answer(f"Unexpected error: {e}")


@router.message()
async def catch_all_message(message: Message, state: FSMContext):
    """Debug: catches any message that didn't match another handler."""
    current_state = await state.get_state()
    logger.warning(
        f"catch_all_message: text={message.text!r}, state={current_state}, "
        f"chat_id={message.chat.id}, from_id={message.from_user.id}"
    )


async def start_bot_polling(bot_token: str, runtime_config: RuntimeConfig, chat_id: Optional[str]):
    global config, allowed_chat_id
    config = runtime_config
    if chat_id:
        try:
            allowed_chat_id = int(chat_id)
        except ValueError:
            pass
            
    bot = Bot(token=bot_token)
    dp = Dispatcher()

    # Register auth middleware on all handler types
    router.message.middleware(AuthMiddleware())
    router.callback_query.middleware(AuthMiddleware())

    dp.include_router(router)
    
    logger.info("Starting aiogram polling loop...")
    try:
        await dp.start_polling(bot)
    finally:
        logger.info("Shutting down aiogram bot session...")
        await bot.session.close()
