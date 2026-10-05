"""Conversational, retrieval-augmented inventory management page."""

from __future__ import annotations

import os

import pandas as pd
import streamlit as st

from modules.rag_engine import build_inventory_knowledge_base, query_smart_inventory_rag


def _stock_snapshot(source_data: pd.DataFrame) -> pd.DataFrame:
    """Reduce daily records to the latest known stock state per product."""
    data = source_data.copy()
    data["date"] = pd.to_datetime(data["date"], errors="coerce")
    for column, default in {
        "product_name": "Unknown Product", "category": "Uncategorized",
        "stock_on_hand": 0, "location": "Unknown", "profit_per_unit": 0,
    }.items():
        if column not in data.columns:
            data[column] = default
    return (
        data.sort_values("date")
        .groupby(["product_id", "location"], as_index=False)
        .agg(
            product_name=("product_name", "last"), category=("category", "last"),
            stock_on_hand=("stock_on_hand", "last"), profit_per_unit=("profit_per_unit", "last"),
        )
    )


def _forecast_fingerprint(data: pd.DataFrame) -> int:
    """Identify a forecast snapshot so the FAISS index is rebuilt only when needed."""
    return int(pd.util.hash_pandas_object(data, index=True).sum())


st.title("Smart Inventory Advisor")
st.caption("Ask grounded questions about the latest forecast, current inventory, and replenishment risks.")

# Configure GROQ_API_KEY as a local environment variable or a Streamlit Cloud secret.
groq_api_key = os.getenv("GROQ_API_KEY", "")

forecast_results = st.session_state.get("forecast_results")
source_data = st.session_state.get("forecast_source")
if forecast_results is None or source_data is None:
    st.info("Run next-week predictions on the Demand Forecasting page first. The resulting forecast becomes this advisor's knowledge base.")
    if st.button("Go to Demand Forecasting", type="primary"):
        st.switch_page("pages/2_📊_Demand_Forecasting.py")
    st.stop()

fingerprint = _forecast_fingerprint(forecast_results)
if st.session_state.get("rag_store_fingerprint") != fingerprint:
    try:
        with st.spinner("Indexing the latest sales forecast and stock snapshot..."):
            st.session_state["inventory_faiss_store"] = build_inventory_knowledge_base(
                forecast_results, _stock_snapshot(source_data)
            )
            st.session_state["rag_store_fingerprint"] = fingerprint
        st.session_state["inventory_chat_history"] = []
    except Exception as exc:
        st.error(f"The inventory knowledge base could not be built: {exc}")
        st.stop()

st.success(f"Knowledge base ready: {len(forecast_results):,} forecasted products are available for retrieval.")
if not groq_api_key.strip():
    st.warning("The inventory advisor is unavailable until the GROQ_API_KEY deployment secret is configured.")
if "inventory_chat_history" not in st.session_state:
    st.session_state["inventory_chat_history"] = []

for message in st.session_state["inventory_chat_history"]:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

st.markdown("**Try a store-manager prompt**")
suggestions = [
    "Which products should I restock first?",
    "Which product has the biggest profit opportunity?",
    "Which products are at risk of stockout?",
    "How will upcoming holidays affect our dairy category sales?",
]
suggestion_columns = st.columns(3)
suggested_query = None
for column, prompt in zip(suggestion_columns, suggestions):
    with column:
        if st.button(prompt, use_container_width=True):
            suggested_query = prompt

typed_query = st.chat_input("Ask about restocking, demand risk, or a purchase order…")
user_query = typed_query or suggested_query
if user_query:
    st.session_state["inventory_chat_history"].append({"role": "user", "content": user_query})
    with st.chat_message("user"):
        st.markdown(user_query)
    with st.chat_message("assistant"):
        if not groq_api_key.strip():
            answer = "The inventory advisor is not configured yet. Add GROQ_API_KEY to the app's deployment secrets."
            st.warning(answer)
        else:
            try:
                with st.spinner("Searching your inventory snapshot..."):
                    answer = query_smart_inventory_rag(
                        user_query, st.session_state["inventory_faiss_store"], groq_api_key
                    )
                st.markdown(answer)
            except (ImportError, TypeError, ValueError, RuntimeError) as exc:
                answer = f"I could not complete that inventory request: {exc}"
                st.error(answer)
    st.session_state["inventory_chat_history"].append({"role": "assistant", "content": answer})
