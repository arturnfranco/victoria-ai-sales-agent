"""VictorIA internal Playground and persistence console."""

from __future__ import annotations

import logging

import streamlit as st

from app.db.session import create_session_factory
from app.services.sales import (
    ConversationNotFoundError,
    PersistenceStateError,
    SalesService,
    build_sales_service,
)


logger = logging.getLogger(__name__)


st.set_page_config(page_title="VictorIA", page_icon="💬", layout="wide")


@st.cache_resource
def get_service() -> SalesService:
    return build_sales_service(create_session_factory())


def render_table(rows: list[dict[str, object]]) -> None:
    """Render small portfolio-scale tables without a Pandas dependency."""

    headers = list(rows[0])

    def cell(value: object) -> str:
        if value is None:
            return "—"
        if hasattr(value, "strftime"):
            return value.strftime("%d/%m/%Y %H:%M")
        return str(value).replace("|", "\\|").replace("\n", " ")

    lines = ["| " + " | ".join(headers) + " |"]
    lines.append("| " + " | ".join("---" for _ in headers) + " |")
    lines.extend(
        "| " + " | ".join(cell(row[key]) for key in headers) + " |" for row in rows
    )
    st.markdown("\n".join(lines))


def render_state(view) -> None:
    output = view.session.last_output
    st.subheader("Estado comercial")
    st.metric("Etapa", view.session.stage.value)
    if output is None:
        st.caption("A qualificação começará após a primeira mensagem.")
        return
    left, right = st.columns(2)
    left.metric("Fit", output.fit.value)
    right.metric("Score", output.qualification_score)
    st.write(
        {
            "serviço": output.service.value if output.service else None,
            "dor_principal": output.primary_pain,
            "objeção": output.objection.value if output.objection else None,
            "próxima_ação": output.next_action.value,
            "oferecer_agendamento": output.should_offer_booking,
        }
    )
    with st.expander("Evidências de qualificação"):
        st.json(output.qualification.model_dump(mode="json"))


def render_playground(service: SalesService) -> None:
    st.header("Playground")
    conversations = service.list_conversations()
    labels = {
        str(item.id): (
            f"{item.started_at:%d/%m/%Y %H:%M} · "
            f"{item.current_stage} · {str(item.id)[:8]}"
        )
        for item in conversations
    }

    with st.expander("Iniciar nova conversa", expanded=not conversations):
        with st.form("new_conversation"):
            name = st.text_input("Nome *")
            email = st.text_input("E-mail")
            phone = st.text_input("Telefone")
            submitted = st.form_submit_button("Iniciar")
        if submitted:
            try:
                view = service.start_conversation(
                    name=name, email=email, phone_number=phone
                )
            except ValueError as exc:
                st.error(str(exc))
            else:
                st.session_state.conversation_id = str(view.conversation.id)
                st.rerun()

    if not conversations:
        st.info("Inicie uma conversa para usar o Playground.")
        return

    ids = list(labels)
    current_id = st.session_state.get("conversation_id")
    default_index = ids.index(current_id) if current_id in ids else 0
    selected_id = st.selectbox(
        "Reabrir conversa",
        ids,
        index=default_index,
        format_func=lambda value: labels[value],
    )
    st.session_state.conversation_id = selected_id
    view = service.get_conversation(selected_id)

    chat, state = st.columns([2, 1])
    with chat:
        st.subheader(view.lead.name or "Lead")
        for message in view.messages:
            with st.chat_message(message.role):
                st.write(message.content)
                st.caption(message.stage)
        if user_message := st.chat_input("Digite a mensagem do lead"):
            with st.spinner("VictorIA está respondendo..."):
                service.handle_message(selected_id, user_message)
            st.rerun()
    with state:
        render_state(view)


def render_leads(service: SalesService) -> None:
    st.header("Leads")
    leads = service.list_leads()
    if not leads:
        st.info("Nenhum lead persistido.")
        return
    render_table(
        [
            {
                "Nome": lead.name,
                "E-mail": lead.email,
                "Telefone": lead.phone_number,
                "Canal": lead.channel,
                "Serviço": lead.service_interest,
                "Fit": lead.qualification_status,
                "Score": lead.lead_score,
                "Reunião": lead.meeting_booked,
                "Criado em": lead.created_at,
            }
            for lead in leads
        ]
    )


def render_conversations(service: SalesService) -> None:
    st.header("Conversas")
    conversations = service.list_conversations()
    if not conversations:
        st.info("Nenhuma conversa persistida.")
        return

    rows = []
    for conversation in conversations:
        view = service.get_conversation(conversation.id)
        rows.append(
            {
                "ID": str(conversation.id),
                "Lead": view.lead.name,
                "Etapa": conversation.current_stage,
                "Status": conversation.status,
                "Prompt": conversation.prompt_version,
                "Qualificado": conversation.qualified,
                "Mensagens": len(view.messages),
                "Início": conversation.started_at,
            }
        )
    render_table(rows)

    selected = st.selectbox(
        "Inspecionar histórico",
        [row["ID"] for row in rows],
        format_func=lambda value: next(
            f'{row["Lead"]} · {value[:8]}' for row in rows if row["ID"] == value
        ),
    )
    view = service.get_conversation(selected)
    for message in view.messages:
        st.markdown(f"**{message.role} · {message.stage}**")
        st.write(message.content)


def main() -> None:
    st.title("VictorIA")
    st.caption("Console interno do agente comercial")

    try:
        service = get_service()
    except ValueError:
        st.error(
            "Configuração incompleta. Defina DATABASE_URL, OPENAI_API_KEY e "
            "OPENAI_MODEL no ambiente do servidor."
        )
        st.stop()

    page = st.sidebar.radio("Navegação", ["Playground", "Leads", "Conversas"])
    try:
        if page == "Playground":
            render_playground(service)
        elif page == "Leads":
            render_leads(service)
        else:
            render_conversations(service)
    except (ConversationNotFoundError, PersistenceStateError):
        logger.exception("streamlit_conversation_state_failure")
        st.error(
            "Não foi possível carregar a conversa com segurança. "
            "Atualize a página ou verifique a persistência."
        )
    except Exception:
        logger.exception("streamlit_operation_failure")
        st.error(
            "Não foi possível concluir a operação. "
            "Tente novamente em instantes."
        )


if __name__ == "__main__":
    main()
