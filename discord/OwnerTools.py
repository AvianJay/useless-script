import ast
import io
import json
import subprocess
import discord
import globalenv
from globalenv import bot, start_bot, get_user_data, set_user_data, config, get_server_config, set_server_config, _config, default_config, get_all_user_data, db, on_close_tasks, reload_config
from discord.ext import commands
from typing import Callable, Union
import chat_exporter
from logger import log
import logging
import time
import asyncio
from pathlib import Path
import UtilCommands
import sys


APP_EMOJI_ASSET_DIR = Path(__file__).resolve().parent / "assets" / "app-emojis"
APP_EMOJI_MANIFEST_PATH = APP_EMOJI_ASSET_DIR / "manifest.json"
BUTTON_EMOJI_ASSET_DIR = Path(__file__).resolve().parent / "assets" / "button-emojis"
BUTTON_EMOJI_MANIFEST_PATH = BUTTON_EMOJI_ASSET_DIR / "manifest.json"
REPO_ROOT = Path(__file__).resolve().parents[1]
GLOBALENV_SOURCE_PATH = REPO_ROOT / "discord" / "globalenv.py"
UTIL_COMMANDS_SOURCE_PATH = REPO_ROOT / "discord" / "UtilCommands.py"
EMOJI_ASSET_SOURCES = [
    ("一般", APP_EMOJI_ASSET_DIR, APP_EMOJI_MANIFEST_PATH),
    ("按鈕", BUTTON_EMOJI_ASSET_DIR, BUTTON_EMOJI_MANIFEST_PATH),
]

def is_owner() -> Callable:
    async def predicate(ctx):
        return ctx.author.id in config("owners", [])
    return commands.check(predicate)

@bot.command(aliases=["set", "cfg"])
@is_owner()
async def settings(ctx, key: str=None, value: str=None):
    if key is None:
        safe_config = _config.copy()
        safe_config["TOKEN"] = "<token>"
        await ctx.send("目前設定：\n" + "\n".join(f"- {k}: {v}" for k, v in safe_config.items()))
    elif value is None:
        await ctx.send(f"{key}: {config(key, '未設定')}")
    elif key in _config:
        # check original type
        original_type = type(default_config.get(key))
        try:
            if original_type is bool:
                if value.lower() in ['true', '1', 'yes', 'on']:
                    value = True
                elif value.lower() in ['false', '0', 'no', 'off']:
                    value = False
                else:
                    await ctx.send(f"無法將 {value} 轉換為布林值。請使用 true/false。")
                    return
            elif original_type is int:
                value = int(value)
            elif original_type is float:
                value = float(value)
            elif original_type is list:
                try:
                    value = json.loads(value)
                except json.JSONDecodeError:  # fallback to comma split
                    value = [v.strip() for v in value.split(',')]
            elif original_type is dict:
                try:
                    value = json.loads(value)
                except json.JSONDecodeError:
                    await ctx.send(f"無法將 {value} 轉換為字典：請使用有效的 JSON 格式。")
                    return
            else:
                pass
        except Exception as e:
            await ctx.send(f"無法將 {value} 轉換為 {original_type.__name__}：{e}")
            return
        config(key=key, value=value, mode="w")
        await ctx.send(f"已更新 {key} 為 {str(value)}。")
    else:
        await ctx.send(f"找不到設定鍵：{key}")


status_map = {
    "done": "\u2713",
    "error": "\u2717",
    "none": "."
}

pending_ani = [
    "|",
    "/",
    "-",
    "\\"
]

class ANSIText:
    def __init__(self):
        self.clear = "\u001b[0m"
        self.gray = "\u001b[30m"
        self.red = "\u001b[31m"
        self.green = "\u001b[32m"
        self.yellow = "\u001b[33m"
        self.blue = "\u001b[34m"
        self.pink = "\u001b[35m"
        self.cyan = "\u001b[36m"
        self.white = "\u001b[37m"

    @classmethod
    def color_text(cls, text: str, color: str):
        return f"{color}{text}{cls().clear}"


ANSI = ANSIText()


def ansi_success(text: str):
    return ANSIText.color_text(text, ANSI.green)


def ansi_error(text: str):
    return ANSIText.color_text(text, ANSI.red)


def ansi_warning(text: str):
    return ANSIText.color_text(text, ANSI.yellow)


def ansi_info(text: str):
    return ANSIText.color_text(text, ANSI.cyan)


def ansi_muted(text: str):
    return ANSIText.color_text(text, ANSI.white)


def ansi_multiline(text: str, color: str):
    lines = (text or "").splitlines()
    if not lines:
        return ANSIText.color_text("(no output)", color)
    return "\n".join(ANSIText.color_text(line, color) for line in lines)


def create_shutdowntask_message(data: list, tick: int):
    texts = []
    for task in data:
        if task.get("status") == "pending":
            pr = pending_ani[tick % len(pending_ani)]
        else:
            pr = status_map.get(task.get("status"), task.get("status"))
        if task.get("time"):
            # 0.00 
            time_str = f"{task['time']:.2f}s"
        else:
            time_str = ""
        color = ANSIText().green if task.get("status") == "done" else ANSIText().red if task.get("status") == "error" else ANSIText().yellow if task.get("status") == "pending" else ANSIText().gray
        texts.append(ANSIText.color_text(f"[{pr}] {task['name']} {time_str} {task.get('error', '')}", color))

    t = '\n'.join(texts)
    return t


def truncate_codeblock_text(text: str, limit: int = 1200):
    safe_text = (text or "").replace("```", "'''").strip()
    if not safe_text:
        return "(no output)"
    if len(safe_text) <= limit:
        return safe_text
    return safe_text[:limit - 3].rstrip() + "..."


def format_config_value(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def summarize_config_value(value, limit: int = 220):
    return truncate_codeblock_text(format_config_value(value), limit)


def get_config_value_placeholder(default_value):
    if isinstance(default_value, bool):
        return "輸入 true / false"
    if isinstance(default_value, int) and not isinstance(default_value, bool):
        return "輸入整數"
    if isinstance(default_value, float):
        return "輸入數字"
    if isinstance(default_value, list):
        return "輸入 JSON 陣列或逗號分隔清單"
    if isinstance(default_value, dict):
        return "輸入 JSON 物件"
    return "輸入新的設定值"


def parse_config_value(raw_value: str, default_value):
    original_type = type(default_value)

    if original_type is bool:
        lowered = raw_value.strip().lower()
        if lowered in ["true", "1", "yes", "on"]:
            return True
        if lowered in ["false", "0", "no", "off"]:
            return False
        raise ValueError("布林值請輸入 true / false。")

    if original_type is int:
        try:
            return int(raw_value)
        except ValueError as e:
            raise ValueError(f"無法將值轉成整數：{e}") from e

    if original_type is float:
        try:
            return float(raw_value)
        except ValueError as e:
            raise ValueError(f"無法將值轉成數字：{e}") from e

    if original_type is list:
        try:
            parsed = json.loads(raw_value)
        except json.JSONDecodeError:
            return [value.strip() for value in raw_value.split(",") if value.strip()]
        if not isinstance(parsed, list):
            raise ValueError("清單值請輸入 JSON 陣列或逗號分隔清單。")
        return parsed

    if original_type is dict:
        try:
            parsed = json.loads(raw_value)
        except json.JSONDecodeError as e:
            raise ValueError(f"字典值請輸入 JSON 物件：{e}") from e
        if not isinstance(parsed, dict):
            raise ValueError("字典值請輸入 JSON 物件。")
        return parsed

    return raw_value


def create_progress_message(header: str, tasks: list, tick: int, *extra_lines: str):
    sections = [header.rstrip()]
    task_text = create_shutdowntask_message(tasks, tick).strip()
    if task_text:
        sections.append(task_text)
    sections.extend(line for line in extra_lines if line)
    return "```ansi\n" + "\n".join(sections) + "\n```"


def load_literal_from_module(module_path: Path, variable_name: str):
    module_ast = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    for node in module_ast.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == variable_name:
                return ast.literal_eval(node.value)
    raise ValueError(f"{variable_name} not found in {module_path.name}")


def run_git_pull():
    try:
        result = subprocess.run(
            ["git", "pull", "--ff-only"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except Exception as e:
        return False, f"git pull failed to start: {e}"

    output_parts = []
    if result.stdout.strip():
        output_parts.append(result.stdout.strip())
    if result.stderr.strip():
        output_parts.append(result.stderr.strip())
    if not output_parts:
        output_parts.append("Already up to date.")

    return result.returncode == 0, "\n".join(output_parts)


def read_runtime_config():
    config_file = Path(globalenv.config_path)
    config_data = json.loads(config_file.read_text(encoding="utf-8"))
    if not isinstance(config_data, dict):
        raise ValueError("config file is not a JSON object")
    return config_file, config_data


def get_missing_config_entries():
    try:
        latest_default_config = load_literal_from_module(GLOBALENV_SOURCE_PATH, "default_config")
        config_file, config_data = read_runtime_config()
    except Exception as e:
        return [], None, f"Could not inspect latest config state: {e}"

    missing_entries = []
    for key, value in latest_default_config.items():
        if key not in config_data:
            missing_entries.append({
                "key": key,
                "default": value,
                "type_name": type(value).__name__,
            })

    latest_config_version = latest_default_config.get("config_version")
    current_config_version = config_data.get("config_version")
    should_update_config_version = (
        isinstance(latest_config_version, int)
        and (
            not isinstance(current_config_version, int)
            or current_config_version < latest_config_version
        )
    )

    if not missing_entries and not should_update_config_version:
        return [], None, None

    return missing_entries, latest_config_version if should_update_config_version else None, None


def apply_config_updates(new_values: dict, config_version=None):
    try:
        config_file, config_data = read_runtime_config()
    except Exception as e:
        return [], f"Could not load config for writing: {e}"

    added_keys = []
    for key, value in new_values.items():
        if key not in config_data:
            added_keys.append(key)
        config_data[key] = value

    if config_version is not None:
        config_data["config_version"] = config_version

    if not added_keys and not new_values and config_version is None:
        return [], None

    try:
        config_file.write_text(
            json.dumps(config_data, ensure_ascii=False, indent=4),
            encoding="utf-8",
        )
        reload_config()
    except Exception as e:
        return [], f"Could not write config updates: {e}"

    return added_keys, None


def get_latest_full_version():
    try:
        latest_version = load_literal_from_module(UTIL_COMMANDS_SOURCE_PATH, "version")
        if not isinstance(latest_version, str):
            raise ValueError("version is not a string")
    except Exception:
        return UtilCommands.full_version

    try:
        git_commit_hash = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
        ).strip()
    except Exception:
        git_commit_hash = "unknown"

    return f"{latest_version} ({git_commit_hash})"


class RestartConfigValueModal(discord.ui.Modal):
    def __init__(self, review_view: "RestartConfigReviewView"):
        super().__init__(title=f"設定 {review_view.config_key}"[:45])
        self.review_view = review_view

        default_text = format_config_value(review_view.default_value)
        input_style = (
            discord.TextStyle.paragraph
            if isinstance(review_view.default_value, (list, dict)) or len(default_text) > 120
            else discord.TextStyle.short
        )
        self.value_input = discord.ui.TextInput(
            label="新的 config 值",
            default=default_text[:4000],
            placeholder=get_config_value_placeholder(review_view.default_value),
            required=True,
            max_length=4000,
            style=input_style,
        )
        self.add_item(self.value_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            parsed_value = parse_config_value(self.value_input.value, self.review_view.default_value)
        except ValueError as e:
            await interaction.response.send_message(f"這個值無法使用：{e}", ephemeral=True)
            return

        self.review_view.selected_value = parsed_value
        self.review_view.used_default = False
        await interaction.response.defer()
        self.review_view.stop()


class RestartConfigReviewView(discord.ui.View):
    def __init__(self, owner_id: int, config_key: str, default_value, message: discord.Message, timeout: float = 300):
        super().__init__(timeout=timeout)
        self.owner_id = owner_id
        self.config_key = config_key
        self.default_value = default_value
        self.message = message
        self.selected_value = None
        self.used_default = None
        self.timed_out = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("只有發起這次重啟的 owner 可以操作這個設定流程。", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        self.timed_out = True
        try:
            await self.message.edit(view=None)
        except Exception:
            pass
        self.stop()

    @discord.ui.button(label="繼續", style=discord.ButtonStyle.success)
    async def continue_with_default(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.selected_value = self.default_value
        self.used_default = True
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label="修改數值", style=discord.ButtonStyle.primary)
    async def modify_value(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(RestartConfigValueModal(self))


def start_restart_process(command):
    use_shell = isinstance(command, str)
    popen_kwargs = {
        "cwd": REPO_ROOT,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "shell": use_shell,
        "creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    }
    if sys.platform != "win32":
        popen_kwargs["start_new_session"] = True

    try:
        subprocess.Popen(command, **popen_kwargs)
    except Exception as e:
        return False, str(e)
    return True, None
    


@bot.command(aliases=["off", "exit", "q", "bye"])
@is_owner()
async def shutdown(ctx):
    await ctx.send("機器人正在關閉...")
    print("Shutting down...")
    if on_close_tasks:
        print("Running on_close tasks...")
        closingtext = "正在執行關閉前任務...共 {} 項。\n".format(len(on_close_tasks))
        msg = await ctx.send(f"```ansi\n{closingtext}\n```")
        tasks = []
        for task in on_close_tasks:
            tasks.append({
                "name": task.__name__,
                "func": task,
                "status": "none",
                "time": None
            })
        tick = 0
        for task in tasks:
            task["status"] = "pending"
            start_time = time.perf_counter()
            corotask = asyncio.create_task(task["func"]())
            while not corotask.done():
                end_time = time.perf_counter()
                task["time"] = end_time - start_time
                await msg.edit(content=f"```ansi\n{closingtext}\n{create_shutdowntask_message(tasks, tick)}\n```")
                tick += 1
                await asyncio.sleep(0.25)
            try:
                corotask.result()
                task["status"] = "done"
            except Exception as e:
                task["status"] = "error"
                task["error"] = str(e)
                log(f"執行關閉前任務 {task['name']} 時發生錯誤: {e}", level=logging.ERROR, module_name="OwnerTools")
            end_time = time.perf_counter()
            task["time"] = end_time - start_time
            await msg.edit(content=f"```ansi\n{closingtext}\n{create_shutdowntask_message(tasks, tick)}\n```")
            tick += 1
            await msg.edit(content=f"```ansi\n{closingtext}\n{create_shutdowntask_message(tasks, tick)}\n```")
    await bot.close()
    
@bot.command(aliases=["res", "reboot"])
@is_owner()
async def restart(ctx):
    msg = await ctx.send("機器人正在重啟...")
    print("Restarting...")
    closingtext = "機器人正在重啟..."
    tasks = []
    tick = 0
    if on_close_tasks:
        print("Running on_close tasks...")
        closingtext = "機器人正在重啟...\n正在執行關閉前任務...共 {} 項。\n".format(len(on_close_tasks))
        await msg.edit(content=f"```ansi\n{closingtext}\n```")
        tasks = []
        for task in on_close_tasks:
            tasks.append({
                "name": task.__name__,
                "func": task,
                "status": "none",
                "time": None
            })
        tick = 0
        for task in tasks:
            task["status"] = "pending"
            start_time = time.perf_counter()
            corotask = asyncio.create_task(task["func"]())
            while not corotask.done():
                end_time = time.perf_counter()
                task["time"] = end_time - start_time
                await msg.edit(content=f"```ansi\n{closingtext}\n{create_shutdowntask_message(tasks, tick)}\n```")
                tick += 1
                await asyncio.sleep(0.25)
            try:
                corotask.result()
                task["status"] = "done"
            except Exception as e:
                task["status"] = "error"
                task["error"] = str(e)
                log(f"執行關閉前任務 {task['name']} 時發生錯誤: {e}", level=logging.ERROR, module_name="OwnerTools")
            end_time = time.perf_counter()
            task["time"] = end_time - start_time
            await msg.edit(content=f"```ansi\n{closingtext}\n{create_shutdowntask_message(tasks, tick)}\n```")
            tick += 1
            await msg.edit(content=f"```ansi\n{closingtext}\n{create_shutdowntask_message(tasks, tick)}\n```")
    await msg.edit(content=create_progress_message(closingtext, tasks, tick, ansi_info("> git pull")))
    git_pull_ok, git_pull_output = run_git_pull()
    git_pull_summary = truncate_codeblock_text(git_pull_output)

    await msg.edit(
        content=create_progress_message(
            closingtext,
            tasks,
            tick,
            ansi_info("> git pull"),
            ansi_multiline(git_pull_summary, ANSI.white),
            ansi_info("> Checking new config keys..."),
        )
    )
    missing_config_entries, pending_config_version, config_sync_error = get_missing_config_entries()

    config_summary_lines = []
    applied_config_values = {}
    if config_sync_error:
        config_summary_lines.append(ansi_error(f"> Config check failed: {truncate_codeblock_text(config_sync_error, 300)}"))
    elif missing_config_entries:
        config_summary_lines.append(ansi_warning(f"> Found {len(missing_config_entries)} missing config key(s)."))
        if pending_config_version is not None:
            config_summary_lines.append(ansi_info(f"> Config version will be updated to {pending_config_version}."))

        for index, entry in enumerate(missing_config_entries, start=1):
            key = entry["key"]
            default_value = entry["default"]
            config_summary_lines.append(
                ansi_warning(
                    f"> Waiting for config {index}/{len(missing_config_entries)}: {key} ({entry['type_name']})"
                )
            )
            config_summary_lines.append(ansi_muted(f"  default = {summarize_config_value(default_value)}"))
            config_summary_lines.append(ansi_info("  choose: [繼續] 套用預設值 / [修改數值] 開啟 modal"))
            await msg.edit(
                content=create_progress_message(
                    closingtext,
                    tasks,
                    tick,
                    ansi_info("> git pull"),
                    ansi_multiline(git_pull_summary, ANSI.white),
                    ansi_info("> Checking new config keys..."),
                    *config_summary_lines,
                )
            )

            review_view = RestartConfigReviewView(ctx.author.id, key, default_value, msg)
            await msg.edit(view=review_view)
            await review_view.wait()
            await msg.edit(view=None)

            if review_view.timed_out or review_view.used_default is None:
                await msg.edit(
                    content=create_progress_message(
                        closingtext,
                        tasks,
                        tick,
                        ansi_info("> git pull"),
                        ansi_multiline(git_pull_summary, ANSI.white),
                        ansi_info("> Checking new config keys..."),
                        *config_summary_lines,
                        ansi_error("> Config review timed out. Restart cancelled."),
                    ),
                    view=None,
                )
                return

            applied_config_values[key] = review_view.selected_value
            action_label = "default" if review_view.used_default else "custom"
            config_summary_lines.append(
                ansi_success(f"  applied {key} ({action_label}) = {summarize_config_value(review_view.selected_value)}")
            )

        added_config_keys, config_apply_error = apply_config_updates(
            applied_config_values,
            config_version=pending_config_version,
        )
        if config_apply_error:
            config_summary_lines.append(ansi_error(f"> Failed to save config updates: {truncate_codeblock_text(config_apply_error, 300)}"))
            await msg.edit(
                content=create_progress_message(
                    closingtext,
                    tasks,
                    tick,
                    ansi_info("> git pull"),
                    ansi_multiline(git_pull_summary, ANSI.white),
                    ansi_info("> Checking new config keys..."),
                    *config_summary_lines,
                    ansi_error("> Restart cancelled because config updates were not saved."),
                ),
                view=None,
            )
            return
        else:
            config_summary_lines.append(ansi_success(f"> Saved {len(added_config_keys)} new config key(s)."))
    else:
        if pending_config_version is not None:
            _, config_apply_error = apply_config_updates({}, config_version=pending_config_version)
            if config_apply_error:
                config_summary_lines.append(ansi_error(f"> Failed to update config version: {truncate_codeblock_text(config_apply_error, 300)}"))
                await msg.edit(
                    content=create_progress_message(
                        closingtext,
                        tasks,
                        tick,
                        ansi_info("> git pull"),
                        ansi_multiline(git_pull_summary, ANSI.white),
                        ansi_info("> Checking new config keys..."),
                        *config_summary_lines,
                        ansi_error("> Restart cancelled because config version sync failed."),
                    ),
                    view=None,
                )
                return
            else:
                config_summary_lines.append(ansi_success(f"> Updated config version to {pending_config_version}."))
        else:
            config_summary_lines.append(ansi_success("> No missing config keys found."))

    latest_full_version = get_latest_full_version()
    if not git_pull_ok:
        config_summary_lines.append(ansi_warning("> git pull did not complete successfully; continuing with the current checkout."))

    await msg.edit(
        content=create_progress_message(
            closingtext,
            tasks,
            tick,
            ansi_info("> git pull"),
            ansi_multiline(git_pull_summary, ANSI.white),
            ansi_info("> Checking new config keys..."),
            *config_summary_lines,
            ansi_info(f"Version: {UtilCommands.full_version} -> {latest_full_version}"),
            ansi_info("> Restarting bot..."),
        )
    )

    restart_command = config("restart_command")
    if restart_command:
        await msg.edit(
            content=create_progress_message(
                closingtext,
                tasks,
                tick,
                ansi_info("> git pull"),
                ansi_multiline(git_pull_summary, ANSI.white),
                ansi_info("> Checking new config keys..."),
                *config_summary_lines,
                ansi_info(f"Version: {UtilCommands.full_version} -> {latest_full_version}"),
                ansi_info("> Restarting bot..."),
                ansi_info("> Running restart command..."),
            )
        )
        started, error = start_restart_process(restart_command)
        if not started:
            await msg.edit(
                content=create_progress_message(
                    closingtext,
                    tasks,
                    tick,
                    ansi_info("> git pull"),
                    ansi_multiline(git_pull_summary, ANSI.white),
                    ansi_info("> Checking new config keys..."),
                    *config_summary_lines,
                    ansi_info(f"Version: {UtilCommands.full_version} -> {latest_full_version}"),
                    ansi_info("> Restarting bot..."),
                    ansi_info("> Running restart command..."),
                    ansi_error(f"Error: {truncate_codeblock_text(error, 300)}"),
                )
            )
            return
        sys.exit(0)
    await msg.edit(
        content=create_progress_message(
            closingtext,
            tasks,
            tick,
            ansi_info("> git pull"),
            ansi_multiline(git_pull_summary, ANSI.white),
            ansi_info("> Checking new config keys..."),
            *config_summary_lines,
            ansi_info(f"Version: {UtilCommands.full_version} -> {latest_full_version}"),
            ansi_info("> Restarting bot..."),
            ansi_warning("> No restart command configured. Please restart the bot manually."),
        )
    )
    sys.exit(0)  # exit with code 0 to indicate successful restart


@bot.command(aliases=["user", "u"])
@is_owner()
async def userdata(ctx, guild_id: int=None, user_id: int=None, key: str=None, value: str=None):
    if guild_id is None or user_id is None:
        await ctx.send("請提供 guild_id 和 user_id。")
        return
    if key is None:
        all_data = get_all_user_data(guild_id, user_id)
        user_data = all_data.get(str(user_id), {})
        if not user_data:
            await ctx.send("沒有找到該用戶的資料。")
            return
        await ctx.send(f"用戶 {user_id} 的資料：\n")
        al = "\n".join(f"- {k}: {v}" for k, v in user_data.items())
        if len(al) > 1900:
            # send as file
            data_file = io.StringIO(al)
            data_file.name = f"user_{user_id}_data.txt"
            await ctx.send("資料過長，已作為檔案發送：", file=discord.File(fp=data_file))
    elif value is None:
        if "." in key:
            # dict key
            main_key, sub_key = key.split(".", 1)
        else:
            main_key, sub_key = key, None
        data = get_user_data(guild_id, user_id, main_key)
        if sub_key and isinstance(data, dict):
            data = data.get(sub_key)
        await ctx.send(f"用戶 {user_id} 的 {key}: {data if data is not None else '未設定'}")
    else:
        if "." in key:
            # dict key
            main_key, sub_key = key.split(".", 1)
            data = get_user_data(guild_id, user_id, main_key) or {}
            if not isinstance(data, dict):
                await ctx.send(f"用戶 {user_id} 的 {main_key} 不是一個字典，無法設定子鍵。")
                return
            data[sub_key] = value
            set_user_data(guild_id, user_id, main_key, data)
            await ctx.send(f"已更新用戶 {user_id} 的 {key} 為 {value}。")
        else:
            set_user_data(guild_id, user_id, key, value)
            await ctx.send(f"已更新用戶 {user_id} 的 {key} 為 {value}。")


@bot.command(aliases=["server", "sc"])
@is_owner()
async def serverconfig(ctx, guild_id: int=None, key: str=None, value: str=None):
    if guild_id is None:
        await ctx.send("請提供 guild_id。")
        return
    if key is None:
        # show all config
        config_data = db.get_all_server_config(guild_id)
        if not config_data:
            await ctx.send("沒有找到該伺服器的設定。")
            return
        await ctx.send(f"伺服器 {guild_id} 的設定：\n")
        al = "\n".join(f"- {k}: {v}" for k, v in config_data.items())
        if len(al) > 1900:
            # send as file
            config_file = io.StringIO(al)
            config_file.name = f"server_{guild_id}_config.txt"
            await ctx.send("設定過長，已作為檔案發送：", file=discord.File(fp=config_file))
        await ctx.send(al)
    elif value is None:
        if "." in key:
            # dict key
            main_key, sub_key = key.split(".", 1)
        else:
            main_key, sub_key = key, None
        data = get_server_config(guild_id, main_key)
        if sub_key and isinstance(data, dict):
            data = data.get(sub_key)
        await ctx.send(f"伺服器 {guild_id} 的 {key}: {data if data is not None else '未設定'}")
    else:
        if "." in key:
            # dict key
            main_key, sub_key = key.split(".", 1)
            data = get_server_config(guild_id, main_key) or {}
            if not isinstance(data, dict):
                await ctx.send(f"伺服器 {guild_id} 的 {main_key} 不是一個字典，無法設定子鍵。")
                return
            data[sub_key] = value
            set_server_config(guild_id, main_key, data)
            await ctx.send(f"已更新伺服器 {guild_id} 的 {key} 為 {value}。")
        else:
            set_server_config(guild_id, key, value)
            await ctx.send(f"已更新伺服器 {guild_id} 的 {key} 為 {value}。")


@bot.command(aliases=["l"])
@is_owner()
async def leaveserver(ctx, guild_id: int):
    guild = bot.get_guild(guild_id)
    if guild is None:
        await ctx.send("找不到該伺服器。")
        return
    await guild.leave()
    await ctx.send(f"已離開伺服器 {guild.name} (ID: `{guild.id}`) 。")


@bot.command(aliases=["invite", "inv"])
@is_owner()
async def getinvite(ctx, guild_id: int, create_if_none: bool=False):
    guild = bot.get_guild(guild_id)
    if guild is None:
        await ctx.send("找不到該伺服器。")
        return
    # Try to fetch existing invites
    try:
        invites = await guild.invites()
    except discord.Forbidden:
        # Lacking permission to list invites
        if create_if_none:
            # Try to create an invite in a channel the bot can create invites in
            channel = None
            for ch in guild.text_channels:
                perms = ch.permissions_for(guild.me)
                if perms.create_instant_invite:
                    channel = ch
                    break
            if channel is None:
                await ctx.send("無法取得邀請清單，且找不到可用來建立邀請的頻道或機器人缺少權限。")
                return
            try:
                invite = await channel.create_invite(max_age=0, max_uses=0, unique=True)
                await ctx.send(f"已替伺服器 {guild.name} 建立邀請連結：{invite.url}")
            except discord.Forbidden:
                await ctx.send("無法創建邀請連結，機器人缺少在該頻道建立邀請的權限。")
            return
        else:
            await ctx.send("無法取得邀請清單，機器人缺少列出邀請的權限。若要自動建立邀請請將 create_if_none 設為 True。")
            return

    # If we got invites list successfully
    if not invites:
        # No existing invites
        if create_if_none:
            channel = None
            for ch in guild.text_channels:
                perms = ch.permissions_for(guild.me)
                if perms.create_instant_invite:
                    channel = ch
                    break
            if channel is None:
                await ctx.send("該伺服器沒有任何邀請連結，且找不到可用於建立邀請的頻道。")
                return
            try:
                invite = await channel.create_invite(max_age=0, max_uses=0, unique=True)
                await ctx.send(f"已替伺服器 {guild.name} 建立邀請連結：{invite.url}")
            except discord.Forbidden:
                await ctx.send("無法創建邀請連結，機器人缺少建立邀請的權限。")
            return
        else:
            await ctx.send("該伺服器沒有任何邀請連結。若要自動建立邀請請將 create_if_none 設為 True。")
            return

    # Return the first invite found
    invite = invites[0]
    await ctx.send(f"伺服器 {guild.name} 的邀請連結：{invite.url}")


@bot.command(aliases=["servers", "ls"])
@is_owner()
async def listservers(ctx, query: str = None):
    guilds = bot.guilds
    if not guilds:
        await ctx.send("機器人目前沒有加入任何伺服器。")
        return
    servers_info = []
    for guild in guilds:
        if query and query.lower() not in guild.name.lower() and query not in str(guild.id):
            continue
        servers_info.append(f"- {guild.name} ({guild.member_count} 人) (ID: `{guild.id}`)")
    await ctx.send(f"機器人目前加入的伺服器： 共 {len(servers_info)} 個。")
    for i in range(0, len(servers_info), 30):
        await ctx.send("\n".join(servers_info[i:i+30]))


@bot.command(aliases=["send", "s", "msg"])
@is_owner()
async def sendmessage(ctx, channel_id: int, *, message: str):
    channel = bot.get_channel(channel_id)
    if channel is None:
        # try get user DM
        user = bot.get_user(channel_id)
        if user is not None:
            try:
                await user.send(message)
                await ctx.send(f"已在用戶 {user.name} 的私訊中發送訊息。")
            except discord.Forbidden:
                await ctx.send("無法在該用戶的私訊中發送訊息，機器人缺少權限。")
            return
        await ctx.send("找不到該頻道。")
        return
    try:
        await channel.send(message)
        await ctx.send(f"已在頻道 {channel.name} 發送訊息。")
    except discord.Forbidden:
        await ctx.send("無法在該頻道發送訊息，機器人缺少權限。")
    except Exception as e:
        await ctx.send(f"發送訊息時發生錯誤：{e}")


@bot.command(aliases=["uploadmissingemoji", "ume"])
@is_owner()
async def uploadmissingemojis(ctx, limit: int = None):
    manifest_sources = [
        (source_name, asset_dir, manifest_path)
        for source_name, asset_dir, manifest_path in EMOJI_ASSET_SOURCES
        if manifest_path.is_file()
    ]

    if not manifest_sources:
        await ctx.send(
            "找不到任何 emoji 清單：" + ", ".join(str(manifest_path) for _, _, manifest_path in EMOJI_ASSET_SOURCES)
        )
        return

    if limit is not None and limit <= 0:
        await ctx.send("limit 必須大於 0。")
        return

    existing_emojis = await bot.fetch_application_emojis()
    existing_names = {emoji.name for emoji in existing_emojis}

    missing_entries = []
    invalid_entries = []
    seen_manifest_names = set()
    for source_name, asset_dir, manifest_path in manifest_sources:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            await ctx.send(f"emoji 清單讀取失敗：{manifest_path} ({e})")
            return

        if not isinstance(manifest, list):
            await ctx.send(f"emoji 清單格式錯誤：{manifest_path} 必須是陣列。")
            return

        for entry in manifest:
            if not isinstance(entry, dict):
                invalid_entries.append((f"{source_name}:<invalid>", "manifest 項目不是物件"))
                continue

            emoji_name = entry.get("emoji_name")
            file_name = entry.get("file")
            if not emoji_name or not file_name:
                invalid_entries.append((f"{source_name}:{emoji_name or '<missing-name>'}", "缺少 emoji_name 或 file"))
                continue

            if emoji_name in seen_manifest_names:
                invalid_entries.append((f"{source_name}:{emoji_name}", "manifest 中有重複名稱"))
                continue
            seen_manifest_names.add(emoji_name)

            if emoji_name in existing_names:
                continue

            asset_path = asset_dir / file_name
            if not asset_path.is_file():
                invalid_entries.append((f"{source_name}:{emoji_name}", f"找不到檔案 {file_name}"))
                continue

            missing_entries.append((emoji_name, asset_path))

    total_missing = len(missing_entries)
    if limit is not None:
        missing_entries = missing_entries[:limit]

    if not missing_entries and not invalid_entries:
        await ctx.send(f"沒有缺少的 application emoji。目前共有 {len(existing_names)} 個。")
        return

    progress = await ctx.send(
        f"開始上傳缺少的 application emoji：本次 {len(missing_entries)} 個"
        f"（總缺少 {total_missing} 個，現有 {len(existing_names)} 個）。"
    )

    uploaded_names = []
    failed_entries = invalid_entries[:]
    for index, (emoji_name, asset_path) in enumerate(missing_entries, start=1):
        try:
            image_bytes = asset_path.read_bytes()
            await bot.create_application_emoji(name=emoji_name, image=image_bytes)
            uploaded_names.append(emoji_name)
        except discord.HTTPException as e:
            failed_entries.append((emoji_name, str(e)))
            log(
                f"上傳 application emoji {emoji_name} 失敗: {e}",
                level=logging.ERROR,
                module_name="OwnerTools"
            )

        if index == len(missing_entries) or index % 5 == 0:
            await progress.edit(
                content=(
                    f"正在上傳缺少的 application emoji... {index}/{len(missing_entries)}\n"
                    f"成功：{len(uploaded_names)} 個，失敗：{len(failed_entries)} 個"
                )
            )

    globalenv.fetched_emojis_cache = None

    summary_lines = [
        f"完成。成功上傳 {len(uploaded_names)} 個 application emoji。",
    ]
    if uploaded_names:
        summary_lines.append("成功名稱：" + ", ".join(uploaded_names))
    if failed_entries:
        summary_lines.append(
            "失敗項目：\n" + "\n".join(f"- {name}: {reason}" for name, reason in failed_entries[:20])
        )
        if len(failed_entries) > 20:
            summary_lines.append(f"其餘失敗項目還有 {len(failed_entries) - 20} 個。")

    await progress.edit(content="\n".join(summary_lines))


@bot.command(aliases=["transcript", "ct"])
@is_owner()
async def createtranscript(ctx, channel_id: int, after_message_id: int=None, before_message_id: int=None, limit: int=500):
    channel = bot.get_channel(channel_id)
    if channel is None or not isinstance(channel, discord.TextChannel):
        await ctx.send("找不到該文字頻道。")
        return
    try:
        messages = []
        async for msg in channel.history(limit=limit, after=discord.Object(id=after_message_id) if after_message_id else None, before=discord.Object(id=before_message_id) if before_message_id else None, oldest_first=True):
            messages.append(msg)
        messages.reverse()  # chat_exporter needs oldest first
        
        transcript = await chat_exporter.raw_export(
            channel,
            messages=messages,
            tz_info="Asia/Taipei",
            guild=channel.guild,
            bot=bot
        )

        # send as file
        transcript_file = io.BytesIO(transcript.encode('utf-8'))
        transcript_file.name = f"transcript_{channel.id}.html"
        await ctx.send("以下是頻道的對話紀錄：", file=discord.File(fp=transcript_file))
    except discord.Forbidden:
        await ctx.send("無法讀取該頻道的歷史訊息，機器人缺少權限。")
    except Exception as e:
        await ctx.send(f"創建對話紀錄時發生錯誤：{e}")


@bot.command(aliases=["dsi"])
@is_owner()
async def devserverinfo(ctx: commands.Context, guild_id: int=None):
    """顯示指定伺服器資訊
    
    用法： devserverinfo [伺服器ID]
    """
    guild = bot.get_guild(guild_id) if guild_id else ctx.guild
    if guild is None:
        await ctx.send("找不到該伺服器。")
        return

    embed = discord.Embed(title=f"{guild.name} 的資訊", color=0x00ff00)
    view = discord.ui.View()
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url if guild.icon else None)
        iconbutton = discord.ui.Button(label="伺服器圖標連結", url=guild.icon.url)
        view.add_item(iconbutton)
    if guild.banner:
        embed.set_image(url=guild.banner.url if guild.banner else None)
        bannerbutton = discord.ui.Button(label="伺服器橫幅連結", url=guild.banner.url)
        view.add_item(bannerbutton)
    embed.add_field(name="伺服器 ID", value=str(guild.id), inline=True)
    embed.add_field(name="創建時間", value=f"<t:{int(guild.created_at.timestamp())}:F>", inline=True)
    embed.add_field(name="擁有者", value=guild.owner.mention if guild.owner else "未知", inline=True)
    embed.add_field(name="加成", value=f"{guild.premium_subscription_count} (等級{guild.premium_tier})", inline=True)
    embed.add_field(
        name="驗證等級",
        value={
            "none": "無",
            "low": "低",
            "medium": "中等",
            "high": "高",
            "highest": "最高"
        }
        .get(
                guild.verification_level.name.lower(), "none"
            ),
        inline=True
    )
    embed.add_field(name="地區", value=str(guild.preferred_locale), inline=True)
    embed.add_field(name="成員數量", value=str(guild.member_count), inline=True)
    embed.add_field(name="頻道數量", value=str(len(guild.channels)), inline=True)
    embed.add_field(name="身分組數量", value=str(len(guild.roles)), inline=True)
    
    # database info
    server_config = db.get_all_server_config(guild.id)
    embed.add_field(name="資料庫設定項目數量", value=str(len(server_config)), inline=True)
    user_data = db.get_all_user_data(guild.id)
    embed.add_field(name="資料庫用戶資料數量", value=str(len(user_data)), inline=True)
    
    await ctx.send(embed=embed, view=view)


@bot.command(aliases=["rc"])
@is_owner()
async def reloadconfig(ctx):
    if reload_config():
        await ctx.send("配置已重新加載。")
    else:
        await ctx.send("重新加載配置時發生錯誤。")


@bot.command(aliases=["ou"])
@is_owner()
async def owner_userinfo(ctx, user: Union[discord.User, discord.Member] = None):
    """顯示用戶資訊，比起基本的可以顯示更多資訊。
    
    用法： owner_userinfo [用戶]
    如果不指定用戶，則顯示自己的資訊。
    """
    if user is None:
        user = ctx.author
    embed = discord.Embed(title=f"{user.display_name} 的資訊", color=0x00ff00)
    embed.set_thumbnail(url=user.avatar.url if user.avatar else None)
    # avatar url button
    button = discord.ui.Button(label="頭像連結", url=user.avatar.url if user.avatar else "https://discord.com/assets/6debd47ed13483642cf09e832ed0bc1b.png")
    view = discord.ui.View()
    view.add_item(button)
    embed.add_field(name="用戶 ID", value=str(user.id), inline=True)
    embed.add_field(name="帳號創建時間", value=user.created_at.strftime("%Y-%m-%d %H:%M:%S"), inline=True)
    if isinstance(user, discord.Member):
        embed.add_field(name="伺服器暱稱", value=user.nick or "無", inline=True)
        embed.add_field(name="加入伺服器時間", value=user.joined_at.strftime("%Y-%m-%d %H:%M:%S"), inline=True)
        # pfp
        if user.display_avatar and user.display_avatar.url != user.avatar.url:
            embed.set_image(url=user.display_avatar.url if user.display_avatar.url != user.avatar.url else None)
            button_serverpfp = discord.ui.Button(label="伺服器頭像連結", url=user.display_avatar.url)
            view.add_item(button_serverpfp)
    # get mutual guilds
    mutual_guilds = [g for g in bot.guilds if g.get_member(user.id)]
    embed.add_field(name="共同伺服器", value="\n".join(g.name for g in mutual_guilds) or "無", inline=False)
    await ctx.send(embed=embed, view=view)


@bot.command(aliases=["eval"])
@is_owner()
async def eval_command(ctx, *, code: str):
    """執行 Python 代碼，僅限機器人擁有者使用。
    
    用法： eval [代碼]
    例如： eval 1 + 1
    """
    try:
        # Create a local scope with commonly used variables
        local_scope = {
            "bot": bot,
            "ctx": ctx,
            "discord": discord,
            "get_user_data": get_user_data,
            "set_user_data": set_user_data,
            "get_server_config": get_server_config,
            "set_server_config": set_server_config,
            "config": config,
            "_config": _config,
            "default_config": default_config,
            "db": db,
            "asyncio": asyncio
        }
        result = eval(code, {"__builtins__": {}}, local_scope)
        if asyncio.iscoroutine(result):
            result = await result
        await ctx.send(f"結果：```{result}```")
    except Exception as e:
        await ctx.send(f"執行代碼時發生錯誤：```{e}```")


@bot.event
async def on_guild_join(guild: discord.Guild):
    # print(f"Joined guild: {guild.name} (ID: {guild.id})")
    log(f"加入了伺服器: {guild.name}，正在快取伺服器資料", module_name="OwnerTools", guild=guild)
    # try to chunk guild data
    try:
        await guild.chunk(cache=True)
        log(f"已快取伺服器資料: {guild.name}", module_name="OwnerTools", guild=guild)
    except Exception as e:
        log(f"無法快取伺服器資料: {e}", module_name="OwnerTools", guild=guild)
    # send to channel
    channel = bot.get_channel(config("join_leave_log_channel_id"))
    try:
        # build embed with server icon and member count
        embed = discord.Embed(
            title="已加入新伺服器",
            description=f"{guild.name} (ID: `{guild.id}`)",
            color=discord.Color.blurple()
        )
        embed.add_field(name="擁有者", value=f"{guild.owner} (ID: {guild.owner_id})", inline=True)
        embed.add_field(name="伺服器 ID", value=str(guild.id), inline=True)
        embed.add_field(name="頻道數", value=str(len(guild.channels)), inline=True)
        embed.add_field(name="成員數", value=str(getattr(guild, "member_count", "未知")), inline=True)
        embed.add_field(name="身分組數", value=str(len(guild.roles)), inline=True)
        embed.add_field(name="加成等級", value=f"等級 {guild.premium_tier} ({guild.premium_subscription_count} 個加成)", inline=True)
        embed.add_field(name="建立時間", value=guild.created_at.strftime("%Y-%m-%d %H:%M:%S"), inline=True)

        # try to get icon URL (works for discord.py v1.x and v2.x)
        icon_url = None
        if getattr(guild, "icon", None):
            try:
                icon_url = guild.icon.url  # v2.x
            except Exception:
                icon_url = getattr(guild, "icon_url", None)  # v1.x fallback
        if icon_url:
            embed.set_thumbnail(url=icon_url)

        await channel.send(embed=embed)
    except discord.Forbidden:
        log(f"無法在設定的頻道發送加入伺服器訊息", level=logging.ERROR, module_name="OwnerTools")
                

@bot.event
async def on_guild_remove(guild):
    log(f"離開了伺服器: {guild.name}", module_name="OwnerTools", guild=guild)
    # send to channel
    channel = bot.get_channel(config("join_leave_log_channel_id"))
    try:
        embed = discord.Embed(
            title="已離開伺服器",
            description=f"{guild.name} (ID: `{guild.id}`)",
            color=discord.Color.red()
        )
        icon_url = None
        if getattr(guild, "icon", None):
            try:
                icon_url = guild.icon.url  # v2.x
            except Exception:
                icon_url = getattr(guild, "icon_url", None)  # v1.x fallback
        if icon_url:
            embed.set_thumbnail(url=icon_url)
        await channel.send(embed=embed)
    except discord.Forbidden:
        log(f"無法在設定的頻道發送離開伺服器訊息", level=logging.ERROR, module_name="OwnerTools")


if __name__ == "__main__":
    start_bot()
