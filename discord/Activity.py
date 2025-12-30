from globalenv import bot, config, on_ready_tasks
from discord.app_commands.commands import GroupT, P, T, validate_name
from discord._types import ClientT
from discord.app_commands import CommandTree
from discord.app_commands.translator import Translator, TranslationContext, TranslationContextLocation, locale_str
from discord.enums import Locale
from typing import Generic, Dict, Any, Optional

class ActivityEntry(Generic[GroupT, P, T]):
    def __init__(self, name: str, description: str):
        name, locale = (name.message, name) if isinstance(name, locale_str) else (name, None)
        self.name: str = validate_name(name)
        self._locale_name: Optional[locale_str] = locale
        description, locale = (
            (description.message, description) if isinstance(description, locale_str) else (description, None)
        )
        self.description: str = description
        self._locale_description: Optional[locale_str] = locale
    
    async def get_translated_payload(self, tree: CommandTree[ClientT], translator: Translator) -> Dict[str, Any]:
        base = self.to_dict(tree)
        name_localizations: Dict[str, str] = {}
        description_localizations: Dict[str, str] = {}

        # Prevent creating these objects in a heavy loop
        name_context = TranslationContext(location=TranslationContextLocation.command_name, data=self)
        description_context = TranslationContext(location=TranslationContextLocation.command_description, data=self)

        for locale in Locale:
            if self._locale_name:
                translation = await translator._checked_translate(self._locale_name, locale, name_context)
                if translation is not None:
                    name_localizations[locale.value] = translation

            if self._locale_description:
                translation = await translator._checked_translate(self._locale_description, locale, description_context)
                if translation is not None:
                    description_localizations[locale.value] = translation

        base['name_localizations'] = name_localizations
        base['description_localizations'] = description_localizations
        return base
    
    def to_dict(self, tree: CommandTree[ClientT]) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "type": 4,
        }

activity_entry = ActivityEntry(name="launch", description="啟動活動")
