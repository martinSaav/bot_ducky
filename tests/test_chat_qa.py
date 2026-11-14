"""Tests for the Q&A over past streams (RAG): what gets indexed, how it is
retrieved and how !pregunta answers.

At ~150 messages per stream a single stream fits in the prompt, so retrieval
is only used across streams: stream summaries plus the messages that carry
content (no commands, no one-word reactions).

Naming: [MethodName]_[Scenario/State]_[ExpectedResult]
The answers checked here are in Spanish because viewers read them.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import make_message


def _pool(conn: MagicMock) -> MagicMock:
    pool = MagicMock()
    pool.acquire.return_value.__aenter__.return_value = conn
    return pool


def _worker(conn: MagicMock):
    from src.chat_embeddings import EmbeddingWorker

    worker = EmbeddingWorker(_pool(conn), MagicMock(), "key", "model", "http://llm")
    worker._embed_single = AsyncMock(return_value=[0.5, 0.25])
    return worker


# ===========================================================================
# What gets indexed
# ===========================================================================

class TestShouldEmbed:

    @pytest.mark.parametrize("text", ["!lrank", "!pregunta que paso", "jajaja", "gg wp", "KEKW KEKW KEKW"])
    def test_shouldEmbed_WhenCommandOrShortReaction_ReturnsFalse(self, text):
        from src.chat_embeddings import should_embed
        assert should_embed(text) is False

    def test_shouldEmbed_WhenMessageHasContent_ReturnsTrue(self):
        from src.chat_embeddings import should_embed
        assert should_embed("que buena jugada con jett en la b") is True


class TestHistoryIndexing:

    async def test_run_WhenCommandArrives_DoesNotEmbedIt(self):
        from src.chat_history_db import PostgresChatHistory

        worker = MagicMock()
        history = PostgresChatHistory("postgresql://unused", embedding_worker=worker)
        history._insert = AsyncMock(side_effect=[1, 2])
        history.enqueue(make_message(text="!lrank", message_id="m1"))
        history.enqueue(make_message(text="que buena jugada con jett en la b", message_id="m2"))
        history._queue.put_nowait(None)

        await asyncio.wait_for(history._run(), timeout=1)

        worker.enqueue.assert_called_once_with(2, "que buena jugada con jett en la b")

    async def test_finishSession_WhenSummaryExists_EmbedsIt(self):
        from src.chat_history_db import PostgresChatHistory

        worker = MagicMock()
        worker.embed_session = AsyncMock()
        history = PostgresChatHistory("postgresql://unused", embedding_worker=worker)
        history._pool = _pool(MagicMock(execute=AsyncMock()))

        await history.finish_session("s1", datetime.now(timezone.utc), "VALORANT", 120, "- Juego: VALORANT")

        worker.embed_session.assert_awaited_once_with("s1", "- Juego: VALORANT")

    async def test_finishSession_WhenNoSummary_DoesNotEmbed(self):
        from src.chat_history_db import PostgresChatHistory

        worker = MagicMock()
        worker.embed_session = AsyncMock()
        history = PostgresChatHistory("postgresql://unused", embedding_worker=worker)
        history._pool = _pool(MagicMock(execute=AsyncMock()))

        await history.finish_session("s1", datetime.now(timezone.utc), "VALORANT", 120, None)

        worker.embed_session.assert_not_called()

    async def test_setSessionSummary_WhenRetriedSummaryArrives_EmbedsIt(self):
        from src.chat_history_db import PostgresChatHistory

        worker = MagicMock()
        worker.embed_session = AsyncMock()
        history = PostgresChatHistory("postgresql://unused", embedding_worker=worker)
        history._pool = _pool(MagicMock(execute=AsyncMock()))

        await history.set_session_summary("s1", "- Juego: LoL")

        worker.embed_session.assert_awaited_once_with("s1", "- Juego: LoL")


class TestSessionEmbeddings:

    async def test_embedSession_StoresTheSummaryVector(self):
        conn = MagicMock(execute=AsyncMock())
        worker = _worker(conn)

        await worker.embed_session("s1", "- Juego: VALORANT")

        sql, *params = conn.execute.await_args.args
        assert "session_embeddings" in sql
        assert params == ["s1", "[0.5,0.25]"]

    async def test_backfillSessions_EmbedsSummariesThatHaveNone(self):
        conn = MagicMock()
        conn.fetch = AsyncMock(return_value=[
            {"twitch_stream_id": "s1", "summary": "uno"},
            {"twitch_stream_id": "s2", "summary": "dos"},
        ])
        worker = _worker(conn)
        worker.embed_session = AsyncMock()

        count = await worker.backfill_sessions()

        assert count == 2
        assert [call.args for call in worker.embed_session.await_args_list] == [("s1", "uno"), ("s2", "dos")]

    async def test_pruneNoise_DeletesEmbeddingsOfCommandsAndShortMessages(self):
        conn = MagicMock(execute=AsyncMock(return_value="DELETE 4000"))
        worker = _worker(conn)

        await worker.prune_noise()

        sql = conn.execute.await_args.args[0]
        assert "DELETE FROM chat_embeddings" in sql


# ===========================================================================
# Retrieval
# ===========================================================================

class TestSearchHistory:

    async def test_searchHistory_ReturnsSummariesAndMessagesWithDates(self):
        conn = MagicMock()
        conn.fetch = AsyncMock(side_effect=[
            [{"started_at": datetime(2026, 11, 4, 23, 0, tzinfo=timezone.utc),
              "game_name": "VALORANT", "summary": "- Juego: VALORANT"}],
            [{"created_at": datetime(2026, 11, 4, 23, 30, tzinfo=timezone.utc),
              "author_login": "frannncololz", "content": "la skin nueva del vandal es horrible"},
             {"created_at": datetime(2026, 11, 2, 22, 0, tzinfo=timezone.utc),
              "author_login": "abuel077", "content": "quiero la skin nueva del vandal"}],
        ])
        worker = _worker(conn)

        result = await worker.search_history("que dijeron de la skin nueva")

        assert result["sessions"] == [
            {"date": "2026-11-04", "game": "VALORANT", "summary": "- Juego: VALORANT"},
        ]
        # Chronological, so the LLM reads the thread in order.
        assert [m["date"] for m in result["messages"]] == ["2026-11-02", "2026-11-04"]
        assert result["messages"][0] == {
            "date": "2026-11-02", "author": "abuel077", "text": "quiero la skin nueva del vandal",
        }
        # The query vector goes to pgvector as a text literal.
        assert all(call.args[1] == "[0.5,0.25]" for call in conn.fetch.await_args_list)

    async def test_searchHistory_WhenEmbeddingFails_ReturnsNothing(self):
        conn = MagicMock(fetch=AsyncMock())
        worker = _worker(conn)
        worker._embed_single = AsyncMock(return_value=None)

        assert await worker.search_history("algo") == {"sessions": [], "messages": []}
        conn.fetch.assert_not_called()


# ===========================================================================
# Answering
# ===========================================================================

def _memory(sessions=None, messages=None) -> MagicMock:
    memory = MagicMock()
    memory.search_history = AsyncMock(return_value={"sessions": sessions or [], "messages": messages or []})
    return memory


class TestChatQA:

    async def test_answer_WhenContextFound_AsksTheLlmWithDatedContext(self):
        from src.chat_qa import ChatQA

        llm = MagicMock()
        llm.complete = AsyncMock(return_value="El 4/11 chat dijo que la skin nueva del Vandal era horrible.")
        memory = _memory(
            sessions=[{"date": "2026-11-04", "game": "VALORANT", "summary": "- Juego: VALORANT"}],
            messages=[{"date": "2026-11-04", "author": "frannncololz", "text": "la skin nueva del vandal es horrible"}],
        )

        answer = await ChatQA(memory, llm).answer("que dijeron de la skin nueva")

        assert answer == "El 4/11 chat dijo que la skin nueva del Vandal era horrible."
        system, user = llm.complete.await_args.args
        assert "2026-11-04" in user and "frannncololz" in user and "que dijeron de la skin nueva" in user
        assert "solo" in system.lower()  # answer only from the given context

    async def test_answer_WhenNothingFound_SaysSoWithoutCallingTheLlm(self):
        from src.chat_qa import ChatQA

        llm = MagicMock(complete=AsyncMock())

        answer = await ChatQA(_memory(), llm).answer("cuando jugo minecraft")

        assert "no encontr" in answer.lower()
        llm.complete.assert_not_called()

    async def test_answer_WhenLlmFails_ReturnsAFriendlyMessage(self):
        from src.chat_qa import ChatQA

        llm = MagicMock(complete=AsyncMock(return_value=None))
        memory = _memory(messages=[{"date": "2026-11-04", "author": "x", "text": "algo que paso en el stream"}])

        answer = await ChatQA(memory, llm).answer("que paso")

        assert "no pude" in answer.lower()

    async def test_answer_WhenLlmIsLong_FitsInOneChatMessage(self):
        from src.chat_qa import ChatQA

        llm = MagicMock(complete=AsyncMock(return_value="palabra " * 200))
        memory = _memory(messages=[{"date": "2026-11-04", "author": "x", "text": "algo que paso en el stream"}])

        answer = await ChatQA(memory, llm).answer("que paso")

        assert len(answer) <= 400
        assert "\n" not in answer


class TestLlmComplete:

    async def test_complete_SendsSystemAndUserAndReturnsTheText(self):
        from src.llm_summary import LlmSummary

        response = MagicMock(status=200)
        response.json = AsyncMock(return_value={"choices": [{"message": {"content": "  respuesta  "}}]})
        session = MagicMock()
        session.post.return_value.__aenter__.return_value = response
        llm = LlmSummary(session, "key", "gemini", "http://llm", 300)

        text = await llm.complete("sistema", "usuario")

        assert text == "respuesta"
        payload = session.post.call_args.kwargs["json"]
        assert payload["messages"] == [
            {"role": "system", "content": "sistema"},
            {"role": "user", "content": "usuario"},
        ]


# ===========================================================================
# !pregunta
# ===========================================================================

@pytest.fixture()
def registry(mock_services):
    from src.commands.registry import Registry

    with patch("src.commands.registry.cfg") as mock_cfg:
        mock_cfg.twitch_channel = "chaarqueen"
        return Registry(svc=mock_services)


class TestPreguntaCommand:

    async def test_pregunta_WhenAsked_RepliesWithTheAnswer(self, registry, mock_services):
        mock_services.qa = MagicMock(answer=AsyncMock(return_value="Jugó LoL el 2/11."))

        await registry.dispatch(make_message(text="!pregunta cuando jugo lol"))

        mock_services.qa.answer.assert_awaited_once_with("cuando jugo lol")
        assert mock_services.chat.say.await_args.args[0] == "Jugó LoL el 2/11."

    async def test_pregunta_WhenNoQuestion_ShowsUsage(self, registry, mock_services):
        mock_services.qa = MagicMock(answer=AsyncMock())

        await registry.dispatch(make_message(text="!pregunta"))

        mock_services.qa.answer.assert_not_called()
        assert "uso" in mock_services.chat.say.await_args.args[0].lower()

    async def test_pregunta_WhenMemoryNotActive_SaysSo(self, registry, mock_services):
        mock_services.qa = None

        await registry.dispatch(make_message(text="!pregunta cuando jugo lol"))

        assert "no est" in mock_services.chat.say.await_args.args[0].lower()
