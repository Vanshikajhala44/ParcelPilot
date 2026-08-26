from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from parcelpilot.agent import ParcelPilotAgent
from parcelpilot.llm_config import get_llm_provider, is_mistral_enabled
from parcelpilot.settings import retriever_label


st.set_page_config(page_title="ParcelPilot Support AI Agent", page_icon="📦", layout="wide")

# Simple branded header
st.markdown(
        """
        <div class='header'>
            <div class='logo'>📦</div>
            <div>
                <div style='font-weight:600;font-size:20px'>ParcelPilot Customer Support AI Agent</div>
                <div style='font-size:12px;color:#666'>Internal support assistant — policy, account, order, and ticket questions</div>
                <div style='font-size:12px;color:#666;margin-top:4px'>LLM: {0} | Retrieval: {1}</div>
            </div>
        </div>
        """.format(get_llm_provider(), retriever_label()),
        unsafe_allow_html=True,
)

# Inject CSS for custom chat bubbles and badges
st.markdown(
        """
        <style>
        :root{--accent:#0b7285;--bg:#ffffff;--muted:#6c757d;--text:#0f1720;--user-text:#023047}
        .stApp { background: var(--bg); }
        .header{display:flex;align-items:center;gap:12px;margin-bottom:12px;color:var(--text)}
        .logo{font-size:28px}
        .bubble{padding:12px;border-radius:12px;box-shadow:0 1px 3px rgba(0,0,0,.06);max-width:78%;display:inline-block;color:var(--text);font-family:Inter, system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial}
        .user{display:flex;justify-content:flex-end;margin:8px 0}
        .assistant{display:flex;justify-content:flex-start;margin:8px 0}
        .user .bubble{background:linear-gradient(135deg,#f0f8ff,#e6f7ff);text-align:right;color:var(--user-text)}
        .assistant .bubble{background:#ffffff;color:var(--text)}
        .badge{display:inline-block;padding:3px 8px;border-radius:12px;font-size:12px;background:#eef7ff;color:var(--accent);margin-left:8px}
        .provenance{font-size:12px;color:var(--muted);margin-top:6px}
        </style>
        """,
        unsafe_allow_html=True,
)


if "agent" not in st.session_state:
    st.session_state.agent = ParcelPilotAgent(
        ROOT,
        user={"user_id": "bob", "role": "support", "account_ids": ["ACCT-001"]},
    )

if "messages" not in st.session_state:
    st.session_state.messages = [
        {
            "role": "assistant",
            "content": "Hi. I can answer support policy questions, check account/order/ticket records, and prepare confirmation-based actions.",
        }
    ]


for message in st.session_state.messages:
    role = message.get("role", "assistant")
    content = message.get("content", "")
    tool = message.get("tool_used")
    sources = message.get("sources") or []

    # Build HTML bubble
    provenance_html = ""
    if sources:
        first = sources[0]
        label = first.get("authority_label", "unknown")
        path = first.get("source") or first.get("title") or "source"
        provenance_html = f"<div class='provenance'>Source: {path} ({label})</div>"

    badge_html = f"<span class='badge'>Tool: {tool}</span>" if tool else ""

    bubble_html = f"<div class='{role}'><div class='bubble'>{content} {badge_html}{provenance_html}</div></div>"
    st.markdown(bubble_html, unsafe_allow_html=True)

    if message.get("action"):
        action = message["action"]
        with st.expander("Prepared action (expand to view details)"):
            st.json(action)


pending_action = None
for message in reversed(st.session_state.messages):
    if message.get("action"):
        pending_action = message["action"]
        break

if pending_action:
    with st.sidebar:
        st.subheader("Pending Action Confirmation")
        st.write(f"Action: {pending_action['action_type']}")
        st.write(f"Target: {pending_action['target_type']} {pending_action['target_id']}")
        if st.button("Confirm and execute"):
            result = st.session_state.agent.actions.execute_action(
                pending_action["action_id"],
                confirmed=True,
            )
            st.session_state.messages.append(
                {
                    "role": "assistant",
                    "content": f"Executed action successfully. Result: {result['status']}",
                    "tool_used": "state_action_execution",
                    "action": result,
                }
            )
            st.rerun()


prompt = st.chat_input("Ask a policy, ticket, account, or order question")
if prompt:
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    response = st.session_state.agent.handle_message(prompt)

    render = {
        "role": "assistant",
        "content": "",
        "tool_used": response.get("tool_used"),
        "sources": response.get("sources") or [],
    }

    if response.get("status") == "clarification_needed":
        render["content"] = response["question"]
    elif response.get("requires_confirmation"):
        render["content"] = (
            "I prepared the following action and need your confirmation before executing it."
        )
        render["action"] = {
            "action_id": response["action_id"],
            "action_type": response["action_type"],
            "target_type": response["target_type"],
            "target_id": response["target_id"],
            "details": response.get("details", {}),
        }
    elif response.get("summary"):
        render["content"] = response["summary"]
    elif response.get("result") is not None:
        result = response["result"]
        if isinstance(result, list):
            render["content"] = "\n".join(f"- {r.get('source', 'result')}" for r in result[:3])
        elif isinstance(result, dict):
            render["content"] = "\n".join(
                f"**{key}**: {value}" for key, value in list(result.items())[:8]
            )
        else:
            render["content"] = str(result)
    else:
        render["content"] = str(response)

    if response.get("sources"):
        render["sources"] = response["sources"]

    st.session_state.messages.append(render)
    st.rerun()
