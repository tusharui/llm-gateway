import streamlit as st
import httpx
import json
import os
import websocket
import threading

API_URL = os.getenv("GATEWAY_API_URL", "http://localhost:8000")
API_KEY = os.getenv("GATEWAY_API_KEY", "sk-gateway-dev-key")

st.set_page_config(page_title="AI Inference Gateway", layout="wide")

st.sidebar.title("AI Gateway")
page = st.sidebar.radio(
    "Navigate",
    ["Dashboard", "Chat", "Models", "Health", "API Keys", "Analytics"],
)

headers = {"Authorization": f"Bearer {API_KEY}"}


def api_get(path, **kwargs):
    with httpx.Client(timeout=30) as client:
        return client.get(f"{API_URL}{path}", headers=headers, **kwargs)


def api_post(path, json_data=None, **kwargs):
    with httpx.Client(timeout=60) as client:
        return client.post(f"{API_URL}{path}", headers=headers, json=json_data, **kwargs)


def api_patch(path, json_data=None, **kwargs):
    with httpx.Client(timeout=10) as client:
        return client.patch(f"{API_URL}{path}", headers=headers, json=json_data, **kwargs)


def api_delete(path, **kwargs):
    with httpx.Client(timeout=10) as client:
        return client.delete(f"{API_URL}{path}", headers=headers, **kwargs)


if page == "Dashboard":
    st.title("AI Inference Gateway")
    st.markdown("**v1.0.0** — Unified AI provider gateway")
    st.divider()

    cols = st.columns(3)
    with cols[0]:
        st.info("POST /chat\n\nChat completions with streaming")
    with cols[1]:
        st.info("GET /models\n\nBrowse available AI models")
    with cols[2]:
        st.info("GET /health\n\nProvider health status")

    cols2 = st.columns(3)
    with cols2[0]:
        st.info("WS /ws/chat\n\nWebSocket real-time streaming")
    with cols2[1]:
        st.info("POST /api-keys\n\nAPI key management")
    with cols2[2]:
        st.info("GET /analytics\n\nUsage analytics")

    with st.expander("Configuration"):
        st.code(f"API URL: {API_URL}")
        st.code(f"API Key: {API_KEY[:12]}...")


elif page == "Chat":
    st.title("Chat")

    if "messages" not in st.session_state:
        st.session_state.messages = []

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    col1, col2 = st.columns([3, 1])
    with col2:
        model = st.selectbox(
            "Model",
            [
                "llama-3.3-70b-versatile",
                "gemini-2.0-flash",
                "openai/gpt-4o-mini",
                "anthropic/claude-3.5-haiku",
                "llama-3.1-8b-instant",
                "gemini-1.5-flash",
            ],
        )
        stream_enabled = st.toggle("Stream", value=True)
        use_websocket = st.toggle("WebSocket mode", value=False)
        temperature = st.slider("Temperature", 0.0, 2.0, 0.7, 0.1)

    if prompt := st.chat_input("Type a message..."):
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        api_messages = [
            {"role": m["role"], "content": m["content"]}
            for m in st.session_state.messages
        ]

        with st.chat_message("assistant"):
            if use_websocket and stream_enabled:
                placeholder = st.empty()
                full_response = ""
                try:
                    ws_url = API_URL.replace("http://", "ws://").replace("https://", "wss://")
                    ws = websocket.create_connection(f"{ws_url}/ws/chat", timeout=60)
                    ws.send(json.dumps({
                        "model": model,
                        "messages": api_messages,
                        "temperature": temperature,
                    }))
                    while True:
                        raw = ws.recv()
                        data = json.loads(raw)
                        if data.get("done"):
                            break
                        if "error" in data:
                            full_response = f"Error: {data['error']}"
                            break
                        choices = data.get("choices", [])
                        if choices:
                            content = choices[0].get("delta", {}).get("content", "")
                            if content:
                                full_response += content
                                placeholder.markdown(full_response + "▌")
                    ws.close()
                    placeholder.markdown(full_response)
                except Exception as e:
                    full_response = f"WebSocket error: {str(e)}"
                    placeholder.markdown(full_response)

            elif stream_enabled:
                placeholder = st.empty()
                full_response = ""
                try:
                    with httpx.Client(timeout=60) as client:
                        with client.stream(
                            "POST",
                            f"{API_URL}/chat",
                            headers=headers,
                            json={
                                "model": model,
                                "messages": api_messages,
                                "stream": True,
                                "temperature": temperature,
                            },
                        ) as resp:
                            for line in resp.iter_lines():
                                if not line:
                                    continue
                                if line.startswith("event: done"):
                                    break
                                if line.startswith("event: error"):
                                    continue
                                if line.startswith("data: "):
                                    data_str = line[6:]
                                    try:
                                        chunk = json.loads(data_str)
                                        delta = chunk.get("choices", [{}])[0].get(
                                            "delta", {}
                                        )
                                        content = delta.get("content", "")
                                        if content:
                                            full_response += content
                                            placeholder.markdown(full_response + "▌")
                                    except json.JSONDecodeError:
                                        continue
                    placeholder.markdown(full_response)
                except Exception as e:
                    full_response = f"Error: {str(e)}"
                    placeholder.markdown(full_response)
            else:
                try:
                    with httpx.Client(timeout=60) as client:
                        resp = client.post(
                            f"{API_URL}/chat",
                            headers=headers,
                            json={
                                "model": model,
                                "messages": api_messages,
                                "stream": False,
                                "temperature": temperature,
                            },
                        )
                        data = resp.json()
                        full_response = data["choices"][0]["message"]["content"]
                        st.markdown(full_response)
                except Exception as e:
                    full_response = f"Error: {str(e)}"
                    st.markdown(full_response)

            st.session_state.messages.append(
                {"role": "assistant", "content": full_response}
            )


elif page == "Models":
    st.title("Models")
    try:
        resp = api_get("/models")
        data = resp.json()
        models = data.get("data", [])
        by_provider = {}
        for m in models:
            by_provider.setdefault(m["provider"], []).append(m)

        for provider, provider_models in by_provider.items():
            with st.expander(f"{provider.title()} ({len(provider_models)} models)", expanded=True):
                for m in provider_models:
                    caps = ", ".join(m.get("capabilities", []))
                    ctx = m.get("context_length")
                    ctx_str = f" | Context: {ctx:,}" if ctx else ""
                    st.markdown(f"**{m['id']}** — {caps}{ctx_str}")
    except Exception as e:
        st.error(f"Failed to fetch models: {e}")


elif page == "Health":
    st.title("Health")
    auto_refresh = st.checkbox("Auto-refresh (10s)", value=True)
    try:
        resp = api_get("/health")
        data = resp.json()
        st.subheader(f"Status: **{data['status'].upper()}**")
        st.caption(f"Timestamp: {data['timestamp']}")

        for p in data.get("providers", []):
            status_color = "green" if p["status"] == "healthy" else "red"
            st.markdown(
                f":{status_color}[●] **{p['provider']}** — {p['status']} ({p['latency_ms']}ms)"
            )
    except Exception as e:
        st.error(f"Health check failed: {e}")

    if auto_refresh:
        import time
        time.sleep(10)
        st.rerun()


elif page == "API Keys":
    st.title("API Keys")

    tab_create, tab_manage = st.tabs(["Create Key", "Manage Keys"])

    with tab_create:
        st.subheader("Create new API key")
        with st.form("create_key"):
            key_name = st.text_input("Name", placeholder="my-app-key")
            rate_limit = st.number_input("Rate limit (requests/window)", value=60, min_value=1, max_value=10000)
            window_ms = st.number_input("Window (ms)", value=60000, min_value=1000, max_value=3600000)
            submitted = st.form_submit_button("Create")

        if submitted and key_name:
            try:
                resp = api_post("/api-keys", json_data={
                    "name": key_name,
                    "rate_limit_max": rate_limit,
                    "rate_limit_window_ms": window_ms,
                })
                if resp.status_code == 200:
                    data = resp.json()
                    st.success("Key created!")
                    st.code(data["key"], language=None)
                    st.warning("Copy this key now — it won't be shown again.")
                else:
                    st.error(f"Error: {resp.text}")
            except Exception as e:
                st.error(f"Failed: {e}")

    with tab_manage:
        st.subheader("All API keys")
        try:
            resp = api_get("/api-keys")
            keys = resp.json().get("keys", [])

            if not keys:
                st.info("No API keys yet. Create one above.")
            else:
                for key in keys:
                    with st.expander(f"{key['name']} ({key['key_prefix']}...)"):
                        col1, col2, col3, col4 = st.columns([2, 1, 1, 1])
                        with col1:
                            status = "Active" if key["is_active"] else "Disabled"
                            st.write(f"**Status:** {status}")
                            st.write(f"**Rate limit:** {key['rate_limit_max']} req / {key['rate_limit_window_ms']}ms")
                            st.write(f"**Created:** {key['created_at']}")
                            st.write(f"**Last used:** {key.get('last_used_at', 'Never')}")
                        with col2:
                            if st.button("Toggle", key=f"toggle_{key['id']}"):
                                api_post(f"/api-keys/{key['id']}/toggle")
                                st.rerun()
                        with col3:
                            new_name = st.text_input("Rename", value=key["name"], key=f"name_{key['id']}")
                            if st.button("Save", key=f"save_{key['id']}"):
                                api_patch(f"/api-keys/{key['id']}", json_data={"name": new_name})
                                st.rerun()
                        with col4:
                            if st.button("Delete", key=f"del_{key['id']}", type="primary"):
                                api_delete(f"/api-keys/{key['id']}")
                                st.rerun()
        except Exception as e:
            st.error(f"Failed to load keys: {e}")


elif page == "Analytics":
    st.title("Analytics")

    days = st.selectbox("Time range", [1, 7, 14, 30], index=1)

    try:
        summary_resp = api_get(f"/analytics/summary?days={days}")
        summary = summary_resp.json()

        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Total Requests", f"{summary.get('total_requests', 0):,}")
        with col2:
            st.metric("Total Tokens", f"{summary.get('total_tokens', 0):,}")
        with col3:
            cost = summary.get("total_cost", 0)
            st.metric("Total Cost", f"${cost:.4f}")
        with col4:
            latency = summary.get("avg_latency", 0)
            st.metric("Avg Latency", f"{int(latency)}ms")

        col5, col6, col7 = st.columns(3)
        with col5:
            st.metric("Successful", f"{summary.get('successful', 0):,}")
        with col6:
            st.metric("Failed", f"{summary.get('failed', 0):,}")
        with col7:
            st.metric("Cached", f"{summary.get('cached', 0):,}")

        st.divider()

        tab_provider, tab_model, tab_timeline, tab_recent = st.tabs(
            ["By Provider", "By Model", "Timeline", "Recent Requests"]
        )

        with tab_provider:
            prov_resp = api_get(f"/analytics/by-provider?days={days}")
            providers = prov_resp.json().get("providers", [])
            if providers:
                st.bar_chart(
                    data={p["provider"]: p["cost"] for p in providers},
                    horizontal=True,
                )
                st.dataframe(providers, use_container_width=True)
            else:
                st.info("No data for this period.")

        with tab_model:
            model_resp = api_get(f"/analytics/by-model?days={days}")
            models = model_resp.json().get("models", [])
            if models:
                st.dataframe(models, use_container_width=True)
            else:
                st.info("No data for this period.")

        with tab_timeline:
            time_resp = api_get(f"/analytics/timeline?days={days}")
            timeline = time_resp.json().get("timeline", [])
            if timeline:
                import pandas as pd
                df = pd.DataFrame(timeline)
                df["day"] = pd.to_datetime(df["day"])
                st.line_chart(df.set_index("day")[["requests", "tokens"]])
                st.line_chart(df.set_index("day")[["cost"]])
                st.dataframe(timeline, use_container_width=True)
            else:
                st.info("No data for this period.")

        with tab_recent:
            recent_resp = api_get(f"/analytics/recent?limit=50")
            requests = recent_resp.json().get("requests", [])
            if requests:
                st.dataframe(requests, use_container_width=True)
            else:
                st.info("No requests recorded yet.")

    except Exception as e:
        st.error(f"Failed to load analytics: {e}")
