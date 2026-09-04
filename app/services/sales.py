"""Persistent, channel-independent orchestration for sales conversations."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Callable

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.agents import SalesAgent
from app.db.models import Conversation, Lead, Message
from app.repositories import (
    ConversationRepository,
    LeadRepository,
    MessageRepository,
)
from app.schemas import (
    ConversationSession,
    MessageRole,
    SalesAgentOutput,
)
from app.services.llm import LLMService, OpenAIResponsesService
from app.services.prompts import PromptLoader


class ConversationNotFoundError(LookupError):
    """Raised when a requested persisted conversation does not exist."""


class PersistenceStateError(RuntimeError):
    """Raised when relational history and the validated snapshot disagree."""


@dataclass(frozen=True)
class ConversationView:
    conversation: Conversation
    lead: Lead
    messages: tuple[Message, ...]
    session: ConversationSession


@dataclass(frozen=True)
class SalesTurnResult:
    output: SalesAgentOutput
    view: ConversationView


class SalesService:
    """Use the same Sales Agent while owning all persistence concerns."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session],
        agent: SalesAgent,
        prompt_loader: PromptLoader | None = None,
        lead_repository: LeadRepository | None = None,
        conversation_repository: ConversationRepository | None = None,
        message_repository: MessageRepository | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._agent = agent
        self._prompt_loader = prompt_loader or PromptLoader()
        self._leads = lead_repository or LeadRepository()
        self._conversations = conversation_repository or ConversationRepository()
        self._messages = message_repository or MessageRepository()

    def start_conversation(
        self,
        *,
        name: str,
        email: str | None = None,
        phone_number: str | None = None,
        prompt_version: str = "sales_v1",
        channel: str = "streamlit",
        external_conversation_id: str | None = None,
    ) -> ConversationView:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("name is required")
        self._prompt_loader.load(prompt_version)
        session = ConversationSession(prompt_version=prompt_version)
        with self._session_factory.begin() as db:
            lead = self._leads.create(
                db,
                name=clean_name,
                email=self._clean_optional(email),
                phone_number=self._clean_optional(phone_number),
                channel=channel,
            )
            conversation = self._conversations.create(
                db,
                lead_id=lead.id,
                channel=channel,
                prompt_version=prompt_version,
                session=session,
                external_conversation_id=external_conversation_id,
            )
            conversation_id = conversation.id
        return self.get_conversation(conversation_id)

    def handle_message(
        self, conversation_id: uuid.UUID | str, content: str
    ) -> SalesTurnResult:
        clean_content = content.strip()
        if not clean_content:
            raise ValueError("message content is required")
        resolved_id = self._as_uuid(conversation_id)
        view = self.get_conversation(resolved_id)
        previous_stage = view.session.stage
        previous_snapshot = view.session.model_dump(mode="json")
        previous_message_count = len(previous_snapshot["messages"])

        output = self._agent.handle_message(view.session, clean_content)

        with self._session_factory.begin() as db:
            conversation = self._conversations.get(db, resolved_id)
            if conversation is None:
                raise ConversationNotFoundError(str(resolved_id))
            lead = self._leads.get(db, conversation.lead_id)
            if lead is None:
                raise PersistenceStateError("conversation references a missing lead")

            current_snapshot = ConversationSession.model_validate(
                conversation.session_snapshot
            )
            if current_snapshot.model_dump(mode="json") != previous_snapshot:
                raise PersistenceStateError(
                    "conversation changed while the sales turn was generated"
                )

            inbound, outbound = view.session.messages[-2:]
            self._messages.add(
                db,
                conversation_id=resolved_id,
                position=previous_message_count,
                role=MessageRole.USER,
                content=inbound.content,
                stage=previous_stage,
                channel=conversation.channel,
            )
            self._messages.add(
                db,
                conversation_id=resolved_id,
                position=previous_message_count + 1,
                role=MessageRole.ASSISTANT,
                content=outbound.content,
                stage=output.stage,
                channel=conversation.channel,
                delivery_status="generated",
            )
            self._conversations.save_session(conversation, view.session)
            self._leads.update_qualification(
                lead,
                service_interest=output.service.value if output.service else None,
                qualification_status=output.fit.value,
                lead_score=output.qualification_score,
            )

        return SalesTurnResult(
            output=output, view=self.get_conversation(resolved_id)
        )

    def get_conversation(
        self, conversation_id: uuid.UUID | str
    ) -> ConversationView:
        resolved_id = self._as_uuid(conversation_id)
        with self._session_factory() as db:
            conversation = self._conversations.get(db, resolved_id)
            if conversation is None:
                raise ConversationNotFoundError(str(resolved_id))
            lead = self._leads.get(db, conversation.lead_id)
            if lead is None:
                raise PersistenceStateError("conversation references a missing lead")
            messages = self._messages.list_for_conversation(db, resolved_id)
            try:
                session = ConversationSession.model_validate(
                    conversation.session_snapshot
                )
            except ValidationError as exc:
                raise PersistenceStateError(
                    "conversation snapshot is invalid"
                ) from exc
            if conversation.prompt_version != session.prompt_version:
                raise PersistenceStateError("stored prompt versions disagree")
            if conversation.current_stage != session.stage.value:
                raise PersistenceStateError("stored conversation stages disagree")
            if len(messages) != len(session.messages):
                raise PersistenceStateError(
                    "message history and session snapshot disagree"
                )
            db.expunge(conversation)
            db.expunge(lead)
            for message in messages:
                db.expunge(message)
        return ConversationView(
            conversation=conversation,
            lead=lead,
            messages=tuple(messages),
            session=session,
        )

    def list_leads(self) -> list[Lead]:
        with self._session_factory() as db:
            records = self._leads.list(db)
            for record in records:
                db.expunge(record)
            return records

    def list_conversations(self) -> list[Conversation]:
        with self._session_factory() as db:
            records = self._conversations.list(db)
            for record in records:
                db.expunge(record)
            return records

    @staticmethod
    def _clean_optional(value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @staticmethod
    def _as_uuid(value: uuid.UUID | str) -> uuid.UUID:
        if isinstance(value, uuid.UUID):
            return value
        try:
            return uuid.UUID(value)
        except (ValueError, AttributeError) as exc:
            raise ConversationNotFoundError(str(value)) from exc


def build_sales_service(
    session_factory: Callable[[], Session],
    *,
    llm: LLMService | None = None,
) -> SalesService:
    """Build the production service while allowing deterministic test clients."""

    resolved_llm = llm or OpenAIResponsesService()
    return SalesService(
        session_factory=session_factory,
        agent=SalesAgent(resolved_llm),
    )
