"""The persistent runtime orchestrator (conversation FSM + voice pipeline).

Pipeline (production shape, same code paths in dev):

    Microphone -> Wake Word -> Audio Capture -> STT -> Backend (Supervisor/LangGraph)
    -> Agent -> Agent Result -> Response Formatter -> Agent Voice Selection -> TTS
    -> Audio Playback

State machine:

    IDLE -> WAKE_DETECTED -> LISTENING -> PROCESSING -> RESPONDING
         -> ACTIVE_CONVERSATION -(no wake needed until timeout)-> LISTENING
         -> IDLE (on conversation timeout)

Only JARVIS owns the wake-word lifecycle. Interruption ("JARVIS, stop") stops TTS
immediately. Approved ToolRequests returned by the backend are re-validated locally
and executed here (the security boundary).
"""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Callable, Optional

from jarvis_shared.agents import AgentId
from jarvis_shared.audio import AudioInput
from jarvis_shared.conversation import ConversationState
from jarvis_shared.log import get_logger
from jarvis_shared.messages import AssistRequest, RuntimeStatus
from jarvis_shared.security import ToolRequest, ToolResult

from jarvis_local_agent.backend_client import BackendClient, BackendError
from jarvis_local_agent.config import RuntimeConfig
from jarvis_local_agent.notifications import Notifier
from jarvis_local_agent.security import LocalSecurityValidator
from jarvis_local_agent.stt import STTProvider
from jarvis_local_agent.tools import ToolExecutor
from jarvis_local_agent.tts import TTSProvider, select_voice
from jarvis_local_agent.wake import WakeWordEngine

log = get_logger(__name__)
_INTERRUPT_WORDS = ("stop", "cancel", "quiet", "enough")


def summarize_result(req: ToolRequest, result: ToolResult) -> str:
    """One short, honest sentence for the agent to speak about what actually happened."""
    if not result.ok:
        reason = (result.error or "it failed").rstrip(".")
        return f"That didn't work: {reason}."
    out = (result.output or "").strip()
    command = str(req.args.get("command", ""))
    if command.startswith("git status"):
        lines = [ln for ln in out.splitlines() if ln.strip() and ln.strip() != "(no output)"]
        return "The working tree is clean." if not lines else (
            f"There {'is' if len(lines) == 1 else 'are'} {len(lines)} changed "
            f"file{'' if len(lines) == 1 else 's'} in the repository.")
    first = out.splitlines()[0] if out else "Done"
    return (first[:160] + ("..." if len(first) > 160 else "")).rstrip(".") + "."


class JarvisRuntime:
    def __init__(
        self,
        config: RuntimeConfig,
        wake: WakeWordEngine,
        stt: STTProvider,
        tts: TTSProvider,
        backend: BackendClient,
        *,
        capture_utterance: Callable[[], str],
        notifier: Optional[Notifier] = None,
        on_state_change: Optional[Callable[[ConversationState], None]] = None,
        on_speaking: Optional[Callable[[str, str], None]] = None,
        on_speaking_end: Optional[Callable[[], None]] = None,
        on_heard: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.config = config
        self.wake = wake
        self.stt = stt
        self.tts = tts
        self.backend = backend
        self._capture = capture_utterance
        self.notifier = notifier or Notifier()
        self._on_state_change = on_state_change
        # UI hooks: let an overlay reflect who is speaking and what was heard.
        self._on_speaking = on_speaking
        self._on_speaking_end = on_speaking_end
        self._on_heard = on_heard

        self.validator = LocalSecurityValidator(config.allowed_paths, config.allowed_apps)
        self.executor = ToolExecutor(config.allowed_paths)

        self.session_id = str(uuid.uuid4())
        self.state = ConversationState.IDLE
        self._last_turn = 0.0
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._backend_online = False
        self._background: list[asyncio.Task] = []
        self._beat_tasks: set[asyncio.Future] = set()
        self._beat_pending = False

    # -- lifecycle --
    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._backend_online = await self.backend.ping()
        self.wake.start(on_wake=self._wake_fired_threadsafe)
        self._background = [
            asyncio.create_task(self._heartbeat_loop(), name="heartbeat"),
            asyncio.create_task(self._approval_loop(), name="approvals"),
        ]
        log.info("runtime started", extra={"backend_online": self._backend_online,
                                           "wake_engine": self.wake.name})
        self._speak_system("JARVIS online. Say 'Hey JARVIS' to begin.")

    def _wake_fired_threadsafe(self) -> None:
        # Called from the wake engine's thread; hop back to the asyncio loop.
        if self._loop and not self._loop.is_closed():
            asyncio.run_coroutine_threadsafe(self._on_wake(), self._loop)

    # -- state helpers --
    def _set_state(self, state: ConversationState) -> None:
        self.state = state
        if self._on_state_change:
            self._on_state_change(state)
        self._request_heartbeat()  # let the dashboard see mic/state changes promptly

    def status(self) -> RuntimeStatus:
        return RuntimeStatus(
            online=True,
            microphone_paused=self.wake.paused,
            conversation_state=self.state.value,
            wake_word=self.config.wake_phrase,
            backend_connected=self._backend_online,
            detail=f"engine={self.wake.name}",
        )

    # -- wake + turn handling --
    async def _on_wake(self) -> None:
        if self.wake.paused:
            return
        self._set_state(ConversationState.WAKE_DETECTED)
        # Immediate low-latency acknowledgement in JARVIS's voice.
        await self._speak_as(AgentId.JARVIS, "Yes. I'm listening.")
        await self._listen_and_handle(woke_from_wake_word=True)

    async def _listen_and_handle(self, woke_from_wake_word: bool) -> None:
        self._set_state(ConversationState.LISTENING)
        # Capture blocks on the mic; keep it off the event loop. The wake engine stays
        # suspended so the phrase we just heard can't retrigger mid-capture.
        self._suspend_wake(True)
        try:
            utterance = (await asyncio.to_thread(self._capture) or "").strip()
        finally:
            self._suspend_wake(False)

        if self._on_heard:
            self._on_heard(utterance)
        if not utterance:
            self._set_state(ConversationState.IDLE)
            return

        # Local interruption handling: "JARVIS, stop" never needs the backend.
        if self._is_interrupt(utterance):
            self.tts.stop()
            await self._speak_as(AgentId.JARVIS, "Stopped.")
            self._set_state(ConversationState.IDLE)
            return

        await self._process(utterance, woke_from_wake_word)
        await self._maybe_continue_conversation()

    def _is_interrupt(self, utterance: str) -> bool:
        low = utterance.lower()
        return "jarvis" in low and any(w in low for w in _INTERRUPT_WORDS)

    async def _process(self, utterance: str, woke_from_wake_word: bool) -> None:
        self._set_state(ConversationState.PROCESSING)

        # STT seam: in dev the capturer already returned text; we still run it through
        # the STTProvider so the pipeline shape matches production exactly.
        transcript = await self.stt.transcribe(
            AudioInput(wav_path=utterance, duration_ms=len(utterance) * 60)
        )
        text = transcript.text or utterance

        if not self._backend_online:
            self._backend_online = await self.backend.ping()
        if not self._backend_online:
            await self._speak_as(AgentId.JARVIS,
                                 "I can't reach the orchestration backend right now.")
            return

        req = AssistRequest(
            session_id=self.session_id,
            utterance=text,
            woke_from_wake_word=woke_from_wake_word,
            client_context={
                "allowed_paths": self.config.allowed_paths,
                "allowed_apps": self.config.allowed_apps,
            },
        )
        resp = await self.backend.assist(req)

        # Optional delegation note spoken by JARVIS before the specialist answers.
        if resp.delegation_note and resp.responding_agent != AgentId.JARVIS:
            await self._speak_as(AgentId.JARVIS, resp.delegation_note)

        self._set_state(ConversationState.RESPONDING)
        await self._speak_as(resp.responding_agent, resp.speech)

        if resp.pending_approval_id:
            self.notifier.notify(
                "JARVIS",
                f"{resp.responding_agent.value} requested an action that needs approval.",
                action="Review",
            )
        # Tool requests the backend allowed WITHOUT a human grant (SAFE ones). We still
        # validate each locally; anything the local gate holds is simply not run.
        for tool, result in await self._run_local_tools(resp.tool_requests, approved=False):
            await self._speak_as(resp.responding_agent, summarize_result(tool, result))

    async def _run_local_tools(self, requests: list[ToolRequest], *, approved: bool
                               ) -> list[tuple[ToolRequest, ToolResult]]:
        """Validate then execute. ``approved`` is True only for work fetched from a granted
        approval; it satisfies "needs approval" but never overrides a hard block."""
        outcomes: list[tuple[ToolRequest, ToolResult]] = []
        for tool in requests:
            verdict = self.validator.validate(tool)
            if not verdict.allows(approved):
                log.warning("local validator refused request", extra={
                    "category": tool.category, "action": tool.action, "reason": verdict.reason,
                    "hard_block": not verdict.ok})
                outcomes.append((tool, ToolResult(
                    request_id=tool.request_id, ok=False, blocked=not verdict.ok,
                    needs_approval=verdict.needs_approval, risk_level=verdict.risk,
                    error=verdict.reason)))
                continue
            result = await asyncio.to_thread(self.executor.execute, tool, verdict)
            log.info("tool executed", extra={"category": tool.category, "action": tool.action,
                                             "ok": result.ok, "approved": approved})
            outcomes.append((tool, result))
        return outcomes

    # -- background loops --
    async def _approval_loop(self) -> None:
        """Pick up work a human has approved, run it, and report what happened."""
        while True:
            try:
                await asyncio.sleep(self.config.approval_poll_seconds)
                if not self.backend.has_credentials:
                    continue
                for item in await self.backend.granted_approvals():
                    await self._execute_approved(item)
            except asyncio.CancelledError:
                raise
            except (BackendError, Exception) as exc:  # keep the loop alive through outages
                log.warning("approval poll failed", extra={"error": type(exc).__name__})

    async def _execute_approved(self, item: dict) -> None:
        approval_id = item["id"]
        if not await self.backend.claim(approval_id):  # someone else got it, or it expired
            return
        try:
            tool = ToolRequest.model_validate(item["request"])
        except Exception:
            await self.backend.report(approval_id, ok=False, output="", error="Malformed request.")
            return
        (tool, result), = await self._run_local_tools([tool], approved=True)
        await self.backend.report(approval_id, ok=result.ok, output=result.output, error=result.error)
        sentence = summarize_result(tool, result)
        agent = AgentId(item["agent_id"]) if item.get("agent_id") in AgentId.__members__ else AgentId.JARVIS
        if self.state == ConversationState.IDLE:
            await self._speak_as(agent, f"Approved action finished. {sentence}")
        else:
            self.notifier.notify("JARVIS", f"{agent.value}: {sentence}")

    async def _heartbeat_loop(self) -> None:
        while True:
            await self._beat()
            await asyncio.sleep(self.config.heartbeat_seconds)

    async def _beat(self) -> None:
        if not self.backend.has_credentials:
            return
        try:
            await self.backend.heartbeat(state=self.state.value, mic_paused=self.wake.paused,
                                         wake_word=self.config.wake_phrase)
            self._backend_online = True
        except Exception as exc:
            self._backend_online = False
            log.debug("heartbeat failed", extra={"error": type(exc).__name__})

    def _request_heartbeat(self) -> None:
        """Called on every state change, possibly from another thread: coalesce into one beat."""
        loop = self._loop
        if loop is None or loop.is_closed() or self._beat_pending:
            return
        self._beat_pending = True

        def schedule() -> None:
            async def once() -> None:
                self._beat_pending = False
                await self._beat()
            # Keep a strong reference: the loop only holds weak refs, so an unreferenced task
            # can be garbage-collected mid-flight and `_beat_pending` would never reset.
            task = asyncio.ensure_future(once())
            self._beat_tasks.add(task)
            task.add_done_callback(self._beat_tasks.discard)

        loop.call_soon_threadsafe(schedule)

    async def _maybe_continue_conversation(self) -> None:
        """Stay in conversation: follow-ups need no wake word until the timeout.

        Each successful turn refreshes the deadline. Silence (an empty capture) or a
        barge-in ends the conversation and returns the FSM to IDLE, at which point the
        wake word is required again.
        """
        self._set_state(ConversationState.ACTIVE_CONVERSATION)
        self._last_turn = time.time()
        deadline = time.time() + self.config.conversation_timeout_s

        while time.time() < deadline:
            self._set_state(ConversationState.LISTENING)
            self._suspend_wake(True)
            try:
                follow = (await asyncio.to_thread(self._capture) or "").strip()
            finally:
                self._suspend_wake(False)

            if not follow:
                break  # trailed off -> leave conversation mode
            if self._on_heard:
                self._on_heard(follow)
            if self._is_interrupt(follow):
                self.tts.stop()
                await self._speak_as(AgentId.JARVIS, "Stopped.")
                break

            await self._process(follow, woke_from_wake_word=False)
            self._last_turn = time.time()
            deadline = time.time() + self.config.conversation_timeout_s

        self._set_state(ConversationState.IDLE)

    # -- speaking --
    async def _speak_as(self, agent_id: AgentId, text: str) -> None:
        if not text:
            return
        profile = await self.backend.voice_profile(agent_id) if self._backend_online \
            else None
        voice_id = profile.voice_id if profile else f"{agent_id.value.lower()}_default"
        settings = select_voice(profile)[1] if profile else None
        from jarvis_shared.voice import VoiceSettings
        result = await self.tts.synthesize(text, voice_id, settings or VoiceSettings())

        if self._on_speaking:
            self._on_speaking(agent_id.value, text)
        # Deafen the wake engine while we talk so JARVIS never wakes itself.
        self._suspend_wake(True)
        try:
            # Run blocking playback off the event loop so the UI stays responsive and
            # a barge-in ("JARVIS, stop") can still cut in.
            await asyncio.to_thread(self.tts.speak, result)
        finally:
            self._suspend_wake(False)
            if self._on_speaking_end:
                self._on_speaking_end()

    def _suspend_wake(self, flag: bool) -> None:
        """Engines that support it stop spotting while we capture or speak."""
        if hasattr(self.wake, "suspended"):
            self.wake.suspended = flag

    def _speak_system(self, text: str) -> None:
        from jarvis_shared.audio import AudioResult
        self.tts.speak(AudioResult(voice_id="jarvis_default", spoken_text=text))

    # -- microphone controls (surfaced in tray) --
    def pause_microphone(self) -> None:
        self.wake.pause()
        self._set_state(ConversationState.PAUSED)
        self.notifier.notify("JARVIS", "Microphone paused.")

    def resume_microphone(self) -> None:
        self.wake.resume()
        self._set_state(ConversationState.IDLE)
        self.notifier.notify("JARVIS", "Microphone resumed. Listening for 'Hey JARVIS'.")

    async def shutdown(self) -> None:
        for task in self._background:
            task.cancel()
        await asyncio.gather(*self._background, return_exceptions=True)
        self.wake.stop()
        self.tts.stop()
        await self.backend.close()
        log.info("runtime stopped")
