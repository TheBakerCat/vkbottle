import inspect
import re
import typing
from abc import abstractmethod
from typing import Awaitable, Callable, Coroutine, Dict, List, Optional, Tuple, Type, Union

import vbml
from vkbottle_types import BaseStateGroup

from vkbottle.tools.dev_tools.mini_types.user.message import MessageMin
from vkbottle.tools.validator import (
    ABCValidator,
    CallableValidator,
    EqualsValidator,
    IsInstanceValidator,
)

from .abc import ABCRule

DEFAULT_PREFIXES = ["!", "/"]
Message = MessageMin
PayloadMap = List[Tuple[str, Union[type, Callable[[typing.Any], bool], ABCValidator, typing.Any]]]
PayloadMapStrict = List[Tuple[str, ABCValidator]]
PayloadMapDict = Dict[str, Union[dict, type]]


class ABCMessageRule(ABCRule):
    @abstractmethod
    async def check(self, message: Message) -> Union[dict, bool]:
        pass


class PeerRule(ABCMessageRule):
    def __init__(self, from_chat: bool = True):
        self.from_chat = from_chat

    async def check(self, message: Message) -> bool:
        return self.from_chat is (message.peer_id != message.from_id)


class CommandRule(ABCMessageRule):
    def __init__(self, command_text: str, prefixes: Optional[List[str]] = None):
        self.prefixes = prefixes or DEFAULT_PREFIXES
        self.command_text = command_text

    async def check(self, message: Message) -> bool:
        for prefix in self.prefixes:
            if message.text == prefix + self.command_text:
                return True
        return False


class VBMLRule(ABCMessageRule):
    def __init__(
        self,
        pattern: Union[str, "vbml.Pattern", List[Union[str, "vbml.Pattern"]]],
        patcher: Optional["vbml.Patcher"] = None,
        flags: Optional[re.RegexFlag] = None,
    ):
        flags = flags or self.config.get("vbml_flags")

        if isinstance(pattern, str):
            pattern = [vbml.Pattern(pattern, flags=flags)]
        elif isinstance(pattern, vbml.Pattern):
            pattern = [pattern]
        elif isinstance(pattern, list):
            pattern = [
                p
                if isinstance(p, vbml.Pattern)
                else vbml.Pattern(p, flags=flags)
                for p in pattern
            ]

        self.patterns = pattern
        self.patcher = patcher or self.config["vbml_patcher"]

    async def check(self, message: Message) -> Union[dict, bool]:
        for pattern in self.patterns:
            result = self.patcher.check(pattern, message.text)
            if result not in (None, False):
                return result
        return False


class RegexRule(ABCMessageRule):
    def __init__(self, regexp: Union[str, List[str], typing.Pattern, List[typing.Pattern]]):
        if isinstance(regexp, typing.Pattern):
            regexp = [regexp]
        elif isinstance(regexp, str):
            regexp = [re.compile(regexp)]
        elif isinstance(regexp, list):
            regexp = [re.compile(exp) for exp in regexp]

        self.regexp = regexp

    async def check(self, message: Message) -> Union[dict, bool]:
        for regexp in self.regexp:
            match = re.match(regexp, message.text)
            if match:
                return {"match": match.groups()}
        return False


class StickerRule(ABCMessageRule):
    def __init__(self, sticker_ids: Union[List[int], int, bool]):
        if not isinstance(sticker_ids, list):
            sticker_ids = [sticker_ids]
        self.sticker_ids = sticker_ids

    async def check(self, message: Message) -> bool:
        if message.attachments and message.attachments[0].sticker:
            return not self.sticker_ids or bool(
                message.attachments[0].sticker.sticker_id in self.sticker_ids
                )
        return False


class FromPeerRule(ABCMessageRule):
    def __init__(self, peer_ids: Union[List[int], int]):
        if isinstance(peer_ids, int):
            peer_ids = [peer_ids]
        self.peer_ids = peer_ids

    async def check(self, message: Message) -> bool:
        return message.peer_id in self.peer_ids


class AttachmentTypeRule(ABCMessageRule):
    def __init__(self, attachment_types: Union[List[str], str]):
        if not isinstance(attachment_types, list):
            attachment_types = [attachment_types]
        self.attachment_types = attachment_types

    async def check(self, message: Message) -> bool:
        if not message.attachments:
            return False
        for attachment in message.attachments:
            if attachment.type.value not in self.attachment_types:
                return False
        return True


class ForwardMessagesRule(ABCMessageRule):
    async def check(self, message: Message) -> bool:
        return bool(message.fwd_messages)


class ReplyMessageRule(ABCMessageRule):
    async def check(self, message: Message) -> bool:
        return bool(message.reply_message)


class GeoRule(ABCMessageRule):
    async def check(self, message: Message) -> bool:
        return bool(message.geo)


class LevensteinRule(ABCMessageRule):
    def __init__(self, levenstein_texts: Union[List[str], str], max_distance: int = 1):
        if isinstance(levenstein_texts, str):
            levenstein_texts = [levenstein_texts]
        self.levenstein_texts = levenstein_texts
        self.max_distance = max_distance

    @staticmethod
    def distance(a: str, b: str) -> int:
        n, m = len(a), len(b)
        if n > m:
            a, b = b, a
            n, m = m, n

        current_row = range(n + 1)
        for i in range(1, m + 1):
            previous_row, current_row = current_row, [i] + [0] * n  # type: ignore
            for j in range(1, n + 1):
                add, delete, change = (
                    previous_row[j] + 1,
                    current_row[j - 1] + 1,
                    previous_row[j - 1],
                )
                if a[j - 1] != b[i - 1]:
                    change += 1
                current_row[j] = min(add, delete, change)  # type: ignore

        return current_row[n]

    async def check(self, message: Message) -> bool:
        for levenstein_text in self.levenstein_texts:
            if self.distance(message.text, levenstein_text) <= self.max_distance:
                return True
        return False


class MessageLengthRule(ABCMessageRule):
    def __init__(self, min_length: int):
        self.min_length = min_length

    async def check(self, message: Message) -> bool:
        return len(message.text) >= self.min_length


class ChatActionRule(ABCMessageRule):
    def __init__(self, chat_action_types: Union[List[str], str]):
        if isinstance(chat_action_types, str):
            chat_action_types = [chat_action_types]
        self.chat_action_types = chat_action_types

    async def check(self, message: Message) -> bool:
        return message.action and message.action.type.value in self.chat_action_types


class FromMeRule(ABCMessageRule):
    def __init__(self, user_id: int, from_me: bool = True):
        self.from_me = from_me
        self.user_id = user_id

    async def check(self, message: Message) -> bool:
        return (message.from_id == self.user_id) is self.from_me


class FromUserRule(ABCMessageRule):
    def __init__(self, from_user: bool = True):
        self.from_user = from_user

    async def check(self, message: Message) -> bool:
        return self.from_user is (message.from_id > 0)


class FuncRule(ABCMessageRule):
    def __init__(self, func: Union[Callable[[Message], Union[bool, Awaitable]]]):
        self.func = func

    async def check(self, message: Message) -> Union[dict, bool]:
        if inspect.iscoroutinefunction(self.func):
            return await self.func(message)  # type: ignore
        return self.func(message)  # type: ignore


class CoroutineRule(ABCMessageRule):
    def __init__(self, coroutine: Coroutine):
        self.coro = coroutine

    async def check(self, message: Message) -> Union[dict, bool]:
        return await self.coro


class StateRule(ABCMessageRule):
    def __init__(self, state: Union[List[BaseStateGroup], BaseStateGroup]):
        if not isinstance(state, list):
            state = [] if state is None else [state]
        self.state = state

    async def check(self, message: Message) -> bool:
        if message.state_peer is None:
            return not self.state
        return message.state_peer.state in self.state


class StateGroupRule(ABCMessageRule):
    def __init__(self, state_group: Union[List[Type[BaseStateGroup]], Type[BaseStateGroup]]):
        if not isinstance(state_group, list):
            state_group = [] if state_group is None else [state_group]
        self.state_group = state_group

    async def check(self, message: Message) -> bool:
        if message.state_peer is None:
            return not self.state_group
        return type(message.state_peer.state) in self.state_group


__all__ = (
    "ABCMessageRule",
    "PeerRule",
    "CommandRule",
    "VBMLRule",
    "StickerRule",
    "FromPeerRule",
    "AttachmentTypeRule",
    "LevensteinRule",
    "FromMeRule",
    "MessageLengthRule",
    "ChatActionRule",
    "FromUserRule",
    "FuncRule",
    "CoroutineRule",
    "StateRule",
    "StateGroupRule",
    "RegexRule",
)
