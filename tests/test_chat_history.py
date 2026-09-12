"""Tests para src/chat_history.py

Naming: [MethodName]_[Scenario/State]_[ExpectedResult]
"""
from __future__ import annotations

import time

import pytest

from src.chat_history import ChatHistory, HistoryEntry
from tests.conftest import make_message


class TestChatHistoryAdd:

    def test_add_WhenIgnoredAuthor_ReturnsFalse(self, history: ChatHistory):
        msg = make_message(text="hola", author="nightbot")
        assert history.add(msg) is False

    def test_add_WhenIgnoredAuthorDifferentCase_ReturnsFalse(self, history: ChatHistory):
        msg = make_message(text="hola", author="NIGHTBOT")
        assert history.add(msg) is False

    def test_add_WhenEmptyText_ReturnsFalse(self, history: ChatHistory):
        msg = make_message(text="   ")
        assert history.add(msg) is False

    def test_add_WithValidMessage_ReturnsTrueAndPersists(self, history: ChatHistory):
        msg = make_message(text="que buena jugada")
        added = history.add(msg)
        assert added is True
        recent = history.since(60)
        assert len(recent) == 1
        assert recent[0].text == "que buena jugada"

    def test_add_NormalizesWhitespace(self, history: ChatHistory):
        """Espacios múltiples se colapsan en uno."""
        msg = make_message(text="hola   mundo")
        history.add(msg)
        assert history.since(60)[0].text == "hola mundo"

    def test_add_WhenMaxMessagesReached_DroppsOldest(self):
        """La deque descarta el mensaje más viejo al superar maxlen."""
        h = ChatHistory(max_messages=3)
        for i in range(5):
            h.add(make_message(text=f"msg{i}", author=f"user{i}", message_id=str(i)))
        recent = h.since(3600)
        texts = [e.text for e in recent]
        assert "msg0" not in texts
        assert "msg1" not in texts
        assert "msg4" in texts


class TestChatHistorySince:

    def test_since_WithOldMessages_FiltersThemOut(self, history: ChatHistory):
        history.add(make_message(text="viejo", message_id="1"))
        # Inyectamos manualmente una entrada con timestamp pasado
        old = HistoryEntry(
            author_id="99",
            display_name="OldUser",
            text="mensaje viejo",
            created_at=time.time() - 7200,  # hace 2 horas
        )
        history._messages.append(old)
        # Pedimos solo la última hora
        recent = history.since(3600)
        texts = [e.text for e in recent]
        assert "mensaje viejo" not in texts
        assert "viejo" in texts

    def test_since_WithNoMessages_ReturnsEmpty(self, history: ChatHistory):
        assert history.since(3600) == []

    def test_since_WithZeroSeconds_ReturnsEmpty(self, history: ChatHistory):
        """Ventana de 0s no debería devolver mensajes recientes (cutoff = now)."""
        history.add(make_message(text="ahora mismo"))
        result = history.since(0)
        # created_at >= time.time() es casi imposible que sea True
        assert len(result) == 0


class TestChatHistoryTopicWords:

    def test_topicWords_WithMessages_ExcludesStopwords(self, history: ChatHistory):
        for word in ["para", "pero", "como"]:
            history.add(make_message(text=f"{word} siempre", message_id=word))
        topics = history.topic_words(history.since(3600))
        for stopword in ["para", "pero", "como"]:
            assert stopword not in topics

    def test_topicWords_WithRepeatedWords_ReturnsMostCommon(self, history: ChatHistory):
        for i in range(5):
            history.add(make_message(text="valorant ganamos", message_id=f"v{i}"))
        history.add(make_message(text="league perdimos", message_id="l1"))
        topics = history.topic_words(history.since(3600), limit=1)
        assert "valorant" in topics or "ganamos" in topics

    def test_topicWords_WithEmptyList_ReturnsEmpty(self, history: ChatHistory):
        assert history.topic_words([]) == []

    def test_topicWords_WithShortWords_IgnoresWordsShorterThanFour(self, history: ChatHistory):
        """Regex exige 4+ caracteres."""
        history.add(make_message(text="ok si no ok"))
        topics = history.topic_words(history.since(3600))
        assert topics == []


class TestChatHistoryParticipants:

    def test_participants_WithMultipleAuthors_ReturnsSortedByCount(self, history: ChatHistory):
        for i in range(3):
            history.add(make_message(text=f"msg{i}", author="heavy", display_name="Heavy", message_id=f"h{i}"))
        history.add(make_message(text="uno", author="light", display_name="Light", message_id="l1"))

        participants = history.participants(history.since(3600))
        names = [name for name, _ in participants]
        assert names[0] == "Heavy"
        assert names[1] == "Light"

    def test_participants_WithEmptyList_ReturnsEmpty(self, history: ChatHistory):
        assert history.participants([]) == []

    def test_participants_LimitsResults(self, history: ChatHistory):
        for i in range(10):
            history.add(make_message(text="x", author=f"user{i}", display_name=f"User{i}", message_id=f"m{i}"))
        participants = history.participants(history.since(3600), limit=3)
        assert len(participants) <= 3
