import streamlit as st
import requests
import uuid
import random

st.set_page_config(page_title="dubizzle Car Assistant", layout="wide")
st.title("dubizzle Car Assistant")

st.markdown("""
<style>

/* ---------- Main page ---------- */

.block-container {
    max-width: 1150px;
    padding-top: 2rem;
    padding-bottom: 3rem;
}

/* ---------- Chat messages ---------- */

[data-testid="stChatMessage"] {
    padding: 1rem 1.15rem;
    border-radius: 14px;
    margin-bottom: 0.75rem;
}

/* Keep assistant responses readable */
[data-testid="stChatMessage"] p {
    line-height: 1.6;
}

/* ---------- Vehicle images ---------- */

[data-testid="stChatMessage"] img {
    width: 100% !important;
    max-width: 420px !important;
    height: 260px !important;
    object-fit: cover;
    border-radius: 12px;
    margin-top: 0.6rem;
    margin-bottom: 1rem;
    display: block;
}

/* ---------- Listing headings ---------- */

[data-testid="stChatMessage"] h3 {
    margin-top: 1.2rem;
    margin-bottom: 0.35rem;
    font-size: 1.15rem;
}

[data-testid="stChatMessage"] h4 {
    margin-top: 0.8rem;
    margin-bottom: 0.3rem;
}

/* ---------- Lists ---------- */

[data-testid="stChatMessage"] ul {
    margin-top: 0.25rem;
    margin-bottom: 0.8rem;
    padding-left: 1.35rem;
}

[data-testid="stChatMessage"] li {
    margin-bottom: 0.2rem;
}

/* ---------- Budget / note callouts ---------- */

[data-testid="stChatMessage"] blockquote {
    border-left: 4px solid #ff6b00;
    padding: 0.65rem 0.9rem;
    margin: 1rem 0;
    border-radius: 6px;
    background: rgba(255, 107, 0, 0.08);
}

/* ---------- Navigation buttons ---------- */

.scroll-btn {
    position: fixed;
    right: 30px;
    color: white;
    border-radius: 50%;
    width: 45px;
    height: 45px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 20px;
    text-decoration: none;
    box-shadow: 0 4px 6px rgba(0,0,0,0.3);
    z-index: 9999;
    transition: 0.3s;
}

.scroll-btn:hover {
    background-color: #FF3333;
    color: white;
}

.btn-top-ans { bottom: 145px; }
.btn-bottom { bottom: 90px; }

</style>

<a href="#top-of-answer"
   class="scroll-btn btn-top-ans"
   title="Go to top of latest answer">⏫</a>

<a href="#very-bottom"
   class="scroll-btn btn-bottom"
   title="Jump to bottom">⏬</a>
""", unsafe_allow_html=True)


# Streamlit reruns the script after each interaction, so keep chat/session state here.
if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())
if "user_id" not in st.session_state:
    st.session_state.user_id = "user_1"
if "messages" not in st.session_state:
    st.session_state.messages = [{"role": "assistant", "content": "Welcome to dubizzle! What kind of car are you looking for today?"}]

# Controls for testing session and cross-session memory.
with st.sidebar:
    st.header("System Controls")
    
    # Changing the user ID lets the demo switch between stored user profiles.
    new_user = st.text_input("Current User", value=st.session_state.user_id)
    if new_user != st.session_state.user_id:
        st.session_state.user_id = new_user
        st.session_state.session_id = str(uuid.uuid4())
        st.session_state.messages = [{"role": "assistant", "content": f"User switched to {new_user}. How can I help?"}]
        st.rerun()
        
    if st.button("Start New Session (Same User)"):
        st.session_state.session_id = str(uuid.uuid4())
        st.session_state.messages = [{"role": "assistant", "content": "Session memory cleared. Long-term user profile retained. What's next?"}]
        st.rerun()

# Restore the current session's messages after a Streamlit rerun.
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"], unsafe_allow_html=True)

st.markdown('<div id="top-of-answer"></div>', unsafe_allow_html=True)

loading_messages = [
    "Revving up the database...",
    "Searching the dubizzle garage...",
    "Looking under the hood...",
    "Checking the latest inventory...",
    "Fetching your preferences...",
    "Shifting into gear..."
]

if prompt := st.chat_input("Ask about available cars or book a viewing..."):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # Send the message to the FastAPI backend.
    with st.chat_message("assistant"):
        with st.spinner(random.choice(loading_messages)):          
            payload = {
                "session_id": st.session_state.session_id,
                "user_id": st.session_state.user_id,
                "message": prompt,
            }
            
            try:
                response = requests.post(
                    "http://localhost:8000/chat",
                    json=payload,
                    timeout=(5,120),
                )
                response.raise_for_status()
            
                bot_reply = response.json().get("response", "")
                st.markdown(bot_reply, unsafe_allow_html=True)
            
                st.session_state.messages.append(
                    {"role": "assistant", "content": bot_reply}
                )
            
            except requests.exceptions.RequestException as e:
                st.error(f"Backend Error: {e}")
st.markdown('<div id="very-bottom"></div>', unsafe_allow_html=True)
